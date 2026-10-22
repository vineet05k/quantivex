# Quantivex 1.2.2: purpose, method and limits

## Version 1.2.2 hardware acceptance

A user-authorized acceptance study completed ten IBM physical-QPU jobs:
one initial Fez pilot and nine controlled technical replicates across Fez,
Kingston and Marrakesh. The evidence records 50,000 total shots and is described
in `README.md` of `Quantivex_IBM_INPUTS_v1.3.0.zip` (see docs/DATA_AND_OUTPUT_LOCATIONS.md). This establishes that the repaired submission
and retrieval path worked for those jobs; it does not establish chemical
accuracy, quantum advantage, hardware generalization or production readiness.

## What the software is

Quantivex is a Python research demonstrator for investigating how a calibration-derived
noise model and physical-qubit placement affect small quantum-chemistry calculations.
It connects electronic-structure calculations, Qiskit compilation, Aer simulation,
visualization, optional language-model tool planning, and separately authorized IBM
Runtime measurements. It is not a validated production chemistry system.

The intended user is a researcher or learner who wants inspectable numerical evidence:
what molecular problem was calculated, which qubits were selected, what the simulated
or measured energy was, and how sampling uncertainty was quantified.

## What question it tries to answer

For a fixed, small molecular state, do different candidate physical-qubit layouts
produce different energy estimates under the chosen noise model? Does a simple
calibration score associate with those errors in an exploratory comparison?

The package also makes these operations accessible through natural-language requests.
It does not establish that its ranking predicts unseen real-device performance, that
it beats the Qiskit compiler, or that a language model understands the scientific task.

## Three ways to use it

| Workflow | What executes | Credentials needed |
| --- | --- | --- |
| Numerical Python / benchmark | Local electronic structure and Aer simulation | None after installation |
| Simulation assistant | An LLM plans local numerical/visualization tools | Hosted provider key, or an already running local tool-capable model |
| IBM execution CLI | Local optimization/compilation, then a separately confirmed remote fixed-state measurement | None for fake-backend tests; IBM account access for a physical QPU |

OpenAI and native Anthropic protocols are supported, along with loopback OpenAI-compatible
servers such as Ollama, LM Studio and vLLM, and other compatible hosted providers.
Compatibility is conditional on the model/server implementing the required tool protocol.
No model weights are bundled. `quantivex check-llm` tests the selected deployment's tool
round trip; local SDK/protocol tests are not live model-quality certification.

The GUI provides the simulation assistant and offline IBM SDK tests. Real account-backed
IBM operations are intentionally CLI-only. Hosted providers receive questions and
summary tool outputs. Raw measurements, integrals and QPY payloads remain in local
records rather than being included in tool responses sent back to the model.

## How the scientific calculation works

1. **Read calibration data.** The bundled historical IBM Torino snapshot is dated
   26 February 2025. It contains qubit readout errors, coherence information, gate
   errors, connectivity and durations. These are calibration parameters, not a
   dataset of historical molecular QPU measurements. The input graph is validated,
   size-bounded and made immutable. Custom snapshot simulation supports symmetric
   native CZ semantics; incompatible CX/ECR exports are rejected.
2. **Define a small chemistry problem.** H2, N2 and LiH are supported in specified
   STO-3G active spaces. Standard geometry integrals are cached; off-grid cases need
   PySCF. The reported reference is the CASCI singlet in the Hartree-Fock spatial
   symmetry. It is not necessarily the lowest unrestricted electronic state.
3. **Map and optimize.** Jordan-Wigner mapping creates a qubit Hamiltonian. Classical
   noiseless optimization prepares a UCCSD variational state. Eight-qubit optimization
   uses an analytic reverse-mode energy gradient checked against central differences;
   four-qubit benchmarks retain their finite-difference path. Its ideal spin expectation
   is checked against the singlet target. VQE and noisy workflows are limited to eight
   logical qubits; available 12-qubit cache entries are reference-only.
4. **Choose physical qubits.** Simple connected paths are enumerated and scored using
   a circuit-agnostic product of calibration success factors. This is a heuristic,
   not a calibrated probability that an entire chemistry experiment succeeds. When
   readout mitigation is on, readout factors are left out of the score (gate-only score),
   because mitigation removes most of the readout effect: on the Torino N2 benchmark
   the gate-only score tracks the mitigated error with Spearman 0.94-0.96, the full
   score with only 0.16-0.38.
5. **Compile and measure.** The same optimized state is compiled using compact state
   preparation or the explicit UCCSD circuit. Aer samples commuting measurement
   groups under an approximate calibration-driven noise model. Qubits missing T1 or T2
   are handled explicitly (missing T2 -> 2*T1, missing T1 -> pure dephasing, neither ->
   no relaxation) and reported in each result's `model_notes`; a partner's known
   relaxation is never dropped. A comparison uses
   a consistent initial layout across its groups; readout factors follow each group's
   actual final physical layout after routing.
6. **Estimate energy and uncertainty.** Each shot contributes a weighted grouped
   energy, so the variance includes covariance between simultaneously measured terms.
   Optional readout rescaling assumes fixed, independent symmetric readout errors.
   The 95% sampling radius is the smaller of a Hoeffding bound and an empirical-Bernstein
   bound (Maurer & Pontil 2009), each at alpha/2 and combined across groups. It adapts to the
   observed variance, stays nonzero when rare outcomes were not observed, and its coverage
   is checked by exact binomial enumeration and by repeated runs.
7. **Compare with the reference.** The labels describe sampling-limited agreement
   with the stated small-model reference. They do not certify physical chemical
   accuracy. Calibration uncertainty, mitigation bias, drift and molecular-model
   error are outside that interval.

Compact state preparation uses a classically computed statevector. The optimizer
is local and noiseless in both simulation and IBM workflows. Real hardware measures
a fixed state; this is not hardware-in-the-loop VQE or a demonstration of quantum advantage.

## What the LLM does, and what Python decides

The LLM can select seven allowlisted tools: dataset summary, qubit-chain ranking,
chemistry calculation, layout comparison, energy curve, chip map and Quantum Blur.
Arguments must satisfy strict schemas and finite-number checks. Numerical tool workers
have a wall-clock timeout; POSIX workers also have CPU and memory limits.

The displayed answer starts with a plain-English summary built from fixed sentence
templates filled only with tool outputs. The model's freeform explanation follows,
labelled **unverified**. A heuristic lists every number in it that equals no numeric tool
output after rounding to the written precision (question text and prose notes are not
evidence; signs must match except for error magnitudes). It cannot tell which quantity a
number refers to, so the absence of flags does not verify the explanation. The raw tool outputs come last. Zero successful tools produce a failed run. An irrelevant successful
tool does not prove that the user's requested task was completed; that remains a review
step, explicitly reflected in the record.

Quantum Blur is an existing image transform used to visualize the calibration map.
It does not enter the chain score or the energy estimator, and no selection benefit is claimed.

## Statistical interpretation

Selected best/worst chains and the compiler baseline are descriptive comparisons.
The reported Spearman correlation uses only uniformly sampled random chains, with an
exact two-sided permutation calculation for up to eight observations and a seeded
Monte Carlo permutation calculation for larger supported subsets. The p-value assumes
exchangeability; the study is exploratory, and repeated geometries sharing chains are
not independent replications. Two-sigma error bars alone are not a significance test.

## What is saved

Assistant folders contain `record.json`, `report.md`, `events.jsonl` and generated
figures. Records include settings without keys, environment versions, the complete
snapshot, tool calls/results and model transcript. Noisy measurement records retain
integrals, optimized parameters, raw counts, group definitions and exact compiled QPY
bytes encoded as base64. Numerical curve inputs are retained as well.

IBM folders contain `plan.json`, `circuits.qpy`, and after submission `job.json`; returned
counts and available execution metadata go into `result.json`. Plans bind numerical
context and groups to circuit metadata, and record/check the actual instance as well
as backend. Submission requires explicit confirmation and a total-shot budget. An
uncertain submission stays locked for recovery rather than automatic resubmission.
These are local integrity/reproducibility controls, not signed experimental attestations.
Only load trusted QPY folders. Older plan versions must be prepared again.

## Contribution and remaining evidence gaps

The supported contribution is an inspectable integration and educational/research
workflow. Its major components have prior art. Substantial scientific novelty,
publication readiness, reliable hardware generalization and superiority over existing
mapping approaches have not been established by the repository or these repairs.

A stronger research contribution would need a defined hypothesis, prospective held-out
devices/dates, repeated independent runs, suitable tuned baselines, model-mismatch
analysis, and actual model-task evaluations if the assistant is part of the claim.

The accompanying audit states which checks were executed for this release. Live hosted
or downloaded models, real IBM authentication/queues/billing/QPU execution, production
OIDC deployment, Docker execution, and Windows/macOS runtime acceptance remain separate
checks. There is no claim that every possible defect has been eliminated.

## Files to read next

- `README.md`: installation and executable commands.
- `docs/IBM_EXECUTION.md`: prepare, inspect, authorize, fetch and recover.
- `docs/LLM_PROVIDERS.md`: provider/server configuration and compatibility diagnostic.
- `docs/benchmark.md`: regenerated simulator study (regenerate the full `benchmark.json` with `scripts/benchmark_layouts.py`).
- `docs/AUDIT_REPORT.md`: remediation evidence and unresolved limitations.
- The companion installation/how-to and results PDFs: practical quick guides.
