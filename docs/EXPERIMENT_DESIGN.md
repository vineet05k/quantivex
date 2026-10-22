# Designing Quantivex experiments

Quantivex can be used interactively through an LLM or deterministically through
its Python API. The interface should match the scientific purpose.

## Choose the correct execution path

| Purpose | Recommended path |
| --- | --- |
| Understand a new calibration snapshot | `quantivex ask` with `get_dataset_summary` |
| Explore one chemistry question | `quantivex ask` with `run_chemistry` |
| Explore whether layout matters | `compare_layouts` |
| Produce a quick energy curve | `energy_curve` |
| Repeat one protocol across datasets | Direct Python script |
| Run statistical replicates across seeds | Direct Python script |
| Produce release or publication evidence | Prespecified Python workflow plus independent analysis |
| Execute on a physical IBM QPU | Explicit IBM CLI workflow; never the LLM agent |

The LLM is useful for selecting and explaining analyses. It should not control a
canonical batch because tool choice and prose can vary between model runs.

## Define the question before running

A controlled study should state:

1. hypothesis or comparison;
2. molecule and bond length(s);
3. basis and active space;
4. circuit mode;
5. calibration snapshot(s) and dates;
6. layout-selection rule;
7. mitigation setting;
8. shots per measurement group;
9. random seeds and number of replicates;
10. primary outcome and uncertainty interpretation.

## Common study designs

| Scientific question | Vary | Hold fixed |
| --- | --- | --- |
| How does calibration noise affect one calculation? | Calibration snapshot | Molecule, geometry, circuit, layout rule, mitigation, shots and seed |
| Does physical-qubit selection matter? | Layout | Hamiltonian, optimized state, circuit mode, mitigation, shots and seed |
| Does readout mitigation help? | Mitigation on/off | Snapshot, state, layout, shots and seed |
| How does geometry affect energy? | Bond length | Snapshot, active space, algorithm, shots and layout rule |
| How does sampling affect stability? | Seed and/or shots | All physical and algorithmic settings |
| Does compact preparation differ from UCCSD? | Circuit mode | Problem, snapshot, layout, mitigation, shots and seed |

Change one intended factor at a time where possible. If several factors change,
the observed difference cannot be attributed to one cause.

## The packaged ten-simulation study

The ten example calibrations in `Quantivex_IBM_INPUTS_v1.3.0.zip` (results in `Quantivex_RESULTS_v1.3.0.zip`) form one controlled
calibration-model comparison, not ten independent chemistry experiments.

Fixed settings:

| Setting | Value |
| --- | --- |
| Molecule | H2 |
| Bond length | 0.75 Å |
| Basis | STO-3G |
| Active space | CAS(2e,2o) |
| Logical qubits | 4 |
| Ansatz | UCCSD, one Trotter step |
| Circuit mode | Compact |
| Layout rule | `best_chain` |
| Readout mitigation | Enabled |
| Shots | 4,000 per measurement group |
| Seed | 1234 |

The varied factor was the fake-backend calibration snapshot: Aachen, Berlin,
Boston, Fez, Kingston, Marrakesh, Miami, Pittsburgh, Prague or Torino. The
canonical outputs were produced by direct API calls, not by asking an LLM to
repeat a prompt.

The study asks: how much does the predicted noisy H2 energy change when the
same calculation is evaluated under different stored calibration-derived noise
models? It does not establish which current IBM QPU is best.

## Replication and independence

Changing only the shot seed creates sampling replicates under one fixed model.
It does not capture:

- calibration drift;
- different dates;
- different physical devices;
- device bias or crosstalk omitted by the model;
- model-selection uncertainty;
- electronic-structure model error.

Runs made close together on one backend are technical replicates. Claims about
devices or dates require prospectively sampled backends/calibrations and
appropriate independent replication.

## Outcomes and uncertainty

Useful outputs include:

- Hartree-Fock energy;
- exact energy within the declared active-space model;
- ideal VQE energy and ansatz error;
- raw and mitigated noisy energies;
- absolute error in mHa;
- sampling standard error;
- finite-sample 95% confidence radius;
- selected layout and circuit depth;
- whether the noisy point estimate improves on Hartree-Fock;
- the qualified model-agreement verdict.

The interval covers finite-shot sampling under the implemented assumptions. It
does not include calibration uncertainty, drift, leakage, coherent bias,
crosstalk, mitigation bias or basis/active-space error. `INCONCLUSIVE_95` means
the sampling bound crosses the chosen tolerance; it does not mean the result is
chemically correct.

## Compact versus UCCSD circuits

`compact` mode prepares the classically computed optimized statevector with a
shorter circuit. It is useful when the question concerns placement, calibration
noise or measurement. Because the target state is already calculated
classically, it is not a scalable chemistry algorithm or hardware-in-the-loop
VQE.

`uccsd` mode executes the gate-level ansatz and is more faithful to the circuit
structure of UCCSD, but it is generally deeper and more sensitive to noise.
State the selected mode in every report.

## Simulation versus physical QPU execution

Calibration-driven simulation:

- uses a JSON calibration snapshot;
- runs locally in Aer;
- can include modeled gate, relaxation and readout noise;
- does not consume IBM QPU quota.

Physical-QPU execution:

- prepares and compiles a fixed state locally;
- requires explicit `prepare`, `inspect`, `submit` and `fetch` commands;
- consumes account resources or quota;
- returns real counts, but still does not constitute hardware-in-the-loop VQE.

Never infer present hardware performance from an old fake-backend snapshot.

## Minimum reporting checklist

Report the package and dependency versions, input hash, calibration provenance
and timestamp, all scientific settings, shots per group, seed, selected layout,
circuit mode, mitigation status, raw and processed results, uncertainty scope,
and all exclusions. Preserve `record.json` or the deterministic batch output,
not only the LLM explanation.

## Recommended progression

1. Run one H2 smoke test on one snapshot.
2. Repeat it with the same seed to confirm reproducibility.
3. Vary seeds to measure sampling variability.
4. Test mitigation on/off using matched settings.
5. Compare layouts on the same snapshot.
6. Compare snapshots while fixing all chemistry settings.
7. Only then design a prospectively replicated QPU experiment.
