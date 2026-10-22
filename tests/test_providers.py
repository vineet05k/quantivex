"""Real SDK-to-HTTP contract tests against local scripted servers, no live LLMs."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from quantivex.agent import Session, ask
from quantivex.calibration import builtin_bytes, builtin_info, export_builtin, load_builtin
from quantivex.config import Settings
from quantivex.errors import AgentError, ConfigError, DataValidationError
from quantivex.providers import AnthropicAdapter, check_compatibility, make_client


@pytest.fixture(autouse=True)
def clean_provider_env(monkeypatch):
    for name in [
        "QUANTIVEX_PROVIDER",
        "QUANTIVEX_MODEL",
        "QUANTIVEX_API_KEY",
        "QUANTIVEX_BASE_URL",
        "QUANTIVEX_TOOL_CHOICE",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_BASE_URL",
        "LOCAL_LLM_API_KEY",
        "LOCAL_LLM_BASE_URL",
        "OLLAMA_BASE_URL",
    ]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def endpoint():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            server.requests.append((self.path, payload, {k.lower(): v for k, v in self.headers.items()}))
            native = self.path == "/v1/messages"
            last = payload["messages"][-1]
            names = [x.get("name", x.get("function", {}).get("name")) for x in payload.get("tools", [])]
            is_probe = "quantivex_probe" in names
            result_turn = last["role"] == "tool" or (
                native
                and isinstance(last["content"], list)
                and any(x.get("type") == "tool_result" for x in last["content"])
            )
            if result_turn:
                raw = last["content"][0]["content"] if native else last["content"]
                text = (
                    json.loads(raw).get("token", "Audited tools completed.") if is_probe else "Audited tools completed."
                )
                if server.mode == "wrong_result":
                    text = "wrong"
                calls = []
            else:
                text = "No tools." if server.mode == "no_tools" else None
                calls = (
                    []
                    if server.mode == "no_tools"
                    else [
                        (
                            "a",
                            "quantivex_probe" if is_probe else "get_dataset_summary",
                            {"value": 8 if server.mode == "bad_args" else 7} if is_probe else {},
                        )
                    ]
                )
                if server.mode == "parallel" and not is_probe:
                    calls.append(("b", "find_qubit_chains", {"length": 2, "draw_map": False}))
            if native:
                content = ([{"type": "text", "text": text}] if text else []) + [
                    {"type": "tool_use", "id": cid, "name": name, "input": args} for cid, name, args in calls
                ]
                body = {
                    "id": "msg_audit",
                    "type": "message",
                    "role": "assistant",
                    "model": payload["model"],
                    "content": content,
                    "stop_reason": "max_tokens"
                    if server.mode == "truncated"
                    else ("tool_use" if calls else "end_turn"),
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            else:
                message = {"role": "assistant", "content": text}
                if calls:
                    message["tool_calls"] = [
                        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
                        for cid, name, args in calls
                    ]
                body = {
                    "id": "chat_audit",
                    "object": "chat.completion",
                    "created": 0,
                    "model": payload["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": message,
                            "finish_reason": "length"
                            if server.mode == "truncated"
                            else ("tool_calls" if calls else "stop"),
                        }
                    ],
                }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.mode = "normal"
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def configured(provider, url, **kwargs):
    return Settings.from_env(
        provider=provider,
        base_url=url if provider == "anthropic" else url + "/v1",
        model="audit-model",
        api_key="dummy-native"
        if provider == "anthropic"
        else (None if provider in ("local", "ollama") else "dummy-hosted"),
        **kwargs,
    )


@pytest.mark.parametrize("provider", ["openai", "openai-compatible", "local", "ollama", "anthropic"])
def test_sdk_http_two_turn_contract(endpoint, provider):
    server, url = endpoint
    result = check_compatibility(configured(provider, url))
    assert result["compatible"] and len(server.requests) == 2
    path, body, headers = server.requests[0]
    if provider == "anthropic":
        assert path == "/v1/messages" and "input_schema" in body["tools"][0]
        assert headers["x-api-key"] == "dummy-native"
        assert body["tool_choice"] == {"type": "auto"}
        second = server.requests[1][1]
        assert second["messages"][-1]["content"][0]["type"] == "tool_result"
    else:
        assert path == "/v1/chat/completions" and body["tools"][0]["type"] == "function"
        assert server.requests[1][1]["messages"][-1]["role"] == "tool"
        if provider in ("local", "ollama"):
            assert headers["authorization"] == "Bearer quantivex-local"


@pytest.mark.parametrize("provider", ["openai", "anthropic", "local"])
@pytest.mark.parametrize("mode", ["no_tools", "bad_args", "wrong_result", "truncated"])
def test_probe_rejects_incompatible_behavior(endpoint, provider, mode):
    server, url = endpoint
    server.mode = mode
    with pytest.raises(AgentError):
        check_compatibility(configured(provider, url))


@pytest.mark.parametrize("provider", ["openai", "anthropic", "ollama", "local", "openai-compatible"])
def test_full_agent_round_trip_and_record(endpoint, provider, chip, tmp_path):
    server, url = endpoint
    server.mode = "parallel"
    result = ask(chip, "Show the dataset and chains", configured(provider, url, output_dir=tmp_path))
    assert [t["tool"] for t in result.tool_calls] == ["get_dataset_summary", "find_qubit_chains"]
    assert all(t["ok"] for t in result.tool_calls)
    record = json.loads(__import__("pathlib").Path(result.record_path).read_text())
    assert record["provider"] == provider and record["model"] == "audit-model"
    assert "dummy-native" not in json.dumps(record) and "dummy-hosted" not in json.dumps(record)
    if provider == "anthropic":
        blocks = server.requests[1][1]["messages"][-1]["content"]
        assert len(blocks) == 2 and all(x["type"] == "tool_result" for x in blocks)


def test_claude_error_results_and_final_no_tools():
    requests = []

    def create(**payload):
        requests.append(payload)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="done")])

    adapter = AnthropicAdapter(SimpleNamespace(messages=SimpleNamespace(create=create)), 500)
    tool = {"type": "function", "function": {"name": "t", "description": "d", "parameters": {"type": "object"}}}
    history = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "a", "function": {"name": "t", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "a", "content": '{"error":"bad argument"}'},
    ]
    adapter.create(model="claude-test", messages=history, tools=[tool], tool_choice="required")
    assert requests[-1]["tool_choice"] == {"type": "any"}
    assert requests[-1]["messages"][-1]["content"][0]["is_error"]
    adapter.create(model="claude-test", messages=history)
    assert requests[-1]["tools"] and requests[-1]["tool_choice"] == {"type": "none"}


def test_local_never_reuses_openai_or_anthropic_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "cloud-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "other-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://cloud.invalid/v1")
    s = Settings.from_env(provider="ollama", model="installed-local")
    assert s.api_key is None and s.endpoint == "http://localhost:11434/v1"
    assert s.require_api_key() == "quantivex-local"


def test_provider_switch_resolves_correct_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "claude-secret")
    s = Settings.from_env(provider="anthropic", model="my-claude")
    assert s.require_api_key() == "claude-secret" and "secret" not in repr(s)


@pytest.mark.parametrize("url", ["https://example.com/v1", "http://192.168.1.2:8000/v1"])
def test_local_rejects_nonlocal_endpoint(url):
    with pytest.raises(ConfigError, match="loopback"):
        Settings.from_env(provider="local", model="my-model", base_url=url)


@pytest.mark.parametrize("url", ["http://localhost:1234/v1", "http://127.0.0.1:8000/v1", "http://[::1]:8000/v1"])
def test_local_accepts_loopback(url):
    assert Settings.from_env(provider="local", model="my-model", base_url=url).endpoint == url


@pytest.mark.parametrize("provider", ["anthropic", "local", "ollama", "openai-compatible"])
def test_model_is_explicit_for_nondefault_providers(provider):
    with pytest.raises(ConfigError, match="Choose a model"):
        Settings.from_env(provider=provider)


def test_missing_anthropic_sdk_is_actionable(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(ConfigError, match="anthropic"):
        make_client(Settings(provider="anthropic", model="claude", api_key="dummy"))


def test_dispatch_enforces_schema_before_execution(chip, settings):
    s = Session(chip, settings)
    for args in [{"length": 2, "top": 999}, {"length": True}, {"length": 2, "draw_map": "yes"}]:
        assert "Invalid tool arguments" in s.dispatch("find_qubit_chains", args)["error"]


def test_calibration_offline_bytes_and_export(tmp_path):
    p = export_builtin(tmp_path / "torino.json")
    assert p.read_bytes() == builtin_bytes()
    assert builtin_info()["num_qubits"] == 133 and builtin_info()["num_links"] == 150
    assert builtin_info()["calibration_timestamp"].startswith("2025-02-26")
    assert load_builtin().qubits
    with pytest.raises(DataValidationError):
        builtin_bytes("../../bad")


@pytest.mark.parametrize("bad", [[], "bad", 1, None])
def test_invalid_duration_containers_are_controlled(bad):
    from quantivex.data import from_dict

    raw = json.loads(builtin_bytes())
    raw["durations_ns"] = bad
    with pytest.raises(DataValidationError, match="durations_ns"):
        from_dict(raw)


@pytest.mark.parametrize("provider", ["openai", "anthropic", "local"])
def test_agent_rejects_unstructured_first_response(endpoint, provider, chip, tmp_path):
    server, url = endpoint
    server.mode = "no_tools"
    with pytest.raises(AgentError, match="structured tool call"):
        ask(chip, "Run a tool", configured(provider, url, output_dir=tmp_path))


def test_agent_tool_budget_rejects_batch_before_any_execution(endpoint, chip, tmp_path):
    server, url = endpoint
    server.mode = "parallel"
    with pytest.raises(AgentError, match="budget"):
        ask(chip, "Use tools", configured("local", url, output_dir=tmp_path, max_tool_calls=1))


def test_cli_flags_and_calibration_export(monkeypatch, capsys, tmp_path):
    from quantivex.cli import main

    output = tmp_path / "historical.json"
    assert main(["calibration", "--output", str(output)]) == 0
    assert output.read_bytes() == builtin_bytes()
    assert "133" in capsys.readouterr().out
    requests = []

    def probe(settings):
        requests.append(settings)
        return {"compatible": True}

    monkeypatch.setattr("quantivex.providers.check_compatibility", probe)
    assert (
        main(
            [
                "check-llm",
                "--provider",
                "local",
                "--model",
                "installed",
                "--base-url",
                "http://localhost:8000/v1",
                "--tool-choice",
                "auto",
            ]
        )
        == 0
    )
    assert requests[0].provider == "local" and requests[0].model == "installed"
    assert main(["doctor", "--provider", "ollama", "--model", "installed"]) == 0


def test_local_proxy_settings_do_not_break_local_sdk(endpoint, monkeypatch):
    _, url = endpoint
    monkeypatch.setenv("ALL_PROXY", "socks5://invalid.invalid:1")
    monkeypatch.setenv("HTTP_PROXY", "socks5://invalid.invalid:1")
    monkeypatch.setenv("HTTPS_PROXY", "socks5://invalid.invalid:1")
    assert check_compatibility(configured("local", url))["compatible"]
    assert check_compatibility(configured("anthropic", url))["compatible"]
