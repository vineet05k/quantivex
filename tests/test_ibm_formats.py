"""IBM calibration CSV and BackendProperties conversion, and input-type detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quantivex.data import load
from quantivex.errors import DataValidationError
from quantivex.ibm_formats import detect_kind, snapshot_from_csv, snapshot_from_csv_text, snapshot_from_properties

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CSV = ROOT / "examples" / "ibm_kingston_calibrations_2026-10-20T06_23_28Z.csv"
EXAMPLE_RUN = ROOT / "examples" / "hardware_runs" / "ibm_kingston_h2_075_run03"

HEADER = (
    "Qubit,T1 (us),T2 (us),Frequency (GHz),Readout assignment error ,Prob meas0 prep1 ,Prob meas1 prep0 ,"
    "Readout length (ns),Single-qubit gate length (ns),√x (sx) error ,CZ error ,Gate length (ns),Operational\n"
)


def _csv(rows: list[str], header: str = HEADER) -> str:
    return header + "".join(r + "\n" for r in rows)


def test_csv_pair_encoded_entries() -> None:
    text = _csv(
        [
            '0,200,150,4.9,0.01,0.012,0.008,1560,32,0.0002,"0_1:0.003",68,Yes',
            '1,180,120,4.8,0.02,0.025,0.015,1560,32,0.0003,"1_0:0.003;1_2:0.004","1_0:68;1_2:68",Yes',
            '2,190,100,4.7,0.015,0.02,0.01,1560,32,0.00025,"2_1:0.004",68,Yes',
        ]
    )
    conv = snapshot_from_csv_text(text, name="ibm_fez_calibrations_2026-10-20T08_15_33Z.csv")
    snap = conv.snapshot
    assert snap.backend == "ibm_fez"
    assert snap.calibration_timestamp == "2026-10-20T08:15:33Z"
    assert [q.id for q in snap.qubits] == [0, 1, 2]
    assert snap.link(0, 1).gate_error == pytest.approx(0.003)
    assert snap.link(1, 2).gate_error == pytest.approx(0.004)
    assert snap.qubit_map[1].t1_us == pytest.approx(180)
    assert snap.qubit_map[2].sx_error == pytest.approx(0.00025)
    assert snap.durations_ns["cz"] == pytest.approx(68)
    assert snap.durations_ns["measure"] == pytest.approx(1560)
    assert conv.source_format == "ibm_csv"


def test_csv_target_only_entries_and_conservative_direction() -> None:
    text = _csv(
        [
            '0,200,150,4.9,0.01,0.012,0.008,1560,32,0.0002,"1:0.003",68,Yes',
            '1,180,120,4.8,0.02,0.025,0.015,1560,32,0.0003,"0:0.005;2:0.004",68,Yes',
            '2,190,100,4.7,0.015,0.02,0.01,1560,32,0.00025,"1:0.004",68,Yes',
        ]
    )
    conv = snapshot_from_csv_text(text, name="cal.csv", backend="ibm_test", timestamp="2026-01-01T00:00:00Z")
    assert conv.snapshot.link(0, 1).gate_error == pytest.approx(0.005)  # larger of the two directions
    assert any("conservative" in n for n in conv.notes)
    assert conv.snapshot.backend == "ibm_test"


def test_csv_non_operational_and_missing_readout() -> None:
    text = _csv(
        [
            '0,200,150,4.9,,0.02,0.01,1560,32,0.0002,"0_1:0.003",68,Yes',
            '1,180,120,4.8,0.02,0.025,0.015,1560,32,0.0003,"1_0:0.003;1_2:0.004",68,Yes',
            '2,190,100,4.7,0.015,0.02,0.01,1560,32,0.00025,"2_1:0.004",68,No',
        ]
    )
    conv = snapshot_from_csv_text(text, name="cal.csv")
    snap = conv.snapshot
    assert [q.id for q in snap.qubits] == [0, 1]
    assert snap.qubit_map[0].readout_error == pytest.approx(0.015)  # mean of the two assignment probabilities
    assert snap.link(1, 2) is None
    assert any("non-operational" in n for n in conv.notes)


def test_csv_units_converted() -> None:
    header = HEADER.replace("T1 (us)", "T1 (ns)")
    text = _csv(
        [
            '0,200000,150,4.9,0.01,0.012,0.008,1560,32,0.0002,"0_1:0.003",68,Yes',
            '1,180000,120,4.8,0.02,0.025,0.015,1560,32,0.0003,"1_0:0.003",68,Yes',
        ],
        header,
    )
    assert snapshot_from_csv_text(text).snapshot.qubit_map[0].t1_us == pytest.approx(200)


@pytest.mark.parametrize(
    ("header", "message"),
    [
        ("Name,T1 (us),CZ error\n", "no 'Qubit' column"),
        ("Qubit,T1 (us),Readout assignment error,ECR error\n", "CZ-native"),
        ("Qubit,T1 (us),Readout assignment error\n", "no 'CZ error'"),
        ("Qubit,T1 (us),CZ error\n", "readout"),
    ],
)
def test_csv_rejects_unsupported_layouts(header: str, message: str) -> None:
    with pytest.raises(DataValidationError, match=message):
        snapshot_from_csv_text(header + "0,100,x\n")


def test_csv_rejects_bad_values() -> None:
    with pytest.raises(DataValidationError, match="invalid qubit"):
        snapshot_from_csv_text(_csv(['a,200,150,4.9,0.01,0.012,0.008,1560,32,0.0002,"0_1:0.003",68,Yes']))
    with pytest.raises(DataValidationError, match="twice"):
        snapshot_from_csv_text(
            _csv(
                [
                    '0,200,150,4.9,0.01,0.012,0.008,1560,32,0.0002,"0_1:0.003",68,Yes',
                    '0,200,150,4.9,0.01,0.012,0.008,1560,32,0.0002,"0_1:0.003",68,Yes',
                ]
            )
        )
    with pytest.raises(DataValidationError, match="not a probability"):
        snapshot_from_csv_text(_csv(['0,200,150,4.9,1.5,0.012,0.008,1560,32,0.0002,"0_1:0.003",68,Yes']))


def _properties() -> dict:
    def q(t1: float, ro: float) -> list[dict]:
        return [
            {"name": "T1", "unit": "us", "value": t1},
            {"name": "T2", "unit": "us", "value": t1 / 2},
            {"name": "readout_error", "unit": "", "value": ro},
            {"name": "readout_length", "unit": "ns", "value": 1560},
        ]

    def gate(kind: str, qubits: list[int], err: float, length: float) -> dict:
        return {
            "gate": kind,
            "qubits": qubits,
            "parameters": [
                {"name": "gate_error", "unit": "", "value": err},
                {"name": "gate_length", "unit": "ns", "value": length},
            ],
        }

    return {
        "backend_name": "ibm_example",
        "last_update_date": "2026-10-20T08:00:00+00:00",
        "qubits": [q(200, 0.01), q(150, 0.02), q(100, 0.03)],
        "gates": [
            gate("sx", [0], 2e-4, 32),
            gate("sx", [1], 3e-4, 32),
            gate("sx", [2], 4e-4, 32),
            gate("cz", [0, 1], 0.003, 68),
            gate("cz", [1, 0], 0.003, 68),
            gate("cz", [1, 2], 0.005, 68),
        ],
    }


def test_properties_conversion() -> None:
    conv = snapshot_from_properties(_properties())
    snap = conv.snapshot
    assert snap.backend == "ibm_example"
    assert snap.num_qubits == 3 and len(snap.links) == 2
    assert snap.link(1, 2).gate_error == pytest.approx(0.005)
    assert snap.qubit_map[2].sx_error == pytest.approx(4e-4)
    nested = snapshot_from_properties({"backend_properties": _properties()})
    assert nested.snapshot.to_dict() == snap.to_dict()


def test_properties_reject_non_cz() -> None:
    props = _properties()
    for g in props["gates"]:
        if g["gate"] == "cz":
            g["gate"] = "ecr"
    with pytest.raises(DataValidationError, match="CZ"):
        snapshot_from_properties(props)


def test_example_csv_matches_captured_properties() -> None:
    """The example CSV and the IBM properties stored in the hardware plan describe the same chip."""
    from_csv = snapshot_from_csv(EXAMPLE_CSV).snapshot
    plan = json.loads((EXAMPLE_RUN / "plan.json").read_text())
    from_props = snapshot_from_properties(plan["calibration_at_preparation"]).snapshot
    assert from_csv.backend == from_props.backend == "ibm_kingston"
    assert {q.id for q in from_csv.qubits} == {q.id for q in from_props.qubits}
    assert set(from_csv.link_map) == set(from_props.link_map)
    for pair, link in from_csv.link_map.items():
        assert link.gate_error == pytest.approx(from_props.link_map[pair].gate_error)
    for qid, qubit in from_csv.qubit_map.items():
        assert qubit.readout_error == pytest.approx(from_props.qubit_map[qid].readout_error)


def test_detect_kind(tmp_path: Path) -> None:
    snap_path = ROOT / "examples" / "ibm_torino_2025-02-26.json"
    assert detect_kind(snap_path) == "quantivex_snapshot"
    assert detect_kind(EXAMPLE_CSV) == "ibm_csv"
    assert detect_kind(EXAMPLE_RUN) == "hardware_run"
    assert detect_kind(tmp_path) == "directory"
    props = tmp_path / "props.json"
    props.write_text(json.dumps(_properties()))
    assert detect_kind(props) == "ibm_properties_json"
    other = tmp_path / "other.json"
    other.write_text('{"a": 1}')
    assert detect_kind(other) == "unknown"
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert detect_kind(broken) == "unknown"
    table = tmp_path / "table.csv"
    table.write_text("a,b\n1,2\n")
    assert detect_kind(table) == "unknown"
    assert load(snap_path).num_qubits > 0
