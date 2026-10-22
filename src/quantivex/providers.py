"""OpenAI Chat Completions transport and native Anthropic Messages adapter.

The agent keeps a provider-neutral internal tool transcript in Chat Completions
shape. The adapter explicitly converts Claude tool_use/tool_result blocks.
"""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace
from typing import Any

from .config import Settings, is_loopback
from .errors import AgentError, ConfigError


def _claude_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system: list[str] = []
    output: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "system":
            system.append(message.get("content") or "")
            continue
        if role == "tool":
            text = message.get("content") or ""
            try:
                is_error = isinstance(json.loads(text), dict) and "error" in json.loads(text)
            except (ValueError, TypeError):
                is_error = False
            blocks = [
                {"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": text, "is_error": is_error}
            ]
            converted_role = "user"
        else:
            converted_role = role
            blocks = []
            if message.get("content"):
                blocks.append({"type": "text", "text": message["content"]})
            for call in message.get("tool_calls") or []:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["function"]["name"],
                        "input": json.loads(call["function"]["arguments"]),
                    }
                )
        if not blocks:
            continue
        # Parallel tool results must be one user turn immediately after tool_use.
        if output and output[-1]["role"] == converted_role:
            output[-1]["content"].extend(blocks)
        else:
            output.append({"role": converted_role, "content": blocks})
    return "\n\n".join(system), output


class AnthropicAdapter:
    """Expose the internal create interface while speaking native Messages on the wire."""

    def __init__(self, client: Any, max_tokens: int):
        self.client = client
        self.max_tokens = max_tokens
        self.last_tools: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> Any:
        system, converted = _claude_messages(messages)
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": converted,
        }
        if tools:
            self.last_tools = tools
            payload["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "input_schema": t["function"]["parameters"],
                }
                for t in tools
            ]
            payload["tool_choice"] = {"type": "any" if tool_choice == "required" else "auto"}
        elif self.last_tools:
            payload["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "input_schema": t["function"]["parameters"],
                }
                for t in self.last_tools
            ]
            payload["tool_choice"] = {"type": "none"}
        response = self.client.messages.create(**payload)
        if response.stop_reason == "max_tokens":
            raise AgentError("Claude response was truncated; raise QUANTIVEX_MAX_TOKENS")
        texts, calls = [], []
        for block in response.content:
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "tool_use":
                calls.append(
                    SimpleNamespace(
                        id=block.id, function=SimpleNamespace(name=block.name, arguments=json.dumps(block.input))
                    )
                )
        message = SimpleNamespace(content="\n".join(texts), tool_calls=calls or None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=response.stop_reason)])


def _loopback_http_client(client_class: Any) -> Any:
    # Use the SDK's own HTTPX generation (httpx or httpx2). An explicit transport
    # also bypasses SDK versions that manually construct proxy mounts despite
    # trust_env=False. Do not follow redirects away from a local endpoint.
    base = next(c for c in client_class.__mro__ if c.__name__ == "Client")
    transport_module = importlib.import_module(base.__module__.split(".")[0])
    return client_class(
        transport=transport_module.HTTPTransport(trust_env=False), trust_env=False, follow_redirects=False
    )


def make_client(settings: Settings) -> Any:
    settings.validated()
    key = settings.require_api_key()
    if settings.provider == "anthropic":
        try:
            from anthropic import Anthropic, DefaultHttpxClient
        except ImportError as exc:
            raise ConfigError('Claude support needs: pip install "quantivex[anthropic]"') from exc
        native_options: dict[str, Any] = {}
        if is_loopback(settings.endpoint):
            native_options["http_client"] = _loopback_http_client(DefaultHttpxClient)
        return AnthropicAdapter(
            Anthropic(
                api_key=key,
                base_url=settings.endpoint,
                timeout=settings.request_timeout_s,
                max_retries=2,
                **native_options,
            ),
            settings.max_tokens,
        )
    from openai import DefaultHttpxClient as OpenAIHttpClient
    from openai import OpenAI

    options: dict[str, Any] = {}
    if is_loopback(settings.endpoint):
        options["http_client"] = _loopback_http_client(OpenAIHttpClient)
    return OpenAI(api_key=key, base_url=settings.endpoint, timeout=settings.request_timeout_s, max_retries=2, **options)


def token_limit(settings: Settings) -> dict[str, int]:
    """Response-token cap in the parameter each API family expects.

    OpenAI's own API uses max_completion_tokens (max_tokens is rejected by its reasoning models);
    other OpenAI-compatible servers (Ollama, LM Studio, vLLM, hosted gateways) use max_tokens. The
    native Anthropic adapter applies settings.max_tokens itself.
    """
    if settings.provider == "anthropic":
        return {}
    if settings.provider == "openai":
        return {"max_completion_tokens": settings.max_tokens}
    return {"max_tokens": settings.max_tokens}


def complete(client: Any, **kwargs: Any) -> Any:
    try:
        response = client.chat.completions.create(**kwargs)
        if not response.choices:
            raise AgentError("Provider returned no response choices")
        choice = response.choices[0]
        if getattr(choice, "finish_reason", None) in ("length", "content_filter"):
            raise AgentError("Provider truncated or filtered its response; no incomplete tool call was executed")
        return response
    except (ConfigError, AgentError):
        raise
    except Exception as exc:  # provider errors differ; do not copy keys/HTTP headers into reports
        raise AgentError(
            f"LLM request failed ({type(exc).__name__}). Check endpoint, model, credentials, "
            "and tool support with quantivex check-llm."
        ) from exc


def check_compatibility(settings: Settings, client: Any = None) -> dict[str, Any]:
    """Two small model turns; real usage if called against a real provider.

    The unpredictable token prevents a model passing just by answering a known
    arithmetic question. No calibration data or chemistry is sent in this probe.
    """
    import secrets

    from jsonschema import Draft202012Validator

    client = client or make_client(settings)
    schema = {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    tools = [
        {
            "type": "function",
            "function": {
                "name": "quantivex_probe",
                "description": "Call with value 7 to obtain a token; then return only that token.",
                "parameters": schema,
            },
        }
    ]
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": "Call quantivex_probe with value 7. "
            "After receiving the tool result, return only its token, with no tool call or explanation.",
        },
        {"role": "user", "content": "Check the tool connection now."},
    ]
    response = complete(
        client,
        model=settings.model,
        messages=messages,
        tools=tools,
        tool_choice=settings.tool_choice,
        **token_limit(settings),
    )
    msg = response.choices[0].message
    calls = msg.tool_calls or []
    if len(calls) != 1 or calls[0].function.name != "quantivex_probe":
        raise AgentError("Compatibility check failed: model did not return the requested structured tool call")
    call = calls[0]
    try:
        arguments = json.loads(call.function.arguments)
        Draft202012Validator(schema).validate(arguments)
        if arguments["value"] != 7:
            raise ValueError("Expected probe argument value 7")
    except Exception as exc:
        raise AgentError("Compatibility check failed: invalid tool arguments") from exc
    token = "qvx_" + secrets.token_hex(8)
    messages.extend(
        [
            {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.function.name, "arguments": call.function.arguments},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": call.id, "content": json.dumps({"token": token})},
        ]
    )
    response = complete(
        client, model=settings.model, messages=messages, tools=tools, tool_choice="auto", **token_limit(settings)
    )
    final = response.choices[0].message
    if final.tool_calls or (final.content or "").strip() != token:
        raise AgentError("Compatibility check failed: model did not consume the tool result correctly")
    return {
        "compatible": True,
        "provider": settings.provider,
        "model": settings.model,
        "checks": ["structured_tool_call", "valid_arguments", "tool_result_round_trip"],
        "scope": "Basic tool protocol only; does not certify scientific judgement or all seven agent tools.",
    }
