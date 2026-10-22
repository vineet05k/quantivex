"""Agent tests with a scripted fake LLM: no API key or network needed."""

import json
from pathlib import Path

import pytest

from quantivex.agent import TOOLS, Session, ask
from quantivex.config import Settings
from quantivex.errors import AgentError, ConfigError

from .conftest import FakeLLM, reply, tool_call


def test_tool_schemas_are_strict():
    for tool in TOOLS:
        params = tool["function"]["parameters"]
        assert params["additionalProperties"] is False
        assert set(params["required"]) <= set(params["properties"])


def test_grounded_run_writes_reports(chip, settings):
    llm = FakeLLM(
        [
            reply(
                calls=[
                    tool_call("get_dataset_summary", {}, "a"),
                    tool_call("run_chemistry", {"molecule": "H2", "bond_length": 0.75}, "b"),
                ]
            ),
            reply("H2 on qubits 0-3: see verdict."),
        ]
    )
    result = ask(chip, "Run H2", settings, llm)
    assert "Computed evidence" in result.answer and "e_noisy_hartree" in result.answer
    # Model prose is shown only after the computed summary, labelled unverified.
    assert "H2 on qubits 0-3: see verdict." not in result.summary.split("unverified")[0]
    assert "Explanation from the language model (unverified)" in result.summary
    assert "Summary (computed by Quantivex" in result.summary and "H2 at 0.75 Å" in result.summary
    assert [t["tool"] for t in result.tool_calls] == ["get_dataset_summary", "run_chemistry"]
    assert all(t["ok"] for t in result.tool_calls)
    assert llm.requests[0]["tool_choice"] == "required"
    assert llm.requests[1]["tool_choice"] == "auto"
    chem = result.tool_results[1]["output"]
    assert chem["noisy"]["verdict"] in {"MODEL_AGREEMENT_95", "INCONCLUSIVE_95", "OUTSIDE_TOLERANCE_95"}
    run = Path(result.run_dir)
    assert (run / "record.json").exists() and (run / "report.md").exists()
    assert (run / chem["figure"]).exists()
    record = json.loads((run / "record.json").read_text())
    assert record["dataset"]["sha256"] == chip.sha256
    assert record["environment"]["quantivex"]


def test_tool_errors_go_back_to_the_model(chip, settings):
    llm = FakeLLM(
        [
            reply(
                calls=[
                    tool_call("run_chemistry", {"molecule": "N2", "bond_length": 1.1, "active_space": "6e6o"}),
                    tool_call("find_qubit_chains", {"length": 40}, "c2"),
                ]
            ),
            reply(calls=[tool_call("find_qubit_chains", {"length": 4, "draw_map": False}, "c3")]),
            reply("done"),
        ]
    )
    result = ask(chip, "q", settings, llm)
    assert result.tool_calls[0]["ok"]  # 12 qubits: exact only, with an explanatory note
    assert "note" in result.tool_results[0]["output"]
    assert not result.tool_calls[1]["ok"] and "16" in result.tool_calls[1]["error"]
    assert result.tool_calls[2]["ok"]
    tool_msgs = [m for m in llm.requests[1]["messages"] if m["role"] == "tool"]
    assert "error" in json.loads(tool_msgs[1]["content"])


@pytest.mark.parametrize("arguments", ["{not json", "[1, 2]"])
def test_malformed_arguments(chip, settings, arguments):
    llm = FakeLLM([reply(calls=[tool_call("find_qubit_chains", arguments)]), reply("sorry")])
    with pytest.raises(AgentError, match="No tool succeeded"):
        ask(chip, "q", settings, llm)
    record = json.loads(next(settings.output_dir.rglob("record.json")).read_text())
    assert "Invalid arguments" in record["tool_calls"][0]["error"]


def test_unknown_tool_and_unexpected_argument(chip, settings):
    llm = FakeLLM(
        [reply(calls=[tool_call("delete_chip", {}), tool_call("render_chip_map", {"colour": "red"}, "x")]), reply("ok")]
    )
    with pytest.raises(AgentError, match="No tool succeeded"):
        ask(chip, "q", settings, llm)
    record = json.loads(next(settings.output_dir.rglob("record.json")).read_text())
    assert "Unknown tool" in record["tool_calls"][0]["error"]
    assert "Invalid tool arguments" in record["tool_calls"][1]["error"]


def test_round_budget_forces_a_final_answer(chip, tmp_path):
    settings = Settings(api_key="k", max_rounds=2, output_dir=tmp_path)
    llm = FakeLLM([reply(calls=[tool_call("get_dataset_summary", {})])] * 2 + [reply("final")])
    result = ask(chip, "q", settings, llm)
    assert "Computed evidence" in result.answer and result.answer != "final"
    assert "tools" not in llm.requests[-1]


def test_missing_key_is_a_config_error(chip, tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="needs an LLM"):
        ask(chip, "q", Settings.from_env(output_dir=tmp_path))


def test_empty_question(chip, settings):
    with pytest.raises(AgentError):
        ask(chip, "  ", settings, FakeLLM([]))


def test_provider_failure_is_normalised(chip, settings):
    class Broken:
        class chat:
            class completions:
                @staticmethod
                def create(**_):
                    raise ConnectionError("network down")

    with pytest.raises(AgentError, match="ConnectionError"):
        ask(chip, "q", settings, Broken())


def test_session_tools_directly(chip, settings):
    s = Session(chip, settings)
    assert s.find_qubit_chains(3, top=2, draw_map=False)["usable_chains"] == 6
    assert "figure" in s.render_noise_blur("readout_error", 0.3, "ry")
    assert "error" in s.dispatch("render_chip_map", {"highlight": [999]})
    curve = s.energy_curve("H2", 0.5, 1.5, points=3, noisy=False)
    assert len(curve["points"]) == 3 and curve["layout"] is None


def test_compare_layouts_tool_end_to_end(chip, settings):
    llm = FakeLLM(
        [
            reply(calls=[tool_call("compare_layouts", {"molecule": "H2", "bond_length": 0.75, "n_random": 2})]),
            reply(calls=[tool_call("energy_curve", {"molecule": "H2", "start": 0.6, "stop": 1.2, "points": 3}, "c2")]),
            reply("Best chain vs Qiskit: see table."),
        ]
    )
    result = ask(chip, "Does layout matter for H2?", settings, llm)
    assert all(t["ok"] for t in result.tool_calls), result.tool_calls
    comp = result.tool_results[0]["output"]
    assert {r["role"] for r in comp["rows"]} >= {"quantivex_best", "qiskit_default", "worst_usable_chain"}
    curve = result.tool_results[1]["output"]
    assert len(curve["points"]) == 3 and curve["layout"] is not None
    report = Path(result.report_path).read_text()
    assert "## Layout comparison" in report and "## Energy curve" in report
    model_tools = [m["content"] for m in llm.requests[-1]["messages"] if m["role"] == "tool"]
    assert all("circuits_qpy_base64" not in m and "numerical_inputs" not in m for m in model_tools)
    assert "numerical_inputs" in curve["points"][0]  # retained in the local evidence
    assert len(result.artifacts) == 3  # comparison chart, chip map, energy curve
    for art in result.artifacts:
        assert (Path(result.run_dir) / art["file"]).exists()
