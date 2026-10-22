# Examples

| File or folder | What it is |
| --- | --- |
| `ibm_torino_2025-02-26.json` | Quantivex snapshot of IBM Torino (from Qiskit's stored processor model, calibration of 2025-02-26) |
| `synthetic_16q_grid.json` | Invented 16-qubit grid for tests and demonstrations |
| `ibm_kingston_calibrations_2026-10-20T06_23_28Z.csv` | IBM Kingston calibration in the IBM Quantum Platform CSV column layout. The values are the real calibration IBM reported when the hardware run below was prepared; the file was written from those properties in the website's layout (it is not a download from the website). |
| `hardware_runs/ibm_kingston_h2_075_run03/` | One real IBM Kingston run of H2 at 0.75 Å (5 groups x 1000 shots), publicly redacted: plan, circuits, receipt, IBM's raw result, analysed result, checksums |

Process all of them:

```bash
quantivex process examples --out example_results
```
