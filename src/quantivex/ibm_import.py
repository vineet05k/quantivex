"""Export calibration snapshots from IBM backends (live, or the fake backends bundled with
qiskit-ibm-runtime, which carry real historical calibration data).

Requires the `ibm` extra: pip install "quantivex[ibm]".
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any

from .data import SCHEMA_VERSION, Snapshot, from_dict
from .errors import DataValidationError

TWO_QUBIT_GATES = ("cz", "ecr", "cx")


def _error(target: Any, name: str, qargs: tuple[int, ...]) -> float | None:
    try:
        props = target[name][qargs]
    except (KeyError, TypeError):
        return None
    if props is None or props.error is None or not 0.0 <= props.error <= 1.0:
        return None
    return float(props.error)


def _duration_ns(target: Any, name: str) -> float | None:
    if name not in target.operation_names:
        return None
    values = [p.duration for p in target[name].values() if p is not None and p.duration]
    return round(statistics.median(values) * 1e9, 3) if values else None


def snapshot_from_backend(backend: Any, label: str | None = None, provenance: str | None = None) -> Snapshot:
    """Build a validated Snapshot from a Qiskit BackendV2 (Target + properties())."""
    target = backend.target
    native = set(target.operation_names) & set(TWO_QUBIT_GATES)
    if native != {"cz"}:
        raise DataValidationError(
            "Snapshot simulator supports native CZ calibration only; use the IBM execution workflow for CX/ECR backends"
        )
    props = backend.properties() if hasattr(backend, "properties") else None
    calibrated = getattr(props, "last_update_date", None) if props is not None else None
    qubits = []
    for i in range(backend.num_qubits):
        readout = _error(target, "measure", (i,))
        if readout is None:
            continue  # unusable without readout calibration
        q: dict[str, Any] = {"id": i, "readout_error": readout}
        sx = _error(target, "sx", (i,))
        if sx is not None:
            q["sx_error"] = sx
        if props is not None:
            try:
                q["t1_us"] = round(float(props.t1(i)) * 1e6, 4)
                q["t2_us"] = round(float(props.t2(i)) * 1e6, 4)
            except Exception:  # noqa: BLE001 - property layout differs across providers
                pass
        qubits.append(q)
    valid = {q["id"] for q in qubits}
    edges: dict[tuple[int, int], float] = {}
    gate_used = None
    for name in TWO_QUBIT_GATES:
        if name not in target.operation_names:
            continue
        gate_used = gate_used or name
        for pair in target[name]:
            if pair is None or len(pair) != 2 or pair[0] not in valid or pair[1] not in valid:
                continue
            err = _error(target, name, pair)
            if err is None:
                continue
            key = (min(pair), max(pair))
            if key in edges and abs(edges[key] - err) > 1e-12:
                raise DataValidationError(
                    "Directional CZ calibrations differ; this symmetric snapshot format cannot represent them"
                )
            edges[key] = err
    if not qubits:
        raise DataValidationError(f"Backend {backend.name} reports no usable qubits")
    durations = {
        k: v
        for k, v in {
            "sx": _duration_ns(target, "sx"),
            "cz": _duration_ns(target, gate_used or "cz"),
            "measure": _duration_ns(target, "measure"),
        }.items()
        if v
    }
    raw = {
        "schema_version": SCHEMA_VERSION,
        "label": label or f"{backend.name.replace('fake_', 'ibm_')} calibration snapshot ({backend.num_qubits} qubits)",
        "provenance": provenance
        or (
            f"Exported from Qiskit backend '{backend.name}' Target/properties on "
            f"{datetime.now(timezone.utc).date().isoformat()}; two-qubit errors from '{gate_used}', "
            "undirected (minimum over directions)."
        ),
        "backend": backend.name,
        "calibration_timestamp": calibrated.isoformat() if calibrated else None,
        "durations_ns": durations,
        "qubits": qubits,
        "edges": [{"source": a, "target": b, "gate_error": e} for (a, b), e in sorted(edges.items())],
    }
    return from_dict(raw)


def fake_backend(name: str) -> Any:
    """Instantiate a qiskit-ibm-runtime fake backend, e.g. 'torino' -> FakeTorino()."""
    try:
        from qiskit_ibm_runtime import fake_provider
    except ImportError as exc:
        raise DataValidationError('Install the IBM extra: pip install "quantivex[ibm]"') from exc
    requested = name.strip().removeprefix("ibm_").removeprefix("fake_").removeprefix("Fake").lower()
    choices = {key.lower().removeprefix("fake"): key for key in dir(fake_provider) if key.startswith("Fake")}
    cls_name = choices.get(requested) or choices.get(requested + "v2")
    cls = getattr(fake_provider, cls_name, None) if cls_name else None
    if cls is None:
        raise DataValidationError(f"No fake backend called {name!r} in qiskit-ibm-runtime")
    return cls()


def live_backend(name: str) -> Any:
    from .ibm_execution import get_backend

    return get_backend(name)
