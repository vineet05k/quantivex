# Choose an LLM provider (Quantivex 1.3.0)

The quantum chemistry and noise calculations run locally. You choose whether the LLM runs locally or uses a hosted service. Hosted services receive your question and tool outputs, including calibration summaries. Local mode makes LLM requests only to a loopback server; it does not automatically start or download a model.

This release implements three protocol paths: OpenAI Chat Completions, OpenAI-compatible Chat Completions (including local servers), and native Anthropic Messages. Compatibility is **model-specific**. The chosen model must return structured tool calls and handle tool results; merely generating conversational text is insufficient.

## Install and start

From the extracted `quantivex` folder:

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -e '.[gui,anthropic]'
quantivex-gui
```

The installed `quantivex-gui` launcher also works outside the source folder. Alternatively use `python -m streamlit run launch_gui.py` from the repository root. In the sidebar choose the provider, model ID, endpoint, and optional/required key. The historical snapshot remains available before an LLM is configured.

Choose the exact model ID your provider account or local server exposes. No default model is guessed for Claude or local/other services. OpenAI retains the project's existing `gpt-4.1-mini` default; you may change it.

## Configuration examples

The commands below use no hard-coded hosted model names beyond the pre-existing OpenAI default. Replace model placeholders with available tool-capable model IDs.

### OpenAI

Set `OPENAI_API_KEY` locally or paste it into the GUI. Then:

```bash
quantivex check-llm --provider openai --model YOUR_OPENAI_MODEL
```

### Claude / Anthropic, using its native API

Install the `anthropic` extra and set `ANTHROPIC_API_KEY` locally or in the GUI:

```bash
quantivex check-llm --provider anthropic --model YOUR_CLAUDE_MODEL
```

The adapter translates tool schemas, `tool_use`, parallel `tool_result` blocks, system messages, error results, and the final no-tools turn. It uses `https://api.anthropic.com` by default. AWS Bedrock and Vertex authentication/protocols are outside this adapter; use a separately compatible gateway if appropriate.

Claude defaults to `auto` tool choice because forced tool use is model-dependent. Quantivex still rejects a first response without a tool call. You can try `--tool-choice required` for models that support forced tool use.

### Ollama on this computer

Install/start Ollama and download a **tool-capable** model following Ollama's own instructions. Then:

```bash
quantivex check-llm --provider ollama --model YOUR_INSTALLED_MODEL
```

Endpoint defaults to `http://localhost:11434/v1`. No OpenAI or Anthropic key is needed or reused. The SDK sends a local placeholder token, ignored by a default unauthenticated Ollama server. If your local endpoint requires authentication, set `LOCAL_LLM_API_KEY` or enter it in the GUI.

A local server may also expose cloud-backed models. Select a model actually installed for local inference if offline operation is required; Quantivex cannot certify where a server routes inference internally.

### LM Studio / local vLLM or another local Chat Completions server

Start your server with a tool-capable model and the correct tool-call parser/template configured. Then:

```bash
quantivex check-llm --provider local --base-url http://localhost:1234/v1 --model YOUR_LOADED_MODEL
# Example vLLM port: --base-url http://localhost:8000/v1
```

Local and Ollama modes require `localhost`, 127.0.0.0/8, or IPv6 loopback. For a model hosted on another machine, explicitly choose `openai-compatible` with the remote endpoint and its credentials.

### Another hosted OpenAI-compatible service

Set `QUANTIVEX_API_KEY` for that service, then:

```bash
quantivex check-llm --provider openai-compatible --base-url https://YOUR_PROVIDER/v1 --model YOUR_MODEL
```

This is protocol support, not a blanket certification of every provider. `--tool-choice auto` is available if the service rejects `required`; it still must produce a structured first tool call. There is no silent fallback to OpenAI or another provider after failure.

## What the compatibility check actually verifies

`quantivex check-llm` makes **two small real model requests** when pointed at a real model. Hosted usage may incur a charge. It does not send the calibration snapshot or run chemistry:

1. Require the requested structured function call and validate its arguments.
2. Return a newly generated token as the tool result and verify that the model uses it in its answer.

An unavailable model, invalid key, unsupported tools, malformed call, truncation or incorrect result yields a nonzero exit code. Passing is a basic protocol check, not a guarantee of scientific accuracy or reliable use of all seven agent tools. `quantivex doctor` inspects configuration/dependencies **without contacting a model**.

After a successful check, run a small real workflow:

```bash
quantivex calibration --output torino.json
quantivex ask torino.json 'Summarize this calibration snapshot.' --provider PROVIDER --model MODEL --base-url ENDPOINT
```

For OpenAI/Claude you can omit `--base-url` to use that provider's default. Choose one provider configuration and keep it consistent between commands.

## Environment variables

| Variable | Meaning |
|---|---|
| `QUANTIVEX_PROVIDER` | `openai`, `anthropic`, `ollama`, `local`, `openai-compatible` |
| `QUANTIVEX_MODEL` | Exact model ID; explicit for every non-OpenAI provider |
| `QUANTIVEX_API_KEY` | Optional explicit credential for the chosen provider; overrides provider-specific key |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Hosted credential for the matching provider |
| `LOCAL_LLM_API_KEY` | Optional local-server authentication; cloud keys are not used automatically |
| `QUANTIVEX_BASE_URL` | Explicit endpoint for the selected provider |
| `OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL`, `OLLAMA_BASE_URL`, `LOCAL_LLM_BASE_URL` | Corresponding provider endpoint when no generic override is set |
| `QUANTIVEX_TOOL_CHOICE` | `auto` or `required`; defaults: OpenAI/other hosted `required`, Claude/local `auto` |
| `QUANTIVEX_TIMEOUT` | Request timeout in seconds, default 120 |
| `QUANTIVEX_TOOL_TIMEOUT` | Budget per numerical tool in seconds, default 900 |
| `QUANTIVEX_MAX_TOKENS` | Response-token cap for every request, default 4096 (sent as `max_completion_tokens` to OpenAI, `max_tokens` to other compatible servers and to Claude) |
| `QUANTIVEX_MAX_TOOL_CALLS` | Total allowed calls per question, default 32 |

In the GUI, paste a session key in the sidebar: without OIDC sign-in the GUI deliberately ignores keys saved in the environment (the CLI uses them).

In PowerShell, set variables for the current session with `$env:QUANTIVEX_PROVIDER="ollama"` and `$env:QUANTIVEX_MODEL="YOUR_MODEL"`. In bash/zsh use `export QUANTIVEX_PROVIDER=ollama` and `export QUANTIVEX_MODEL=YOUR_MODEL`. CLI flags override the corresponding environment settings. API keys are not accepted as command-line flags and are omitted from saved run records.

## Test evidence and limits

The test suite uses actual OpenAI and Anthropic Python SDKs with scripted local HTTP servers. It checks all five provider selections, structured request formats, two-turn probe behavior, full agent execution, parallel Claude tool results, malformed responses, credential isolation, and GUI routing. These are protocol/engineering tests with deterministic fake responses.

**The automated tests do not certify any particular live model.** Model quality, context limits, latency, deployment-specific tool parsing and scientific judgement depend on the model you choose. Run `quantivex check-llm` against your actual model, and keep its output with your results.

Version 1.0.3 addresses the covariance, confidence, inference and final-answer acceptance
findings described in the earlier audit. Since 1.2.0 answers lead with a templated summary of computed
outputs; the model's explanation is shown after it, labelled unverified, with unmatched numbers flagged; tool relevance and full task
completion require review. GUI inputs never prefill server secrets, and server credentials
are disabled in the default local GUI (enter a session key instead). Optional configured
OIDC plus an allowed-email list enables server credentials only at the configured endpoint.
The CLI continues to use environment credentials.

Primary protocol references checked 19 October 2026:

- Ollama OpenAI compatibility: https://docs.ollama.com/api/openai-compatibility
- Claude tool definitions and tool choice: https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
- Claude tool-result handling: https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls
