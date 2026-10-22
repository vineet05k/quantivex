import json

import pytest

from quantivex.data import from_dict, load, save, summary
from quantivex.errors import DataValidationError

from .conftest import line_chip


def test_valid_snapshot(chip):
    assert chip.num_qubits == 7
    assert len(chip.links) == 6
    assert chip.link(1, 0) is chip.link(0, 1)
    assert chip.adjacency[2] == {1, 3, 6}
    assert len(chip.sha256) == 64


def test_v1_schema_still_loads():
    raw = {
        "qubits": [{"id": 0, "readout_error": 0.1}, {"id": 1, "readout_error": 0.2}],
        "edges": [{"source": 0, "target": 1, "gate_error": 0.01}],
    }
    snap = from_dict(raw)
    assert snap.qubits[0].t1_us is None
    assert snap.durations_ns["cz"] > 0


def test_hash_is_deterministic_and_content_sensitive():
    a, b = from_dict(line_chip()), from_dict(line_chip())
    c = from_dict(line_chip(readout=0.02))
    assert a.sha256 == b.sha256 != c.sha256


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda r: r.update(qubits=[]), "non-empty"),
        (lambda r: r["qubits"].append(dict(r["qubits"][0])), "Duplicate qubit"),
        (lambda r: r["qubits"][0].update(id=True), "non-negative integer"),
        (lambda r: r["qubits"][0].update(id=-1), "non-negative integer"),
        (lambda r: r["qubits"][0].update(readout_error=1.5), r"\[0, 1\]"),
        (lambda r: r["qubits"][0].update(readout_error=float("nan")), "finite"),
        (lambda r: r["qubits"][0].update(t1_us=0), "positive"),
        (lambda r: r["qubits"][0].pop("readout_error"), "missing readout_error"),
        (lambda r: r["edges"].append({"source": 0, "target": 99, "gate_error": 0.1}), "unknown qubit"),
        (lambda r: r["edges"].append({"source": 3, "target": 3, "gate_error": 0.1}), "self-loop"),
        (lambda r: r["edges"].append({"source": 1, "target": 0, "gate_error": 0.1}), "Duplicate undirected"),
        (lambda r: r.update(schema_version=9), "schema_version"),
    ],
)
def test_validation_errors(mutate, message):
    raw = line_chip()
    mutate(raw)
    with pytest.raises(DataValidationError, match=message):
        from_dict(raw)


def test_roundtrip(tmp_path, chip):
    path = save(chip, tmp_path / "chip.json")
    again = load(path)
    assert [q.readout_error for q in again.qubits] == [q.readout_error for q in chip.qubits]
    assert json.loads(path.read_text())["schema_version"] == 2


def test_load_errors(tmp_path):
    with pytest.raises(DataValidationError, match="not found"):
        load(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(DataValidationError, match="not valid JSON"):
        load(bad)


def test_summary(torino):
    s = summary(torino)
    assert s["num_qubits"] == 133
    assert s["num_links"] == 150
    assert s["dead_links"] == 11
    assert s["has_coherence_data"]
    assert s["calibration_timestamp"].startswith("2025-02-26")
    assert s["worst_qubits_by_readout"][0]["id"] == 86
