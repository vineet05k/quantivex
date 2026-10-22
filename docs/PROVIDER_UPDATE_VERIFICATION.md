> Historical verification record for an earlier release. Current acceptance is in docs/AUDIT_REPORT.md.

# Quantivex 1.0.1: provider and calibration update

Date: 19 October 2026. Based on the supplied Quantivex 1.0.0 ZIP. This is a scoped provider/access update, not the missing v1.1 implementation described in the external PDF guides.

## Implemented

- User-selected OpenAI, native Claude/Anthropic, Ollama, local OpenAI-compatible servers (such as appropriately configured LM Studio/vLLM), and other compatible hosted endpoints.
- GUI and CLI provider/model/endpoint selection; separate native Claude credentials; no automatic use of cloud credentials in local modes.
- Native Anthropic system/schema/tool-use conversion, parallel results, tool errors, output truncation handling, and no-tools finalization.
- Loopback transport bypasses environment proxies and does not follow redirects to another endpoint. Remote servers must be selected explicitly using the compatible-provider mode.
- A two-request `quantivex check-llm` diagnostic for the actual model; structured-call and argument checks plus a random-token tool-result round trip. Hosted tests incur actual usage when run by a user.
- Runtime JSON-schema validation, a total tool-call budget, and a first-response tool requirement.
- GUI shot settings and stale-result handling corrected.
- Bundled historical Torino JSON exposed in installed resources, GUI download, and offline CLI export. The original snapshot is preserved byte-for-byte, including its historical provenance.
- Installed GUI launcher and complete source-distribution test fixtures. SDK versions are recorded in run metadata.

## Verification actually performed

| Check | Result |
|---|---|
| Added provider/GUI tests | **49 passed** in the complete regression run |
| Final focused run: all 49 additions plus the original report-writing integration test | **50 passed**, 31.28 seconds |
| Full 156-test regression, including original numerical tests | **154 passed, 2 failed** before numerical computation because this executor could not read PySCF's `/proc/<pid>/statm` process-memory path |
| Rerun both affected PySCF tests with equivalent `/proc/self/statm` memory counters | **2 passed**, 1.89 seconds; no scientific formulas or numerical tolerances changed |
| Measured coverage in the full run | **93.39%** of package statements; GUI `app.py` remains excluded by existing configuration |
| Ruff lint and formatting | Passed |
| Project type check | Passed for 21 source files with third-party imports skipped; the validation invocation disabled psutil affinity probing because of the same executor PID/proc issue |
| Wheel and source distribution build | Passed |
| Built wheel, outside source directory | CLI version, offline calibration export, 133-qubit resource loading and installed GUI AppTest passed |
| Generated source distribution | All 156 tests collected; missing-fixture issue corrected |

The initial full-run failures and the successful reruns are both retained in `validation/`; they are not rewritten as a clean single-run pass. The PySCF executor workaround is included as a validation reference, not installed as product code. The actual OpenAI and Anthropic SDKs were exercised through scripted local HTTP servers. These tests check serialization, headers, tool messages, argument limits, result handling, record writing, and error behavior.

**No real OpenAI or Claude model, no downloaded local LLM, and no quantum hardware were used.** Therefore this is protocol-tested integration, not a blanket certification of all providers/models. Run the included diagnostic with your actual chosen model. Windows/macOS execution, live-model quality, context limits, latency and scientific reasoning remain unverified.

## Included data

- `examples/ibm_torino_2025-02-26.json` already existed in the original ZIP.
- This release adds an identical installed package resource, with license/attribution notices.
- 133 qubits, 150 links; calibrated 2025-02-26T15:26:08-05:00.
- Raw file SHA-256: `ae34d9a98c99d3109daed45bb19ec37c247d1663eb3e4def3b9463924ef8c2fc`.
- No IBM account, LLM API key or network is required to inspect/export it. Regeneration from `FakeTorino` is documented, but no new download is necessary.

## Local use

Create and activate a Python environment in the extracted `quantivex` folder, then:

```bash
python -m pip install -e ".[gui,anthropic]"
quantivex-gui
```

Select the provider and exact model ID in the sidebar. A local server/model must already be installed and running. There are no model weights bundled in this repository.

```bash
# Check an installed tool-capable Ollama model (no cloud key):
quantivex check-llm --provider ollama --model YOUR_INSTALLED_MODEL

# Inspect or export the included data entirely offline:
quantivex calibration
quantivex calibration --output torino.json
```

Use `docs/LLM_PROVIDERS.md` for OpenAI, native Claude, custom hosted services, LM Studio/vLLM and environment variables. Use `docs/CALIBRATION_DATA.md` for data provenance and regeneration.

## Scientific findings still open

The covariance omission in grouped uncertainties, validity of score-correlation inference, scope of trust verdicts, and faithfulness of final LLM prose remain open from the original audit. This update does not change the numerical chemistry/noise algorithms, establish hardware validity, demonstrate scientific novelty, or certify every natural-language answer. The README/report wording now qualifies the exploratory correlation evidence.
