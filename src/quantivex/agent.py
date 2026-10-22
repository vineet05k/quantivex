"""The LLM agent: the required interface to Quantivex.

The model chooses tools; Python renders the returned evidence. Controls:

* the first model turn must call a tool; freeform drafts are not scientific answers;
* bounded: every tool argument is validated against a strict schema and bounds;
* self-correcting: tool errors are returned to the model, which may retry;
* auditable: every call, its arguments, outcome and timing are recorded, and the
  run directory holds record.json, report.md and every figure.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from . import viz
from .chemistry import MOLECULES, get_problem
from .config import Settings
from .data import Snapshot, summary
from .errors import AgentError, QuantivexError
from .experiment import compare_layouts, energy_curve, estimate_energy
from .hardware import rank_chains
from .narrative import compose_answer
from .providers import complete as _complete
from .providers import make_client, token_limit
from .report import RunRecorder, environment, write_reports
from .vqe import CIRCUIT_MODES, run_vqe

log = logging.getLogger(__name__)

MAX_NOISY_QUBITS = 8
ACTIVE_SPACES = {"2e2o": (2, 2), "4e4o": (4, 4), "6e6o": (6, 6)}

SYSTEM_PROMPT = """You are Quantivex, a research assistant for noise-aware quantum chemistry on real \
quantum-processor calibration data.

Rules:
- Start with get_dataset_summary unless you already called it in this conversation.
- Use tools for every number. Never invent energies, errors, qubit IDs or statistics; quote them as returned.
- If a tool returns an error, fix the arguments and retry (for example pick a cached bond length it lists).
- Defaults when the user is vague: molecule N2, active space 2e2o (4 qubits), bond length 1.10 angstrom.
- Use compare_layouts for questions about which qubits to use or whether layout matters; it is the \
exploratory comparison of chain score and simulated error (Spearman rho).
- Use energy_curve for dissociation or bond-length scans; render_noise_blur for noise-map visuals.
- Explain model agreement using the reported confidence bound; this does not certify physical chemical accuracy.
- Treat all layout comparisons as exploratory; do not infer significance from two-sigma error bars.
- With readout mitigation on (default) chains are ranked by the gate-only score. When reporting the \
score-vs-error correlation, quote rho, the permutation p-value, the sample size and correlation_reliable.
- State the data provenance and calibration date. Results are simulations driven by calibration data, \
not hardware runs. Be concise: lead with the answer, then the evidence, then caveats."""


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


_MOLECULE = {"type": "string", "enum": sorted(MOLECULES)}
_SPACE = {"type": "string", "enum": list(ACTIVE_SPACES), "description": "Active space; qubits = 2 x orbitals"}
_BOND = {"type": "number", "minimum": 0.3, "maximum": 4.0, "description": "Bond length in angstrom"}
_MODE = {
    "type": "string",
    "enum": list(CIRCUIT_MODES),
    "description": "compact = optimised state via exact state preparation (fewest gates); uccsd = full ansatz",
}

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_dataset_summary",
            "description": "Chip size, provenance, calibration date, typical errors, weakest qubits and dead links.",
            "parameters": _schema({}),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_qubit_chains",
            "description": "Rank chains (paths of linked qubits) by estimated success probability from calibration.",
            "parameters": _schema(
                {
                    "length": {"type": "integer", "minimum": 1, "maximum": 16},
                    "top": {"type": "integer", "minimum": 1, "maximum": 10},
                    "draw_map": {"type": "boolean", "description": "Render the chip map with the best chain"},
                    "readout_mitigation": {
                        "type": "boolean",
                        "description": "True (default) ranks by the gate-only score, matching mitigated runs",
                    },
                },
                ["length"],
            ),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_chemistry",
            "description": "Exact, Hartree-Fock and noiseless VQE energies, then the VQE state on the noisy chip "
            "(on the best chain or Qiskit's layout) with a trust verdict.",
            "parameters": _schema(
                {
                    "molecule": _MOLECULE,
                    "bond_length": _BOND,
                    "active_space": _SPACE,
                    "layout": {"type": "string", "enum": ["best_chain", "qiskit_default"]},
                    "circuit_mode": _MODE,
                    "readout_mitigation": {
                        "type": "boolean",
                        "description": "Default true; also selects the matching chain score (gate-only when true)",
                    },
                },
                ["molecule", "bond_length"],
            ),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_layouts",
            "description": "Run the same chemistry circuit on the best chain, Qiskit's default layout, the worst "
            "chain and random chains; returns errors, verdicts and the score-vs-error correlation.",
            "parameters": _schema(
                {
                    "molecule": _MOLECULE,
                    "bond_length": _BOND,
                    "active_space": _SPACE,
                    "n_random": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 30,
                        "description": "Random chains for the score-vs-error correlation (default 15 for 4 qubits, "
                        "5 for 8 qubits, where each run takes ~30 s); 15+ are needed for a reliable test",
                    },
                    "circuit_mode": _MODE,
                    "readout_mitigation": {
                        "type": "boolean",
                        "description": "Default true; also selects the matching chain score (gate-only when true)",
                    },
                },
                ["molecule", "bond_length"],
            ),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "energy_curve",
            "description": "Potential-energy curve over bond lengths: HF, exact, noiseless VQE, optional noisy VQE.",
            "parameters": _schema(
                {
                    "molecule": _MOLECULE,
                    "start": _BOND,
                    "stop": _BOND,
                    "points": {"type": "integer", "minimum": 3, "maximum": 12},
                    "active_space": _SPACE,
                    "noisy": {"type": "boolean"},
                },
                ["molecule", "start", "stop"],
            ),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render_chip_map",
            "description": "Draw the chip: qubits shaded by readout error, dead links, optional highlighted qubits.",
            "parameters": _schema(
                {"highlight": {"type": "array", "items": {"type": "integer", "minimum": 0}, "maxItems": 16}}
            ),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render_noise_blur",
            "description": "Quantum Blur (Wootton 2020) of the chip's noise map: one quantum state, "
            "every pixel interferes.",
            "parameters": _schema(
                {
                    "metric": {"type": "string", "enum": ["readout_error", "two_qubit_error", "sx_error", "t1_decay"]},
                    "theta": {"type": "number", "minimum": 0.0, "maximum": 1.5},
                    "rotation": {"type": "string", "enum": ["rx", "ry"]},
                }
            ),
        },
    },
]


def _round_bond(x: float) -> float:
    return round(float(x), 2)


@dataclass
class Session:
    """Holds the snapshot, settings and run directory for one question."""

    snapshot: Snapshot
    settings: Settings
    recorder: RunRecorder = field(init=False)

    def __post_init__(self) -> None:
        self.recorder = RunRecorder(self.settings.output_dir)

    # --- tools ---------------------------------------------------------------
    def get_dataset_summary(self) -> dict[str, Any]:
        return summary(self.snapshot)

    def find_qubit_chains(
        self, length: int, top: int = 5, draw_map: bool = True, readout_mitigation: bool = True
    ) -> dict[str, Any]:
        chains = rank_chains(self.snapshot, length, include_readout=not readout_mitigation)
        out: dict[str, Any] = {
            "length": length,
            "usable_chains": len(chains),
            "best": [c.as_dict() for c in chains[:top]],
            "worst": chains[-1].as_dict(),
            "note": "ESP = product of (1 - error) over the chain's single-qubit gates and links, plus readouts "
            "when readout mitigation is off. With mitigation the gate-only score is used: in a 40-chain Torino "
            "study it tracked mitigated energy error far better than the full score.",
        }
        if draw_map:
            out["figure"] = self._chip_map(chains[0].qubits, "Best chain").path.name
        return out

    def _problem(self, molecule: str, bond_length: float, active_space: str) -> Any:
        ne, no = ACTIVE_SPACES[active_space]
        return get_problem(molecule, _round_bond(bond_length), ne, no)

    def run_chemistry(
        self,
        molecule: str,
        bond_length: float,
        active_space: str = "2e2o",
        layout: str = "best_chain",
        circuit_mode: str = "compact",
        readout_mitigation: bool = True,
    ) -> dict[str, Any]:
        problem = self._problem(molecule, bond_length, active_space)
        out: dict[str, Any] = {"problem": problem.describe()}
        if problem.n_qubits > MAX_NOISY_QUBITS:
            out["note"] = (
                f"{problem.n_qubits} qubits: exact and Hartree-Fock only. Noisy simulation is limited "
                f"to {MAX_NOISY_QUBITS} qubits; use active_space 2e2o or 4e4o."
            )
            return out
        vqe = run_vqe(problem)
        out["vqe"] = vqe.describe()
        chain = (
            rank_chains(self.snapshot, problem.n_qubits, include_readout=not readout_mitigation)[0].qubits
            if layout == "best_chain"
            else None
        )
        est = estimate_energy(
            vqe, self.snapshot, chain, self.settings.shots, self.settings.seed, circuit_mode, readout_mitigation
        )
        out["noisy"] = est.describe(problem.e_exact, problem.e_hf)
        out["layout_source"] = layout
        out["figure"] = self._chip_map(est.layout, "Layout used").path.name
        return out

    def compare_layouts(
        self,
        molecule: str,
        bond_length: float,
        active_space: str = "2e2o",
        n_random: int | None = None,
        circuit_mode: str = "compact",
        readout_mitigation: bool = True,
    ) -> dict[str, Any]:
        problem = self._problem(molecule, bond_length, active_space)
        if problem.n_qubits > MAX_NOISY_QUBITS:
            raise QuantivexError(f"compare_layouts is limited to {MAX_NOISY_QUBITS} qubits; use 2e2o or 4e4o")
        if n_random is None:
            # Each 8-qubit density-matrix run takes ~30 s, so 18 runs would not fit the default tool budget.
            n_random = 15 if problem.n_qubits <= 4 else 5
        comp = compare_layouts(
            self.snapshot,
            problem.molecule,
            problem.bond_length,
            problem.n_electrons,
            problem.n_orbitals,
            n_random,
            self.settings.shots,
            self.settings.seed,
            circuit_mode,
            readout_mitigation,
        )
        out = comp.describe()
        path = viz.plot_layout_comparison(comp, self.recorder.artifact_path("layout_comparison"))
        self.recorder.add("chart", path, f"Layout comparison: {problem.molecule} at {problem.bond_length} Å")
        out["figure"] = path.name
        out["chip_map"] = self._chip_map(
            comp.row("quantivex_best").estimate.layout,
            "Quantivex best chain",
            comp.row("qiskit_default").estimate.layout,
        ).path.name
        return out

    def energy_curve(
        self, molecule: str, start: float, stop: float, points: int = 7, active_space: str = "2e2o", noisy: bool = True
    ) -> dict[str, Any]:
        ne, no = ACTIVE_SPACES[active_space]
        if 2 * no > MAX_NOISY_QUBITS:
            raise QuantivexError("Curves with VQE are limited to 8 qubits, including noiseless curves")
        lo, hi = sorted((start, stop))
        grid = sorted({_round_bond(x) for x in np.linspace(lo, hi, points)})
        layout = None
        if noisy:
            if 2 * no > MAX_NOISY_QUBITS:
                raise QuantivexError(f"Noisy curves are limited to {MAX_NOISY_QUBITS} qubits; set noisy=false")
            layout = rank_chains(self.snapshot, 2 * no, include_readout=False)[0].qubits
        pts = energy_curve(
            molecule, grid, ne, no, self.snapshot if noisy else None, layout, self.settings.shots, self.settings.seed
        )
        path = viz.plot_energy_curve(
            pts,
            self.recorder.artifact_path("energy_curve"),
            f"{molecule} potential-energy curve, CAS({ne},{no})" + (f", noisy chain {list(layout)}" if layout else ""),
        )
        self.recorder.add("chart", path, f"{molecule} energy curve")
        return {"points": [p.describe() for p in pts], "layout": list(layout) if layout else None, "figure": path.name}

    def render_chip_map(self, highlight: list[int] | None = None) -> dict[str, Any]:
        if highlight:
            unknown = [q for q in highlight if q not in self.snapshot.qubit_map]
            if unknown:
                raise QuantivexError(f"Qubits {unknown} are not on this chip")
        return {"figure": self._chip_map(tuple(highlight or ()), "Highlighted qubits").path.name}

    def render_noise_blur(
        self, metric: str = "readout_error", theta: float = 0.3, rotation: str = "rx"
    ) -> dict[str, Any]:
        path = viz.plot_noise_blur(self.snapshot, self.recorder.artifact_path("quantum_blur"), metric, theta, rotation)
        self.recorder.add("image", path, f"Quantum Blur of {metric.replace('_', ' ')}")
        return {
            "figure": path.name,
            "metric": metric,
            "theta": theta,
            "rotation": rotation,
            "note": "Encodes the noise map in one 12-qubit state; per-qubit rotations make pixels interfere.",
        }

    def _chip_map(self, chain: tuple[int, ...], label: str, secondary: tuple[int, ...] | None = None) -> Any:
        path = viz.plot_chip_map(
            self.snapshot,
            self.recorder.artifact_path("chip_map"),
            chain or None,
            label,
            secondary if secondary and tuple(secondary) != tuple(chain) else None,
        )
        return self.recorder.add("chart", path, f"Chip map: {label} {list(chain) if chain else ''}".strip())

    # --- dispatch ------------------------------------------------------------
    def dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        handlers: dict[str, Callable[..., dict[str, Any]]] = {
            t["function"]["name"]: getattr(self, t["function"]["name"]) for t in TOOLS
        }
        if name not in handlers:
            return {"error": f"Unknown tool {name!r}"}
        try:
            schema = next(t["function"]["parameters"] for t in TOOLS if t["function"]["name"] == name)
            json.dumps(args, allow_nan=False)
            Draft202012Validator(schema).validate(args)
            return handlers[name](**args)
        except ValidationError as exc:
            return {"error": f"Invalid tool arguments: {exc.message}"}
        except (QuantivexError, ValueError, TypeError, KeyError) as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}


@dataclass
class AgentResult:
    question: str
    model: str
    answer: str
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    artifacts: list[dict[str, str]]
    run_dir: str
    record_path: str
    report_path: str
    summary: str = ""
    evidence: str = ""
    unverified_numbers: list[str] = field(default_factory=list)


def _evidence_answer(results: list[dict[str, Any]]) -> str:
    """Render computed outputs only. Unverified model prose is retained solely in the record."""

    def compact(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: ("Saved in record.json" if k in ("measurement_record", "numerical_inputs") else compact(v))
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [compact(v) for v in value]
        return value

    lines = [
        "### Computed evidence (raw tool outputs)",
        "",
        "Local simulation/tool outputs; no QPU was invoked. Bulky measurement records are in record.json.",
        "",
    ]
    for result in results:
        if "error" not in result["output"]:
            lines.extend(
                [
                    "### " + result["tool"],
                    "",
                    "```json",
                    json.dumps(compact(result["output"]), indent=2, allow_nan=False),
                    "```",
                    "",
                ]
            )
    return "\n".join(lines)


def ask(snapshot: Snapshot, question: str, settings: Settings | None = None, client: Any = None) -> AgentResult:
    """Use a model to select bounded tools, then return a deterministic evidence report.

    The answer leads with a summary built only from tool outputs; the model's explanation follows,
    labelled unverified, with any numbers that match no tool output flagged.
    Failed or interrupted ordinary runs preserve checkpoints and completed tools.
    """
    import os

    from .worker import bounded_tool

    if not question or not question.strip():
        raise AgentError("Question must not be empty")
    settings = (settings or Settings.from_env()).validated()
    session = Session(snapshot, settings)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question.strip()},
    ]
    trace: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    record: dict[str, Any] = {
        "schema_version": 2,
        "status": "running",
        "question": question,
        "model": settings.model,
        "provider": settings.provider,
        "endpoint": settings.endpoint,
        "answer": "Incomplete run.",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "label": snapshot.label,
            "sha256": snapshot.sha256,
            "calibration_timestamp": snapshot.calibration_timestamp,
            "provenance": snapshot.provenance,
            "snapshot": snapshot.to_dict(),
        },
        "settings": {
            "shots": settings.shots,
            "seed": settings.seed,
            "max_rounds": settings.max_rounds,
            "max_tool_calls": settings.max_tool_calls,
            "max_tokens": settings.max_tokens,
            "request_timeout_s": settings.request_timeout_s,
            "tool_choice": settings.tool_choice,
            "tool_wall_seconds": settings.tool_timeout_s,
            "tool_cpu_seconds_posix": max(10, int(settings.tool_timeout_s) - 10),
            "tool_address_space_gib_posix": 8,
            "worker_memory_gib_posix": 8,
        },
        "environment": environment(),
        "tool_calls": trace,
        "tool_results": results,
        "messages": messages,
        "artifacts": [],
        "model_draft_unverified": None,
    }

    def checkpoint(event: str) -> tuple[Any, Any]:
        record["artifacts"] = [a.describe() for a in session.recorder.artifacts]
        with (session.recorder.run_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {"event": event, "time": datetime.now(timezone.utc).isoformat(), "completed_tools": len(trace)},
                    allow_nan=False,
                )
                + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
        return write_reports(record, snapshot, session.recorder.run_dir)

    checkpoint("started")
    try:
        client = client or make_client(settings)
        draft = None
        for round_no in range(1, settings.max_rounds + 1):
            checkpoint("model_request_started")
            response = _complete(
                client,
                model=settings.model,
                messages=messages,
                tools=TOOLS,
                tool_choice=settings.tool_choice if round_no == 1 else "auto",
                **token_limit(settings),
            )
            msg = response.choices[0].message
            if not msg.tool_calls:
                if round_no == 1:
                    raise AgentError(
                        "Model did not return a structured tool call. "
                        "Run quantivex check-llm; select a tool-capable model"
                    )
                draft = msg.content or ""
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.function.name, "arguments": c.function.arguments},
                        }
                        for c in msg.tool_calls
                    ],
                }
            )
            if len(trace) + len(msg.tool_calls) > settings.max_tool_calls:
                raise AgentError("Total tool-call budget exceeded; no further calls were executed")
            for call in msg.tool_calls:
                if not call.id or not isinstance(call.function.arguments, str):
                    raise AgentError("Provider returned an incomplete structured tool call")
                started = time.perf_counter()
                try:

                    def reject_constant(value: str) -> Any:
                        raise ValueError("Nonfinite JSON constant: " + value)

                    args = json.loads(call.function.arguments or "{}", parse_constant=reject_constant)
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be a JSON object")
                    json.dumps(args, allow_nan=False)
                except (json.JSONDecodeError, ValueError) as exc:
                    args, output = {}, {"error": f"Invalid arguments: {exc}"}
                else:
                    output = bounded_tool(session, call.function.name, args)
                trace.append(
                    {
                        "round": round_no,
                        "tool": call.function.name,
                        "arguments": args,
                        "ok": "error" not in output,
                        "seconds": round(time.perf_counter() - started, 2),
                        "error": output.get("error", ""),
                    }
                )
                results.append({"tool": call.function.name, "arguments": args, "output": output})
                # The model needs summary results, not circuit payloads/integrals/raw measurements.
                model_output = json.loads(json.dumps(output, allow_nan=False))

                def trim(value: Any) -> None:
                    if isinstance(value, dict):
                        value.pop("measurement_record", None)
                        value.pop("numerical_inputs", None)
                        for item in value.values():
                            trim(item)
                    elif isinstance(value, list):
                        for item in value:
                            trim(item)

                trim(model_output)
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": json.dumps(model_output, allow_nan=False)}
                )
                checkpoint("tool_completed")
        if draft is None:
            draft = (
                _complete(client, model=settings.model, messages=messages, **token_limit(settings))
                .choices[0]
                .message.content
                or ""
            )
        record["model_draft_unverified"] = draft
        if not any(t["ok"] for t in trace):
            raise AgentError("No tool succeeded; no scientific answer is available. Inspect the failed run record")
        summary_md, unmatched = compose_answer(results, draft, question)
        evidence_md = _evidence_answer(results)
        record["summary"] = summary_md
        record["evidence"] = evidence_md
        record["draft_unmatched_numbers"] = unmatched
        record["answer"] = summary_md + "\n\n" + evidence_md
        record["status"] = "partial_evidence" if any(not t["ok"] for t in trace) else "evidence_returned"
        record["task_completion_verified"] = False
        json_path, md_path = checkpoint("finished")
    except BaseException as exc:
        record["status"] = "failed"
        record["failure_type"] = type(exc).__name__
        record["answer"] = (
            "Run failed. Completed tool evidence is retained below; no final scientific conclusion was produced."
        )
        checkpoint("failed")
        if isinstance(exc, (QuantivexError, KeyboardInterrupt, SystemExit)):
            raise
        raise AgentError(f"Agent run failed ({type(exc).__name__}); inspect its saved record") from exc
    return AgentResult(
        question,
        settings.model,
        record["answer"],
        trace,
        results,
        record["artifacts"],
        str(session.recorder.run_dir),
        str(json_path),
        str(md_path),
        summary=record.get("summary", ""),
        evidence=record.get("evidence", ""),
        unverified_numbers=list(record.get("draft_unmatched_numbers", [])),
    )
