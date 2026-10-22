import numpy as np
import pytest
from qiskit.quantum_info import Statevector

from quantivex.chemistry import CHEMICAL_ACCURACY_HA, get_problem, qubit_hamiltonian
from quantivex.errors import ChemistryError
from quantivex.vqe import build_circuit, run_vqe


@pytest.mark.parametrize(
    "molecule, r", [("H2", 0.75), ("H2", 2.0), ("N2", 1.10), ("N2", 1.70), ("N2", 2.40), ("LiH", 1.60)]
)
def test_two_orbital_vqe_is_exact(molecule, r):
    v = run_vqe(get_problem(molecule, r))
    assert abs(v.error_mha) < 1e-3
    assert v.converged


def test_eight_qubit_vqe_within_chemical_accuracy_at_equilibrium():
    v = run_vqe(get_problem("N2", 1.10, 4, 4))
    assert 0 <= v.energy - v.problem.e_exact < CHEMICAL_ACCURACY_HA


@pytest.mark.parametrize("mode", ["uccsd", "compact"])
def test_circuit_reproduces_optimised_energy(mode):
    p = get_problem("N2", 2.0)
    v = run_vqe(p)
    e = np.real(Statevector(build_circuit(v, mode)).expectation_value(qubit_hamiltonian(p)))
    assert e == pytest.approx(v.energy, abs=1e-7)


def test_unknown_mode():
    with pytest.raises(ChemistryError):
        build_circuit(run_vqe(get_problem("H2", 0.75)), "magic")


def test_vqe_is_cached():
    p = get_problem("H2", 0.75)
    assert run_vqe(p) is run_vqe(p)


@pytest.mark.parametrize("molecule, ne, no", [("N2", 2, 2), ("H2", 2, 2), ("LiH", 2, 2)])
def test_compact_circuit_verified_on_every_cached_geometry(molecule, ne, no):
    """Regression: StatePreparation once flipped an amplitude sign when tiny components were present."""
    from quantivex.chemistry import cached_geometries

    for r in cached_geometries(molecule, ne, no):
        v = run_vqe(get_problem(molecule, r, ne, no))
        build_circuit(v, "compact")  # raises ChemistryError if the prepared state is wrong
