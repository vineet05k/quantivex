# Quantivex 1.2.1 - response to the independent re-audit of 1.2.0

> Scope note for Quantivex 1.2.2: this document preserves the historical 1.2.1
> re-audit and therefore retains statements about evidence that was unavailable
> at that time. Subsequent IBM hardware acceptance evidence is documented in
> `README.md` of `Quantivex_IBM_INPUTS_v1.3.0.zip` (see DATA_AND_OUTPUT_LOCATIONS.md). The later evidence does not change this
> report's scientific and generalization limitations.


**Date:** 20 October 2026. The re-audit ("Quantivex v1.2.0 - independent forensic re-audit", 19 October 2026)
reported ten findings (seven medium, three low) and no critical or high finding. All ten were confirmed by
reading the source, fixed, and given regression tests (`tests/test_audit_v120.py`). The auditor's reproduction
probes were re-run against 1.2.1 (`validation/v1.2.1/audit_probes.py` and `.log`).

| Finding | Confirmed? | Fix in 1.2.1 | Evidence now |
| --- | --- | --- | --- |
| Q120-01 mutable cached excitations (21 mHa silent drift) | yes | private cache, defensive copies; every circuit checked against the optimiser energy (1e-6 Ha) | probe drift 3.6e-12 mHa; deliberate 21 mHa mismatch raises |
| Q120-02 prose checker over-reassures | yes | question and prose strings are not evidence; signs matter (except error magnitudes); displayed-precision rounding only; small integers and scientific notation checked; reassurance reworded | 777.7 Ha flagged; no "every number" claim; 9 adversarial tests |
| Q120-03 partner relaxation dropped | yes | per-qubit channel with identity for missing partner; missing T2 -> 2*T1, missing T1 -> dephasing; `model_notes` | P(excited) = exp(-1.032) in both qubit orders |
| Q120-04 OpenAI token cap not sent | yes | `max_completion_tokens` (OpenAI) / `max_tokens` (compatible servers) on every request | serialized HTTP request bodies captured (`http_request_parameters.log`) |
| Q120-05 mitigation fallback vs ranking | yes | one shared eligibility rule; gate-only ranking excludes unmitigable chains; fallback reported per row | probe returns `readout_mitigation=false`, `requested=true` and a note |
| Q120-06 guide vs GUI key policy | yes | GUI says "paste a session API key"; guides, README, provider doc and Docker line corrected | AppTest with synthetic env key |
| Q120-07 2-sigma presented as significance | yes | 2-sigma described as descriptive everywhere; exact paired sign-flip test added to the benchmark | p = 0.50-0.56; 5-seed minimum 0.0625 stated |
| Q120-08 skips counted as passes; V3/V7 controls; 25% vs 30% | yes | PASS/FAIL/SKIP with essential checks; V3 uses identical compiled circuits; V7 varies only the sampler seed and compares with an exact density-matrix value; README states 30% | `validate_results.log`: 7 passed, 0 failed, 0 skipped; exact noisy error 39.49 mHa |
| Q120-09 recorded 300 s budget | yes | records configured wall, CPU and memory limits | test with 1234 s; e2e record shows 900 |
| Q120-10 rho sign ambiguous | yes | `cost_vs_error_spearman_rho` (+1 good), `esp_vs_error_spearman_rho` (-1 good), `rho_meaning`; wording fixed | narrative test |

Note on Q120-07: the paired test was chosen after the five seeds had already been run once, which the benchmark
states. It confirms rather than changes the earlier conclusion (no demonstrated win over Qiskit).

### Checks executed for 1.2.1 (all files in `validation/v1.2.1/`)

| Check | Result |
| --- | --- |
| Test suite (Python 3.13, Linux) | 249 passed, 0 failed (`pytest.log`, `pytest.xml`) |
| ruff lint and format; mypy | clean; 43 files formatted; no issues in 25 source files |
| `validate_results.py` | 7 passed, 0 failed, 0 skipped |
| CLI end to end, scripted server, three provider modes | summary + flagged invented number; token cap in every serialized request |
| GUI end to end (Playwright) | answer, warning, evidence expander, second session |
| Clean-environment wheel install outside the source tree | see `clean_wheel_install.log` |

### Still not verified (unchanged)

Real hosted or local models; Windows, macOS, Python 3.10-3.12; Docker; OIDC deployment; real IBM hardware;
held-out devices or dates for the chain score; any agent-quality evaluation. The auditor's scientific
disposition stands: an inspectable research-software demonstrator with a strong within-simulator association,
not a validated device-noise predictor, not a demonstrated improvement over Qiskit, and not novel chemistry.

---

# Quantivex 1.2.0 - merge review and verification

**Date:** 20 October 2026. Scope: 1.2.0 merges the 1.1.0 line into the independently audited 1.0.3
release and fixes the problems found while reviewing 1.0.3. Every check below was executed for this
release; the log or file named in the last column is included in `validation/v1.2.0/`.

| Check | Result | Evidence |
| --- | --- | --- |
| Full test suite (Python 3.13, Linux) | 229 passed, 0 failed, 0 skipped, 97 s | `pytest.log`, `pytest.xml` |
| Lint, format, types | ruff clean, 42 files formatted, mypy clean (25 files) | `ruff.log`, `mypy.log` |
| Independent scientific checks V1-V7 | 7/7 pass | `validate_results.log` |
| Clean-environment wheel install, run outside the source tree | `quantivex version` 1.2.0, `doctor`, offline calibration export, agent `ask`, IBM `prepare` + `local-test` | `clean_wheel_install.log` |
| CLI agent end to end (scripted OpenAI-compatible server) | summary, flagged invented number (12.34), evidence | `cli_end_to_end.log`, `fake_llm_server.py` |
| GUI end to end (Playwright, Chromium) | question to answer, warning for the invented number, evidence expander, second session | `gui_end_to_end.png`, `gui_playwright_check.py` |
| Tested dependency versions | pip freeze | `tested_environment.txt` |

The CLI log was captured before a final wording change in the summary (the comparison sentence now says
"Error reduction of the calibration-best chain over Qiskit's layout"); the wheel and GUI checks used the final text.

## Issues found in 1.0.3 and how 1.2.0 handles them

| Issue in 1.0.3 | Effect | 1.2.0 change | Verified by |
| --- | --- | --- | --- |
| Hoeffding-only radius (66-122 mHa on Torino N2) | Every realistic result was `INCONCLUSIVE_95`, so the verdict carried no information | Radius = min(Hoeffding, empirical Bernstein), each at alpha/2: 37-49 mHa; best-chain results become `OUTSIDE_TOLERANCE_95` | exact binomial coverage tests (2-4000 shots); V7: 30/30 repeated runs covered |
| Full chain score with readout mitigation on | rho 0.16-0.29 with mitigated error (not significant) | gate-only score when mitigation is on (1.1.0 finding) | benchmark: rho 0.94-0.96, permutation p <= 1e-4; V5: 0.98, 0.98, 0.90 |
| 6 random chains by default | correlation could never be reliable | 15 by default (5 at 8 qubits), `correlation_reliable` flag | `docs/benchmark.md` |
| Answer showed only JSON evidence | correct but unreadable for non-experts | templated plain-English summary first; model text labelled unverified with unmatched numbers flagged; evidence last | `tests/test_narrative.py`, e2e checks |
| Fixed 300 s tool budget | 8-qubit layout comparison took 294 s with 5 chains on this machine; 15 chains could not finish | `QUANTIVEX_TOOL_TIMEOUT`, default 900 s, CPU limit follows | manual 8-qubit run; config test |
| mypy error in `ibm_gui.py` | type check failed | explicit `str()` conversion | `mypy.log` |
| Audit cited files that were not shipped (`final_gui_suite.log`, `analytic_gradient.log`, `final_payload_regression.log`, `independent_science.json`, `ibm_boundary_probe.json`, `installed_plan.log`, `installed_result.log`, `release_manifest.json`, `evidence/...`) | claims could not be checked from the archive | 1.0.3 evidence moved to `validation/v1.0.3/` unchanged; its claims about missing files remain unverified; 1.2.0 cites only shipped files | this table |
| 1.0.3's own final suite recorded 217 passed, 1 failed | not an all-pass release | 1.2.0 suite: 229 passed, 0 failed | `pytest.log` |

## Not verified for 1.2.0

- A real hosted or local language model (only a scripted OpenAI-compatible server). Run `quantivex check-llm` and one real question before relying on it.
- Windows and macOS, Python 3.10-3.12, Docker, OIDC deployment.
- Real IBM hardware, authentication, queues and billing (only fake-backend local tests).
- An 8-qubit noisy run shows errors above 1 Ha on every chain: such comparisons are mechanically correct but chemically uninformative.

This is engineering verification of a research tool. It is not a scientific certification and does not show
quantum advantage or physical chemical accuracy.

---

# Historical: 1.0.3 audit as delivered (evidence paths refer to that release; see above for missing files)


**Date:** 19 October 2026. **Decision:** the reproduced software defects have received concrete changes and local acceptance checks. This is a more defensible local research demonstrator; it is not scientific certification, a claim of zero defects, or clearance for production/shared deployment.

This report concerns the revised 1.0.3 source and accompanying distribution. The original 1.0.2 ZIP was preserved, SHA-256 `391b9cc6b625e626d059ed20368f2fd741eaa8801d17b04e6486ca0533317e94`. Package and per-file hashes for this release are in the accompanying release manifest. The report intentionally does not contain its own archive hash, avoiding a circular artifact dependency.

## 1. Key facts with evidence

| Check | Executed result | Evidence |
| --- | --- | --- |
| Broad ordinary suite, final numerical implementation | 217 passed, 1 failed, 0 errors, 0 skipped; 535.01 s | `evidence/complete_release_suite.log`, `.xml` |
| Earlier complete ordinary suite, before final cache guards | 213 passed, 0 failed, 0 errors, 0 skipped; 890.67 s | `evidence/baseline_213_suite.log`, `.xml` |
| Final GUI acceptance after bounded-wait correction | 9 passed, 0 failed, 0 errors, 0 skipped; 52.83 s | `final_gui_suite.log`, `.xml` |
| Final analytic gradient acceptance | Three checks passed, including independent central differences and eight-qubit energy accuracy (14.59 s) | `analytic_gradient.log` |
| Final payload/report filtering check | End-to-end layout/curve flow verifies raw numerical inputs remain local while computed summaries go to the model | `evidence/final_payload_regression.log` |
| Independent fermion construction | Four occupation-bit matrices, including 8 qubits; maximum element error 4.83e-13 Ha | `independent_science.json` and replay script |
| Entire integral cache | All 170 entries independently checked; HF maximum difference 1.14e-13 Ha; singlet FCI maximum difference 7.09e-09 Ha | `independent_science.json`, `singlet_cache.json` |
| Fresh geometry-to-integrals calculations | Five cases, including 12-qubit N2, independently recomputed with PySCF | `independent_science.json` |
| Layout statistics | Independent enumeration of all 720 permutations per geometry agrees with rho 0.486 and p 0.3556 | `independent_permutations.json` |
| Wheel integrity | All 28 installed package/resource files match active source; installed-wheel offline IBM workflow executed outside source | `wheel_integrity.json`, `installed_plan.log`, `installed_result.log` |
| Real SDK execution boundary | Actual IBMBackend + SamplerV2; five circuits x 128 shots, 640 total; one service call, correct pinned instance | `ibm_boundary_probe.json` |
| Static checks | Ruff lint/format and mypy pass; see qualifications below | `ruff.log`, `format.log`, `mypy_final.log` |
| Dependencies | pip-audit 2.10.1: 79 dependency entries, 0 returned advisories, 0 skipped entries. This checks known advisories only. | `dependency_audit.json`, `runtime_dependency_pins.txt`, `pip_check.log` |
| Guides | Installation/how-to exactly 2 pages; results guide 4 pages; all rendered pages visually reviewed | `pdf_checks.json` |

The SDK boundary test mocks the service execution method and uses synthetic job ID `AUDIT_JOB_NOT_REAL`. It is not a physical hardware experiment. No real credentials, live LLM, production identity provider or IBM job were used.

### Interpreting the PySCF test results

The executor intermittently exposes a process under a different `/proc` PID mapping from `os.getpid()`. PySCF's memory estimator then raises FileNotFoundError before a fresh SCF calculation. The first complete remediation run had **208 passes and two such failures**. The earlier complete ordinary suite subsequently passed all 213 tests without the adapter. The final release suite above records the later exact result, without silently treating environment failures as passes.

The separately labelled validation adapter reads the same resident/virtual memory counters from `/proc/self/statm`. It changes no integrals, energies, optimizer, simulation parameters or tolerances, and is **not installed into the product**. The diagnostic adapter replay completed with 212 passes and one eight-qubit VQE calculation stopped by the 240-second/20000-evaluation resource guard under concurrent load. Its log is preserved; this is not claimed as an all-pass run, and the adapter does not alter the numerical algorithm. The ordinary broad release suite is authoritative. This also demonstrates that expensive workloads can return a budget error on slow or contended machines. A further ordinary suite covers the final cache-isolation changes. Cached calculations do not need fresh PySCF. Restricted-container PySCF compatibility remains a documented environment limitation.

## 2. Remediation register and acceptance evidence

“Addressed locally” means the implemented control and its stated tests passed; it does not close an external-service or scientific-validity gate.

| Finding | Implemented change | Acceptance and remaining limits |
| --- | --- | --- |
| F01 - credentials / GUI account boundary | Password widgets start empty; default GUI does not use server keys. Optional OIDC, allowed verified emails and endpoint binding gate server credentials. IBM GUI accepts offline plans only; real operations use CLI. Launcher defaults to loopback. | Two fresh sessions' widget protobufs contain no server-key sentinel; unauthenticated/unverified identities stop before workspaces; changed endpoints cannot receive a server key; GUI rejects account-backed plans. Real IdP/shared deployment untested. |
| F02 - fabricated final conclusions | Successful tool outputs are rendered deterministically. Freeform model draft is stored as unverified, never promoted to scientific answer. Zero successes fail the run; task completion is explicitly unverified. | Failed-tool/invented energy and successful-irrelevant-summary counterexamples do not display the invented claim. This avoids needing a universal hallucination detector; users still review relevance/completeness. |
| F03 - missing group covariance | Estimate variance of weighted per-shot group energy, including covariance and readout factors. Hardware and simulation use the same estimator. | Positive/negative covariance counterexamples agree with independent values, including attenuation scaling. |
| F04 - unjustified trust rule | Replace broad trustworthiness labels with qualified model-agreement labels based on finite-sample Hoeffding/union bounds. No interval from standard error alone. | Rare-event zero-variance example stays inconclusive. Exact binomial coverage enumeration checks multiple sample sizes/probabilities and scaled observables. Bound assumes fixed IID group shots/fixed factors; excludes systematic/model error. |
| F05 - CX/ECR reinterpreted as CZ | Reject non-CZ native imports, mixed unsupported native types and inconsistent directional CZ errors. Manual schema declares CZ assumptions. | Actual FakeManila/FakeBrisbane imports rejected; native CX/CZ/ECR compilation remains supported by separate IBM execution path. Real predictive noise-model fidelity unproven. |
| F06 - selected-layout inference / plot sign | Random sample drawn from full path population; selected best/worst/compiler rows excluded from correlation; exact permutation inference for <=8 points, seeded Monte Carlo above that. ESP-axis rho sign corrected; scientific prose narrowed. | Independent permutations agree; six figures and benchmark regenerated. p=0.3556 does not support the earlier significance implication. Shared chains across geometries remain non-independent, explicitly stated. |
| F07 - stale mutable inputs/cache | Defensive immutable calibration mappings/tuples and normalized effective-input hash. Cached numerical integral/VQE parameter arrays are immutable; public Hamiltonian/spin access returns defensive copies; mutable metadata containers are rejected. | Mutation attempts fail; changing a copied duration changes a newly constructed snapshot's hash. Identity cache is now safe against ordinary input mutation. |
| F08 - failed-run evidence loss | Start and per-tool atomic JSON checkpoints, fsynced events, final failure report; preserve prior completed tools and redacted failure class. | Successful tool followed by provider ConnectionError retains records/results/events. SIGKILL, disk failure and power loss cannot guarantee a final checkpoint. |
| F09 - incomplete resource bounds | Cap graph IDs/sizes, shots, optimizer iterations/evaluations/time, VQE at 8 qubits; refuse 12-qubit curves even when noiseless. Agent numerical tools run in subprocesses with wall timeout and POSIX memory/CPU limits. | Billion-index target rejected before allocation; actual worker timeout exercised; 12-qubit VQE rejected. Windows has wall timeout but no implemented memory cap. This is not multi-tenant DoS certification. |
| F10 - spin-target ambiguity | Explicit singlet/HF spatial-symmetry reference metadata; ideal VQE spin expectation checked; immutable cached integrals. Keep physically intended LiH singlet reference. | All 170 singlet FCI references agree within 1e-8 Ha. LiH 3.5 A's unrestricted lower triplet remains distinct (2.338927 mHa gap). Spatial symmetry is identified, not a new general projection algorithm. |
| F11 - incomplete provenance / instance context | Record full snapshot, integrals, optimized parameters, counts, QPY and model trace. Bind groups/numerical context into QPY metadata. Pin/check actual IBM instance, retain available backend/Runtime metadata and metrics. | Group/reference/integral edits rejected; mismatched instance blocked before sampler construction; actual SDK service payload uses reviewed instance. These are trusted-file consistency checks, not cryptographic signatures or real billing acceptance. |
| F12 - Docker output ownership | Create/chown both output roots before switching to non-root user. | Source/configuration inspection only. Docker daemon/container execution and volume permissions remain unverified. |
| F13 - export authentication mismatch | Export and execution share the same IBM service loader. | Constructor probe confirms IBM-specific token/instance reaches export; no LLM credential reuse. Real authentication untested. |
| F14 - nonfinite JSON input | Reject NaN/Infinity at parsing and direct dispatch; strict finite JSON persistence; validate image values/angles. | Nonfinite tool/image inputs rejected before successful output. |
| F15 - duplicated release tree | Explicit clean staging excludes caches, old validation output, build directories and nested generated source. Wheel is intentionally provided as an alternative installation format. | Final archive manifest lists one intended source root; wheel contents compared byte-for-byte with that source. |
| F16 - version/documentation drift | Rewrite current README/architecture/scientific scope, update provider/IBM/calibration docs, regenerate benchmark/figures and both guides. Older verification notes marked historical; old v1.1 companion PDFs excluded. | All current guides identify 1.0.3 and current behavior. Unavailable v1.1 experiments remain unverified. |

## 3. Additional issue found during the renewed audit

**Measurement-group routing and readout factors:** the original simulator used the first compiled group's final physical-qubit map to correct every group. Independent compilation can route groups differently. The revised implementation derives attenuation from each group's own final layout. It also chooses a Qiskit baseline initial layout once and uses that layout for all groups in that comparison. A regression checks deliberately reversed final maps; regenerated measurement records retain per-group maps/factors and QPY.

This is a code-path correction, not a claim that the checked old benchmark actually exercised every possible routing mismatch. The numerical benchmark was regenerated after the change.

**Cached result aliasing:** further review found that returned VQE parameters and cached Pauli operators could be changed by callers, and incorrectly typed snapshot backend/date fields could admit mutable containers. Parameters are now immutable, Hamiltonian/spin getters return defensive copies, and metadata must be string/null. Dedicated mutation tests exercise all three paths. The final release checks include these follow-up tests.

**Eight-qubit optimization cost:** an intermediate ordinary run had 215 passes and one eight-qubit calculation stopped by the unchanged 240-second resource guard. Finite-difference BFGS repeatedly recomputed the UCC state. The final eight-qubit path supplies an analytic reverse-mode gradient for the same product of exponentials and the same energy objective. Its derivatives are checked against separate central differences at nonzero random parameters, including eight qubits, and the existing energy-accuracy case is rerun. Four-qubit optimization and the published four-qubit benchmark retain their original finite-difference path. This is a performance/implementation correction, not a new scientific method; resource limits remain enforced.

## 4. Statistical method and independent numerical checks

For each measurement group, let a shot value be the sum of real Pauli coefficients times measured parity, divided by fixed readout attenuation where enabled. The mean uses the observed frequencies; variance of that mean uses the unbiased sample variance divided by the returned number of shots. This includes all within-group covariance automatically.

For group g, a valid interval width is at most `2 * sum(abs(scaled nonidentity coefficients))`. With m groups and per-group sample counts N_g, the two-sided Hoeffding radius is `width_g * sqrt(log(2m/0.05)/(2N_g))`. Summing group radii, with the union bound, gives at least 95% simultaneous sampling coverage under the fixed IID assumptions. The bound can be conservative. A zero observed sample variance does not make the bound zero. Readout calibration estimation uncertainty is not included.

Agreement requires the whole interval to lie inside 1.6 mHa of the stated small-model reference. Outside-tolerance requires the whole interval to lie outside; overlap is inconclusive. These are per-estimate intervals, not familywise guarantees across an adaptively selected collection of many displayed experiments. They are not physical chemical-accuracy certificates.

The all-cache unrestricted fixed-(N_alpha,N_beta) check still finds the known LiH 3.5 A lower triplet. The separate singlet FCI run checks the intended target and resolves the earlier ambiguity without replacing a correct singlet reference. Independent occupation-bit fermionic matrix construction does not use the package's Jordan-Wigner ladder helpers.

The random-only correlation test recomputed all 720 permutations for each six-chain sample. Its exchangeability assumptions and small selected demonstration do not establish hardware generalization. No post-hoc change was made to obtain a significant result.

## 5. Scientific contribution and operational disposition

**Supported:** a working integration of small-model chemistry, calibrated simulation, layout exploration, visualizations, provider protocol planning and an explicit IBM job workflow. The package contains authentic historical calibration parameters and a reproducible simulator benchmark.

**Not established:** a new fundamental chemistry/mapping method, substantial scientific novelty, publication-ready validation, superiority over tuned existing tools, physically accurate energies, useful quantum advantage, or reliable unseen-device predictions. UCC/VQE, calibration-aware placement, readout rescaling, Quantum Blur and LLM tool use have relevant prior art. Repairs do not create an experimental scientific contribution by themselves.

A stronger research claim requires a specified hypothesis/contribution, suitable baselines, prospective held-out devices/dates, independent repeated trials, systematic-error analysis, physical measurement evidence where relevant, and held-out model-task evaluations if the assistant is part of the contribution.

### External acceptance still required

- Live tool behavior and task quality for the user's chosen OpenAI/Claude/local/compatible model deployment.
- Actual IBM authentication, account permissions, instance access, server acceptance, queue/quota/billing and physical QPU result retrieval.
- Live OIDC deployment and operator-configured verified-email allowlist, network isolation and service permissions.
- Docker execution as non-root with real mounted volumes; Windows/macOS and dependency-minimum combinations.
- Restricted-procfs PySCF behavior outside this executor; the test adapter is not a product patch.
- Migration from deprecated Runtime 0.50.x SamplerV2 to the new Executor API before relaxing the dependency pin.

At the time of this historical 1.2.1 re-audit, no real QPU job had been submitted and no paid model requests had been made. That absence was a limitation of the 1.2.1 evidence, not a simulated success. Subsequent 1.2.2 hardware acceptance is documented separately.

## 6. Environment and verification qualifications

```json
{
  "openai": "3.35.1",
  "anthropic": "1.15.0",
  "pyscf": "2.14.0",
  "qiskit-ibm-runtime": "0.50.0",
  "streamlit": "1.66.0",
  "quantivex": "1.0.3",
  "python": "3.12.14",
  "platform": "Linux-6.18.55-x86_64-with-glibc2.39",
  "qiskit": "2.6.0",
  "qiskit_aer": "0.17.2",
  "numpy": "2.5.4",
  "scipy": "1.18.1"
}
```

`pip check` reports no broken requirements. The recorded runtime closure is a tested-environment snapshot, not a portable lockfile or a clean independent resolution of all minimum dependency versions. The optional OIDC auth dependency is not represented as a live-tested deployment.

Ruff lint/format checks cover source/tests/scripts. Mypy covers 24 source units using serial execution, a fresh cache, `--follow-imports=skip` and psutil disabled because the executor blocks its process/worker mechanisms. An attempted parallel type check was blocked by an IPC permission error; that is preserved separately. The broad 218-case run had 217 passes and one AppTest failure at its default three-second wait. The GUI harness now sets explicit 30/60-second bounded defaults, without weakening functional assertions; the final GUI rerun result appears above. The full broad suite was not repeated solely for that harness timing change. The earlier instrumented coverage snapshots exclude `app.py` and do not automatically include subprocess child execution. The final ordinary suite runs without coverage instrumentation; no final coverage percentage is claimed. GUI and child behavior have explicit regression checks, and the intermediate coverage snapshots are retained for diagnosis, not presented as final-source coverage or correctness proof. A final payload/report-filtering correction strips curve integrals from outgoing model summaries while retaining them locally; its end-to-end regression is recorded separately. The broad suite was not repeated for that limited filtering change.

The supplied ZIP has no git history. Authorship, commit history, signed releases, external CI execution and original data-collection procedures cannot be authenticated from it. This renewed review uses local source inspection, executable counterexamples, independent numerical calculations, real SDK local/boundary tests, packaging checks and document verification. It is not an external human certification.

## 7. Reproduction and evidence

From the extracted source, install `.[dev]`, then run `pytest`, Ruff checks, and `python scripts/benchmark_layouts.py`. Expect SDK deprecation/local-option warnings. For a normal host use the ordinary suite first. Use the provided procfs adapter only to reproduce this executor-specific qualified check, and disclose that qualification.

The accompanying evidence ZIP contains final logs/XML/coverage, environment/dependency information, independent scientific scripts/results, the synthetic SDK boundary probe, installed-wheel plans/counts, benchmark log and PDF checks. Synthetic records are clearly labelled and must never be represented as real IBM measurements. `release_manifest.json` identifies the final archive and its contents. Raw measurement records in `docs/benchmark.json` permit independent recomputation of the reported simulator estimates.

Primary API/method context:

- IBM Runtime service/instance selection: https://quantum.cloud.ibm.com/docs/en/api/qiskit-ibm-runtime/qiskit-runtime-service
- IBM local testing mode: https://quantum.cloud.ibm.com/docs/en/guides/local-testing-mode
- Streamlit authentication and the distinction from authorization: https://docs.streamlit.io/develop/concepts/connections/authentication
- PySCF FCI/CASCI: https://pyscf.org/user/ci.html and https://pyscf.org/user/mcscf.html
- Statistical permutation context: https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.spearmanr.html
- Prior-art references and the original forensic analysis remain in the earlier audit and `docs/REFERENCES.md`. No exhaustive new novelty search is claimed for this remediation release.

**Disposition:** use as a local, inspectable research demonstrator with the stated limitations. The implemented controls address the reproduced counterexamples. Real-model, real-hardware, deployment and scientific-validity gates remain open.
