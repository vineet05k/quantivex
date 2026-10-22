"""Independent validation checks for Quantivex results (no LLM involved).

Usage: python scripts/validate_results.py [snapshot.json]

Checks
  V1  Exact and Hartree-Fock energies agree with a fresh PySCF calculation (Sun et al. 2020).
  V2  Noiseless VQE reaches the exact energy within chemical accuracy (Peruzzo 2014; Romero 2018).
  V3  Quantivex's noise model agrees with Qiskit Aer's own device model for the same chip
      (AerSimulator.from_backend(FakeTorino)), on the identical (frozen) compiled circuits.
      Tolerance: max(10 mHa, 30% of the Aer error).
  V4  Readout mitigation reduces the error (van den Berg et al. 2022).
  V5  The chain score predicts the error for several random seeds (Nishio 2020; Murali 2019).
  V6  Same seed -> identical numbers (reproducibility).
  V7  Error bars: 30 runs that differ only in the sampler seed (same compiled circuits) are compared
      with the exact noisy expectation from a density-matrix calculation (no sampling).

Each check reports PASS, FAIL or SKIP. SKIP is never counted as a pass; V2, V4, V6 and V7 are
essential, and the exit code is non-zero if any check fails or an essential check is skipped.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
from qiskit.quantum_info import SparsePauliOp

from quantivex.chemistry import CHEMICAL_ACCURACY_HA, get_problem
from quantivex.data import load
from quantivex.experiment import compare_layouts, estimate_energy
from quantivex.hardware import rank_chains
from quantivex.vqe import run_vqe

ROOT = Path(__file__).resolve().parents[1]
results: list[tuple[str, str, str]] = []
ESSENTIAL = ("V2", "V4", "V6", "V7")


def record(name: str, ok: bool | None, detail: str) -> None:
    """ok=None means the check could not run here (SKIP)."""
    status = "SKIP" if ok is None else "PASS" if ok else "FAIL"
    results.append((name, status, detail))
    print(f"[{status}] {name}: {detail}")


def _circuits(estimate) -> list:  # type: ignore[no-untyped-def, type-arg]
    import base64
    import io

    from qiskit import qpy

    return list(qpy.load(io.BytesIO(base64.b64decode(estimate.measurement_record["circuits_qpy_base64"]))))


def _exact_noisy_energy(estimate, snap) -> float:  # type: ignore[no-untyped-def]
    """Infinite-shot expectation of the readout-mitigated estimator, by density-matrix simulation.

    Measurements are removed, so the model's readout error is absent; for its symmetric, independent
    readout error the mitigated estimator is unbiased for exactly this quantity.
    """
    from qiskit_aer import AerSimulator

    from quantivex.noise import build_noise_model

    rec = estimate.measurement_record
    sim = AerSimulator(method="density_matrix", noise_model=build_noise_model(snap))
    total = 0.0
    for qc, terms, phys in zip(_circuits(estimate), rec["groups"], rec["per_group_layouts"], strict=True):
        bare = qc.remove_final_measurements(inplace=False)
        bare.save_probabilities(qubits=list(phys))
        probs = np.asarray(sim.run(bare).result().data()["probabilities"])
        n = len(phys)
        outcomes = np.arange(len(probs))
        for label, coeff in terms:
            support = [q for q in range(n) if label[n - 1 - q] != "I"]
            parity = np.zeros(len(probs), dtype=int)
            for q in support:
                parity ^= (outcomes >> q) & 1
            total += coeff * float(np.sum(probs * (1 - 2 * parity)))
    return total


def _same_rows(a: list[dict], b: list[dict]) -> tuple[bool, str]:  # type: ignore[type-arg]
    """Rows must match exactly; QPY blobs embed random IDs, so compare their decoded circuits instead."""
    import base64
    import io

    from qiskit import qpy

    def circuits(row: dict) -> list:  # type: ignore[type-arg]
        blob = row["measurement_record"].pop("circuits_qpy_base64")
        return list(qpy.load(io.BytesIO(base64.b64decode(blob))))

    same_circuits = all(circuits(x) == circuits(y) for x, y in zip(a, b, strict=True))
    same = same_circuits and a == b
    return same, "identical numbers, counts and compiled circuits" if same else "rows differ"


def main() -> int:
    snap = load(sys.argv[1] if len(sys.argv) > 1 else ROOT / "examples" / "ibm_torino_2025-02-26.json")
    problem = get_problem("N2", 2.0)
    vqe = run_vqe(problem)
    chain = rank_chains(snap, problem.n_qubits)[0].qubits

    # V1
    if importlib.util.find_spec("pyscf"):
        from quantivex.chemistry import compute_with_pyscf

        live = compute_with_pyscf("N2", 2.0, 2, 2)
        d = max(abs(live.e_exact - problem.e_exact), abs(live.e_hf - problem.e_hf))
        record("V1 energies vs fresh PySCF", d < 1e-8, f"max difference {d:.1e} Ha")
    else:
        record("V1 energies vs fresh PySCF", None, "PySCF not installed (Windows); run in WSL/Docker")

    # V2
    record(
        "V2 noiseless VQE vs exact",
        abs(vqe.energy - problem.e_exact) < CHEMICAL_ACCURACY_HA,
        f"error {1000 * (vqe.energy - problem.e_exact):.4f} mHa (limit 1.6 mHa)",
    )

    # V3
    if importlib.util.find_spec("qiskit_ibm_runtime") and (snap.backend or "").endswith("torino"):
        from qiskit_aer import AerSimulator
        from qiskit_ibm_runtime.fake_provider import FakeTorino

        from quantivex.experiment import _expectation

        ours = estimate_energy(vqe, snap, chain, 20000, 11, mitigate_readout=False)
        frozen = _circuits(ours)  # the identical compiled circuits our model sampled
        res = AerSimulator.from_backend(FakeTorino(), seed_simulator=11).run(frozen, shots=20000).result()
        groups = [SparsePauliOp.from_list(g) for g in ours.measurement_record["groups"]]
        aer = sum(_expectation(g, res.get_counts(i), 20000)[0] for i, g in enumerate(groups))
        e_ours, e_aer = 1000 * (ours.energy - problem.e_exact), 1000 * (aer - problem.e_exact)
        tol = max(10.0, 0.3 * abs(e_aer))
        record(
            "V3 noise model vs Aer device model (same compiled circuits)",
            abs(e_ours - e_aer) < tol,
            f"raw error: Quantivex model {e_ours:.1f} mHa, Aer FakeTorino {e_aer:.1f} mHa (tolerance {tol:.1f})",
        )
    else:
        record("V3 noise model vs Aer device model", None, "needs the ibm extra and a Torino snapshot")

    # V4
    raw = estimate_energy(vqe, snap, chain, 8000, 5, mitigate_readout=False)
    mit = estimate_energy(vqe, snap, chain, 8000, 5)
    e_raw, e_mit = abs(raw.energy - problem.e_exact), abs(mit.energy - problem.e_exact)
    record(
        "V4 readout mitigation helps", e_mit < e_raw, f"raw {1000 * e_raw:.1f} mHa -> mitigated {1000 * e_mit:.1f} mHa"
    )

    # V7: error bars against the exact noisy expectation; only the sampler seed varies.
    gate_chain = rank_chains(snap, problem.n_qubits, include_readout=False)[0].qubits
    reps = [estimate_energy(vqe, snap, gate_chain, 4000, 1234, sampler_seed=1000 + k) for k in range(30)]
    same_layout = len({e.layout for e in reps}) == 1
    exact_noisy = _exact_noisy_energy(reps[0], snap)
    energies = np.array([e.energy for e in reps])
    spread, stated = energies.std(ddof=1), float(np.mean([e.stderr for e in reps]))
    covered = sum(abs(e.energy - exact_noisy) <= e.confidence_radius for e in reps)
    bias = abs(energies.mean() - exact_noisy)
    record(
        "V7 error bars vs exact noisy expectation (30 sampler seeds, frozen circuits)",
        same_layout and 0.75 < stated / spread < 1.33 and covered >= 28 and bias < 3 * spread / np.sqrt(30),
        f"exact noisy error {1000 * (exact_noisy - problem.e_exact):.2f} mHa; mean of runs off by "
        f"{1000 * bias:.2f} mHa; stated stderr {1000 * stated:.2f} vs observed spread {1000 * spread:.2f} mHa; "
        f"95% radius covered the exact value in {covered}/30 runs",
    )

    # V5 + V6
    stats = []
    for seed in (101, 202, 303):
        comp = compare_layouts(snap, "N2", 2.0, n_random=15, seed=seed)
        stats.append((comp.spearman_rho or 0.0, comp.spearman_p if comp.spearman_p is not None else 1.0))
    record(
        "V5 chain cost predicts error (3 seeds, 15 random chains each)",
        all(r > 0.5 and p < 0.05 for r, p in stats),
        "Spearman rho(cost, error) (p) = " + ", ".join(f"{r:.2f} ({p:.1g})" for r, p in stats),
    )
    a = compare_layouts(snap, "N2", 2.0, n_random=2, seed=7).describe()["rows"]
    b = compare_layouts(snap, "N2", 2.0, n_random=2, seed=7).describe()["rows"]
    ok, detail = _same_rows(a, b)
    record("V6 reproducible with the same seed", ok, detail)

    count = {k: sum(r[1] == k for r in results) for k in ("PASS", "FAIL", "SKIP")}
    missing = [r[0] for r in results if r[1] == "SKIP" and r[0].split()[0] in ESSENTIAL]
    print(f"\n{count['PASS']} passed, {count['FAIL']} failed, {count['SKIP']} skipped (of {len(results)})")
    if missing:
        print("Essential checks skipped: " + ", ".join(missing))
    return 1 if count["FAIL"] or missing else 0


if __name__ == "__main__":
    np.set_printoptions(precision=4)
    sys.exit(main())
