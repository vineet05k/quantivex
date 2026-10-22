# Quantivex prompt guide

This guide applies to Quantivex 1.2.2 and later releases that retain the same
agent tools.

## What a prompt does

`quantivex ask` sends the question to a configured language model. The model
does not calculate energies or execute quantum circuits. It selects from seven
allowlisted tools, and Quantivex validates the arguments before Python, PySCF,
Qiskit and Aer perform the numerical work.

The displayed result contains:

1. a deterministic summary populated from tool outputs;
2. an LLM explanation labelled **unverified**;
3. the raw computed evidence.

The run folder retains the complete record. Review the computed summary and raw
evidence rather than treating the LLM prose as authoritative.

## Configure the run before prompting

Shots and random seed are runtime settings. A sentence in a prompt does not
override them.

```bash
export QUANTIVEX_SHOTS=4000
export QUANTIVEX_SEED=1234
export QUANTIVEX_TOOL_TIMEOUT=900

quantivex check-llm
```

General command form:

```bash
quantivex ask CALIBRATION.json "YOUR QUESTION"
```

The supported molecules are H2, N2 and LiH. Available active-space identifiers
are `2e2o`, `4e4o` and `6e6o`, but noisy VQE and layout comparisons are limited
to at most eight logical qubits. Bond lengths must be between 0.3 and 4.0 Å and
must also be available from the integral cache or live PySCF calculation.

## Prompt catalogue

### 1. Summarize a calibration dataset

Tool: `get_dataset_summary`

```bash
quantivex ask CALIBRATION.json \
  "Use get_dataset_summary to report the provenance, calibration date, number of qubits and links, median readout and two-qubit errors, coherence information, dead links and weakest qubits."
```

This inspects calibration data. It does not run chemistry.

### 2. Rank connected qubit chains

Tool: `find_qubit_chains`

```bash
quantivex ask CALIBRATION.json \
  "Use find_qubit_chains to identify the five best connected four-qubit chains for a run with readout mitigation enabled. Draw the best chain and report the ranking score and estimated success probability."
```

Supported arguments include chain length 1–16, up to ten returned chains, map
rendering, and the readout-mitigation setting. A high score is a calibration-
based prediction, not proof of superior hardware performance.

### 3. Run one chemistry calculation

Tool: `run_chemistry`

```bash
quantivex ask CALIBRATION.json \
  "Run H2 at 0.75 angstrom using active space 2e2o, compact circuit mode, best_chain layout and readout mitigation enabled. Report the Hartree-Fock, exact active-space, ideal VQE, raw noisy and mitigated noisy energies; selected qubits; sampling uncertainty; error in mHa; and confidence verdict."
```

Alternative supported choices are:

- molecule: H2, N2 or LiH;
- layout: `best_chain` or `qiskit_default`;
- circuit mode: `compact` or `uccsd`;
- readout mitigation: enabled or disabled.

`compact` prepares a classically computed optimized statevector using fewer
gates. It is useful for studying mapping and noise, but is not a scalable
replacement for gate-level UCCSD.

### 4. Compare physical-qubit layouts

Tool: `compare_layouts`

```bash
quantivex ask CALIBRATION.json \
  "Use compare_layouts for H2 at 0.75 angstrom with active space 2e2o, compact circuit mode, readout mitigation enabled and 15 random chains. Compare the best chain, worst usable chain, Qiskit default and random chains. Report the errors, verdicts, Spearman correlation, permutation p-value, sample size and correlation_reliable."
```

The random-chain count may be 0–30. At least 15 random chains are recommended
for the repository's four-qubit exploratory correlation. Best/worst/compiler
rows are descriptive and are excluded from the random-chain correlation.

### 5. Generate a potential-energy curve

Tool: `energy_curve`

Noiseless example:

```bash
quantivex ask CALIBRATION.json \
  "Use energy_curve for H2 from 0.50 to 2.00 angstrom with 10 points and active space 2e2o. Include Hartree-Fock, exact active-space and noiseless VQE energies, with noisy simulation disabled."
```

Noisy example:

```bash
quantivex ask CALIBRATION.json \
  "Use energy_curve for H2 from 0.50 to 2.00 angstrom with 8 points, active space 2e2o and noisy simulation enabled. Report the selected layout and explain the difference between ideal and noisy curves."
```

Curves support 3–12 requested points. Noisy curves run a simulation at every
available grid point and can take substantially longer.

### 6. Render a chip map

Tool: `render_chip_map`

```bash
quantivex ask CALIBRATION.json \
  "Render a chip map showing readout error and dead links, and highlight qubits 18, 31, 32 and 33."
```

Only qubit identifiers present in the supplied snapshot can be highlighted.

### 7. Render a Quantum Blur image

Tool: `render_noise_blur`

```bash
quantivex ask CALIBRATION.json \
  "Render a Quantum Blur visualization using two_qubit_error, theta 0.8 and ry rotation. Explain that the image is a visualization rather than a chemistry-accuracy result."
```

Metrics are `readout_error`, `two_qubit_error`, `sx_error` and `t1_decay`;
theta is 0.0–1.5; rotation is `rx` or `ry`. Quantum Blur does not affect layout
selection or energy estimation.

## Recommended exploratory prompt

```bash
quantivex ask CALIBRATION.json \
  "First summarize the calibration provenance, date and principal noise properties. Then run H2 at 0.75 angstrom using active space 2e2o, compact circuit mode, best_chain layout and readout mitigation enabled. Report the selected qubits, all reference and noisy energies, sampling standard error, 95 percent confidence radius, error in mHa, whether it beats Hartree-Fock and the confidence verdict. State the uncertainty limitations and that no physical QPU was invoked."
```

## Requests that require another interface

| Request | Correct method |
| --- | --- |
| Change shots or seed | Set `QUANTIVEX_SHOTS` or `QUANTIVEX_SEED` before the command |
| Repeat an identical study across many files | `quantivex process <folder>` (deterministic; no LLM needed for the numbers) |
| Run an unsupported molecule | Extend and validate the chemistry implementation first |
| Download a live IBM calibration | `quantivex export --backend ...` |
| Execute on a physical IBM QPU | Explicit `quantivex ibm prepare/inspect/submit/fetch` workflow |
| Search the web | Use a separate research tool; the Quantivex agent has no web-search tool |

No LLM tool can submit an IBM job. This prevents a model from autonomously
consuming IBM quota.

## Prompt-review checklist

Before accepting a result, verify that:

- the expected tools were actually called;
- molecule, geometry, active space, layout and circuit mode match the question;
- shots and seed in `record.json` match the intended environment;
- provenance and calibration date are stated;
- the answer distinguishes local simulation from QPU execution;
- confidence statements are limited to the reported sampling uncertainty;
- any unverified LLM explanation agrees with the raw tool evidence.
