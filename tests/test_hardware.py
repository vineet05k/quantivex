import math
from itertools import pairwise

import pytest

from quantivex.data import from_dict
from quantivex.errors import HardwareError
from quantivex.hardware import coordinates, iter_chains, layout_cost, nll, rank_chains, score_chain

from .conftest import line_chip


def test_nll():
    assert nll(0.0) == 0.0
    assert nll(None) == 0.0
    assert nll(1.0) == math.inf
    assert nll(0.1) == pytest.approx(-math.log(0.9))


def test_chain_enumeration_counts(chip):
    # Line 0..5 plus branch 2-6: paths of 3 qubits = 4 on the line + 2 through the branch (1-2-6, 3-2-6).
    chains = list(iter_chains(chip, 3))
    assert len(chains) == 6
    assert len(set(chains)) == 6
    assert all(c[0] < c[-1] for c in chains)  # each path listed once


def test_every_chain_is_a_physical_path(torino):
    for c in iter_chains(torino, 6):
        assert all(torino.link(a, b) is not None for a, b in pairwise(c))


def test_rank_prefers_low_error_links():
    snap = from_dict(line_chip(errors=[0.001, 0.001, 0.2, 0.2, 0.2]))
    best = rank_chains(snap, 3)[0]
    assert best.qubits == (0, 1, 2)
    assert best.esp == pytest.approx(math.exp(-best.cost))


def test_dead_links_are_excluded():
    snap = from_dict(line_chip(errors=[0.01, 0.9, 0.01, 0.01, 0.01]))  # 1-2 is dead
    for c in iter_chains(snap, 2):
        assert set(c) != {1, 2}
    assert layout_cost(snap, (1, 2)) == math.inf or layout_cost(snap, (1, 2)) > 2


def test_score_chain_rejects_invalid(chip):
    with pytest.raises(HardwareError, match="not physically linked"):
        score_chain(chip, [0, 2])
    with pytest.raises(HardwareError, match="repeats"):
        score_chain(chip, [0, 1, 0])
    with pytest.raises(HardwareError, match="not in this snapshot"):
        score_chain(chip, [0, 99])
    assert layout_cost(chip, [0, 2]) is None


@pytest.mark.parametrize("length", [0, -1, 17, True])
def test_bad_lengths(chip, length):
    with pytest.raises(HardwareError):
        list(iter_chains(chip, length))


def test_budget(torino):
    with pytest.raises(HardwareError, match="budget"):
        rank_chains(torino, 10, budget=100)


def test_torino_best_chain_is_stable(torino):
    best = rank_chains(torino, 4)[0]
    assert best.qubits == (81, 80, 92, 99) or best.qubits == (99, 92, 80, 81)
    assert 86 not in best.qubits  # 56% readout error


def test_heavy_hex_coordinates(torino):
    pos = coordinates(torino)
    assert len(pos) == 133
    assert pos[0] == (0.0, 0.0) and pos[14] == (14.0, 0.0)
    assert pos[15][1] == -1.0  # bridge qubit between rows 0 and 1
    assert len(set(pos.values())) == 133


def test_fallback_coordinates(chip):
    assert len(set(coordinates(chip).values())) == chip.num_qubits
