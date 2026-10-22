"""quantivex process: discovery, results folder, integrity checks, failures and the model report."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from quantivex.cli import main
from quantivex.errors import QuantivexError
from quantivex.process import ProcessConfig, discover, process

from .conftest import line_chip

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_RUN = ROOT / "examples" / "hardware_runs" / "ibm_kingston_h2_075_run03"
FAST = ProcessConfig(shots=200, report="none", predict_hardware=False)


@pytest.fixture
def inputs(tmp_path: Path) -> Path:
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "line.json").write_text(json.dumps(line_chip()))
    (folder / "notes.json").write_text('{"hello": 1}')
    (folder / ".hidden.json").write_text(json.dumps(line_chip()))
    return folder


def _manifest_ok(out: Path) -> bool:
    for line in (out / "SHA256SUMS.txt").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        if hashlib.sha256((out / name).read_bytes()).hexdigest() != digest:
            return False
    return True


def test_calibration_results_folder(inputs: Path, tmp_path: Path) -> None:
    out = tmp_path / "results"
    result = process([inputs], out, FAST, log=lambda *_: None)
    assert result.exit_code == 0
    assert len(result.calibrations) == 1 and not result.hardware
    assert [s["input"] for s in result.skipped] == ["in/notes.json"]
    folder = out / "calibrations" / "line"
    for name in ("snapshot.json", "conversion.json", "dataset_summary.json", "chains.json", "chemistry.json"):
        assert (folder / name).is_file(), name
    assert (folder / "chip_map.png").stat().st_size > 1000
    rows = list(csv.DictReader((out / "calibrations.csv").open()))
    assert rows[0]["id"] == "line" and rows[0]["verdict"]
    run = json.loads((out / "run.json").read_text())
    assert run["counts"]["calibrations"] == 1
    assert run["language_model_report"]["status"].startswith("not requested")
    assert all(not str(i["input"]).startswith("/") for i in run["inputs"])  # no absolute paths recorded
    assert "No explanation was written" in (out / "report.md").read_text()
    assert _manifest_ok(out)
    assert not list(tmp_path.glob(".results.partial-*"))


def test_refuses_to_overwrite(inputs: Path, tmp_path: Path) -> None:
    out = tmp_path / "results"
    out.mkdir()
    with pytest.raises(QuantivexError, match="overwrite"):
        process([inputs], out, FAST)


def test_nothing_to_process(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(QuantivexError, match="No IBM calibration files"):
        process([empty], tmp_path / "out", FAST)


def test_bad_input_does_not_stop_batch(inputs: Path, tmp_path: Path) -> None:
    bad = line_chip()
    bad["qubits"][0]["readout_error"] = 3.0
    (inputs / "bad.json").write_text(json.dumps(bad))
    result = process([inputs], tmp_path / "out", FAST, log=lambda *_: None)
    assert result.exit_code == 1
    assert len(result.calibrations) == 1 and result.failed[0]["input"] == "in/bad.json"
    assert (tmp_path / "out" / "failures" / "bad.json").is_file()


def test_invalid_config_rejected(inputs: Path, tmp_path: Path) -> None:
    with pytest.raises(QuantivexError, match="shots"):
        process([inputs], tmp_path / "out", ProcessConfig(shots=5))


def test_discover_ignores_symlinks(inputs: Path, tmp_path: Path) -> None:
    (inputs / "link.json").symlink_to(inputs / "line.json")
    items, skipped = discover([inputs])
    assert [i.display for i in items] == ["in/line.json"]
    assert any(s["reason"] == "symbolic link not followed" for s in skipped)


@pytest.fixture
def hardware(tmp_path: Path) -> Path:
    target = tmp_path / "hw" / EXAMPLE_RUN.name
    shutil.copytree(EXAMPLE_RUN, target)
    return target.parent


def test_hardware_run_reanalysed(hardware: Path, tmp_path: Path) -> None:
    pytest.importorskip("qiskit_ibm_runtime")
    config = ProcessConfig(report="none", predict_hardware=True)
    result = process([hardware], tmp_path / "out", config, log=lambda *_: None)
    row = result.hardware[0]
    assert row["integrity_ok"] is True
    assert row["integrity"]["raw_counts_match_result"] is True
    assert row["integrity"]["energy_matches_stored_result"] is True
    assert row["total_shots"] == 5000
    assert row["error_mha"] == pytest.approx(105.572, abs=1e-3)
    assert row["confidence_radius_mha"] == pytest.approx(121.312, abs=1e-3)
    assert row["verdict"] == "INCONCLUSIVE_95"
    assert row["excludes_reference"] is False and row["beats_hartree_fock"] is False
    assert row["predicted_error_mha"] == pytest.approx(59.7, abs=0.2)
    assert (tmp_path / "out" / "figures" / "hardware_errors.png").is_file()


def test_tampered_hardware_counts_detected(hardware: Path, tmp_path: Path) -> None:
    run = hardware / EXAMPLE_RUN.name
    result = json.loads((run / "result.json").read_text())
    first = next(iter(result["counts"][0]))
    result["counts"][0][first] += 1
    (run / "result.json").write_text(json.dumps(result))
    out = process([hardware], tmp_path / "out", FAST, log=lambda *_: None)
    row = out.hardware[0]
    assert row["integrity_ok"] is False
    assert row["integrity"]["manifest"] == "mismatch"


def test_tampered_circuits_fail(hardware: Path, tmp_path: Path) -> None:
    qpy = hardware / EXAMPLE_RUN.name / "circuits.qpy"
    data = bytearray(qpy.read_bytes())
    data[-1] ^= 1
    qpy.write_bytes(bytes(data))
    (hardware.parent / "line.json").write_text(json.dumps(line_chip()))
    out = process([hardware, hardware.parent / "line.json"], tmp_path / "out", FAST, log=lambda *_: None)
    assert out.failed and out.failed[0]["kind"] == "hardware_run"
    assert out.exit_code == 1


class _FakeClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **_: Any) -> Any:
        message = SimpleNamespace(content=self.text)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])


def _settings() -> Any:
    from quantivex.config import Settings

    return Settings.from_env(validate=False, provider="openai", model="test-model", api_key="test-key")


def test_model_report_checked(inputs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import quantivex.providers as providers

    first = process([inputs], tmp_path / "a", FAST, log=lambda *_: None)
    error = round(first.calibrations[0]["error_mha"], 1)
    monkeypatch.setattr(providers, "make_client", lambda s: _FakeClient(f"The error was {error} mHa and 999.9 mHa."))
    config = ProcessConfig(shots=200, report="llm", predict_hardware=False)
    result = process([inputs], tmp_path / "b", config, settings=_settings(), log=lambda *_: None)
    report = (tmp_path / "b" / "report.md").read_text()
    assert result.report_status.startswith("written by openai:test-model")
    assert f"The error was {error} mHa" in report
    assert "`999.9`" in report  # the invented number is flagged
    run = json.loads((tmp_path / "b" / "run.json").read_text())
    assert run["language_model_report"]["unmatched_numbers"] == ["999.9"]


def test_model_required_but_missing(inputs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import quantivex.providers as providers
    from quantivex.errors import ConfigError

    def fail(_: Any) -> Any:
        raise ConfigError("no credential")

    monkeypatch.setattr(providers, "make_client", fail)
    config = ProcessConfig(shots=200, report="llm", require_llm=True, predict_hardware=False)
    result = process([inputs], tmp_path / "out", config, settings=_settings(), log=lambda *_: None)
    assert result.exit_code == 3
    assert (tmp_path / "out" / "calibrations.csv").is_file()  # results kept


def test_cli_process(inputs: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["process", str(inputs), "--out", str(tmp_path / "cli"), "--shots", "200", "--no-report"])
    assert code == 0
    assert "Done: 1 calibration file(s)" in capsys.readouterr().out
    assert main(["process", str(inputs), "--out", str(tmp_path / "cli"), "--no-report"]) == 2


def test_cli_process_default_output_folder(
    inputs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("QUANTIVEX_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("QUANTIVEX_RESULTS_DIR", raising=False)
    assert main(["process", str(inputs), "--shots", "200", "--no-report"]) == 0
    created = list((tmp_path / "data" / "results").iterdir())
    assert len(created) == 1 and created[0].name.startswith("results_")
    assert (created[0] / "report.md").is_file()
    monkeypatch.setenv("QUANTIVEX_RESULTS_DIR", str(tmp_path / "elsewhere"))
    assert main(["process", str(inputs), "--shots", "200", "--no-report"]) == 0
    assert len(list((tmp_path / "elsewhere").iterdir())) == 1
