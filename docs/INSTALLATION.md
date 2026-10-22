# Installation and local-model setup

Quantivex supports Python 3.10–3.13. Python 3.12 is the recommended baseline
for the tested Qiskit, Aer, PySCF and IBM Runtime combination. Python 3.14 is
not a supported release target for this version.

## Linux or WSL with `uv`

From the directory containing `pyproject.toml`:

```bash
uv python install 3.12
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install ".[gui,ibm,anthropic]"
quantivex version
quantivex doctor
```

If PySCF reports `cannot allocate memory in static TLS block` under WSL, set
the glibc loader allowance in that terminal before starting Quantivex:

```bash
export GLIBC_TUNABLES="glibc.rtld.optional_static_tls=32768"
```

This is a process-environment workaround for the native library loader; it is
not an API key or a Quantivex scientific setting.

## External data directories

Quantivex does not write generated records into the checkout by default:

```text
~/QuantivexData/results/
~/QuantivexData/simulation_runs/
~/QuantivexData/hardware_runs/
```

To use a different root:

```bash
export QUANTIVEX_DATA_DIR="$HOME/my-quantivex-data"
```

See [DATA_AND_OUTPUT_LOCATIONS.md](DATA_AND_OUTPUT_LOCATIONS.md) for the example input and result
archives and all output settings.

## Ollama without a hosted LLM key

Confirm that Ollama is reachable and that the selected model is installed:

```bash
curl -fsS http://127.0.0.1:11434/api/tags
```

Then configure Quantivex:

```bash
export QUANTIVEX_PROVIDER=ollama
export QUANTIVEX_MODEL=quantivex-qwen
export QUANTIVEX_BASE_URL=http://127.0.0.1:11434/v1
export QUANTIVEX_MAX_TOKENS=4096
export QUANTIVEX_TIMEOUT=600
export QUANTIVEX_TOOL_TIMEOUT=900

quantivex check-llm
```

Use the exact Ollama model ID available on the local server. The compatibility
check verifies a basic structured-tool round trip; it does not certify the
model's scientific judgement.

## Smoke test

Export the installed historical Torino example to the external data directory:

```bash
mkdir -p "$HOME/QuantivexData/calibration_inputs"
quantivex calibration \
  --output "$HOME/QuantivexData/calibration_inputs/ibm_torino_2025-02-26.json"
```

Run a summary:

```bash
quantivex ask \
  "$HOME/QuantivexData/calibration_inputs/ibm_torino_2025-02-26.json" \
  "Use get_dataset_summary to summarize this calibration snapshot."
```

The printed report and record paths should be under
`~/QuantivexData/simulation_runs/` unless an override was set.

## Optional IBM support

Installing the `ibm` extra enables explicit prepare, inspect, submit, fetch and
recover commands. It does not authorize or submit a physical-QPU job by itself.
See [IBM_EXECUTION.md](IBM_EXECUTION.md). Real submissions remain CLI-only and
require `--confirm` plus an explicit total-shot budget.
