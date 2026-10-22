"""Noiseless VQE with a UCCSD ansatz.

Parameters are optimised classically with exact state-vector algebra
(sparse matrix exponentials), which is fast and deterministic. The optimised
parameters are then bound into a gate-level circuit (`build_circuit`) whose
behaviour on noisy hardware is studied in `experiment`.
"""

from __future__ import annotations

import logging
import time
import warnings
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from qiskit import QuantumCircuit
from qiskit.circuit.library import PauliEvolutionGate, StatePreparation
from qiskit.quantum_info import SparsePauliOp, Statevector
from qiskit.synthesis import LieTrotter
from scipy.optimize import minimize
from scipy.sparse import SparseEfficiencyWarning, csr_matrix
from scipy.sparse.linalg import expm_multiply

from .chemistry import ActiveSpaceProblem, qubit_hamiltonian, spin_squared_operator, uccsd_excitations
from .errors import ChemistryError

CIRCUIT_MODES = ("compact", "uccsd")
COMPACT_AMPLITUDE_CUTOFF = 1e-5
CIRCUIT_ENERGY_TOLERANCE_HA = 1e-6  # gate-level state vs optimiser energy

log = logging.getLogger(__name__)


@dataclass(frozen=True, eq=False)
class VQEResult:
    problem: ActiveSpaceProblem
    parameters: np.ndarray
    energy: float
    n_evaluations: int
    converged: bool
    seconds: float
    spin_squared: float = 0.0

    def __post_init__(self) -> None:
        values = np.asarray(self.parameters, dtype=float)
        immutable = np.frombuffer(values.tobytes(), dtype=float).reshape(values.shape)
        object.__setattr__(self, "parameters", immutable)

    @property
    def error_mha(self) -> float:
        return 1000.0 * (self.energy - self.problem.e_exact)

    def describe(self) -> dict[str, object]:
        return {
            "ansatz": "UCCSD (one Trotter step)",
            "parameters": len(self.parameters),
            "e_vqe_ideal_hartree": round(self.energy, 8),
            "ansatz_error_mha": round(self.error_mha, 4),
            "optimizer_evaluations": self.n_evaluations,
            "converged": self.converged,
            "spin_squared_expectation": self.spin_squared,
            "target_spin_s": 0,
            "target_match": abs(self.spin_squared) < 1e-5,
        }


def _generators(problem: ActiveSpaceProblem) -> list[csr_matrix]:
    mats = []
    for exc in uccsd_excitations(problem):
        a = (exc.operator - exc.operator.adjoint()).simplify().to_matrix(sparse=True)
        mats.append(csr_matrix(a.real))  # JW excitation generators are real antisymmetric
    return mats


def _energy_gradient(
    h: csr_matrix, generators: list[csr_matrix], initial: np.ndarray, parameters: np.ndarray
) -> tuple[float, np.ndarray]:
    """Exact reverse-mode derivative for a product of one-parameter exponentials.

    Forward states are phi[j+1] = exp(x[j]*G[j]) phi[j]. Back-propagate
    H*phi[-1] with exp(-x[j]*G[j]); anti-Hermitian G makes that the adjoint.
    This differentiates the same UCC state, without finite-difference repeats.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SparseEfficiencyWarning)
        warnings.simplefilter("ignore", UserWarning)
        states = [initial]
        for theta, generator in zip(parameters, generators, strict=True):
            states.append(expm_multiply(theta * generator, states[-1]))
        co_state = h @ states[-1]
        energy = float(np.real(np.vdot(states[-1], co_state)))
        gradient = np.empty(len(generators))
        for j in reversed(range(len(generators))):
            gradient[j] = 2 * np.real(np.vdot(co_state, generators[j] @ states[j + 1]))
            co_state = expm_multiply(-parameters[j] * generators[j], co_state)
    return energy, gradient


@lru_cache(maxsize=128)
def run_vqe(problem: ActiveSpaceProblem, maxiter: int = 2000) -> VQEResult:
    """Minimise <HF| U(t)^dagger H U(t) |HF> with U(t) = prod_k exp(t_k (T_k - T_k^dagger))."""
    if problem.n_qubits > 8:
        raise ChemistryError("VQE is limited to 8 qubits; 12-qubit cases provide reference energies only")
    if not 1 <= maxiter <= 2000:
        raise ChemistryError("maxiter must be in 1..2000")
    start = time.perf_counter()
    evaluations = 0
    h = csr_matrix(qubit_hamiltonian(problem).to_matrix(sparse=True).real)
    gens = _generators(problem)
    psi0 = np.zeros(2**problem.n_qubits)
    psi0[problem.hf_index] = 1.0

    def state(x: np.ndarray) -> np.ndarray:
        v = psi0
        with warnings.catch_warnings():
            # expm_multiply converts formats internally; the efficiency warnings are harmless here.
            warnings.simplefilter("ignore", SparseEfficiencyWarning)
            warnings.simplefilter("ignore", UserWarning)
            for theta, g in zip(x, gens, strict=True):
                v = expm_multiply(theta * g, v)
        return v

    def check_budget() -> None:
        nonlocal evaluations
        evaluations += 1
        if evaluations > 20000 or time.perf_counter() - start > 240:
            raise ChemistryError("VQE exceeded its 240-second/20000-evaluation budget")

    def energy(x: np.ndarray) -> float:
        check_budget()
        v = state(x)
        return float(v @ (h @ v))

    def energy_with_gradient(x: np.ndarray) -> tuple[float, np.ndarray]:
        check_budget()
        return _energy_gradient(h, gens, psi0, x)

    if problem.n_qubits > 4:
        res = minimize(
            energy_with_gradient,
            np.zeros(len(gens)),
            jac=True,
            method="BFGS",
            options={"maxiter": maxiter, "gtol": 1e-7},
        )
    else:
        res = minimize(energy, np.zeros(len(gens)), method="BFGS", options={"maxiter": maxiter, "gtol": 1e-7})
    # BFGS often stops with "precision loss" exactly at the optimum; judge convergence by the gradient.
    converged = bool(res.success) or float(np.linalg.norm(res.jac)) < 1e-4
    psi = state(res.x)
    s2 = float(np.real(np.vdot(psi, spin_squared_operator(problem.n_orbitals).to_matrix(sparse=True) @ psi)))
    if abs(s2) > 1e-5:
        raise ChemistryError(
            "Optimized state does not match the singlet reference target; no energy comparison returned"
        )
    result = VQEResult(
        problem, np.asarray(res.x), float(res.fun), int(res.nfev), converged, time.perf_counter() - start, s2
    )
    log.info(
        "VQE %s R=%.2f: %.8f Ha (ansatz error %.4f mHa, %d evals)",
        problem.molecule,
        problem.bond_length,
        result.energy,
        result.error_mha,
        result.n_evaluations,
    )
    return result


def build_circuit(result: VQEResult, mode: str = "compact") -> QuantumCircuit:
    """Gate-level circuit preparing the optimised VQE state.

    mode="uccsd":   the UCCSD ansatz itself (HF preparation + one Pauli evolution per excitation).
    mode="compact": the same optimised state, compiled by exact state preparation. For small
                    active spaces this needs far fewer two-qubit gates (17 vs 44 CZ for 4 qubits
                    on Torino), so the hardware-noise study is not swamped by synthesis overhead.
    """
    if mode not in CIRCUIT_MODES:
        raise ChemistryError(f"circuit mode must be one of {CIRCUIT_MODES}, got {mode!r}")
    qc = _build_circuit(result, mode)
    # Independent consistency check: the gates must prepare a state with the optimised energy.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SparseEfficiencyWarning)
        energy = float(np.real(Statevector(qc).expectation_value(qubit_hamiltonian(result.problem))))
    if abs(energy - result.energy) > CIRCUIT_ENERGY_TOLERANCE_HA:
        raise ChemistryError(
            f"Circuit/optimiser mismatch: the {mode} circuit prepares E = {energy:.9f} Ha but the optimiser "
            f"reported {result.energy:.9f} Ha"
        )
    return qc


def _build_circuit(result: VQEResult, mode: str) -> QuantumCircuit:
    if mode == "compact":
        with warnings.catch_warnings():  # Pauli-evolution matrices go through scipy's sparse expm
            warnings.simplefilter("ignore", SparseEfficiencyWarning)
            psi = Statevector(_build_circuit(result, "uccsd")).data
        # Drop negligible amplitudes: below 1e-5 they change the energy by < 1e-10 Ha, and near-zero
        # amplitudes can destabilise StatePreparation synthesis (observed in Qiskit 2.6: a 1e-7 component
        # flipped the sign of a large amplitude, fidelity 0.55). The result is always verified below.
        target = np.where(np.abs(psi) < COMPACT_AMPLITUDE_CUTOFF, 0.0, psi)
        target = target / np.linalg.norm(target)
        qc = QuantumCircuit(result.problem.n_qubits, name=f"vqe_state_{result.problem.molecule}")
        qc.append(StatePreparation(target), range(result.problem.n_qubits))
        fidelity = float(abs(np.vdot(psi, Statevector(qc).data)) ** 2)
        if fidelity < 1 - 1e-8:
            raise ChemistryError(
                f"Compact state preparation failed verification (fidelity {fidelity:.6f}); use circuit_mode='uccsd'"
            )
        return qc
    p = result.problem
    qc = QuantumCircuit(p.n_qubits, name=f"uccsd_{p.molecule}")
    for i in range(p.n_alpha):
        qc.x(i)
    for i in range(p.n_beta):
        qc.x(p.n_orbitals + i)
    for theta, exc in zip(result.parameters, uccsd_excitations(p), strict=True):
        # exp(theta * A) with A = T - T^dagger anti-Hermitian equals exp(-i * theta * (iA)).
        herm = (1j * (exc.operator - exc.operator.adjoint())).simplify()
        herm = SparsePauliOp(herm.paulis, np.real(herm.coeffs))
        qc.append(PauliEvolutionGate(herm, time=float(theta), synthesis=LieTrotter()), range(p.n_qubits))
    return qc
