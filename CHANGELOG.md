# 1.3.0 - batch processing of IBM files (2026-10-22)

### Added
- `quantivex process INPUTS... --out DIR` and `quantivex.process.process()`: give the package IBM files (or
  folders of them) and get one complete, checksummed results folder: per-calibration chip maps, ranked qubit
  chains, noisy-simulation energies with 95% margins and verdicts; per-hardware-run recomputed energies,
  verdicts, integrity checks and noise-model predictions; summary tables, figures, run record and a report.
- `quantivex.ibm_formats`: readers for the IBM Quantum Platform calibration CSV and IBM `BackendProperties`
  JSON, with tolerant column matching and a recorded account of every conversion choice; `detect_kind()`.
- The language model writes the report's explanation from computed results only; every number it writes is
  checked (`--require-llm` makes the explanation mandatory, `--no-report` turns it off).
- Hardware re-analysis verifies the plan and circuit hashes, the folder manifest, that IBM's raw counts equal
  the stored counts, and that the recomputed energy equals the stored one.
- Examples: an IBM-layout calibration CSV and one redacted real hardware run; guides from the 1.2.3
  documentation pack (`docs/PROMPT_GUIDE.md`, `docs/EXPERIMENT_DESIGN.md`), `docs/EVIDENCE.md`, `SECURITY.md`.
- CI runs the batch processor end to end on the bundled examples from the built wheel.

### Changed
- Software and data are separated. The repository holds the package; example data ships as two release assets:
  `Quantivex_IBM_INPUTS_v1.3.0.zip` (IBM calibration files and real hardware-run folders) and
  `Quantivex_RESULTS_v1.3.0.zip` (the results folder `quantivex process` writes from exactly those inputs).
- Merged from the 1.2.3 packaging release: generated data goes to `~/QuantivexData/` by default, never into the
  checkout (`QUANTIVEX_DATA_DIR`, `QUANTIVEX_OUTPUT_DIR`, `QUANTIVEX_HARDWARE_OUTPUT_DIR`); the browser app shows
  the output folders; the Docker image uses a `/data/quantivex` volume; `docs/INSTALLATION.md`,
  `docs/RESULTS_GUIDE.md`, `docs/DATA_AND_OUTPUT_LOCATIONS.md`, `scripts/run_calibration_study.py` and the IBM
  Quantum quick-start guide are included.
- `quantivex process` without `--out` writes a new timestamped folder under `~/QuantivexData/results/`
  (`QUANTIVEX_RESULTS_DIR` overrides).
- Documentation headers and provider notes updated; the README now starts from the batch workflow.

### Note on predictions
- The calibration converter uses the median CZ gate duration over all links (as `quantivex export` does).
  A first-pass check in 1.2.2 took the first listed CZ duration, which on IBM Fez gave 81-83 mHa predicted
  errors; the package value is 77-79 mHa. Kingston (59.7) and Marrakesh (77.4) are unchanged.

# 1.2.3 - source/data separation (2026-10-22)

### Changed

- Distribute source code, the ten historical fake-backend calibration inputs,
  the ten corresponding local simulation results, and the sanitized IBM QPU
  evidence as independent release archives.
- Write simulation records to `~/QuantivexData/simulation_runs` by default
  instead of a directory under the current checkout.
- Write GUI-created offline IBM job folders to
  `~/QuantivexData/hardware_runs` by default.
- Add `QUANTIVEX_DATA_DIR` as the common data-root override and
  `QUANTIVEX_HARDWARE_OUTPUT_DIR` as the hardware-folder override. The existing
  `QUANTIVEX_OUTPUT_DIR` and CLI `--out` options still take precedence for
  simulation runs.
- Document how separately downloaded study inputs and results are overlaid and
  how controlled batches are run without an LLM.
- Replace two stale generated installation/results PDFs with current Markdown
  documentation so the release does not retain instructions for repository-local
  output folders. The scientific backend-physics PDF is unchanged.

### Scientific provenance

- No historical calibration, simulation or physical-QPU result was
  recalculated or relabelled for 1.2.3.
- The ten simulations retain their Quantivex 1.2.1 provenance.
- The initial Fez plan retains its 1.2.1 preparation provenance and successful
  1.2.2 retrieval provenance. The nine controlled jobs retain their 1.2.2
  preparation and retrieval provenance.

# 1.2.2 - IBM Runtime retrieval fixes (2026-10-20)

### Hardware acceptance evidence

- One Fez pilot was prepared under 1.2.1 and retrieved successfully through
  the repaired 1.2.2 fetch path.
- Nine controlled plans were prepared and retrieved under 1.2.2: three each
  on Fez, Kingston and Marrakesh.
- The ten jobs contain 50,000 total shots. Raw Runtime results, count tables,
  compiled circuits, plans, receipts and derived summaries are retained.
- These fixed-state measurements validate the exercised integration path.
  They do not establish chemical accuracy, quantum advantage or broad hardware
  generalization.

### Fixed
- Normalize Qiskit Runtime `JobStatus` enum values before comparing or returning
  them, so a completed IBM job is recognized as `DONE`.
- Delegate IBM-specific result metadata, including `ExecutionSpans`, to IBM's
  `RuntimeEncoder`. Returned fetch records are JSON-normalized as well, so both
  `result.json` and CLI output remain serializable.
- Add regression coverage using actual `JobStatus` and `ExecutionSpans` objects
  from qiskit-ibm-runtime 0.50.0.

# 1.2.1 - re-audit fixes (2026-10-20)

Fixes all ten findings (Q120-01 to Q120-10) of the independent forensic re-audit of 1.2.0. Each has a
regression test in `tests/test_audit_v120.py`.

### Fixed
- **Q120-01** `uccsd_excitations()` returns fresh operator copies from a private cache, and every built circuit
  is checked against the optimiser energy (state-vector expectation within 1e-6 Ha), so a circuit/energy
  mismatch raises instead of passing silently. The audit's 21 mHa drift now cannot occur.
- **Q120-02** The draft checker no longer treats numbers from the question or from prose fields as evidence,
  requires matching signs (except error-like magnitudes), uses displayed-precision rounding only (no 0.5%
  relative slack), checks small integers, and parses scientific notation. The reassurance now reads "no
  unmatched numbers were detected by this heuristic; it does not verify the explanation's claims".
- **Q120-03** A CZ keeps each qubit's own relaxation when its partner lacks coherence data. Missing T2 is
  modelled as 2*T1 (amplitude damping only), missing T1 as pure dephasing; `model_notes` in each result says so.
- **Q120-04** OpenAI-style requests now carry the response-token cap (`max_completion_tokens` for OpenAI,
  `max_tokens` for other compatible servers), including the compatibility probe and the final answer.
- **Q120-05** Ranking and estimation share one mitigation-eligibility rule: the gate-only (mitigated) ranking
  excludes chains whose readout cannot be mitigated. If mitigation still falls back (for example on Qiskit's
  own layout), the result says so (`readout_mitigation_requested`, `model_notes`, `rows_with_mitigation_fallback`).
- **Q120-06** The GUI tells the user to paste a session key when only an environment key exists (it
  deliberately ignores environment keys without OIDC). Guides and README now say this.
- **Q120-07** Two-sigma comparisons are described as a descriptive noise scale everywhere. The multi-seed
  benchmark adds an exact paired sign-flip test (p = 0.50-0.56; the 5-seed minimum is 0.0625).
- **Q120-08** `validate_results.py` reports PASS / FAIL / SKIP separately and fails if an essential check is
  skipped. V3 runs both noise models on the identical compiled circuits. V7 varies only the sampler seed
  (new `sampler_seed` argument) and compares with the exact noisy energy from a density-matrix calculation.
  The README now states V3's real tolerance (max(10 mHa, 30%)).
- **Q120-09** `record.json` records the configured tool budget (wall, CPU and memory), not a fixed 300 s.
- **Q120-10** Correlation fields are `cost_vs_error_spearman_rho` (+1 = score works) and
  `esp_vs_error_spearman_rho` (-1 = score works), with `rho_meaning`. Renamed from `score_vs_error_*`.

# 1.2.0 - merged release (2026-10-20)

Merges the 1.1.0 line (mitigation-aware chain score, 15-chain correlation, validation script) into the
1.0.3 audit-and-repair release, and fixes the problems found when reviewing 1.0.3.

### Changed
- **Tighter, still valid 95% radius.** Verdicts use the smaller of a Hoeffding bound and an empirical-Bernstein
  bound (Maurer & Pontil 2009), each at alpha/2 and union-bounded over groups. On the Torino N2 benchmark the
  radius falls from 66-122 mHa to 37-49 mHa, so 39-79 mHa errors are now `OUTSIDE_TOLERANCE_95` instead of
  always `INCONCLUSIVE_95`. Coverage is checked by exact binomial enumeration (2-4000 shots) and by 30 repeated
  runs (30/30 covered). IBM hardware results use the same radius.
- **Mitigation-aware chain score (from 1.1.0).** With readout mitigation on, chains are ranked by the gate-only
  score. With 1.0.3's covariance estimator and permutation test: gate-only rho 0.94-0.96, full score 0.16-0.29.
- `compare_layouts` samples 15 random chains by default (was 6), reports `correlation_sample_size`,
  `correlation_reliable` (p < 0.05 and n >= 15) and `chain_score`. At 8 qubits the agent defaults to 5.
- **Plain-English answers return, without trusting the model.** The answer starts with a templated summary
  built only from tool outputs, then the model's explanation labelled unverified, with every number that matches
  no tool output flagged (`draft_unmatched_numbers` in `record.json`), then the raw evidence. The GUI shows the
  summary and puts the evidence in an expander.
- Tool budget is configurable (`QUANTIVEX_TOOL_TIMEOUT`, default 900 s, was a fixed 300 s that 8-qubit
  layout comparisons could not meet). The CPU limit follows it.

### Added
- `scripts/validate_results.py` (from 1.1.0, ported) with a new V7 check of error bars against repeated runs.
  V6 compares decoded circuits, because QPY bytes embed random identifiers.
- Multi-seed Quantivex-vs-Qiskit table in `docs/benchmark.md`.
- `quantivex.narrative` and tests; Bernstein coverage tests; tool-timeout configuration test.

### Fixed
- mypy error in the IBM GUI (`Optional[str]` passed as circuit mode).
- The 1.0.3 audit report cited evidence files that were not in the archive; 1.2.0 evidence lives in
  `validation/v1.2.0/` and only files that exist are cited.

# 1.1.0 (2026-10-19, parallel line)

- Mitigation-aware (gate-only) chain score; 15 random chains; `validate_results.py`; multi-seed benchmark.
- Withdrew an over-claimed v1.0.0 correlation (rho about 0.8 from about 9 chains at one seed).

# 1.0.3 - forensic remediation (2026-10-19)

- Remove server credentials from widget defaults; IBM GUI is offline-only; optional OIDC allowlist.
- Render computed evidence rather than unverified model conclusions; checkpoint failed runs.
- Correct grouped covariance; add finite-sample sampling bounds and qualified agreement labels.
- Reject incompatible snapshot gate semantics; native IBM targets remain supported separately.
- Use random-only permutation analysis and correct ESP-axis annotation signs.
- Freeze calibration/integral inputs; bound graph sizes, VQE and tool worker resources.
- Identify/check singlet targets; preserve raw measurements, circuits and experiment context.
- Use a reverse-mode analytic UCC energy gradient for eight-qubit optimization, checked against central differences; retain resource limits.
- Pin IBM instances, bind QPY semantics, unify export authentication, retain available metadata.
- Fix Docker output ownership; reject nonfinite tool input; clean release staging and guides.

Earlier release history follows; current behavior is defined by the 1.0.3 source and audit.

# 1.0.2 — optional IBM Runtime execution (2026-10-19)

- Added an independent IBM execution workspace and `ibm prepare/inspect/submit/fetch/recover/local-test` CLI commands.
- Compile fixed, locally optimized states against the selected backend's actual target, with optional physical layouts.
- Persist native circuits, shot budgets, calibration provenance, SDK versions, server job IDs, and raw counts.
- Require explicit submission and budget; prevent duplicate submission from the same job folder and support tagged recovery.
- New hardware estimator includes covariance within measurement groups and reports sampling uncertainty without a broad trust label.
- IBM credentials are separate from LLM credentials; local fake-backend tests need neither.
- Added actual SamplerV2 local-mode tests and injected service tests; live QPU authentication/execution remains untested.
- Constrained IBM extra to the tested 0.50 API family. Original simulation/statistical algorithms are unchanged.

---

# 1.0.1 — provider access and offline calibration packaging (2026-10-19)

- Added provider-aware CLI/GUI configuration for OpenAI, native Anthropic/Claude, Ollama, local servers and other OpenAI-compatible endpoints.
- Added native Claude transcript conversion, parallel tool-result grouping, error results and controlled finalization.
- Added `quantivex check-llm`, a two-turn tool-protocol probe; real endpoints incur real usage.
- Local providers use loopback endpoints, bypass environment proxies and do not inherit cloud API keys.
- Added runtime JSON-schema validation, first-response tool checks, output-truncation rejection and total tool-call limits.
- Included the historical Torino JSON in wheel resources; added offline inspect/export and GUI download.
- Added `quantivex-gui`, source-distribution fixtures, and data/license attribution.
- GUI now separates provider credentials, accepts all supported shot settings, and hides stale results after input changes.
- Malformed durations containers now produce controlled validation errors.
- Protocol tests use actual SDKs with local scripted HTTP servers; no live LLM/model or hardware certification is claimed.
- Physics/statistical algorithms are unchanged; audit findings about covariance, correlation inference, broad trust labels and final prose faithfulness remain open.

---

# Changelog

## 1.0.0 (2026-10-19)

A rebuild that implements the "Quantum Chemistry Agent" specification. Breaking changes throughout.

### Added
- Chemistry pipeline: PySCF active spaces chosen by symmetry, a Jordan–Wigner Hamiltonian, and UCCSD VQE for N₂, H₂ and LiH. A bundled integral cache (170 geometries) means it works without PySCF.
- Noisy simulation driven by the snapshot: Qiskit Target plus an Aer noise model (depolarising, T1/T2, readout), shot-based energies with QWC grouping, standard errors and readout-error mitigation.
- Deterministic trust verdict (TRUSTWORTHY / DEGRADED / UNTRUSTWORTHY), a beats-Hartree–Fock check and the noise-to-correlation ratio.
- Layout comparison: best chain vs Qiskit default vs worst and random chains, with the Spearman correlation between score and error.
- Chain search on the heavy-hex graph with an estimated-success-probability score.
- Quantum Blur, verified against a Qiskit simulation; chip map, energy curve and comparison figures.
- An agent with 7 strictly-typed tools, multi-round error recovery, an audit trail, and `record.json` + `report.md` per run.
- Schema v2 snapshots (T1, T2, single-qubit error, durations, calibration timestamp) and a real `ibm_torino` snapshot. The exporter supports live and fake IBM backends.
- CLI subcommands `ask`, `export`, `doctor` and `version`; Streamlit GUI; Dockerfile; GitHub Actions CI on three operating systems and three Python versions; 107 tests (96% coverage); ruff and mypy.

### Fixed
- GUI blank after the first interaction (rendering now lives in `main()`).
- The LLM crashed on invalid tool arguments.
- The exporter recorded the export time instead of the calibration time.
- Booleans were accepted as qubit IDs.

### Removed
- The connected-subset ranking (superseded by chain search, which matches circuit needs).

## 0.2.0
- LLM made mandatory; multi-round tool calling.

## 0.1.0
- Initial hackathon prototype: connected-subset ranking with an optional LLM.
