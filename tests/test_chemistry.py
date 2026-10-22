import importlib.util
from itertools import pairwise

import numpy as np
import pytest

from quantivex import chemistry as chem
from quantivex.errors import ChemistryError

HAS_PYSCF = importlib.util.find_spec("pyscf") is not None
EQUILIBRIUM_CASES = [("H2", 0.75, 2, 2), ("N2", 1.10, 2, 2), ("N2", 1.10, 4, 4), ("LiH", 1.60, 2, 2)]


@pytest.mark.parametrize("molecule, r, ne, no", EQUILIBRIUM_CASES)
def test_hartree_fock_determinant_energy(molecule, r, ne, no):
    """Catches operator-ordering bugs: a particle-hole-flipped Hamiltonian has the right spectrum
    but puts the HF energy on the wrong determinant."""
    p = chem.get_problem(molecule, r, ne, no)
    h = chem.qubit_hamiltonian(p).to_matrix(sparse=True)
    assert h[p.hf_index, p.hf_index].real == pytest.approx(p.e_hf, abs=1e-8)


@pytest.mark.parametrize("molecule, r, ne, no", EQUILIBRIUM_CASES)
def test_sector_ground_state_matches_casci(molecule, r, ne, no):
    p = chem.get_problem(molecule, r, ne, no)
    assert chem.sector_ground_energy(p) == pytest.approx(p.e_exact, abs=1e-8)


def test_hamiltonian_is_hermitian_and_number_conserving():
    p = chem.get_problem("N2", 1.10, 4, 4)
    h = chem.qubit_hamiltonian(p)
    n = chem.number_operator(p.n_qubits)
    hm, nm = h.to_matrix(), n.to_matrix()
    assert np.allclose(hm, hm.conj().T)
    assert np.allclose(hm @ nm, nm @ hm)
    hf = np.zeros(2**p.n_qubits)
    hf[p.hf_index] = 1
    assert hf @ nm @ hf == pytest.approx(p.n_electrons)


def test_excitation_counts():
    assert len(chem.uccsd_excitations(chem.get_problem("N2", 1.10, 2, 2))) == 3
    assert len(chem.uccsd_excitations(chem.get_problem("N2", 1.10, 4, 4))) == 26


def test_curve_is_smooth_across_cache():
    """Active orbitals are chosen by symmetry: correlation energy must grow smoothly with R."""
    grid = chem.cached_geometries("N2", 2, 2)
    assert len(grid) == 33
    corr = [chem.get_problem("N2", r).e_hf - chem.get_problem("N2", r).e_exact for r in grid]
    assert all(b >= a - 1e-6 for a, b in pairwise(corr))


@pytest.mark.parametrize(
    "args, message",
    [(("Xe2", 1.0, 2, 2), "Unknown molecule"), (("N2", 9.0, 2, 2), "bond length"), (("N2", 1.1, 8, 8), "Active space")],
)
def test_invalid_requests(args, message):
    with pytest.raises(ChemistryError, match=message):
        chem.get_problem(*args)


@pytest.mark.skipif(not HAS_PYSCF, reason="PySCF not installed")
def test_cache_matches_live_pyscf():
    cached = chem.get_problem("N2", 1.50, 2, 2)
    live = chem.compute_with_pyscf("N2", 1.50, 2, 2)
    assert cached.source.startswith("bundled")
    assert live.e_exact == pytest.approx(cached.e_exact, abs=1e-8)
    assert live.e_hf == pytest.approx(cached.e_hf, abs=1e-8)


@pytest.mark.skipif(not HAS_PYSCF, reason="PySCF not installed")
def test_off_grid_geometry_uses_pyscf():
    p = chem.get_problem("H2", 0.77)
    assert p.source == "pyscf"
    assert chem.sector_ground_energy(p) == pytest.approx(p.e_exact, abs=1e-8)


def test_off_grid_without_pyscf_explains(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_pyscf(name, *args, **kwargs):
        if name.startswith("pyscf"):
            raise ImportError("no pyscf")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pyscf)
    with pytest.raises(ChemistryError, match="Cached bond lengths"):
        chem.compute_with_pyscf("H2", 0.77, 2, 2)
