"""Batch processing: give Quantivex IBM files, get one complete, checksummed results folder.

Inputs may be any mix of

* IBM Quantum Platform calibration CSV files,
* IBM ``BackendProperties`` JSON files,
* Quantivex calibration snapshots (``quantivex export`` or bundled data),
* Quantivex hardware-run folders (``plan.json`` + ``circuits.qpy`` + returned results),
* directories containing any of the above (searched recursively).

Every number is computed by deterministic code first. A language model then writes a
plain-language report from those numbers; its text is labelled unverified and every number it
writes is checked against the computed results. If no model is reachable the results folder is
still complete, and the report records why the explanation is missing.

The output folder is written under a temporary name and renamed only when complete, so a crash
never leaves a folder that looks finished. An existing folder is never overwritten.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import time
import traceback
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .errors import QuantivexError

FORMAT_VERSION = 1
MAX_DEPTH = 6
MAX_INPUTS = 500
_SLUG = re.compile(r"[^a-z0-9]+")

# Column order of the two summary tables (also used in the Markdown summary).
CALIBRATION_COLUMNS = [
    "id",
    "source_format",
    "backend",
    "calibration_timestamp",
    "qubits",
    "links",
    "median_readout_error",
    "median_cz_error",
    "best_chain",
    "best_chain_esp",
    "e_noisy_hartree",
    "error_mha",
    "confidence_radius_mha",
    "verdict",
    "beats_hartree_fock",
    "status",
]
HARDWARE_COLUMNS = [
    "id",
    "backend",
    "prepared_at",
    "total_shots",
    "energy_hartree",
    "error_mha",
    "stderr_mha",
    "confidence_radius_mha",
    "verdict",
    "excludes_reference",
    "beats_hartree_fock",
    "predicted_error_mha",
    "integrity_ok",
    "status",
]


@dataclass(frozen=True)
class ProcessConfig:
    """What to compute for every calibration file. Defaults match the packaged H2 study."""

    molecule: str = "H2"
    bond_length: float = 0.75
    active_space: str = "2e2o"
    circuit_mode: str = "compact"
    readout_mitigation: bool = True
    shots: int = 4000
    seed: int = 1234
    top_chains: int = 5
    compare_layouts: bool = False
    random_chains: int = 15
    predict_hardware: bool = True
    report: str = "llm"  # "llm" or "none"
    require_llm: bool = False

    def validated(self) -> ProcessConfig:
        from .agent import ACTIVE_SPACES

        if self.active_space not in ACTIVE_SPACES:
            raise QuantivexError(f"active_space must be one of {sorted(ACTIVE_SPACES)}")
        if self.circuit_mode not in ("compact", "uccsd"):
            raise QuantivexError("circuit_mode must be 'compact' or 'uccsd'")
        if not 100 <= self.shots <= 100_000:
            raise QuantivexError("shots must be between 100 and 100000")
        if not 0 <= self.seed <= 2**31 - 1:
            raise QuantivexError("seed is outside the supported range")
        if not 0.3 <= self.bond_length <= 4.0:
            raise QuantivexError("bond_length must be between 0.3 and 4.0 angstrom")
        if not 1 <= self.top_chains <= 20:
            raise QuantivexError("top_chains must be between 1 and 20")
        if not 0 <= self.random_chains <= 30:
            raise QuantivexError("random_chains must be between 0 and 30")
        if self.report not in ("llm", "none"):
            raise QuantivexError("report must be 'llm' or 'none'")
        return self


@dataclass
class ProcessResult:
    out_dir: Path
    calibrations: list[dict[str, Any]] = field(default_factory=list)
    hardware: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    report_status: str = "not requested"

    @property
    def exit_code(self) -> int:
        """0 complete; 1 some inputs failed; 2 nothing processed; 3 model report required but missing."""
        if not self.calibrations and not self.hardware:
            return 2
        if self.failed:
            return 1
        if self.report_status.startswith("required but"):
            return 3
        return 0


# --------------------------------------------------------------------------------------------- utilities
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean(value: Any) -> Any:
    """JSON-safe copy: non-finite floats become None, tuples become lists, unknown objects strings."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    try:
        return _clean(float(value))
    except (TypeError, ValueError):
        return str(value)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_clean(value), indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _cell(row.get(k)) for k in columns})


def _cell(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    if isinstance(value, float):
        return f"{value:.6g}" if math.isfinite(value) else ""
    return "" if value is None else value


def _slug(name: str, taken: set[str]) -> str:
    base = _SLUG.sub("_", name.lower()).strip("_")[:60] or "input"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}_{n}", n + 1
    taken.add(slug)
    return slug


def _median(values: Iterable[float]) -> float | None:
    data = [v for v in values if v is not None]
    return statistics.median(data) if data else None


# --------------------------------------------------------------------------------------------- discovery
@dataclass(frozen=True)
class InputItem:
    kind: str
    path: Path
    display: str  # path relative to the given input root: no user-home paths in the results


def discover(inputs: Sequence[str | Path]) -> tuple[list[InputItem], list[dict[str, Any]]]:
    """Find processable inputs. Symbolic links are not followed; hidden entries are ignored."""
    from .ibm_formats import detect_kind

    found: list[InputItem] = []
    skipped: list[dict[str, Any]] = []
    seen: set[Path] = set()

    def visit(path: Path, root: Path, depth: int) -> None:
        display = path.name if path == root else path.relative_to(root.parent).as_posix()
        if path.is_symlink():
            skipped.append({"input": display, "reason": "symbolic link not followed"})
            return
        resolved = path.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        if not path.exists():
            skipped.append({"input": display, "reason": "not found"})
            return
        kind = detect_kind(path)
        if kind == "directory":
            if depth >= MAX_DEPTH:
                skipped.append({"input": display, "reason": f"deeper than {MAX_DEPTH} folder levels"})
                return
            for child in sorted(path.iterdir()):
                if not child.name.startswith((".", "__")):
                    visit(child, root, depth + 1)
            return
        if kind == "unknown":
            if path.suffix.lower() in (".csv", ".json"):
                skipped.append({"input": display, "reason": "not a recognised IBM or Quantivex file"})
            return
        found.append(InputItem(kind, path, display))

    for given in inputs:
        p = Path(given)
        visit(p, p, 0)
    if len(found) > MAX_INPUTS:
        raise QuantivexError(f"{len(found)} inputs found; the limit for one run is {MAX_INPUTS}")
    return found, skipped


# --------------------------------------------------------------------------------------------- calibrations
def _convert(item: InputItem) -> Any:
    from .data import load
    from .ibm_formats import Conversion, snapshot_from_csv, snapshot_from_properties

    if item.kind == "ibm_csv":
        return snapshot_from_csv(item.path)
    if item.kind == "ibm_properties_json":
        raw = json.loads(item.path.read_text(encoding="utf-8"))
        return snapshot_from_properties(
            raw, label=item.path.stem, provenance=f"IBM BackendProperties ({item.path.name})"
        )
    return Conversion(load(item.path), "quantivex_snapshot", [])


class _Problem:
    """Problem and noiseless VQE computed once per run (identical for every calibration)."""

    def __init__(self, config: ProcessConfig) -> None:
        from .agent import ACTIVE_SPACES, MAX_NOISY_QUBITS
        from .chemistry import get_problem
        from .vqe import run_vqe

        ne, no = ACTIVE_SPACES[config.active_space]
        self.problem = get_problem(config.molecule, round(config.bond_length, 2), ne, no)
        if self.problem.n_qubits > MAX_NOISY_QUBITS:
            raise QuantivexError(f"Noisy simulation is limited to {MAX_NOISY_QUBITS} qubits; use 2e2o or 4e4o")
        self.vqe = run_vqe(self.problem)


def _process_calibration(
    item: InputItem, slug: str, folder: Path, config: ProcessConfig, problem: _Problem
) -> dict[str, Any]:
    from . import viz
    from .data import save, summary
    from .experiment import compare_layouts, estimate_energy
    from .hardware import rank_chains

    folder.mkdir(parents=True)
    conversion = _convert(item)
    snap = conversion.snapshot
    save(snap, folder / "snapshot.json")
    _write_json(folder / "conversion.json", {"input": item.display, **conversion.describe()})
    dataset = summary(snap)
    _write_json(folder / "dataset_summary.json", dataset)

    n = problem.problem.n_qubits
    chains = rank_chains(snap, n, include_readout=not config.readout_mitigation)
    top = [c.as_dict() for c in chains[: config.top_chains]]
    _write_json(
        folder / "chains.json",
        {"length": n, "usable_chains": len(chains), "best": top, "worst": chains[-1].as_dict()},
    )
    est = estimate_energy(
        problem.vqe, snap, chains[0].qubits, config.shots, config.seed, config.circuit_mode, config.readout_mitigation
    )
    noisy = est.describe(problem.problem.e_exact, problem.problem.e_hf)
    chemistry = {
        "problem": problem.problem.describe(),
        "vqe": problem.vqe.describe(),
        "noisy": noisy,
        "layout_source": "best_chain",
        "execution_type": "local calibration-driven Aer simulation; no IBM QPU used",
    }
    _write_json(folder / "chemistry.json", chemistry)
    viz.plot_chip_map(snap, folder / "chip_map.png", list(est.layout), "Best chain (used)")
    if config.compare_layouts:
        comp = compare_layouts(
            snap,
            problem.problem.molecule,
            problem.problem.bond_length,
            problem.problem.n_electrons,
            problem.problem.n_orbitals,
            config.random_chains,
            config.shots,
            config.seed,
            config.circuit_mode,
            config.readout_mitigation,
        )
        _write_json(folder / "layout_comparison.json", comp.describe())
        viz.plot_layout_comparison(comp, folder / "layout_comparison.png")

    links = [link.gate_error for link in snap.links]
    return {
        "id": slug,
        "input": item.display,
        "source_format": conversion.source_format,
        "backend": snap.backend,
        "calibration_timestamp": snap.calibration_timestamp,
        "qubits": snap.num_qubits,
        "links": len(snap.links),
        "median_readout_error": _median(q.readout_error for q in snap.qubits),
        "median_cz_error": _median(links),
        "best_chain": list(chains[0].qubits),
        "best_chain_esp": round(chains[0].esp, 6),
        "e_noisy_hartree": noisy.get("e_noisy_hartree"),
        "error_mha": noisy.get("error_mha"),
        "confidence_radius_mha": noisy.get("confidence_radius_mha"),
        "verdict": noisy.get("verdict"),
        "beats_hartree_fock": noisy.get("beats_hartree_fock"),
        "conversion_notes": conversion.notes,
        "status": "ok",
    }


# --------------------------------------------------------------------------------------------- hardware
def _verify_manifest(folder: Path) -> dict[str, Any]:
    manifest = folder / "SHA256SUMS.txt"
    if not manifest.is_file():
        return {"manifest": "absent"}
    bad, checked = [], 0
    for line in manifest.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        digest, name = parts[0], parts[1].lstrip("*")
        target = (folder / name).resolve()
        if folder.resolve() not in target.parents or not target.is_file():
            bad.append(name)
            continue
        checked += 1
        if sha256_file(target) != digest:
            bad.append(name)
    return {"manifest": "ok" if not bad else "mismatch", "files_checked": checked, "mismatched_files": bad}


def _raw_counts(folder: Path) -> list[dict[str, int]] | None:
    raw_path = folder / "ibm_runtime_raw_result.json"
    if not raw_path.is_file():
        return None
    try:
        from qiskit_ibm_runtime import RuntimeDecoder
    except ImportError:
        return None
    raw = json.loads(raw_path.read_text(encoding="utf-8"), cls=RuntimeDecoder)
    return [pub.data.meas.get_counts() for pub in raw]


def predict_hardware_error(plan: dict[str, Any], circuits: list[Any]) -> dict[str, Any]:
    """Exact (density-matrix) expectation of the compiled circuits under Quantivex's noise model.

    The model is built from the calibration IBM reported when the job was prepared, and includes
    unmitigated symmetric readout error, so it is directly comparable with the measured energy.
    """
    import numpy as np
    from qiskit_aer import AerSimulator

    from .ibm_formats import snapshot_from_properties
    from .noise import build_noise_model

    snap = snapshot_from_properties(plan["calibration_at_preparation"]).snapshot
    sim = AerSimulator(method="density_matrix", noise_model=build_noise_model(snap))
    n = int(plan["logical_qubits"])
    energy = 0.0
    for qc, terms, meta in zip(circuits, plan["groups"], plan["circuits"], strict=True):
        phys = list(meta["final_layout"][:n])
        bare = qc.remove_final_measurements(inplace=False)
        bare.save_probabilities(qubits=phys)
        probs = np.asarray(sim.run(bare).result().data()["probabilities"])
        idx = np.arange(len(probs))
        for label, coeff in terms:
            support = [q for q in range(n) if label[n - 1 - q] != "I"]
            parity = np.zeros(len(probs), dtype=int)
            attenuation = 1.0
            for q in support:
                parity ^= (idx >> q) & 1
                attenuation *= 1 - 2 * snap.qubit_map[phys[q]].readout_error
            energy += float(np.real(coeff)) * attenuation * float(np.sum(probs * (1 - 2 * parity)))
    reference = float(plan["reference_energy_hartree"])
    return {
        "predicted_energy_hartree": energy,
        "predicted_error_mha": 1000 * (energy - reference),
        "model": "Quantivex noise model from calibration at preparation; exact density matrix; readout unmitigated",
    }


def _process_hardware(item: InputItem, slug: str, folder: Path, config: ProcessConfig) -> dict[str, Any]:
    from qiskit.quantum_info import SparsePauliOp

    from .experiment import confidence_radius, verdict
    from .ibm_execution import energy_from_counts, load_plan

    folder.mkdir(parents=True)
    plan, circuits, plan_digest = load_plan(item.path)  # validates plan schema and circuit checksum
    integrity = {"circuits_sha256": "ok", **_verify_manifest(item.path)}
    problem = plan.get("problem", {})
    base = {
        "id": slug,
        "input": item.display,
        "backend": plan.get("backend"),
        "prepared_at": plan.get("created_at"),
        "execution": plan.get("execution"),
        "logical_qubits": plan.get("logical_qubits"),
        "shots_per_group": plan.get("shots_per_group"),
        "molecule": problem.get("molecule"),
        "bond_length_angstrom": problem.get("bond_length_angstrom"),
        "circuit_mode": plan.get("circuit_mode"),
        "initial_layout": plan["circuits"][0].get("initial_layout") if plan.get("circuits") else None,
        "two_qubit_gates": plan["circuits"][0].get("two_qubit_gates") if plan.get("circuits") else None,
        "depth": plan["circuits"][0].get("depth") if plan.get("circuits") else None,
    }
    result_path = item.path / "result.json"
    if not result_path.is_file():
        pending = {**base, "status": "no results yet (job not fetched)", "integrity": integrity}
        _write_json(folder / "analysis.json", pending)
        return {**pending, "integrity_ok": integrity["manifest"] != "mismatch"}

    stored = json.loads(result_path.read_text(encoding="utf-8"))
    integrity["plan_sha256_matches_result"] = stored.get("plan_sha256") == plan_digest
    counts = stored.get("counts")
    raw = _raw_counts(item.path)
    if raw is not None:
        integrity["raw_counts_match_result"] = raw == counts
        counts = raw  # IBM's own returned record is the primary source
    else:
        integrity["raw_counts_match_result"] = "not checked (raw IBM file absent or qiskit-ibm-runtime missing)"
    if not isinstance(counts, list):
        raise QuantivexError("result.json has no measurement counts")

    energy, stderr, actual = energy_from_counts(plan["groups"], counts)
    radius = confidence_radius([SparsePauliOp.from_list(g) for g in plan["groups"]], counts)
    reference = float(plan["reference_energy_hartree"])
    error = energy - reference
    judged = verdict(error, stderr, radius)
    e_hf = problem.get("e_hf_hartree")
    recomputed_vs_stored = abs(energy - float(stored["energy_hartree"])) if "energy_hartree" in stored else None
    integrity["energy_matches_stored_result"] = recomputed_vs_stored is not None and recomputed_vs_stored < 1e-9
    integrity_ok = all(
        v is True or v == "ok" or (isinstance(v, str) and v.startswith(("absent", "not checked")))
        for k, v in integrity.items()
        if k not in ("files_checked", "mismatched_files")
    )
    record: dict[str, Any] = {
        **base,
        "total_shots": sum(actual),
        "energy_hartree": energy,
        "reference_energy_hartree": reference,
        "hartree_fock_energy_hartree": e_hf,
        "error_mha": 1000 * error,
        "stderr_mha": 1000 * stderr,
        "confidence_radius_mha": 1000 * radius,
        "verdict": judged["verdict"],
        "excludes_reference": abs(error) > radius,
        "beats_hartree_fock": None if e_hf is None else abs(error) < abs(float(e_hf) - reference),
        "readout_mitigation": False,
        "uncertainty_scope": judged["reason"] + "; " + judged["uncertainty_excludes"],
        "integrity": integrity,
        "integrity_ok": integrity_ok,
        "status": "ok",
    }
    if config.predict_hardware and isinstance(plan.get("calibration_at_preparation"), dict):
        try:
            record.update(predict_hardware_error(plan, circuits))
        except Exception as exc:  # noqa: BLE001 - a failed prediction must not discard measured results
            record["prediction_unavailable"] = f"{type(exc).__name__}: {exc}"[:300]
    _write_json(folder / "analysis.json", record)
    return record


# --------------------------------------------------------------------------------------------- figures
def _figures(
    out: Path, calibrations: list[dict[str, Any]], hardware: list[dict[str, Any]], hf_mha: float | None
) -> None:
    import matplotlib.pyplot as plt

    from . import viz  # noqa: F401 - selects the non-interactive backend

    def bars(rows: list[dict[str, Any]], label_key: str, path: Path, title: str, predicted: bool) -> None:
        rows = [r for r in rows if r.get("error_mha") is not None]
        if not rows:
            return
        labels = [str(r.get(label_key) or r["id"]) for r in rows]
        err = [abs(r["error_mha"]) for r in rows]
        rad = [r.get("confidence_radius_mha") or 0.0 for r in rows]
        fig, ax = plt.subplots(figsize=(max(5.0, 0.7 * len(rows) + 2), 4.0), dpi=150)
        x = range(len(rows))
        ax.bar(x, err, color="#2f6fd6", alpha=0.85, label="error")
        ax.errorbar(x, err, yerr=rad, fmt="none", ecolor="#4b5563", elinewidth=1, capsize=3, label="± 95% radius")
        if predicted:
            pred = [
                (i, r["predicted_error_mha"]) for i, r in enumerate(rows) if r.get("predicted_error_mha") is not None
            ]
            if pred:
                ax.scatter(
                    [p[0] for p in pred],
                    [abs(p[1]) for p in pred],
                    marker="D",
                    s=28,
                    color="#d1495b",
                    zorder=3,
                    label="predicted by noise model",
                )
        if hf_mha is not None:
            ax.axhline(hf_mha, ls="--", color="gray", lw=1, label=f"Hartree-Fock error ({hf_mha:.2f})")
        ax.axhline(1.6, ls=":", color="#1a8a4a", lw=1, label="1.6 mHa tolerance")
        ax.set_xticks(list(x), labels, rotation=45, ha="right", fontsize=7)
        ax.set_ylabel("|E - E_exact| (mHa)")
        ax.set_ylim(bottom=0)
        ax.legend(fontsize=7, frameon=False, ncol=5, loc="lower center", bbox_to_anchor=(0.5, 1.0))
        fig.suptitle(title, fontsize=10, fontweight="bold")
        fig.tight_layout(rect=(0, 0, 1, 0.95))
        fig.savefig(path)
        plt.close(fig)

    figs = out / "figures"
    figs.mkdir(exist_ok=True)
    bars(calibrations, "id", figs / "calibration_errors.png", "Simulated error per calibration", False)
    bars(hardware, "id", figs / "hardware_errors.png", "Measured error per hardware run", True)


# --------------------------------------------------------------------------------------------- reports
def _fmt(value: Any, digits: int = 1) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return str(value)


def _table(rows: list[dict[str, Any]], columns: list[tuple[str, str, int]]) -> str:
    head = "| " + " | ".join(c[1] for c in columns) + " |\n| " + " | ".join("---" for _ in columns) + " |\n"
    return head + "".join("| " + " | ".join(_fmt(r.get(c[0]), c[2]) for c in columns) + " |\n" for r in rows)


_VERDICT_WORDS = {
    "MODEL_AGREEMENT_95": "reliable",
    "OUTSIDE_TOLERANCE_95": "not reliable",
    "INCONCLUSIVE_95": "undecided",
}


def _summary_markdown(result: ProcessResult, config: ProcessConfig, problem: dict[str, Any] | None) -> str:
    lines = [f"# Quantivex results ({__version__})", ""]
    lines.append(
        f"Processed {len(result.calibrations)} calibration file(s) and {len(result.hardware)} hardware run(s); "
        f"{len(result.failed)} failed and {len(result.skipped)} skipped."
    )
    if result.calibrations and problem:
        lines += [
            "",
            "## Calibration files (local noisy simulation)",
            "",
            f"Calculation: {config.molecule} at {config.bond_length} Å, active space {config.active_space}, "
            f"{config.circuit_mode} circuit, read-out mitigation {'on' if config.readout_mitigation else 'off'}, "
            f"{config.shots} shots per group, seed {config.seed}. Exact energy {problem.get('e_exact_hartree')} Ha, "
            f"Hartree-Fock {problem.get('e_hf_hartree')} Ha. Verdicts use a 1.6 mHa tolerance and the 95% radius.",
            "",
            _table(
                [
                    {**r, "verdict": _VERDICT_WORDS.get(str(r.get("verdict")), r.get("verdict"))}
                    for r in result.calibrations
                ],
                [
                    ("id", "Calibration", 0),
                    ("backend", "Chip", 0),
                    ("calibration_timestamp", "Calibrated", 0),
                    ("best_chain", "Best qubits", 0),
                    ("error_mha", "Error (mHa)", 1),
                    ("confidence_radius_mha", "± 95%", 1),
                    ("verdict", "Verdict", 0),
                ],
            ),
        ]
    if result.hardware:
        lines += [
            "",
            "## Hardware runs (measured on IBM processors)",
            "",
            "Energies recomputed from the returned counts. No read-out correction was applied on hardware.",
            "",
            _table(
                [
                    {**r, "verdict": _VERDICT_WORDS.get(str(r.get("verdict")), r.get("verdict"))}
                    for r in result.hardware
                ],
                [
                    ("id", "Run", 0),
                    ("backend", "Chip", 0),
                    ("total_shots", "Shots", 0),
                    ("error_mha", "Error (mHa)", 1),
                    ("confidence_radius_mha", "± 95%", 1),
                    ("verdict", "Verdict", 0),
                    ("predicted_error_mha", "Predicted (mHa)", 1),
                    ("integrity_ok", "Checks pass", 0),
                ],
            ),
        ]
    if result.failed:
        lines += ["", "## Failed inputs", ""] + [f"- `{f['input']}`: {f['error']}" for f in result.failed]
    if result.skipped:
        lines += ["", "## Skipped inputs", ""] + [f"- `{s['input']}`: {s['reason']}" for s in result.skipped]
    lines += [
        "",
        "Uncertainty covers finite-shot sampling only; it excludes calibration uncertainty, drift, device bias "
        "and electronic-structure model error.",
    ]
    return "\n".join(lines) + "\n"


def _facts(result: ProcessResult, config: ProcessConfig, problem: dict[str, Any] | None) -> dict[str, Any]:
    keep_c = [
        "id",
        "backend",
        "calibration_timestamp",
        "best_chain",
        "error_mha",
        "confidence_radius_mha",
        "verdict",
        "beats_hartree_fock",
        "median_readout_error",
        "median_cz_error",
    ]
    keep_h = [
        "id",
        "backend",
        "total_shots",
        "error_mha",
        "stderr_mha",
        "confidence_radius_mha",
        "verdict",
        "excludes_reference",
        "beats_hartree_fock",
        "predicted_error_mha",
        "integrity_ok",
    ]

    def rounded(row: dict[str, Any], keep: list[str]) -> dict[str, Any]:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in row.items() if k in keep}

    return _clean(
        {
            "calculation": asdict(config) | {"problem": problem},
            "calibrations": [rounded(r, keep_c) for r in result.calibrations],
            "hardware_runs": [rounded(r, keep_h) for r in result.hardware],
            "failed_inputs": len(result.failed),
            "tolerance_mha": 1.6,
        }
    )


_REPORT_SYSTEM = (
    "You write a short plain-language results report for scientists from other fields. Use ONLY the "
    "numbers in the JSON you are given; never compute new numbers, never invent values. Verdict codes: "
    "MODEL_AGREEMENT_95 = reliable, OUTSIDE_TOLERANCE_95 = not reliable, INCONCLUSIVE_95 = undecided. "
    "Explain what was measured or simulated, the main pattern across chips or runs, how the noise-model "
    "prediction compares with measurements if present, and the limits (sampling-only uncertainty, small "
    "test problem). 150-300 words, Markdown, no headings beyond level 3."
)


def _llm_report(facts: dict[str, Any], settings: Any) -> tuple[str | None, str]:
    from .providers import complete, make_client, token_limit

    try:
        client = make_client(settings)
        response = complete(
            client,
            model=settings.model,
            messages=[
                {"role": "system", "content": _REPORT_SYSTEM},
                {"role": "user", "content": json.dumps(facts, indent=1, sort_keys=True)},
            ],
            **token_limit(settings),
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            return None, "model returned an empty reply"
        return text, f"written by {settings.provider}:{settings.model}"
    except QuantivexError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 - the deterministic results must survive any provider failure
        return None, f"{type(exc).__name__} while contacting the model"


# --------------------------------------------------------------------------------------------- main entry
def process(
    inputs: Sequence[str | Path],
    out_dir: str | Path,
    config: ProcessConfig | None = None,
    settings: Any = None,
    log: Any = print,
) -> ProcessResult:
    """Process IBM calibration files and hardware-run folders into one results folder."""
    from .narrative import check_draft
    from .report import environment

    config = (config or ProcessConfig()).validated()
    out = Path(out_dir)
    if out.exists():
        raise QuantivexError(f"Refusing to overwrite existing output folder: {out}")
    items, skipped = discover(inputs)
    if not items:
        raise QuantivexError("No IBM calibration files or hardware-run folders were found in the inputs")

    work = out.with_name(f".{out.name}.partial-{os.getpid()}")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    result = ProcessResult(out_dir=out, skipped=skipped)
    started = time.monotonic()
    taken: set[str] = set()
    problem: _Problem | None = None
    inputs_record = []
    try:
        for index, item in enumerate(items, start=1):
            slug = _slug(item.path.stem if item.path.is_file() else item.path.name, taken)
            log(f"[{index}/{len(items)}] {item.kind}: {item.display}")
            digest = sha256_file(item.path) if item.path.is_file() else None
            inputs_record.append({"input": item.display, "kind": item.kind, "id": slug, "sha256": digest})
            try:
                if item.kind == "hardware_run":
                    result.hardware.append(_process_hardware(item, slug, work / "hardware" / slug, config))
                else:
                    if problem is None:
                        problem = _Problem(config)
                    result.calibrations.append(
                        _process_calibration(item, slug, work / "calibrations" / slug, config, problem)
                    )
            except Exception as exc:  # noqa: BLE001 - one bad input must not stop the batch
                message = f"{type(exc).__name__}: {exc}"[:500]
                result.failed.append({"input": item.display, "kind": item.kind, "error": message})
                _write_json(
                    work / "failures" / f"{slug}.json",
                    {
                        "input": item.display,
                        "kind": item.kind,
                        "error": message,
                        "traceback": traceback.format_exc(limit=6),
                    },
                )
                log(f"    failed: {message}")

        problem_info = problem.problem.describe() if problem else None
        hf_mha = None
        if problem:
            hf_mha = 1000 * abs(problem.problem.e_hf - problem.problem.e_exact)
        elif result.hardware:
            first = next((r for r in result.hardware if r.get("hartree_fock_energy_hartree") is not None), None)
            if first:
                hf_mha = 1000 * abs(first["hartree_fock_energy_hartree"] - first["reference_energy_hartree"])
        if result.calibrations:
            _write_csv(work / "calibrations.csv", result.calibrations, CALIBRATION_COLUMNS)
        if result.hardware:
            _write_csv(work / "hardware.csv", result.hardware, HARDWARE_COLUMNS)
        _figures(work, result.calibrations, result.hardware, hf_mha)

        summary_md = _summary_markdown(result, config, problem_info)
        (work / "summary.md").write_text(summary_md, encoding="utf-8")
        facts = _facts(result, config, problem_info)
        _write_json(
            work / "summary.json",
            {
                "format_version": FORMAT_VERSION,
                **facts,
                "calibration_rows": result.calibrations,
                "hardware_rows": result.hardware,
                "skipped": result.skipped,
                "failed": result.failed,
            },
        )

        explanation, status, flagged = None, "not requested (report='none')", []
        if config.report == "llm" and (result.calibrations or result.hardware):
            if settings is None:
                from .config import Settings

                settings = Settings.from_env(validate=False)
            explanation, status = _llm_report(facts, settings)
            if explanation is None and config.require_llm:
                status = f"required but missing: {status}"
            elif explanation is None:
                status = f"not generated: {status}"
            if explanation:
                flagged = check_draft(explanation, [{"output": facts}])
        result.report_status = status
        report = [summary_md, "## Explanation from the language model (unverified)", ""]
        if explanation:
            report += [explanation, ""]
            report.append(
                "**Check these numbers:** " + ", ".join(f"`{t}`" for t in flagged) + " match no computed result."
                if flagged
                else "Automatic check: every number in this explanation matches a computed result. "
                "The check does not verify the explanation's reasoning."
            )
        else:
            report.append(f"No explanation was written ({status}). All results above are complete without it.")
        (work / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")

        _write_json(
            work / "run.json",
            {
                "format_version": FORMAT_VERSION,
                "quantivex_version": __version__,
                "finished_at": _now(),
                "duration_seconds": round(time.monotonic() - started, 1),
                "config": asdict(config),
                "inputs": inputs_record,
                "counts": {
                    "calibrations": len(result.calibrations),
                    "hardware_runs": len(result.hardware),
                    "failed": len(result.failed),
                    "skipped": len(result.skipped),
                },
                "language_model_report": {"status": status, "unmatched_numbers": flagged},
                "software_environment": environment(),
                "scope": "Calibration files: local Aer simulation, no IBM QPU used. Hardware runs: re-analysis of "
                "counts already returned by IBM; nothing is submitted.",
            },
        )
        files = sorted(p for p in work.rglob("*") if p.is_file())
        (work / "SHA256SUMS.txt").write_text(
            "".join(f"{sha256_file(p)}  {p.relative_to(work).as_posix()}\n" for p in files), encoding="utf-8"
        )
        work.rename(out)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return result
