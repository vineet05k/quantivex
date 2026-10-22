# Interpreting Quantivex results

First identify how the record was produced. Similar-looking energy fields can
come from different execution paths.

| Record class | Execution | Interpretation |
| --- | --- | --- |
| Assistant simulation | Local Qiskit Aer with calibration-derived noise | A model prediction, not a QPU measurement |
| IBM local test | Runtime SDK local mode and a fake backend | Integration simulation, not a QPU measurement |
| IBM QPU result | Remote physical backend through IBM Runtime | Physical measurements of a fixed locally optimized state |

## Assistant answer sections

1. **Summary computed by Quantivex** uses deterministic templates filled from
   tool outputs.
2. **Language-model explanation (unverified)** is interpretation and can still
   be wrong. The numeric checker detects some unmatched numbers but does not
   validate scientific claims.
3. **Computed evidence** contains the raw structured tool outputs. `record.json`
   is the primary local record.

When the prose and raw evidence disagree, use the raw evidence and investigate
the mismatch.

## Main chemistry quantities

- `e_hf_hartree`: Hartree–Fock reference in the stated small model.
- `e_exact_hartree`: exact diagonalization within that same active-space model,
  not the experimental molecular energy.
- `e_vqe_ideal_hartree`: noiseless optimized ansatz energy.
- `e_noisy_raw_hartree`: noisy finite-shot estimate before readout correction.
- `e_noisy_hartree`: reported noisy estimate after any applied correction.
- `error_mha`: difference from the exact small-model reference in millihartree.
- `stderr_mha`: estimated sampling standard error.
- `confidence_radius_mha`: finite-sample 95% sampling radius used by the
  verdict logic.

Chemical accuracy is treated as 1.6 mHa. A result can have a small standard
error and still be inaccurate because systematic hardware/noise-model bias is
not sampling uncertainty.

## Verdicts

- `MODEL_AGREEMENT_95`: the reported interval lies within the 1.6 mHa tolerance
  around the exact small-model reference.
- `OUTSIDE_TOLERANCE_95`: the reported interval is wholly outside that
  tolerance.
- `INCONCLUSIVE_95`: the interval overlaps the tolerance boundary.
- `UNASSESSED`: Quantivex did not compute the required bound.

These labels assess agreement with the selected electronic-structure model.
They do not establish agreement with experiment, quantum advantage or broad
device reliability.

## Uncertainty limits

The sampling calculation includes within-group covariance. It excludes:

- calibration uncertainty and drift;
- coherent device bias, leakage and crosstalk;
- mitigation bias;
- dependence across separate jobs;
- basis-set and active-space error;
- uncertainty in molecular geometry.

More shots can reduce sampling noise but cannot by themselves remove these
systematic errors.

## Files and integrity

Simulation runs contain `report.md`, `record.json` and generated figures under
the configured simulation output directory. IBM job folders contain reviewed
plans, compiled QPY circuits, receipts and results under the explicit job
directory.

SHA-256 manifests detect changes after packaging. They are not digital
signatures from IBM. Public QPU evidence is sanitized and therefore has hashes
different from the private audit evidence.
