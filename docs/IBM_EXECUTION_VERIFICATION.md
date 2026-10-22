> Historical verification record for the earlier local/injected-service release.
> Its statements about missing live-QPU testing describe the evidence available
> at that time. Quantivex 1.2.2 subsequently completed a user-authorized ten-job
> IBM hardware acceptance study; see `README.md` of `Quantivex_IBM_INPUTS_v1.3.0.zip` (see docs/DATA_AND_OUTPUT_LOCATIONS.md).

# Quantivex 1.0.2: optional IBM execution verification

Date: 19 October 2026. This update extends the supplied 1.0.1 provider/data release.
The original audit and provider verification retain their original scope; this is not a claim that
every scientific issue in the project has been resolved.

## Implementation

- CLI: `ibm prepare`, `inspect`, `submit`, `fetch`, `recover`, `local-test`.
- GUI: independent IBM execution workspace, usable without an LLM key.
- Actual backend target/native gates, optional initial physical layout, QPY persistence, checksums,
  group/total shot budgets, calibration identifiers/timestamps and SDK versions.
- Explicit submission, active-backend/instruction checks, exclusive durable receipt, unique server tag,
  job-ID recovery, and polling without automatic resubmission.
- Raw energy estimator uses each group's per-shot weighted energy, including covariance; returns
  actual shot counts and sampling standard error. No old-snapshot readout mitigation or broad trust verdict.
- IBM credentials are loaded only by the IBM SDK from the IBM-specific environment or saved account.
  They are not saved in plan/result files or obtained from LLM API credentials.

## Verification performed

Selected test command:

```bash
PYTHONPATH=src python -m pytest tests/test_ibm_execution.py tests/test_provider_gui.py \
  tests/test_cli_config.py tests/test_data.py tests/test_experiment.py -m 'not slow' -q --disable-warnings
```

Result: **68 passed, 1 deselected, 8 warnings in 37.86 seconds**. Includes **28 new IBM workflow tests**.
See `validation/ibm_regression.txt`.

The tests cover:

- Actual Runtime SamplerV2 local execution on FakeManilaV2 and Aer, including the same options schema
  used for hardware. Legacy local mode ignores suppression options; those warnings are expected,
  and no local test is claimed to verify server-side suppression behavior.
- Real target compilation on FakeManilaV2 (CX), FakeTorino (CZ) and FakeBrisbane (ECR).
- QPY round trips, explicit noncontiguous physical layouts, routing and logical classical bit order.
- H2 sampled energy vs exact small-model reference, independent X/Y/Z basis checks vs statevectors.
- Positive and negative within-group covariance, identity terms, independent groups, count validation,
  actual shot counts, invalid layouts/options, damaged plans/circuits.
- Injected remote-service contracts: confirmation/budget gates, inactive backend and changed target,
  saved job ID, queue polling, counts retrieval, mutation detection, uncertain submission/recovery,
  backend/tag validation and prevention of a second execution call from the same job folder.
- GUI offline preparation, separation from LLM keys, disabled live submission without authorization,
  and reset of authorization when a different plan is selected.
- Existing simulation estimator, historical-data/CLI and provider-GUI regressions.

`ruff check src tests scripts` and `ruff format --check src tests scripts`: passed (37 files formatted).
Project type checking: passed, **23 source files**. The executor's process inspection issue required
disabling mypy's psutil use and running one worker, with `--follow-imports=skip --no-incremental` and
a fresh cache; third-party libraries were not type-checked. Logs: `validation/ibm_lint.txt`, `validation/ibm_mypy.txt`.

Wheel and source distribution built successfully. Installed-wheel smoke testing outside the source tree
verified version 1.0.2, bundled calibration inspection, fake-backend preparation and actual Runtime local
execution. The wheel's Python modules were checked against the source files and parsed using Python 3.10
syntax rules. This is not a Python 3.10 execution test. Logs: `validation/ibm_build.txt`,
`validation/ibm_wheel_install.txt`, `validation/ibm_wheel_smoke.txt`.

Environment: Linux / Python 3.12; Qiskit 2.6.0, IBM Runtime 0.50.0, Aer 0.17.2, NumPy 2.5.4,
pytest 9.1.1, Streamlit 1.66.0. The optional IBM dependency is constrained to `>=0.50,<0.51`.

## What remains unverified

At the time of this historical verification, no live IBM credentials,
authentication, account entitlements, real queue, quota/billing behavior,
network-failure recovery or physical-QPU execution had been tested. Those
historical local and injected-service tests remain valid for their stated
scope. A later user-authorized Quantivex 1.2.2 acceptance study exercised
authentication, submission, queueing and retrieval for ten physical-QPU jobs.
Billing behavior and live network-failure recovery remain unverified.

VQE parameters remain locally/noiselessly optimized. Compact state preparation uses a classical
statevector. Sampling errors exclude device bias/drift and correlations across shots/groups. The exact
reference is only the same small STO-3G active-space model. The current simulation and IBM hardware estimators include within-group
covariance. Historical benchmark records retain the estimator version stated
in their own provenance.
Previous statistical/novelty limitations and unverified LLM final prose remain open.

The entire older physics suite was not rerun for this scoped update. Historical `validation/` logs without
an `ibm_` prefix refer to the earlier 1.0.1 provider update and must not be presented as a new full-suite run.

## Reviewable local test

```bash
pip install -e ".[ibm,gui]"
quantivex ibm prepare demo-job --fake FakeManilaV2 --shots 4000
quantivex ibm local-test demo-job --fake FakeManilaV2
```

For real-device requirements, explicit submission and recovery instructions, see `docs/IBM_EXECUTION.md`.
