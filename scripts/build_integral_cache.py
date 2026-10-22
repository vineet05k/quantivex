"""Regenerate src/quantivex/resources/integrals.json.gz with PySCF (Linux/macOS).

Usage: python scripts/build_integral_cache.py
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pyscf

from quantivex.chemistry import MOLECULES, compute_with_pyscf

GRIDS = {
    "N2": np.round(np.arange(0.80, 2.4001, 0.05), 2),
    "H2": np.round(np.arange(0.30, 2.5001, 0.05), 2),
    "LiH": np.round(np.arange(1.00, 3.5001, 0.10), 2),
}
OUT = Path(__file__).resolve().parents[1] / "src" / "quantivex" / "resources" / "integrals.json.gz"


def main() -> None:
    entries = {}
    for molecule, grid in GRIDS.items():
        for ne, no in MOLECULES[molecule]["active_spaces"]:  # dict keys
            for r in grid:
                p = compute_with_pyscf(molecule, float(r), ne, no)
                entries[f"{molecule}|{r:.2f}|{ne}|{no}"] = {
                    "bond_length": float(r),
                    "e_core": p.e_core,
                    "e_hf": p.e_hf,
                    "e_exact": p.e_exact,
                    "h1": [round(float(x), 14) for x in p.h1.ravel()],
                    "eri": [round(float(x), 14) for x in p.eri.ravel()],
                }
            print(f"{molecule} CAS({ne},{no}): {len(grid)} geometries")
    payload = {"basis": "sto-3g", "pyscf_version": pyscf.__version__, "entries": entries}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(gzip.compress(json.dumps(payload, separators=(",", ":")).encode(), mtime=0))
    print(f"Wrote {len(entries)} entries, {OUT.stat().st_size / 1e3:.0f} kB -> {OUT}")


if __name__ == "__main__":
    main()
