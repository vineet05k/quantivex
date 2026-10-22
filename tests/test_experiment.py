import pytest

from quantivex.chemistry import get_problem
from quantivex.data import from_dict
from quantivex.errors import HardwareError
from quantivex.experiment import (
    DEGRADED,
    TRUSTWORTHY,
    UNTRUSTWORTHY,
    compare_layouts,
    energy_curve,
    estimate_energy,
    verdict,
)
from quantivex.noise import build_noise_model, build_target
from quantivex.vqe import run_vqe

from .conftest import line_chip


@pytest.fixture(scope="module")
def h2():
    return run_vqe(get_problem("H2", 0.75))


def test_verdict_thresholds():
    assert verdict(0.0005, 0.0001, 0.0002)["verdict"] == TRUSTWORTHY
    assert verdict(0.0005, 0.001, 0.002)["verdict"] == DEGRADED  # finite-sample bound overlaps the tolerance boundary
    assert verdict(0.010, 0.0, 0)["verdict"] == UNTRUSTWORTHY
    assert verdict(-0.050, 0.0, 0)["verdict"] == UNTRUSTWORTHY


def test_target_and_noise_model(chip):
    target = build_target(chip)
    assert target.num_qubits == 7
    assert (0, 1) in target["cz"] and (1, 0) in target["cz"]
    model = build_noise_model(chip)
    assert "cz" in model.noise_instructions


def test_dead_link_not_in_target():
    snap = from_dict(line_chip(errors=[0.01, 0.9, 0.01, 0.01, 0.01]))
    assert (1, 2) not in build_target(snap)["cz"]


def test_noiseless_chip_reproduces_exact_energy(h2):
    perfect = from_dict(line_chip(errors=[0.0] * 5, readout=0.0, t1=None, sx=0.0))
    est = estimate_energy(h2, perfect, [0, 1, 2, 3], shots=20000, seed=3)
    assert est.energy == pytest.approx(h2.problem.e_exact, abs=4 * est.stderr + 1e-9)
    assert est.layout == (0, 1, 2, 3)


def test_single_qubit_noise_alone_is_visible(h2):
    """A 2e-4 single-qubit error over a state-preparation circuit's many sx gates costs mHa."""
    sx_only = from_dict(line_chip(errors=[0.0] * 5, readout=0.0, t1=None, sx=2e-4))
    est = estimate_energy(h2, sx_only, [0, 1, 2, 3], 20000, 3)
    assert est.energy - h2.problem.e_exact > 3 * est.stderr


def test_noise_increases_error(h2):
    clean = from_dict(line_chip(errors=[0.001] * 5, readout=0.002))
    dirty = from_dict(line_chip(errors=[0.05] * 5, readout=0.002))
    e_clean = abs(estimate_energy(h2, clean, [0, 1, 2, 3], 8000, 5).energy - h2.problem.e_exact)
    e_dirty = abs(estimate_energy(h2, dirty, [0, 1, 2, 3], 8000, 5).energy - h2.problem.e_exact)
    assert e_dirty > e_clean


def test_readout_mitigation_removes_readout_bias(h2):
    noisy_readout = from_dict(line_chip(errors=[0.0] * 5, readout=0.05, t1=None, sx=0.0))
    raw = estimate_energy(h2, noisy_readout, [0, 1, 2, 3], 20000, 9, mitigate_readout=False)
    mit = estimate_energy(h2, noisy_readout, [0, 1, 2, 3], 20000, 9)
    exact = h2.problem.e_exact
    assert abs(mit.energy - exact) < abs(raw.energy - exact)
    assert abs(mit.energy - exact) < 4 * mit.stderr
    assert mit.mitigated and not raw.mitigated


def test_layout_validation(chip, h2):
    with pytest.raises(HardwareError, match="needs 4"):
        estimate_energy(h2, chip, [0, 1], 1000)
    with pytest.raises(HardwareError, match="not in this snapshot"):
        estimate_energy(h2, chip, [0, 1, 2, 99], 1000)


def test_qiskit_default_layout(chip, h2):
    est = estimate_energy(h2, chip, None, 1000)
    assert len(est.layout) == 4


def test_energy_curve_noiseless_only():
    pts = energy_curve("H2", [0.5, 0.75, 1.5])
    assert [p.bond_length for p in pts] == [0.5, 0.75, 1.5]
    assert all(p.noisy is None and abs(p.e_vqe - p.e_exact) < 1e-6 for p in pts)
    assert min(pts, key=lambda p: p.e_exact).bond_length == 0.75


@pytest.mark.slow
def test_compare_layouts_on_torino(torino):
    comp = compare_layouts(torino, "N2", 2.0, n_random=6, shots=4000, seed=1234)
    roles = [r.role for r in comp.rows]
    assert roles[:3] == ["quantivex_best", "qiskit_default", "worst_usable_chain"]
    best, worst = comp.row("quantivex_best"), comp.row("worst_usable_chain")
    assert best.abs_error_ha < worst.abs_error_ha
    assert comp.spearman_rho is not None and -1 <= comp.spearman_rho <= 1
    assert comp.describe()["correlation_scope"].startswith("random chains only")
    d = comp.describe()
    assert d["quantivex_vs_qiskit_error_mha"]["combined_two_sigma_mha"] > 0
