"""Historical calibration resources available without network or IBM credentials."""

from __future__ import annotations

import hashlib
import json
from importlib import resources
from pathlib import Path
from typing import Any

from .data import Snapshot, from_dict
from .errors import DataValidationError

BUILTIN_SNAPSHOTS = ("ibm_torino_2025-02-26.json",)


def builtin_bytes(name: str = BUILTIN_SNAPSHOTS[0]) -> bytes:
    if name not in BUILTIN_SNAPSHOTS:
        raise DataValidationError(f"Unknown bundled snapshot {name!r}")
    return resources.files("quantivex").joinpath("resources").joinpath(name).read_bytes()


def load_builtin(name: str = BUILTIN_SNAPSHOTS[0]) -> Snapshot:
    return from_dict(json.loads(builtin_bytes(name)))


def builtin_info(name: str = BUILTIN_SNAPSHOTS[0]) -> dict[str, Any]:
    s = load_builtin(name)
    return {
        "file": name,
        "num_qubits": s.num_qubits,
        "num_links": len(s.links),
        "calibration_timestamp": s.calibration_timestamp,
        "canonical_data_sha256": s.sha256,
        "file_sha256": hashlib.sha256(builtin_bytes(name)).hexdigest(),
        "provenance": s.provenance,
    }


def export_builtin(path: str | Path, name: str = BUILTIN_SNAPSHOTS[0]) -> Path:
    output = Path(path)
    output.write_bytes(builtin_bytes(name))
    return output
