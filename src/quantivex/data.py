"""Calibration snapshots: schema, strict validation, loading and summaries.

Schema version 2 (version 1 files, with only readout and gate errors, still load)::

    {
      "schema_version": 2,
      "label": "...", "provenance": "...", "backend": "ibm_torino",
      "calibration_timestamp": "2025-02-26T15:26:08-05:00",
      "durations_ns": {"sx": 32, "cz": 68, "measure": 1560},
      "qubits": [{"id": 0, "readout_error": 0.02, "t1_us": 224.1, "t2_us": 314.6, "sx_error": 0.0003}],
      "edges":  [{"source": 0, "target": 1, "gate_error": 0.004}]
    }

All error rates are probabilities in [0, 1]. Edges are undirected.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .errors import DataValidationError

SCHEMA_VERSION = 2
DEFAULT_DURATIONS_NS: dict[str, float] = {"sx": 32.0, "cz": 68.0, "measure": 1560.0}
DEAD_LINK_THRESHOLD = 0.5


@dataclass(frozen=True)
class Qubit:
    id: int
    readout_error: float
    t1_us: float | None = None
    t2_us: float | None = None
    sx_error: float | None = None


@dataclass(frozen=True)
class Link:
    source: int
    target: int
    gate_error: float

    @property
    def pair(self) -> tuple[int, int]:
        return (min(self.source, self.target), max(self.source, self.target))


@dataclass(frozen=True, eq=False)  # identity hash: usable as a cache key
class Snapshot:
    """An immutable, validated calibration snapshot of one quantum processor."""

    label: str
    provenance: str
    qubits: tuple[Qubit, ...]
    links: tuple[Link, ...]
    backend: str | None = None
    calibration_timestamp: str | None = None
    durations_ns: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_DURATIONS_NS))
    sha256: str = ""

    def __post_init__(self) -> None:
        for name in ("backend", "calibration_timestamp"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise DataValidationError(f"{name} must be a string or null")
        object.__setattr__(self, "durations_ns", MappingProxyType(dict(self.durations_ns)))
        object.__setattr__(self, "qubits", tuple(self.qubits))
        object.__setattr__(self, "links", tuple(self.links))
        if not self.qubits or len(self.qubits) > 512 or max(q.id for q in self.qubits) > 511:
            raise DataValidationError("Snapshot supports 1..512 qubits with IDs 0..511")
        content = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        object.__setattr__(self, "sha256", hashlib.sha256(content).hexdigest())

    @cached_property
    def qubit_map(self) -> Mapping[int, Qubit]:
        return MappingProxyType({q.id: q for q in self.qubits})

    @cached_property
    def link_map(self) -> Mapping[tuple[int, int], Link]:
        return MappingProxyType({link.pair: link for link in self.links})

    @cached_property
    def adjacency(self) -> Mapping[int, frozenset[int]]:
        adj: dict[int, set[int]] = {q.id: set() for q in self.qubits}
        for link in self.links:
            adj[link.source].add(link.target)
            adj[link.target].add(link.source)
        return MappingProxyType({k: frozenset(v) for k, v in adj.items()})

    @property
    def num_qubits(self) -> int:
        return len(self.qubits)

    @property
    def max_qubit_id(self) -> int:
        return max(q.id for q in self.qubits)

    def link(self, a: int, b: int) -> Link | None:
        return self.link_map.get((min(a, b), max(a, b)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "native_two_qubit_gate": "cz",
            "label": self.label,
            "provenance": self.provenance,
            "backend": self.backend,
            "calibration_timestamp": self.calibration_timestamp,
            "durations_ns": dict(self.durations_ns),
            "qubits": [{k: v for k, v in vars(q).items() if v is not None} for q in self.qubits],
            "edges": [vars(link) for link in self.links],
        }


def _probability(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise DataValidationError(f"{where} must be a finite number, got {value!r}")
    if not 0.0 <= value <= 1.0:
        raise DataValidationError(f"{where} must be in [0, 1], got {value}")
    return float(value)


def _positive_or_none(value: Any, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise DataValidationError(f"{where} must be a finite number, got {value!r}")
    if value <= 0:
        raise DataValidationError(f"{where} must be positive, got {value}")
    return float(value)


def _qubit_id(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DataValidationError(f"{where} must be a non-negative integer, got {value!r}")
    if value > 511:
        raise DataValidationError(f"{where} must be at most 511")
    return value


def from_dict(raw: Any) -> Snapshot:
    """Validate a parsed JSON object and return an immutable Snapshot."""
    if not isinstance(raw, dict):
        raise DataValidationError("Snapshot must be a JSON object")
    if raw.get("native_two_qubit_gate", "cz") != "cz":
        raise DataValidationError("Only symmetric CZ calibration snapshots are supported")
    version = raw.get("schema_version", 1)
    if version not in (1, 2):
        raise DataValidationError(f"Unsupported schema_version {version!r}")
    if not isinstance(raw.get("qubits"), list) or not raw["qubits"]:
        raise DataValidationError("'qubits' must be a non-empty list")
    if not isinstance(raw.get("edges"), list):
        raise DataValidationError("'edges' must be a list")

    if len(raw["qubits"]) > 512 or len(raw["edges"]) > 8192:
        raise DataValidationError("Snapshot exceeds the 512-qubit/8192-edge workload limit")
    qubits: list[Qubit] = []
    seen_ids: set[int] = set()
    for i, q in enumerate(raw["qubits"]):
        if not isinstance(q, dict):
            raise DataValidationError(f"qubits[{i}] must be an object")
        qid = _qubit_id(q.get("id"), f"qubits[{i}].id")
        if qid in seen_ids:
            raise DataValidationError(f"Duplicate qubit id {qid}")
        seen_ids.add(qid)
        if "readout_error" not in q:
            raise DataValidationError(f"qubits[{i}] is missing readout_error")
        t1 = _positive_or_none(q.get("t1_us"), f"qubit {qid} t1_us")
        t2 = _positive_or_none(q.get("t2_us"), f"qubit {qid} t2_us")
        sx = q.get("sx_error")
        qubits.append(
            Qubit(
                id=qid,
                readout_error=_probability(q["readout_error"], f"qubit {qid} readout_error"),
                t1_us=t1,
                t2_us=t2,
                sx_error=None if sx is None else _probability(sx, f"qubit {qid} sx_error"),
            )
        )

    links: list[Link] = []
    seen_pairs: set[tuple[int, int]] = set()
    for i, e in enumerate(raw["edges"]):
        if not isinstance(e, dict):
            raise DataValidationError(f"edges[{i}] must be an object")
        a = _qubit_id(e.get("source"), f"edges[{i}].source")
        b = _qubit_id(e.get("target"), f"edges[{i}].target")
        if a not in seen_ids or b not in seen_ids:
            raise DataValidationError(f"edges[{i}] references an unknown qubit ({a}, {b})")
        if a == b:
            raise DataValidationError(f"edges[{i}] is a self-loop on qubit {a}")
        pair = (min(a, b), max(a, b))
        if pair in seen_pairs:
            raise DataValidationError(f"Duplicate undirected edge {pair}")
        seen_pairs.add(pair)
        if "gate_error" not in e:
            raise DataValidationError(f"edges[{i}] is missing gate_error")
        links.append(Link(a, b, _probability(e["gate_error"], f"edge {pair} gate_error")))

    durations = dict(DEFAULT_DURATIONS_NS)
    raw_durations = raw.get("durations_ns", {})
    if not isinstance(raw_durations, dict):
        raise DataValidationError("durations_ns must be an object")
    for key, value in raw_durations.items():
        dur = _positive_or_none(value, f"durations_ns.{key}")
        if dur is not None:
            durations[str(key)] = dur

    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    return Snapshot(
        label=str(raw.get("label") or "unlabeled"),
        provenance=str(raw.get("provenance") or "unspecified"),
        qubits=tuple(sorted(qubits, key=lambda q: q.id)),
        links=tuple(sorted(links, key=lambda link: link.pair)),
        backend=raw.get("backend"),
        calibration_timestamp=raw.get("calibration_timestamp") or raw.get("timestamp_utc"),
        durations_ns=durations,
        sha256=hashlib.sha256(canonical).hexdigest(),
    )


def load(path: str | Path) -> Snapshot:
    """Load and validate a snapshot JSON file."""
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DataValidationError(f"File not found: {p}") from exc
    except json.JSONDecodeError as exc:
        raise DataValidationError(f"{p} is not valid JSON: {exc}") from exc
    return from_dict(raw)


def save(snapshot: Snapshot, path: str | Path) -> Path:
    p = Path(path)
    p.write_text(json.dumps(snapshot.to_dict(), indent=2), encoding="utf-8")
    return p


def summary(snapshot: Snapshot, worst: int = 5) -> dict[str, Any]:
    """Compact, LLM-friendly facts about a snapshot."""
    readout = [q.readout_error for q in snapshot.qubits]
    gates = [link.gate_error for link in snapshot.links]
    t1 = [q.t1_us for q in snapshot.qubits if q.t1_us is not None]
    t2 = [q.t2_us for q in snapshot.qubits if q.t2_us is not None]

    def med(values: list[float]) -> float | None:
        return round(statistics.median(values), 6) if values else None

    return {
        "label": snapshot.label,
        "provenance": snapshot.provenance,
        "backend": snapshot.backend,
        "calibration_timestamp": snapshot.calibration_timestamp,
        "data_sha256": snapshot.sha256,
        "num_qubits": snapshot.num_qubits,
        "num_links": len(snapshot.links),
        "median_readout_error": med(readout),
        "median_two_qubit_error": med(gates),
        "median_t1_us": med(t1),
        "median_t2_us": med(t2),
        "has_coherence_data": bool(t1),
        "dead_links": sum(1 for g in gates if g >= DEAD_LINK_THRESHOLD),
        "worst_qubits_by_readout": [
            {"id": q.id, "readout_error": q.readout_error}
            for q in sorted(snapshot.qubits, key=lambda q: -q.readout_error)[:worst]
        ],
        "worst_links": [
            {"pair": list(link.pair), "gate_error": link.gate_error}
            for link in sorted(snapshot.links, key=lambda link: -link.gate_error)[:worst]
        ],
    }
