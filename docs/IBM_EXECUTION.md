# IBM quantum hardware execution (v1.2.2)

Quantivex already used Qiskit and Aer to simulate chemistry circuits locally. This version adds
an optional IBM Runtime **SamplerV2** path for running those circuits on an accessible IBM QPU.
Your Python application still runs on your computer; IBM executes the quantum circuits remotely.

| Mode | Quantum execution | IBM account | LLM required |
| --- | --- | --- | --- |
| Simulation assistant | Local Aer using a supplied historical calibration snapshot | No | Yes, for the assistant |
| IBM offline test | Runtime SDK local testing mode with a fake backend | No | No |
| IBM QPU | Named live IBM backend through Runtime | Yes, API key and backend/instance access | No |

This measures a **fixed state optimized locally using noiseless VQE**. Neither the optimizer nor the
LLM autonomously submits hardware iterations. This is not hardware-in-the-loop VQE or evidence of quantum advantage.
Both compact state preparation and the gate-level UCCSD circuit are available; compact preparation uses a
classically computed statevector and does not scale as a general quantum algorithm. The new workflow limits
problems to eight logical qubits and uses the package's existing small STO-3G active spaces.

## Install and test without credentials

From the unpacked project folder:

```bash
pip install -e ".[ibm,gui]"
quantivex ibm prepare demo-job --fake FakeManilaV2 --molecule H2 --bond-length 0.75 --shots 4000
quantivex ibm inspect demo-job
quantivex ibm local-test demo-job --fake FakeManilaV2
```

`demo-job` must not already exist. `FakeManilaV2` provides a small, historically calibrated device with CX gates.
The resulting record says `local_test`; it is a simulation. `torino` can also be used, but large fake devices
can require more memory/time. The bundled historical Torino JSON is not used to transpile real jobs.

The tested IBM SDK is `qiskit-ibm-runtime==0.50.0`; the IBM extra constrains the dependency to `>=0.50,<0.51`.
This integration uses the tested `SamplerV2` API (deprecated by SDK 0.50.0). It does not use the separate new
Executor Sampler introduced in 0.50.0. Do not interchange their result APIs without adapting and testing them.

## Set up real IBM access

Create/sign in to your IBM Quantum Platform account, create an API key and identify an accessible
instance/backend. IBM credentials are separate from your OpenAI, Claude, or local-model configuration.
Backend access, available quota, queues, and charges depend on your IBM instance and plan.

Either use an existing Qiskit saved account, or supply these environment variables in the shell that
starts Quantivex (for example using your shell's secret prompt or credential manager):

| Variable | Meaning |
| --- | --- |
| `QUANTIVEX_IBM_TOKEN` | IBM Quantum Platform API key; never placed in the job folder |
| `QUANTIVEX_IBM_INSTANCE` | Instance CRN/name to select explicitly |
| `QUANTIVEX_IBM_ACCOUNT` | Optional SDK saved-account name, used instead of a token |

Without these, the SDK loads its default saved account. An explicit token selects the
`ibm_quantum_platform` channel. Quantivex does not save tokens or accept them as command-line arguments.
For a trusted personal computer, an optional interactive way to save a named SDK account is:

```python
from getpass import getpass
from qiskit_ibm_runtime import QiskitRuntimeService
QiskitRuntimeService.save_account(
    channel="ibm_quantum_platform",
    token=getpass("IBM API key: "),
    instance=input("IBM instance CRN: ").strip(),
    name="quantivex",
    set_as_default=True,
)
```

That SDK method writes credentials to your local Qiskit account store. Do not commit the store or tokens.

## Prepare, review, submit, retrieve

Replace `YOUR_BACKEND_NAME` with an actual backend accessible to your IBM instance. Preparation contacts
IBM to get the device target/calibration but **does not create a Runtime job**:

```bash
quantivex ibm prepare hardware-job --backend YOUR_BACKEND_NAME --molecule H2 --bond-length 0.75 --shots 4000
quantivex ibm inspect hardware-job
```

Review `plan.json`: backend, logical/physical layouts, native operations, all measurement-group circuits,
gate counts, depths, shots per group, **total** shots, compilation seed, calibration identity/timestamp,
software versions and circuit checksum. Optional `--layout 0 1 2 3` chooses physical qubits; use indices
that are suitable for the chosen device. Without it, Qiskit chooses the layout from that live device's target.

Submission is a separate explicit action. Replace `YOUR_REVIEWED_SHOT_BUDGET` below with an integer
at least as large as the plan's total shots. This budget is a shot-count gate, not a monetary limit.

```bash
quantivex ibm submit hardware-job --confirm --max-total-shots YOUR_REVIEWED_SHOT_BUDGET
quantivex ibm fetch hardware-job
```

`submit` uses job mode, returns promptly with a job ID and does not wait in the queue. `fetch` polls once;
run it again later to retrieve a completed result. It never creates a new job. Runtime's execution-time limit
defaults to 300 seconds; set `--max-execution-time` during preparation to change it. This is not a cost estimate,
queue timeout, or guarantee about quota/billing. This release does not estimate a price or QPU duration;
check your IBM plan and platform usage information before submitting.

Use `quantivex-gui`, then **Workspace → IBM execution**, for offline fake-backend tests without an LLM.
Real IBM preparation, submission and retrieval are CLI-only. The GUI rejects account-backed plans.

## What is saved and computed

- `plan.json` and `circuits.qpy`: measurement definitions and the exact compiled native circuits.
- `job.json`: plan checksum, unique server job tag, backend, job ID and calibration at submission.
- `result.json`: raw logical-register counts, returned shot counts, energy in hartree, sampling standard error,
  and difference from the exact energy of the same small classical chemistry model.

Transpilation uses **the actual backend target**, including native gates and their directions. It does not
substitute the old undirected CZ snapshot model. Each measurement group preserves its logical classical
`meas` register even when routing changes the physical layout. Prepared instructions and backend status
are checked again before submission; if a device change makes them invalid, prepare a new job folder.

The hardware path uses raw counts without historical readout mitigation. Twirling and dynamical decoupling
are explicitly disabled. Each shot's weighted group energy is used to compute the sample variance, including
covariance between simultaneously measured Pauli terms. Group variances are summed assuming independent
sampling. The standard error **only describes sampling**, and excludes readout/gate bias, drift, crosstalk and
correlation across shots/groups. Zero observed variance does not prove zero uncertainty. No broad
TRUSTWORTHY label is assigned to a hardware result. Both simulation and hardware estimators now include within-group covariance. Hardware results include a finite-sample 95% sampling confidence radius (the tighter of Hoeffding and empirical Bernstein, as in simulation).

Checksums detect accidental changes; they are not signatures. Only open QPY/job folders from trusted sources.
Calibration recorded during preparation/submission is not guaranteed to describe the later execution exactly.

## Network interruptions and recovery

The submission receipt is created exclusively before contacting the execution endpoint. Once it exists,
Quantivex refuses another submission from that folder, including after a connection error. This prevents
automatic retries of a possibly accepted job. Separate copied/new folders can still submit another job;
this is not a global IBM idempotency guarantee.

If `job.json` has no job ID, find its unique `job_tag` on the IBM Quantum dashboard. Once you find the
existing job, attach its ID without submitting anything:

```bash
quantivex ibm recover hardware-job --job-id THE_EXISTING_IBM_JOB_ID
quantivex ibm fetch hardware-job
```

Recovery verifies the server job's backend and tag. If no job is found, investigate with IBM before preparing
another submission; there is no automatic "unlock and retry" action.

## Validation limits

Automated tests exercise the actual SDK locally on FakeManilaV2 and Aer, measurement basis/bit order,
energy vs exact statevectors, covariance, integrity checks, GUI preparation, CLI execution, and injected remote
submission/recovery contracts. Additional target compilation checks cover CZ and ECR devices.
Automated tests cover local SDK execution and injected remote-service contracts.
In addition, a user-authorized acceptance study successfully exercised IBM
authentication, submission, queueing and result retrieval on Fez, Kingston and
Marrakesh. The supplied evidence contains ten completed jobs and 50,000 total
shots; see `README.md` of `Quantivex_IBM_INPUTS_v1.3.0.zip` (see DATA_AND_OUTPUT_LOCATIONS.md).

This limited acceptance study does not validate billing behavior, every account
or instance configuration, network-failure behavior, long-term backend
availability, scientific hardware generalization, or the language model's
scientific judgment.

## Official references (checked 19 October 2026)

- [IBM credentials](https://quantum.cloud.ibm.com/docs/en/guides/save-credentials)
- [SamplerV2 API](https://quantum.cloud.ibm.com/docs/en/api/qiskit-ibm-runtime/sampler-v2)
- [Local testing mode](https://quantum.cloud.ibm.com/docs/en/guides/local-testing-mode)
- [Sampler input/output](https://quantum.cloud.ibm.com/docs/en/guides/sampler-input-output)
- [Runtime job API](https://quantum.cloud.ibm.com/docs/en/api/qiskit-ibm-runtime/runtime-job-v2)

## Integrity and acceptance (unchanged since 1.0.3)

Plans use schema 2; prepare older folders again. Review `instance` as well as backend.
Submission rejects a resolved instance that differs from the reviewed plan. QPY metadata
binds groups, integrals, state parameters and reference context. This detects inconsistent
edits, not malicious replacement of an entire trusted-local folder. Tokens are not saved.
Raw counts, available primitive/publication metadata, backend properties and available
job metrics are retained. Metrics may be unavailable even when counts were retrieved.
Live IBM authentication, queueing and physical-QPU result retrieval were
exercised in the supplied ten-job acceptance evidence. Billing behavior,
identity-provider variants, failure recovery against IBM's live service and
long-term backend compatibility remain unverified. SDK 0.50.0 emits SamplerV2
deprecation warnings; migration to the new Executor interface needs a
separately tested change.
