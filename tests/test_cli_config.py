import importlib.util
import json
from pathlib import Path

import pytest

from quantivex import __version__
from quantivex.cli import main
from quantivex.config import Settings, data_root, default_hardware_output_dir, default_simulation_output_dir
from quantivex.errors import ConfigError

from .conftest import TORINO


def test_version(capsys):
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_ask_requires_question():
    with pytest.raises(SystemExit):
        main(["ask", str(TORINO)])


def test_ask_without_key_fails_cleanly(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main(["ask", str(TORINO), "hello", "--out", str(tmp_path)]) == 2
    assert "needs an LLM" in capsys.readouterr().err


def test_bad_data_file_fails_cleanly(capsys, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"qubits": []}))
    assert main(["ask", str(bad), "hello"]) == 2
    assert "non-empty" in capsys.readouterr().err


def test_doctor_reports_missing_key(monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main(["doctor"]) == 1
    assert "OPENAI_API_KEY" in capsys.readouterr().out


@pytest.mark.skipif(importlib.util.find_spec("qiskit_ibm_runtime") is None, reason="needs the ibm extra")
def test_export_fake_backend(tmp_path):
    out = tmp_path / "torino.json"
    assert main(["export", "--fake", "torino", "--output", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["schema_version"] == 2 and len(data["qubits"]) == 133
    assert data["calibration_timestamp"].startswith("2025-02-26")
    assert all("t1_us" in q for q in data["qubits"])


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("QUANTIVEX_SHOTS", "8000")
    monkeypatch.setenv("QUANTIVEX_MODEL", "my-model")
    s = Settings.from_env(seed=5)
    assert (s.shots, s.model, s.seed) == (8000, "my-model", 5)
    assert "secret" not in repr(Settings(api_key="secret"))  # keys never leak into logs


def test_generated_data_defaults_are_user_owned(monkeypatch, tmp_path: Path):
    home = tmp_path / "home"
    working_tree = tmp_path / "checkout"
    home.mkdir()
    working_tree.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(working_tree)
    monkeypatch.delenv("QUANTIVEX_DATA_DIR", raising=False)
    monkeypatch.delenv("QUANTIVEX_OUTPUT_DIR", raising=False)
    monkeypatch.delenv("QUANTIVEX_HARDWARE_OUTPUT_DIR", raising=False)

    expected = home / "QuantivexData"
    assert data_root() == expected
    assert Settings.from_env().output_dir == expected / "simulation_runs"
    assert default_simulation_output_dir() == expected / "simulation_runs"
    assert default_hardware_output_dir() == expected / "hardware_runs"
    assert working_tree not in Settings.from_env().output_dir.parents


def test_generated_data_environment_overrides(monkeypatch, tmp_path: Path):
    root = tmp_path / "data-root"
    simulations = tmp_path / "custom-simulations"
    hardware = tmp_path / "custom-hardware"
    monkeypatch.setenv("QUANTIVEX_DATA_DIR", str(root))
    monkeypatch.setenv("QUANTIVEX_OUTPUT_DIR", str(simulations))
    monkeypatch.setenv("QUANTIVEX_HARDWARE_OUTPUT_DIR", str(hardware))

    assert data_root() == root
    assert Settings.from_env().output_dir == simulations
    assert default_simulation_output_dir() == simulations
    assert default_hardware_output_dir() == hardware


@pytest.mark.parametrize(
    "env, value", [("QUANTIVEX_SHOTS", "lots"), ("QUANTIVEX_SHOTS", "5"), ("QUANTIVEX_MAX_ROUNDS", "0")]
)
def test_settings_validation(monkeypatch, env, value):
    monkeypatch.setenv(env, value)
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_tool_timeout_from_env_and_bounds(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("QUANTIVEX_TOOL_TIMEOUT", "1200")
    assert Settings.from_env().tool_timeout_s == 1200
    monkeypatch.setenv("QUANTIVEX_TOOL_TIMEOUT", "5")
    with pytest.raises(ConfigError, match="tool_timeout_s"):
        Settings.from_env()
