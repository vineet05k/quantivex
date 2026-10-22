"""GUI provider controls and offline package data; model calls are mocked."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

ENTRY = Path(__file__).parents[1] / "launch_gui.py"


def button(app, label):
    return next(b for b in app.button if b.label == label)


def select(app, label):
    return next(s for s in app.selectbox if s.label == label)


def text(app, label):
    return next(t for t in app.text_input if t.label == label)


def test_gui_offline_data_and_compatible_local_flow(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("QUANTIVEX_MODEL", raising=False)
    monkeypatch.setenv("QUANTIVEX_SHOTS", "500")
    monkeypatch.chdir(tmp_path)  # no examples/ directory
    app = AppTest.from_file(str(ENTRY), default_timeout=30).run(timeout=30)
    assert not app.exception
    assert app.metric[0].value == "133"
    assert len(app.get("download_button")) == 1
    select(app, "Provider").set_value("ollama").run()
    text(app, "Model ID").set_value("my-local-model").run()
    with patch("quantivex.providers.check_compatibility", return_value={"compatible": True}) as check:
        button(app, "Check LLM compatibility").click().run()
        assert check.call_args.args[0].provider == "ollama"
        assert check.call_args.args[0].api_key == ""  # no paid key required
        assert app.success
    app.text_area[0].set_value("First question").run()
    report = tmp_path / "report.md"
    record = tmp_path / "record.json"
    report.write_text("report")
    record.write_text("{}")
    result = SimpleNamespace(
        answer="Mock result",
        artifacts=[],
        run_dir=str(tmp_path),
        report_path=str(report),
        record_path=str(record),
        tool_calls=[],
    )
    with patch("quantivex.agent.ask", return_value=result) as ask:
        button(app, "Ask Quantivex").click().run()
        assert ask.call_args.args[2].provider == "ollama"
        assert any(x.value == "Mock result" for x in app.markdown)
    app.text_area[0].set_value("Changed question").run()
    assert not any(x.value == "Mock result" for x in app.markdown)


def test_switch_to_claude_keeps_separate_key_field(monkeypatch):
    monkeypatch.delenv("QUANTIVEX_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-dummy")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "claude-dummy")
    app = AppTest.from_file(str(ENTRY), default_timeout=30).run(timeout=30)
    assert text(app, "API key (optional for local servers)").value == ""
    select(app, "Provider").set_value("anthropic").run()
    assert not app.exception
    assert text(app, "API key (optional for local servers)").value == ""
    assert text(app, "API base URL").value == "https://api.anthropic.com"
