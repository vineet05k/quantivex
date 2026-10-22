# Data packages and output locations

The GitHub repository contains the software only (plus a few small examples used by the tests). Data is
distributed separately as two release assets, so anyone can download the package, bring their own IBM files and
get their own results folder.

## Release assets

| Asset | Contents | Role |
| --- | --- | --- |
| `Quantivex_v1.3.0_SOURCE.zip` (or `git clone`) | software, tests, documentation, small examples | the package |
| `Quantivex_IBM_INPUTS_v1.3.0.zip` | IBM files: 10 calibration snapshots, 1 live calibration, 1 calibration CSV, 10 real hardware-run folders | example **inputs** |
| `Quantivex_RESULTS_v1.3.0.zip` | the results folder written by `quantivex process` from exactly those inputs | example **outputs** |

Each asset has an adjacent `.sha256` file. Verify before use:

```bash
sha256sum -c Quantivex_IBM_INPUTS_v1.3.0.zip.sha256
```

## Using your own IBM files

```bash
quantivex process path/to/your_ibm_files/ --out path/to/your_results/
```

Accepted inputs, in any mix and in nested folders:

- the calibration CSV downloaded from IBM Quantum Platform (Compute resources -> a processor -> Calibration data);
- IBM `BackendProperties` JSON (`backend.properties().to_dict()`);
- Quantivex snapshots (`quantivex export --backend ibm_fez --output fez.json`);
- hardware-run folders created by `quantivex ibm prepare / submit / fetch`.

Without `--out`, a new folder `results_<date>_<time>` is created under `~/QuantivexData/results/`.

## Reproducing the example outputs

```bash
unzip Quantivex_IBM_INPUTS_v1.3.0.zip
quantivex process Quantivex_IBM_INPUTS_v1.3.0/ --out my_results/ --no-report
```

`my_results/calibrations.csv` and `my_results/hardware.csv` should match the files in
`Quantivex_RESULTS_v1.3.0.zip`. With the same library versions every computed number matches exactly (checked for
this release on a fresh install); only the compiled-circuit bytes embedded in `chemistry.json`, timestamps in
`run.json` and the checksums that cover them differ. On other platforms or library versions simulated values can
differ in the last digits; hardware values are recomputed from fixed counts and always match. The
language-model explanation in `report.md` differs from run to run and is not part of the reproducibility check.

## Where Quantivex writes

By default Quantivex never writes into the source checkout:

```text
~/QuantivexData/
├── results/            quantivex process
├── simulation_runs/    quantivex ask and the browser app
└── hardware_runs/      offline IBM job folders created in the browser app
```

| Setting | Effect |
| --- | --- |
| `QUANTIVEX_DATA_DIR` | common root (default `~/QuantivexData`) |
| `QUANTIVEX_RESULTS_DIR` | parent folder for `quantivex process` results |
| `QUANTIVEX_OUTPUT_DIR` | folder for `quantivex ask` runs (also `--out`) |
| `QUANTIVEX_HARDWARE_OUTPUT_DIR` | folder for browser-created offline IBM jobs |

IBM command-line jobs (`quantivex ibm prepare ...`) always use the folder given on the command line.

## Provenance of the example data

- The 10 calibration snapshots were exported from Qiskit's stored IBM processor models (historical
  calibrations). They are calibration models, not measurements of a molecule.
- The 10 hardware runs are real IBM jobs (H2 at 0.75 A; Fez pilot, 3 Fez, 3 Kingston, 3 Marrakesh; 50,000 shots),
  publicly redacted: IBM instance identifiers, account names, job IDs, job tags and local paths were replaced by
  aliases. Redaction changed JSON files, so their checksums differ from the private originals.
- The calibration CSV is Kingston's real calibration at the time of a hardware run, written in the IBM Quantum
  Platform CSV column layout (not a download from the website).
- The results zip is regenerated with Quantivex 1.3.0. Earlier releases' historical records (1.2.1 simulations,
  1.2.2 retrieval) are unchanged and remain available from the 1.2.3 release assets.
