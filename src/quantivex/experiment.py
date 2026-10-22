"""Noisy experiments: how much does the choice of physical qubits change a chemistry result?

`estimate_energy` compiles the VQE circuit onto a chosen physical layout,
simulates it under the snapshot's noise model with finite shots (including
readout error) and estimates <H> from qubit-wise-commuting measurement groups.

`compare_layouts` runs the *same* circuit on several layouts: Quantivex's best
chain, Qiskit's own noise-aware choice, the worst usable chain and random
chains. It reports each layout's energy error and the Spearman correlation
between chain cost and error in a random subset. This exploratory simulation
is not a test of predictive accuracy on real hardware.
"""

from __future__ import annotations

import base64
import io
import logging
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np
from qiskit import QuantumCircuit, qpy, transpile
from qiskit.quantum_info import SparsePauliOp
from qiskit_aer import AerSimulator

from .chemistry import CHEMICAL_ACCURACY_HA, ActiveSpaceProblem, get_problem, qubit_hamiltonian
from .data import Snapshot
from .errors import HardwareError
from .hardware import MIN_MITIGATION_ATTENUATION, ChainScore, layout_cost, rank_chains, score_chain
from .noise import build_noise_model, build_target, coherence_notes
from .vqe import VQEResult, build_circuit, run_vqe

log = logging.getLogger(__name__)

# Legacy exported names remain aliases; user-facing labels describe model agreement.
TRUSTWORTHY = "MODEL_AGREEMENT_95"
DEGRADED = "INCONCLUSIVE_95"
UNTRUSTWORTHY = "OUTSIDE_TOLERANCE_95"
DEFAULT_RANDOM_CHAINS = 15
MAX_RANDOM_CHAINS = 30
RELIABLE_CORRELATION_SAMPLE = 15


def verdict(error_ha: float, stderr_ha: float, confidence_radius: float | None = None) -> dict[str, Any]:
    """Classify agreement with a specified small-model reference, never chemical truth.

    A radius must come from a finite-sample bound; a plug-in standard error is
    insufficient, especially for zero observed variance. No radius means unassessed.
    """
    if not math.isfinite(error_ha) or not math.isfinite(stderr_ha) or stderr_ha < 0:
        raise ValueError("Error and nonnegative standard error must be finite")
    if confidence_radius is None:
        label = "UNASSESSED"
    else:
        if not math.isfinite(confidence_radius) or confidence_radius < 0:
            raise ValueError("Confidence radius must be finite and nonnegative")
        label = (
            TRUSTWORTHY
            if abs(error_ha) + confidence_radius <= CHEMICAL_ACCURACY_HA
            else UNTRUSTWORTHY
            if abs(error_ha) - confidence_radius > CHEMICAL_ACCURACY_HA
            else DEGRADED
        )
    return {
        "verdict": label,
        "reason": "95% sampling confidence under fixed IID group distributions; "
        "comparison is to the stated small-model reference only",
        "error_mha": round(1000 * error_ha, 3),
        "two_sigma_mha": round(2000 * stderr_ha, 3),
        "confidence_radius_mha": None if confidence_radius is None else round(1000 * confidence_radius, 3),
        "uncertainty_excludes": "calibration uncertainty, drift, mitigation bias and electronic-structure model error",
    }


@dataclass(frozen=True)
class EnergyEstimate:
    energy: float  # readout-mitigated when `mitigated` is True
    stderr: float
    raw_energy: float
    mitigated: bool
    layout: tuple[int, ...]
    two_qubit_gates: int
    depth: int
    shots: int
    mode: str = "compact"
    confidence_radius: float | None = None
    measurement_record: dict[str, Any] = field(default_factory=dict)
    mitigation_requested: bool = True
    notes: tuple[str, ...] = ()

    def describe(self, exact: float, e_hf: float | None = None) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        if e_hf is not None:
            corr = abs(exact - e_hf)
            extra = {
                "beats_hartree_fock": abs(self.energy - exact) < corr,
                "noise_error_over_correlation_energy": round(abs(self.energy - exact) / corr, 2) if corr > 0 else None,
            }
        return {
            "layout": list(self.layout),
            "circuit_mode": self.mode,
            "e_noisy_hartree": round(self.energy, 6),
            "e_noisy_raw_hartree": round(self.raw_energy, 6),
            "readout_mitigation": self.mitigated,
            "readout_mitigation_requested": self.mitigation_requested,
            "stderr_mha": round(1000 * self.stderr, 3),
            "two_qubit_gates": self.two_qubit_gates,
            "depth": self.depth,
            "shots_per_group": self.shots,
            **verdict(self.energy - exact, self.stderr, self.confidence_radius),
            "measurement_record": self.measurement_record,
            **extra,
            **({"model_notes": list(self.notes)} if self.notes else {}),
        }


def _measurement_circuits(circuit: QuantumCircuit, groups: Sequence[SparsePauliOp]) -> list[QuantumCircuit]:
    out = []
    n = circuit.num_qubits
    for group in groups:
        qc = circuit.copy()
        for q in range(n):
            letters = {str(p[q]) for p in group.paulis} - {"I"}
            if letters == {"X"}:
                qc.h(q)
            elif letters == {"Y"}:
                qc.sdg(q)
                qc.h(q)
        qc.measure_all()
        out.append(qc)
    return out


def _expectation(
    group: SparsePauliOp, counts: dict[str, int], shots: int, attenuation: Sequence[float] | None = None
) -> tuple[float, float]:
    """Mean and unbiased variance of the grouped per-shot energy, including covariance."""
    n = group.num_qubits
    if shots < 2 or sum(counts.values()) != shots or not counts:
        raise HardwareError("Counts must sum to at least two shots")
    values, weights = [], []
    for bits, count in counts.items():
        if len(bits) != n or set(bits) - {"0", "1"} or type(count) is not int or count < 0:
            raise HardwareError("Invalid measurement counts")
        value = 0.0
        for pauli, coeff in zip(group.paulis, group.coeffs, strict=True):
            support = [q for q in range(n) if str(pauli[q]) != "I"]
            scale = math.prod(attenuation[q] for q in support) if attenuation is not None else 1.0
            if not math.isfinite(scale) or scale <= 0:
                raise HardwareError("Readout attenuation must be finite and positive")
            parity = sum(int(bits[n - 1 - q]) for q in support) % 2
            value += float(coeff.real) * (1 - 2 * parity) / scale
        values.append(value)
        weights.append(count)
    mean = float(np.dot(values, weights) / shots)
    variance = float(np.dot((np.asarray(values) - mean) ** 2, weights) / (shots * (shots - 1)))
    return mean, variance


def sampling_radius(
    groups: Sequence[SparsePauliOp],
    shots: Sequence[int],
    attenuation: Sequence[float] | None = None,
    alpha: float = 0.05,
) -> float:
    """Two-sided Hoeffding bounds per group, union bound then triangle inequality.

    Each grouped shot is in an interval of width <= 2*sum(abs(scaled coefficient))
    over nonidentity terms. This bound stays positive for unseen rare outcomes.
    Calibration factors are treated as fixed known inputs, not estimated parameters.
    """
    if not 0 < alpha < 1 or len(groups) != len(shots) or not groups or min(shots) < 2:
        raise ValueError("Invalid confidence inputs")
    radius = 0.0
    for group, count in zip(groups, shots, strict=True):
        width = 0.0
        for pauli, coeff in zip(group.paulis, group.coeffs, strict=True):
            support = [q for q in range(group.num_qubits) if str(pauli[q]) != "I"]
            if support:
                scale = math.prod(attenuation[q] for q in support) if attenuation is not None else 1.0
                if not math.isfinite(scale) or scale <= 0:
                    raise ValueError("Invalid attenuation")
                width += 2 * abs(float(coeff.real)) / scale
        radius += width * math.sqrt(math.log(2 * len(groups) / alpha) / (2 * count))
    return radius


def _group_width(group: SparsePauliOp, attenuation: Sequence[float] | None) -> float:
    """Range of one grouped shot value: 2 * sum |scaled non-identity coefficient|."""
    width = 0.0
    for pauli, coeff in zip(group.paulis, group.coeffs, strict=True):
        support = [q for q in range(group.num_qubits) if str(pauli[q]) != "I"]
        if support:
            scale = math.prod(attenuation[q] for q in support) if attenuation is not None else 1.0
            if not math.isfinite(scale) or scale <= 0:
                raise ValueError("Invalid attenuation")
            width += 2 * abs(float(coeff.real)) / scale
    return width


def bernstein_radius(
    groups: Sequence[SparsePauliOp],
    shots: Sequence[int],
    sample_variances: Sequence[float],
    attenuation: Sequence[Sequence[float] | None] | None = None,
    alpha: float = 0.05,
) -> float:
    """Two-sided empirical-Bernstein bound (Maurer & Pontil 2009, Thm 4), union over groups.

    For IID shot values in a range of width W with unbiased sample variance V, each side holds with
    probability >= 1 - d/2 at  sqrt(2 V ln(4/d) / n) + 7 W ln(4/d) / (3 (n - 1)),  d = alpha / m.
    Unlike Hoeffding it adapts to the observed variance; the range term keeps it positive when rare
    outcomes were never observed (zero sample variance).
    """
    m = len(groups)
    if not 0 < alpha < 1 or m == 0 or len(shots) != m or len(sample_variances) != m or min(shots) < 2:
        raise ValueError("Invalid confidence inputs")
    factors = attenuation if attenuation is not None else [None] * m
    log_term = math.log(4 * m / alpha)
    radius = 0.0
    for group, n, variance, att in zip(groups, shots, sample_variances, factors, strict=True):
        if not math.isfinite(variance) or variance < 0:
            raise ValueError("Sample variance must be finite and nonnegative")
        width = _group_width(group, att)
        radius += math.sqrt(2 * variance * log_term / n) + 7 * width * log_term / (3 * (n - 1))
    return radius


def confidence_radius(
    groups: Sequence[SparsePauliOp],
    counts: Sequence[dict[str, int]],
    attenuation: Sequence[Sequence[float] | None] | None = None,
    alpha: float = 0.05,
) -> float:
    """95% finite-sample radius for the summed group means: the tighter of Hoeffding and empirical
    Bernstein, each computed at alpha/2, so their minimum keeps 1 - alpha simultaneous coverage."""
    factors = list(attenuation) if attenuation is not None else [None] * len(groups)
    shots, variances = [], []
    for group, c, att in zip(groups, counts, factors, strict=True):
        n = sum(c.values())
        _, var_of_mean = _expectation(group, c, n, att)
        shots.append(n)
        variances.append(var_of_mean * n)  # unbiased per-shot sample variance
    hoeffding = sum(
        sampling_radius([g], [n], att, alpha=alpha / 2 / len(groups))
        for g, n, att in zip(groups, shots, factors, strict=True)
    )
    return min(hoeffding, bernstein_radius(groups, shots, variances, factors, alpha=alpha / 2))


def readout_factors(circuits: Sequence[Any], snapshot: Snapshot, logical_qubits: int) -> list[list[float]]:
    """Each measurement circuit can route differently; use its own final logical map."""
    return [
        [1 - 2 * snapshot.qubit_map[q].readout_error for q in circuit.layout.final_index_layout()[:logical_qubits]]
        for circuit in circuits
    ]


@lru_cache(maxsize=8)
def _device(snapshot: Snapshot) -> tuple[Any, Any]:
    return build_target(snapshot), build_noise_model(snapshot)


def estimate_energy(
    vqe: VQEResult,
    snapshot: Snapshot,
    layout: Sequence[int] | None,
    shots: int = 4000,
    seed: int = 1234,
    mode: str = "compact",
    mitigate_readout: bool = True,
    sampler_seed: int | None = None,
) -> EnergyEstimate:
    """Noisy energy of the optimised VQE state on `layout` (None = let Qiskit choose).

    `seed` fixes compilation (and sampling unless `sampler_seed` is given, which varies shot noise only).
    """
    problem = vqe.problem
    if problem.n_qubits > 8 or type(shots) is not int or not 2 <= shots <= 100000:
        raise HardwareError("Simulation supports up to 8 logical qubits and 2..100000 shots per group")
    if layout is not None:
        layout = list(layout)
        if len(layout) != problem.n_qubits:
            raise HardwareError(f"Layout needs {problem.n_qubits} qubits, got {len(layout)}")
        unknown = [q for q in layout if q not in snapshot.qubit_map]
        if unknown:
            raise HardwareError(f"Qubits {unknown} are not in this snapshot")
    target, noise_model = _device(snapshot)
    hamiltonian = qubit_hamiltonian(problem)
    groups = hamiltonian.group_commuting(qubit_wise=True)
    circuits = _measurement_circuits(build_circuit(vqe, mode), groups)
    if layout is None:
        selected = transpile(circuits[0], target=target, optimization_level=3, seed_transpiler=seed)
        layout = selected.layout.initial_index_layout()[: problem.n_qubits]
    compiled = transpile(circuits, target=target, initial_layout=layout, optimization_level=3, seed_transpiler=seed)
    chosen = tuple(compiled[0].layout.initial_index_layout()[: problem.n_qubits])
    if layout is not None and list(chosen) != layout:
        raise HardwareError(f"Compiler did not honour the requested layout {layout} (got {list(chosen)})")
    method = "density_matrix" if problem.n_qubits <= 8 else "automatic"
    sim = AerSimulator(
        noise_model=noise_model, method=method, seed_simulator=seed if sampler_seed is None else sampler_seed
    )
    result = sim.run(compiled, shots=shots).result()
    # Measurements land on the physical qubits of the *final* layout (after any routing swaps).
    attenuation_by_group = readout_factors(compiled, snapshot, problem.n_qubits)
    attenuation_ok = all(
        min(factors) > MIN_MITIGATION_ATTENUATION for factors in attenuation_by_group
    )  # near-random readout would explode the variance
    notes = coherence_notes(snapshot, chosen)
    if mitigate_readout and not attenuation_ok:
        bad = sorted(
            {
                q
                for c in compiled
                for q in c.layout.final_index_layout()[: problem.n_qubits]
                if 1 - 2 * snapshot.qubit_map[q].readout_error <= MIN_MITIGATION_ATTENUATION
            }
        )
        notes.insert(
            0,
            f"Readout mitigation was requested but not applied: qubits {bad} read out almost randomly "
            "(1 - 2*readout_error <= 0.05). This energy is unmitigated.",
        )
    raw, energy, var = 0.0, 0.0, 0.0
    all_counts = []
    used_factors: list[list[float] | None] = []
    for i, group in enumerate(groups):
        counts = result.get_counts(i)
        all_counts.append(counts)
        raw += _expectation(group, counts, shots)[0]
        factors = attenuation_by_group[i] if (mitigate_readout and attenuation_ok) else None
        e, v = _expectation(group, counts, shots, factors)
        used_factors.append(factors)
        energy += e
        var += v
    radius_95 = confidence_radius(groups, all_counts, used_factors)
    buffer = io.BytesIO()
    qpy.dump(compiled, buffer)
    ops = compiled[0].count_ops()
    return EnergyEstimate(
        energy,
        math.sqrt(var),
        raw,
        mitigate_readout and attenuation_ok,
        chosen,
        int(ops.get("cz", 0)),
        compiled[0].depth(),
        shots,
        mode,
        radius_95,
        {
            "groups": [list(zip(g.paulis.to_labels(), g.coeffs.real.tolist(), strict=True)) for g in groups],
            "counts": all_counts,
            "attenuation_by_group": attenuation_by_group,
            "per_group_layouts": [c.layout.final_index_layout()[: problem.n_qubits] for c in compiled],
            "seed": seed,
            "sampler_seed": seed if sampler_seed is None else sampler_seed,
            "state_parameters": vqe.parameters.tolist(),
            "circuits_qpy_base64": base64.b64encode(buffer.getvalue()).decode(),
            "h1": problem.h1.tolist(),
            "eri": problem.eri.tolist(),
            "e_core": problem.e_core,
        },
        mitigate_readout,
        tuple(notes),
    )


@dataclass
class LayoutRow:
    role: str
    estimate: EnergyEstimate
    chain: ChainScore | None
    exact: float
    e_hf: float | None = None

    @property
    def abs_error_ha(self) -> float:
        return abs(self.estimate.energy - self.exact)

    def describe(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, **self.estimate.describe(self.exact, self.e_hf)}
        if self.chain is not None:
            d["chain_esp"] = round(self.chain.esp, 5)
            d["chain_cost"] = round(self.chain.cost, 5)
        return d


@dataclass
class LayoutComparison:
    problem: ActiveSpaceProblem
    vqe: VQEResult
    rows: list[LayoutRow] = field(default_factory=list)
    spearman_rho: float | None = None
    spearman_p: float | None = None
    score_includes_readout: bool = False

    def row(self, role: str) -> LayoutRow:
        return next(r for r in self.rows if r.role == role)

    @property
    def n_correlation(self) -> int:
        return sum(r.role.startswith("random_chain_") and r.chain is not None for r in self.rows)

    def describe(self) -> dict[str, Any]:
        best, qiskit = self.row("quantivex_best"), self.row("qiskit_default")
        return {
            "problem": self.problem.describe(),
            "vqe": self.vqe.describe(),
            "rows": [r.describe() for r in self.rows],
            "cost_vs_error_spearman_rho": None if self.spearman_rho is None else round(self.spearman_rho, 3),
            "cost_vs_error_p_value": None if self.spearman_p is None else round(self.spearman_p, 4),
            "esp_vs_error_spearman_rho": None if self.spearman_rho is None else round(-self.spearman_rho, 3),
            "rho_meaning": "cost = -ln(ESP). A good score gives cost-vs-error rho near +1 (higher cost, higher "
            "error), equivalently ESP-vs-error rho near -1 (higher success probability, lower error).",
            "mitigation_consistent": all(r.estimate.mitigated == r.estimate.mitigation_requested for r in self.rows),
            "rows_with_mitigation_fallback": [
                r.role for r in self.rows if r.estimate.mitigation_requested and not r.estimate.mitigated
            ],
            "correlation_sample_size": self.n_correlation,
            "correlation_reliable": bool(
                self.spearman_p is not None
                and self.spearman_p < 0.05
                and self.n_correlation >= RELIABLE_CORRELATION_SAMPLE
            ),
            "chain_score": "full ESP (readout + gates)"
            if self.score_includes_readout
            else "gate-only ESP (readout errors are mitigated, so readout is excluded from the ranking)",
            "quantivex_vs_qiskit_error_mha": {
                "quantivex_best": round(1000 * best.abs_error_ha, 3),
                "qiskit_default": round(1000 * qiskit.abs_error_ha, 3),
                "difference_mha": round(1000 * (qiskit.abs_error_ha - best.abs_error_ha), 3),
                "combined_two_sigma_mha": round(2000 * math.hypot(best.estimate.stderr, qiskit.estimate.stderr), 3),
                "note": "Descriptive shot-noise scale for one seed; not a significance test. Use the paired "
                "multi-seed benchmark for comparisons.",
            },
            "correlation_scope": "random chains only; full-population uniform sample without replacement",
            "permutation_method": "exact"
            if sum(r.role.startswith("random_chain_") for r in self.rows) <= 8
            else "Monte Carlo 9999 permutations + observed",
            "interpretation_note": (
                "Exploratory random-chain subset only; selected best/worst/Qiskit rows excluded from correlation. "
                "Permutation p-value assumes exchangeability and is not evidence of hardware generalization. "
                "No significance claim follows from a two-sigma comparison; repeated geometry samples share chains."
            ),
        }


def compare_layouts(
    snapshot: Snapshot,
    molecule: str = "N2",
    bond_length: float = 1.10,
    n_electrons: int = 2,
    n_orbitals: int = 2,
    n_random: int = DEFAULT_RANDOM_CHAINS,
    shots: int = 4000,
    seed: int = 1234,
    mode: str = "compact",
    mitigate_readout: bool = True,
) -> LayoutComparison:
    """Same circuit on several layouts.

    The chain score matches the analysis: gate-only ESP when readout is mitigated (a 40-chain Torino
    study found the full score predicts mitigated error poorly, Spearman ~0.38, and the gate-only
    score well, ~0.96), full ESP otherwise. Correlation uses the random chains only.
    """
    problem = get_problem(molecule, bond_length, n_electrons, n_orbitals)
    vqe = run_vqe(problem)
    include_readout = not mitigate_readout
    chains = rank_chains(snapshot, problem.n_qubits, include_readout=include_readout)
    picks: list[tuple[str, ChainScore]] = [("quantivex_best", chains[0]), ("worst_usable_chain", chains[-1])]
    rng = random.Random(seed)
    if type(n_random) is not int or not 0 <= n_random <= MAX_RANDOM_CHAINS:
        raise HardwareError(f"n_random must be an integer in 0..{MAX_RANDOM_CHAINS}")
    middle = chains  # uniform draw from full enumerated population; selected rows remain descriptive
    for i, c in enumerate(rng.sample(middle, min(n_random, len(middle)))):
        picks.append((f"random_chain_{i + 1}", c))
    comparison = LayoutComparison(problem, vqe, score_includes_readout=include_readout)
    for role, chain in picks:
        est = estimate_energy(vqe, snapshot, chain.qubits, shots, seed, mode, mitigate_readout)
        comparison.rows.append(LayoutRow(role, est, chain, problem.e_exact, problem.e_hf))
    est = estimate_energy(vqe, snapshot, None, shots, seed, mode, mitigate_readout)
    cost = layout_cost(snapshot, est.layout, include_readout)
    qiskit_chain = score_chain(snapshot, est.layout, include_readout) if cost is not None else None
    comparison.rows.insert(1, LayoutRow("qiskit_default", est, qiskit_chain, problem.e_exact, problem.e_hf))
    scored = [r for r in comparison.rows if r.chain is not None and r.role.startswith("random_chain_")]
    if len(scored) >= 4:
        from itertools import permutations

        costs = np.asarray([r.chain.cost for r in scored if r.chain])
        errors = np.asarray([r.abs_error_ha for r in scored])
        if len(set(costs)) > 1 and len(set(errors)) > 1:
            from scipy.stats import rankdata

            x, y = rankdata(costs), rankdata(errors)
            rho = float(np.corrcoef(x, y)[0, 1])
            rng_stats = np.random.default_rng(seed)
            exact_test = len(scored) <= 8
            samples = permutations(y) if exact_test else (rng_stats.permutation(y) for _ in range(9999))
            extreme, total = 0, 0
            for perm in samples:
                extreme += abs(float(np.corrcoef(x, perm)[0, 1])) >= abs(rho) - 1e-12
                total += 1
            comparison.spearman_rho = rho
            comparison.spearman_p = extreme / total if exact_test else (extreme + 1) / (total + 1)
    return comparison


@dataclass
class CurvePoint:
    bond_length: float
    e_hf: float
    e_exact: float
    e_vqe: float
    noisy: EnergyEstimate | None
    evidence: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "bond_length_angstrom": self.bond_length,
            "e_hf": round(self.e_hf, 6),
            "e_exact": round(self.e_exact, 6),
            "e_vqe_ideal": round(self.e_vqe, 6),
            "reference_target": "singlet in HF spatial symmetry",
            "numerical_inputs": self.evidence,
        }
        if self.noisy is not None:
            d.update(
                {
                    "e_noisy": round(self.noisy.energy, 6),
                    "noisy_error_mha": round(1000 * (self.noisy.energy - self.e_exact), 3),
                    "stderr_mha": round(1000 * self.noisy.stderr, 3),
                    "measurement_record": self.noisy.measurement_record,
                    **verdict(self.noisy.energy - self.e_exact, self.noisy.stderr, self.noisy.confidence_radius),
                }
            )
        return d


def energy_curve(
    molecule: str,
    bond_lengths: Sequence[float],
    n_electrons: int = 2,
    n_orbitals: int = 2,
    snapshot: Snapshot | None = None,
    layout: Sequence[int] | None = None,
    shots: int = 4000,
    seed: int = 1234,
    mode: str = "compact",
    mitigate_readout: bool = True,
) -> list[CurvePoint]:
    """Potential-energy curve: HF, exact, ideal VQE and (optionally) noisy VQE on one layout."""
    points = []
    for r in bond_lengths:
        problem = get_problem(molecule, round(float(r), 2), n_electrons, n_orbitals)
        vqe = run_vqe(problem)
        noisy = (
            estimate_energy(vqe, snapshot, layout, shots, seed, mode, mitigate_readout)
            if snapshot is not None
            else None
        )
        evidence = {
            "parameters": vqe.parameters.tolist(),
            "h1": problem.h1.tolist(),
            "eri": problem.eri.tolist(),
            "e_core": problem.e_core,
            "mode": mode,
        }
        points.append(CurvePoint(problem.bond_length, problem.e_hf, problem.e_exact, vqe.energy, noisy, evidence))
    return points
