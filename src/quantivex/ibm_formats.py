"""Read the calibration files IBM produces, without an IBM account or network access.

Two IBM formats are converted into a validated :class:`~quantivex.data.Snapshot`:

* the **calibration CSV** downloaded from IBM Quantum Platform (Compute resources -> QPU ->
  Calibration data -> download): one row per qubit, two-qubit errors encoded per row as
  ``"<target>:<error>;..."`` or ``"<control>_<target>:<error>;..."``;
* the **BackendProperties JSON** returned by ``backend.properties().to_dict()`` (also stored by
  Quantivex in every hardware plan under ``calibration_at_preparation.backend_properties``).

Only processors whose native two-qubit gate is CZ (IBM Heron) are accepted, as everywhere else in
Quantivex. Header matching is tolerant of spacing, case, units and IBM's occasional trailing spaces,
because the column names of the CSV export have changed between platform versions. Every
conversion records what it did (dropped qubits, merged directions, defaults used) in ``notes``.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import statistics
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .data import DEFAULT_DURATIONS_NS, SCHEMA_VERSION, Snapshot, from_dict
from .errors import DataValidationError

__all__ = [
    "Conversion",
    "detect_kind",
    "snapshot_from_csv",
    "snapshot_from_csv_text",
    "snapshot_from_properties",
]

MAX_CSV_BYTES = 20 * 1024 * 1024
_PAIR = re.compile(r"(?:(\d+)\s*[_\-]\s*(\d+)|(\d+))\s*:\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")
_TS_FILE = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[T_ ](\d{2})[:_\-]?(\d{2})(?:[:_\-]?(\d{2}))?)?")
_BACKEND_FILE = re.compile(r"(ibm_[a-z0-9]+)", re.IGNORECASE)


@dataclass
class Conversion:
    """A converted snapshot plus a human-readable account of the conversion."""

    snapshot: Snapshot
    source_format: str
    notes: list[str] = field(default_factory=list)

    def describe(self) -> dict[str, Any]:
        return {
            "source_format": self.source_format,
            "backend": self.snapshot.backend,
            "calibration_timestamp": self.snapshot.calibration_timestamp,
            "qubits": self.snapshot.num_qubits,
            "links": len(self.snapshot.links),
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------------------------- detection
def detect_kind(path: str | Path) -> str:
    """Classify an input path.

    Returns one of ``hardware_run`` (a folder with ``plan.json``), ``directory``, ``ibm_csv``,
    ``ibm_properties_json``, ``quantivex_snapshot`` or ``unknown``. Files are only sniffed, never executed.
    """
    p = Path(path)
    if p.is_dir():
        return "hardware_run" if (p / "plan.json").is_file() else "directory"
    suffix = p.suffix.lower()
    if suffix == ".csv":
        try:
            with p.open(encoding="utf-8-sig", newline="") as stream:
                header = next(csv.reader(stream), [])
        except (OSError, UnicodeDecodeError):
            return "unknown"
        keys = {_norm(h) for h in header}
        return "ibm_csv" if "qubit" in keys and any(k.startswith("t1") for k in keys) else "unknown"
    if suffix == ".json":
        try:
            if p.stat().st_size > MAX_CSV_BYTES * 5:
                return "unknown"
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return "unknown"
        if isinstance(raw, dict):
            if isinstance(raw.get("qubits"), list) and isinstance(raw.get("edges"), list):
                return "quantivex_snapshot"
            if isinstance(raw.get("gates"), list) and isinstance(raw.get("qubits"), list):
                return "ibm_properties_json"
            nested = raw.get("backend_properties")
            if isinstance(nested, dict) and isinstance(nested.get("gates"), list):
                return "ibm_properties_json"
    return "unknown"


# --------------------------------------------------------------------------------------------- helpers
def _norm(header: str) -> str:
    return re.sub(r"\s+", " ", str(header).replace("﻿", "").strip().lower())


def _float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        x = float(value)
    else:
        text = str(value).strip()
        if not text or text.lower() in ("nan", "none", "null", "-", "n/a", "na"):
            return None
        try:
            x = float(text)
        except ValueError:
            return None
    return x if math.isfinite(x) else None


def _unit_scale_to_us(header: str) -> float:
    h = _norm(header)
    if "(ns)" in h:
        return 1e-3
    if "(ms)" in h:
        return 1e3
    if "(s)" in h:
        return 1e6
    return 1.0


def _unit_scale_to_ns(header: str) -> float:
    h = _norm(header)
    if "(us)" in h or "(µs)" in h or "(μs)" in h:
        return 1e3
    if "(s)" in h:
        return 1e9
    return 1.0


def _truthy(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip().lower()
    if not text:
        return True
    return text not in ("no", "false", "0", "n", "f", "inactive", "faulty")


def _finalize(
    *,
    label: str,
    provenance: str,
    backend: str | None,
    timestamp: str | None,
    qubits: dict[int, dict[str, Any]],
    pair_errors: dict[tuple[int, int], list[float]],
    durations: dict[str, list[float]],
    notes: list[str],
    source_format: str,
) -> Conversion:
    if not qubits:
        raise DataValidationError("No usable qubits were found in the calibration file")
    edges = []
    asym = 0
    for (a, b), values in sorted(pair_errors.items()):
        if a not in qubits or b not in qubits:
            continue
        if len(values) > 1 and max(values) - min(values) > 1e-12:
            asym += 1
        edges.append({"source": a, "target": b, "gate_error": min(1.0, max(0.0, max(values)))})
    if asym:
        notes.append(f"{asym} links had different errors per direction; the larger (conservative) value was used")
    if not edges:
        raise DataValidationError("No two-qubit (CZ) links were found in the calibration file")
    dur = dict(DEFAULT_DURATIONS_NS)
    for key, values in durations.items():
        good = [v for v in values if v and v > 0]
        if good:
            dur[key] = round(statistics.median(good), 3)
        else:
            notes.append(f"No {key} duration in the file; default {DEFAULT_DURATIONS_NS[key]} ns used")
    raw = {
        "schema_version": SCHEMA_VERSION,
        "native_two_qubit_gate": "cz",
        "label": label,
        "provenance": provenance,
        "backend": backend,
        "calibration_timestamp": timestamp,
        "durations_ns": dur,
        "qubits": [qubits[k] for k in sorted(qubits)],
        "edges": edges,
    }
    return Conversion(from_dict(raw), source_format, notes)


# --------------------------------------------------------------------------------------------- CSV
_Matcher = Callable[[str], bool]
_COLUMNS: dict[str, _Matcher] = {
    "qubit": lambda h: h == "qubit" or h.startswith("qubit "),
    "t1": lambda h: h.startswith("t1"),
    "t2": lambda h: h.startswith("t2"),
    "readout": lambda h: h.startswith("readout assignment error") or h.startswith("readout error"),
    "p01": lambda h: h.startswith("prob meas0 prep1") or h == "prob_meas0_prep1",
    "p10": lambda h: h.startswith("prob meas1 prep0") or h == "prob_meas1_prep0",
    "readout_length": lambda h: h.startswith("readout length"),
    "sx": lambda h: "(sx)" in h or h.startswith("sx error") or h.startswith("√x"),
    "sq_length": lambda h: h.startswith("single-qubit gate length") or h.startswith("single qubit gate length"),
    "cz": lambda h: h.startswith("cz error"),
    "ecr": lambda h: h.startswith("ecr error"),
    "cnot": lambda h: h.startswith("cnot error") or h.startswith("cx error"),
    "tq_length": lambda h: h.startswith("gate length") or h.startswith("gate time") or h.startswith("cz gate length"),
    "operational": lambda h: h.startswith("operational"),
}


def _map_columns(header: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for column in header:
        h = _norm(column)
        for key, match in _COLUMNS.items():
            if key not in mapping and match(h):
                mapping[key] = column
                break
    return mapping


def _pairs(cell: Any, row_qubit: int) -> list[tuple[int, int, float]]:
    out = []
    for m in _PAIR.finditer(str(cell or "")):
        if m.group(3) is not None:
            a, b = row_qubit, int(m.group(3))
        else:
            a, b = int(m.group(1)), int(m.group(2))
        value = _float(m.group(4))
        if value is not None and a != b:
            out.append((a, b, value))
    return out


def _from_filename(name: str) -> tuple[str | None, str | None]:
    backend = None
    m = _BACKEND_FILE.search(name)
    if m:
        backend = m.group(1).lower()
    ts = None
    t = _TS_FILE.search(name)
    if t:
        date, hh, mm, ss = t.groups()
        ts = date if hh is None else f"{date}T{hh}:{mm}:{ss or '00'}Z"
    return backend, ts


def snapshot_from_csv_text(
    text: str,
    *,
    name: str = "calibration.csv",
    backend: str | None = None,
    timestamp: str | None = None,
) -> Conversion:
    """Convert the text of an IBM Quantum Platform calibration CSV."""
    if len(text.encode("utf-8")) > MAX_CSV_BYTES:
        raise DataValidationError("Calibration CSV is larger than 20 MB")
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    header = list(reader.fieldnames or [])
    cols = _map_columns(header)
    if "qubit" not in cols:
        raise DataValidationError(f"{name}: no 'Qubit' column; this is not an IBM calibration CSV")
    if "cz" not in cols:
        if "ecr" in cols or "cnot" in cols:
            raise DataValidationError(
                f"{name}: this processor reports ECR/CNOT errors, not CZ. Quantivex currently supports "
                "CZ-native (IBM Heron) processors only."
            )
        raise DataValidationError(f"{name}: no 'CZ error' column was found")
    if "readout" not in cols and not ("p01" in cols and "p10" in cols):
        raise DataValidationError(f"{name}: no readout-error column was found")

    file_backend, file_ts = _from_filename(Path(name).name)
    backend = backend or file_backend
    timestamp = timestamp or file_ts
    notes: list[str] = []
    if backend is None:
        notes.append("Backend name not found in the file name; pass backend= to record it")
    if timestamp is None:
        notes.append("Calibration time not found in the file name; pass timestamp= to record it")
    unmapped = [h for h in header if h not in cols.values() and _norm(h)]
    if unmapped:
        notes.append("Columns not used: " + ", ".join(sorted(_norm(h) for h in unmapped)))

    qubits: dict[int, dict[str, Any]] = {}
    pair_errors: dict[tuple[int, int], list[float]] = {}
    durations: dict[str, list[float]] = {"sx": [], "cz": [], "measure": []}
    dropped: list[int] = []
    missing_readout: list[int] = []
    clipped = 0
    for line, row in enumerate(reader, start=2):
        qid_value = _float(row.get(cols["qubit"]))
        if qid_value is None or qid_value < 0 or qid_value != int(qid_value):
            raise DataValidationError(f"{name}, line {line}: invalid qubit number {row.get(cols['qubit'])!r}")
        qid = int(qid_value)
        if qid in qubits or qid in dropped:
            raise DataValidationError(f"{name}, line {line}: qubit {qid} appears twice")
        if "operational" in cols and not _truthy(row.get(cols["operational"])):
            dropped.append(qid)
            continue
        readout = _float(row.get(cols["readout"])) if "readout" in cols else None
        if readout is None and "p01" in cols and "p10" in cols:
            p01, p10 = _float(row.get(cols["p01"])), _float(row.get(cols["p10"]))
            if p01 is not None and p10 is not None:
                readout = 0.5 * (p01 + p10)
        if readout is None:
            missing_readout.append(qid)
            continue
        if not 0.0 <= readout <= 1.0:
            raise DataValidationError(f"{name}, line {line}: readout error {readout} is not a probability")
        entry: dict[str, Any] = {"id": qid, "readout_error": readout}
        for key, field_name in (("t1", "t1_us"), ("t2", "t2_us")):
            if key in cols:
                value = _float(row.get(cols[key]))
                if value is not None and value > 0:
                    entry[field_name] = value * _unit_scale_to_us(cols[key])
        if "sx" in cols:
            sx = _float(row.get(cols["sx"]))
            if sx is not None:
                if sx > 1.0 or sx < 0.0:
                    clipped += 1
                    sx = min(1.0, max(0.0, sx))
                entry["sx_error"] = sx
        qubits[qid] = entry
        if "sq_length" in cols:
            v = _float(row.get(cols["sq_length"]))
            if v:
                durations["sx"].append(v * _unit_scale_to_ns(cols["sq_length"]))
        if "readout_length" in cols:
            v = _float(row.get(cols["readout_length"]))
            if v:
                durations["measure"].append(v * _unit_scale_to_ns(cols["readout_length"]))
        for a, b, err in _pairs(row.get(cols["cz"]), qid):
            pair_errors.setdefault((min(a, b), max(a, b)), []).append(err)
        if "tq_length" in cols:
            scale = _unit_scale_to_ns(cols["tq_length"])
            cell = row.get(cols["tq_length"])
            single = _float(cell)
            values = [v for _, _, v in _pairs(cell, qid)] or ([single] if single else [])
            durations["cz"].extend(v * scale for v in values if v)
    if dropped:
        notes.append(f"{len(dropped)} non-operational qubit(s) excluded: {sorted(dropped)}")
    if missing_readout:
        notes.append(f"{len(missing_readout)} qubit(s) without a readout error excluded: {sorted(missing_readout)}")
    if clipped:
        notes.append(f"{clipped} single-qubit error value(s) outside [0, 1] were clipped")
    out_of_range = [p for p, v in pair_errors.items() if any(x < 0 or x > 1 for x in v)]
    if out_of_range:
        notes.append(f"{len(out_of_range)} two-qubit error value(s) outside [0, 1] were clipped")
    stem = Path(name).stem
    return _finalize(
        label=stem,
        provenance=f"IBM Quantum Platform calibration CSV ({Path(name).name})",
        backend=backend,
        timestamp=timestamp,
        qubits=qubits,
        pair_errors=pair_errors,
        durations=durations,
        notes=notes,
        source_format="ibm_csv",
    )


def snapshot_from_csv(path: str | Path, *, backend: str | None = None, timestamp: str | None = None) -> Conversion:
    """Convert an IBM Quantum Platform calibration CSV file."""
    p = Path(path)
    try:
        if p.stat().st_size > MAX_CSV_BYTES:
            raise DataValidationError(f"{p.name} is larger than 20 MB")
        text = p.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise DataValidationError(f"File not found: {p}") from exc
    except UnicodeDecodeError as exc:
        raise DataValidationError(f"{p.name} is not UTF-8 text") from exc
    return snapshot_from_csv_text(text, name=p.name, backend=backend, timestamp=timestamp)


# --------------------------------------------------------------------------------------------- properties
def _params(entries: Iterable[Mapping[str, Any]]) -> dict[str, tuple[float | None, str]]:
    out: dict[str, tuple[float | None, str]] = {}
    for item in entries or []:
        if isinstance(item, Mapping) and "name" in item:
            out[str(item["name"])] = (_float(item.get("value")), str(item.get("unit") or ""))
    return out


def snapshot_from_properties(
    properties: Mapping[str, Any], *, label: str | None = None, provenance: str | None = None
) -> Conversion:
    """Convert an IBM ``BackendProperties`` dictionary (``backend.properties().to_dict()``)."""
    if "backend_properties" in properties and isinstance(properties["backend_properties"], Mapping):
        properties = properties["backend_properties"]
    gates = properties.get("gates")
    qlist = properties.get("qubits")
    if not isinstance(gates, list) or not isinstance(qlist, list):
        raise DataValidationError("Not an IBM BackendProperties object (needs 'gates' and 'qubits' lists)")
    names = {str(g.get("gate")) for g in gates if isinstance(g, Mapping)}
    if "cz" not in names:
        raise DataValidationError(
            "These properties contain no CZ gates. Quantivex currently supports CZ-native (IBM Heron) processors only."
        )
    notes: list[str] = []
    sx: dict[int, float] = {}
    durations: dict[str, list[float]] = {"sx": [], "cz": [], "measure": []}
    pair_errors: dict[tuple[int, int], list[float]] = {}
    for g in gates:
        if not isinstance(g, Mapping):
            continue
        kind, qs = g.get("gate"), g.get("qubits") or []
        p = _params(g.get("parameters", []))
        err = p.get("gate_error", (None, ""))[0]
        length, unit = p.get("gate_length", (None, ""))
        scale = 1e9 if unit == "s" else 1e3 if unit in ("us", "µs") else 1.0
        if kind == "sx" and len(qs) == 1:
            if err is not None:
                sx[int(qs[0])] = min(1.0, max(0.0, err))
            if length:
                durations["sx"].append(length * scale)
        elif kind == "cz" and len(qs) == 2:
            a, b = sorted(int(q) for q in qs)
            if a != b and err is not None:
                pair_errors.setdefault((a, b), []).append(err)
            if length:
                durations["cz"].append(length * scale)
    qubits: dict[int, dict[str, Any]] = {}
    missing: list[int] = []
    for qid, entries in enumerate(qlist):
        p = _params(entries)
        if "operational" in p and p["operational"][0] == 0.0:
            missing.append(qid)
            continue
        readout = p.get("readout_error", (None, ""))[0]
        if readout is None:
            p01, p10 = p.get("prob_meas0_prep1", (None, ""))[0], p.get("prob_meas1_prep0", (None, ""))[0]
            readout = None if p01 is None or p10 is None else 0.5 * (p01 + p10)
        if readout is None:
            missing.append(qid)
            continue
        entry: dict[str, Any] = {"id": qid, "readout_error": min(1.0, max(0.0, readout))}
        for key, field_name in (("T1", "t1_us"), ("T2", "t2_us")):
            value, unit = p.get(key, (None, "us"))
            if value is not None and value > 0:
                entry[field_name] = value * (1e6 if unit == "s" else 1e-3 if unit == "ns" else 1.0)
        if qid in sx:
            entry["sx_error"] = sx[qid]
        qubits[qid] = entry
        rl, unit = p.get("readout_length", (None, "ns"))
        if rl:
            durations["measure"].append(rl * (1e9 if unit == "s" else 1e3 if unit in ("us", "µs") else 1.0))
    if missing:
        notes.append(f"{len(missing)} qubit(s) non-operational or without readout error excluded: {missing[:20]}")
    backend = properties.get("backend_name")
    timestamp = properties.get("last_update_date")
    return _finalize(
        label=label or str(backend or "ibm_properties"),
        provenance=provenance or "IBM BackendProperties (backend.properties())",
        backend=str(backend) if backend else None,
        timestamp=str(timestamp) if timestamp else None,
        qubits=qubits,
        pair_errors=pair_errors,
        durations=durations,
        notes=notes,
        source_format="ibm_properties_json",
    )
