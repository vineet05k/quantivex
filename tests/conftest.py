from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from quantivex.config import Settings
from quantivex.data import Snapshot, from_dict, load

ROOT = Path(__file__).resolve().parents[1]
TORINO = ROOT / "examples" / "ibm_torino_2025-02-26.json"


def line_chip(
    errors: list[float] | None = None, readout: float = 0.01, t1: float | None = 200.0, sx: float = 2e-4
) -> dict[str, Any]:
    """A 6-qubit line 0-1-2-3-4-5 plus a branch 2-6. errors = gate errors along the line."""
    errors = errors or [0.005, 0.006, 0.007, 0.008, 0.009]
    qubits = [{"id": i, "readout_error": readout, "sx_error": sx} for i in range(7)]
    if t1:
        for q in qubits:
            q.update(t1_us=t1, t2_us=t1 * 0.8)
    edges = [{"source": i, "target": i + 1, "gate_error": e} for i, e in enumerate(errors)]
    edges.append({"source": 2, "target": 6, "gate_error": 0.02})
    return {"schema_version": 2, "label": "test line chip", "provenance": "unit test", "qubits": qubits, "edges": edges}


@pytest.fixture
def chip() -> Snapshot:
    return from_dict(line_chip())


@pytest.fixture(scope="session")
def torino() -> Snapshot:
    return load(TORINO)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(model="fake-model", api_key="test", shots=2000, seed=7, output_dir=tmp_path / "runs")


# --- a scripted fake LLM ----------------------------------------------------------------------
def tool_call(name: str, args: dict[str, Any] | str, cid: str = "c1") -> SimpleNamespace:
    arguments = args if isinstance(args, str) else json.dumps(args)
    return SimpleNamespace(id=cid, function=SimpleNamespace(name=name, arguments=arguments))


def reply(content: str | None = None, calls: list[SimpleNamespace] | None = None) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))])


class FakeLLM:
    """Returns scripted responses in order; records every request (deep-copied messages)."""

    def __init__(self, script: list[SimpleNamespace]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> SimpleNamespace:
        snapshot = dict(kwargs)
        snapshot["messages"] = json.loads(json.dumps(kwargs["messages"], default=str))
        self.requests.append(snapshot)
        if not self.script:
            raise RuntimeError("FakeLLM script exhausted")
        return self.script.pop(0)
