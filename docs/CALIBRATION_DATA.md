# Included historical IBM calibration data

Quantivex includes **real historical calibration parameters**, not measurements of a molecule on quantum hardware.

- File: `examples/ibm_torino_2025-02-26.json` (also installed under `quantivex/resources/`).
- Backend: IBM Torino, as distributed by `FakeTorino` in `qiskit-ibm-runtime 0.50.0`.
- Calibration timestamp: **2025-02-26T15:26:08-05:00**.
- Size: **133 qubits, 150 undirected links**; eleven links have gate error >= 0.5 and are excluded from usable paths.
- Parameters: readout and single-qubit gate errors, T1/T2, two-qubit CZ errors, and median gate durations.
- Export simplification: undirected couplers use the minimum reported directional error; durations are medians. This JSON is a reduced model, not the full backend properties object.
- Raw JSON SHA-256: `ae34d9a98c99d3109daed45bb19ec37c247d1663eb3e4def3b9463924ef8c2fc`.

These exact bytes already shipped in the original v1.0.0 ZIP. Version 1.0.1 additionally places them in the wheel and source distribution. The GUI reads the installed resource and offers a download button, regardless of working directory.

## Offline access: no IBM account, key, or network

```bash
quantivex calibration
quantivex calibration --output torino.json
```

The first command prints provenance, calibration date, size and hashes. The second exports a byte-identical JSON copy. It does not need an LLM key either.

```python
from quantivex.calibration import load_builtin
snapshot = load_builtin()
print(snapshot.num_qubits, snapshot.calibration_timestamp)
```

## Recreate the historical export

Optional: install the IBM extra, using the original upstream version for exact reproducibility:

```bash
python -m pip install '.[ibm]' 'qiskit-ibm-runtime==0.50.0'
quantivex export --fake torino --output regenerated_torino.json
```

Fake backends carry saved data; creating a fake backend does not access your IBM account. Export metadata includes the regeneration date, so the file hash may differ even when numerical calibration parameters match. A later upstream version could change snapshots; the bundled copy remains immutable.

For a **current live snapshot**, `quantivex export --backend BACKEND_NAME --output live.json` needs the IBM extra, an IBM account, and configured IBM credentials. Access rules depend on IBM's service. The optional CLI IBM execution workflow can submit fixed-state chemistry circuits after explicit confirmation. Snapshot imports support symmetric native CZ only; CX/ECR imports are rejected rather than silently converted.

## Attribution and license

Source: Qiskit IBM Runtime's FakeTorino calibration data, exported using `quantivex.ibm_import`. Upstream reference: https://github.com/Qiskit/qiskit-ibm-runtime/tree/0.50.0/qiskit_ibm_runtime/fake_provider/backends/torino

Qiskit IBM Runtime is distributed under the Apache License 2.0. Its license is retained in `third_party/qiskit-ibm-runtime-LICENSE.txt`. The export changes the representation and reduces backend properties to Quantivex's schema. This attribution does not imply IBM endorsement or that the snapshot predicts current hardware behavior.
