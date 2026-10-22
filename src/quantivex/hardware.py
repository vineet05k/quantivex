"""Qubit-chain selection on a calibrated coupling graph.

A *chain* is a simple path of physically linked qubits, the layout most VQE
ansatze and nearest-neighbour circuits need. Chains are scored with the
negative log of an estimated success probability (ESP; Nishio et al. 2020)::

    cost = sum_q -ln(1 - readout_q) + sum_q -ln(1 - sx_q) + sum_links -ln(1 - cz_link)
    ESP  = exp(-cost)

This is circuit-agnostic: each qubit and link is counted once. It is a proxy
for comparing layouts, not a prediction of a specific circuit's fidelity.

Mitigation-aware scoring (include_readout=False): when readout errors are
mitigated, the readout term no longer drives the result and only adds noise to
the ranking. In a 40-chain study on ibm_torino (N2, CAS(2,2), 1.1 and 2.0 A) the
full score predicted the *raw* energy error well (Spearman 0.87-0.91) but the
*mitigated* error poorly (0.38-0.39); the gate-only score (single-qubit + links)
predicted the mitigated error with Spearman 0.96-0.97. Quantivex therefore uses
the gate-only score whenever readout mitigation is on.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from itertools import pairwise

from .data import DEAD_LINK_THRESHOLD, Snapshot
from .errors import HardwareError

# Readout mitigation divides by (1 - 2 * readout_error); at or below this the variance explodes and the
# estimator refuses to mitigate. Shared by ranking and estimation so both use the same eligibility rule.
MIN_MITIGATION_ATTENUATION = 0.05


def readout_mitigable(snapshot: Snapshot, qubits: Sequence[int]) -> bool:
    """True if every qubit's readout can be mitigated by the estimator."""
    return all(1 - 2 * snapshot.qubit_map[q].readout_error > MIN_MITIGATION_ATTENUATION for q in qubits)


MAX_CHAIN_LENGTH = 16
DEFAULT_PATH_BUDGET = 2_000_000


def nll(error: float | None) -> float:
    """Negative log-likelihood of success for one operation with this error rate."""
    if error is None or error <= 0.0:
        return 0.0
    if error >= 1.0:
        return math.inf
    return -math.log1p(-error)


@dataclass(frozen=True)
class ChainScore:
    qubits: tuple[int, ...]
    cost: float
    readout_cost: float
    single_qubit_cost: float
    link_cost: float
    worst_link_error: float
    includes_readout: bool = True

    @property
    def esp(self) -> float:
        return math.exp(-self.cost)

    def as_dict(self) -> dict[str, object]:
        return {
            "qubits": list(self.qubits),
            "esp": round(self.esp, 6),
            "cost": round(self.cost, 6),
            "readout_cost": round(self.readout_cost, 6),
            "single_qubit_cost": round(self.single_qubit_cost, 6),
            "link_cost": round(self.link_cost, 6),
            "worst_link_error": round(self.worst_link_error, 6),
            "score": "full ESP (readout + gates)" if self.includes_readout else "gate-only (readout mitigated)",
        }


def score_chain(snapshot: Snapshot, chain: Sequence[int], include_readout: bool = True) -> ChainScore:
    """Score a chain. Raises HardwareError if it is not a valid simple path on the chip.

    include_readout=False gives the gate-only score, the right one when readout errors are mitigated.
    """
    if len(chain) == 0:
        raise HardwareError("A chain needs at least one qubit")
    if len(set(chain)) != len(chain):
        raise HardwareError(f"Chain {list(chain)} repeats a qubit")
    missing = [q for q in chain if q not in snapshot.qubit_map]
    if missing:
        raise HardwareError(f"Qubits {missing} are not in this snapshot")
    readout = sum(nll(snapshot.qubit_map[q].readout_error) for q in chain)
    single = sum(nll(snapshot.qubit_map[q].sx_error) for q in chain)
    link_cost, worst = 0.0, 0.0
    for a, b in pairwise(chain):
        link = snapshot.link(a, b)
        if link is None:
            raise HardwareError(f"Qubits {a} and {b} are not physically linked")
        link_cost += nll(link.gate_error)
        worst = max(worst, link.gate_error)
    total = (readout if include_readout else 0.0) + single + link_cost
    return ChainScore(tuple(chain), total, readout, single, link_cost, worst, include_readout)


def iter_chains(
    snapshot: Snapshot,
    length: int,
    max_link_error: float = DEAD_LINK_THRESHOLD,
    budget: int = DEFAULT_PATH_BUDGET,
) -> Iterator[tuple[int, ...]]:
    """Yield every simple path with `length` qubits once (direction-normalised).

    Links with error >= max_link_error are treated as absent. Raises HardwareError
    if more than `budget` partial paths would be explored.
    """
    if not isinstance(length, int) or isinstance(length, bool) or length < 1:
        raise HardwareError("length must be a positive integer")
    if length > MAX_CHAIN_LENGTH:
        raise HardwareError(f"length must be at most {MAX_CHAIN_LENGTH}")
    if length > snapshot.num_qubits:
        raise HardwareError(f"The chip has only {snapshot.num_qubits} qubits")
    adj = {
        q: sorted(n for n in nbrs if (link := snapshot.link(q, n)) and link.gate_error < max_link_error)
        for q, nbrs in snapshot.adjacency.items()
    }
    explored = 0
    for start in sorted(adj):
        stack: list[tuple[int, ...]] = [(start,)]
        while stack:
            path = stack.pop()
            explored += 1
            if explored > budget:
                raise HardwareError(
                    f"Search budget of {budget} paths exceeded for length {length}; "
                    "use a shorter chain or raise the budget"
                )
            if len(path) == length:
                if length == 1 or path[0] < path[-1]:
                    yield path
                continue
            for n in adj[path[-1]]:
                if n not in path:
                    stack.append((*path, n))


def rank_chains(
    snapshot: Snapshot,
    length: int,
    max_link_error: float = DEAD_LINK_THRESHOLD,
    budget: int = DEFAULT_PATH_BUDGET,
    include_readout: bool = True,
) -> list[ChainScore]:
    """All usable chains of `length` qubits, best (lowest cost) first. Deterministic.

    include_readout=False is the score for readout-mitigated runs, so chains whose readout cannot be
    mitigated (see readout_mitigable) are excluded: the gate-only score would not describe them.
    """
    scored = [score_chain(snapshot, c, include_readout) for c in iter_chains(snapshot, length, max_link_error, budget)]
    scored = [s for s in scored if math.isfinite(s.cost)]
    if not include_readout:
        scored = [s for s in scored if readout_mitigable(snapshot, s.qubits)]
    if not scored:
        suffix = "" if include_readout else " whose readout can be mitigated (try readout_mitigation=false)"
        raise HardwareError(f"No usable chain of {length} linked qubits{suffix} exists on this chip")
    scored.sort(key=lambda s: (s.cost, s.qubits))
    return scored


def layout_cost(snapshot: Snapshot, layout: Sequence[int], include_readout: bool = True) -> float | None:
    """Chain cost if `layout` is a valid chain on the chip, else None."""
    try:
        return score_chain(snapshot, layout, include_readout).cost
    except HardwareError:
        return None


def coordinates(snapshot: Snapshot) -> dict[int, tuple[float, float]]:
    """2-D drawing positions. Detects IBM heavy-hex rows; falls back to a grid-like layout."""
    ids = [q.id for q in snapshot.qubits]
    runs: list[list[int]] = []
    current = [ids[0]]
    for q in ids[1:]:
        if snapshot.link(current[-1], q) is not None and q == current[-1] + 1:
            current.append(q)
        else:
            runs.append(current)
            current = [q]
    runs.append(current)
    rows = [r for r in runs if len(r) >= 5]
    pos: dict[int, tuple[float, float]] = {}
    if len(rows) >= 2:
        row_of = {}
        for r_index, row in enumerate(rows):
            for col, q in enumerate(row):
                pos[q] = (float(col), -2.0 * r_index)
                row_of[q] = r_index
        for q in ids:
            if q in pos:
                continue
            nbrs = [n for n in snapshot.adjacency[q] if n in pos]
            if nbrs:
                x = sum(pos[n][0] for n in nbrs) / len(nbrs)
                y = (sum(pos[n][1] for n in nbrs) / len(nbrs)) - (1.0 if len(nbrs) == 1 else 0.0)
                pos[q] = (x, y)
        if len(pos) == len(ids):
            return pos
    # Fallback: lay qubits out on a square grid in id order.
    width = max(1, math.ceil(math.sqrt(len(ids))))
    return {q: (float(i % width), -float(i // width)) for i, q in enumerate(ids)}
