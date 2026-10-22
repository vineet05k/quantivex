"""Run one fixed H2 simulation across external calibration snapshots."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from quantivex import __version__
from quantivex.agent import Session
from quantivex.config import Settings
from quantivex.data import load
from quantivex.report import environment


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--input-dir", type=Path, required=True, help="directory containing calibration JSON files")
    result.add_argument("--output-dir", type=Path, required=True, help="external destination for results")
    result.add_argument("--shots", type=int, default=4000)
    result.add_argument("--seed", type=int, default=1234)
    result.add_argument("--overwrite", action="store_true", help="replace existing per-calibration output JSON files")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    input_dir = args.input_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    inputs = sorted(input_dir.glob("*.json"))
    if not inputs:
        raise SystemExit(f"No JSON calibration files found in {input_dir}")

    result_dir = output_dir / "outputs"
    figure_dir = output_dir / "figures"
    work_dir = output_dir / "work"
    for directory in (result_dir, figure_dir, work_dir):
        directory.mkdir(parents=True, exist_ok=True)

    calculation = {
        "molecule": "H2",
        "bond_length_angstrom": 0.75,
        "active_space": "2e2o",
        "circuit_mode": "compact",
        "layout": "best_chain",
        "readout_mitigation": True,
        "shots": args.shots,
        "seed": args.seed,
    }

    for index, input_path in enumerate(inputs, start=1):
        output_path = result_dir / f"{input_path.stem}_output.json"
        if output_path.exists() and not args.overwrite:
            raise SystemExit(f"Refusing to overwrite {output_path}; use --overwrite after reviewing it")

        print(f"[{index}/{len(inputs)}] Processing {input_path.name}", flush=True)
        snapshot = load(input_path)
        settings = Settings.from_env(
            shots=args.shots,
            seed=args.seed,
            output_dir=work_dir / input_path.stem,
        )
        session = Session(snapshot, settings)
        dataset_summary = session.get_dataset_summary()
        chemistry = session.run_chemistry(
            molecule="H2",
            bond_length=0.75,
            active_space="2e2o",
            layout="best_chain",
            circuit_mode="compact",
            readout_mitigation=True,
        )

        source_figure = session.recorder.run_dir / chemistry["figure"]
        figure_name = f"{input_path.stem}_chip_map.png"
        shutil.copy2(source_figure, figure_dir / figure_name)
        chemistry["figure"] = f"../figures/{figure_name}"

        record = {
            "format_version": 1,
            "quantivex_version": __version__,
            "input_file": input_path.name,
            "input_sha256": snapshot.sha256,
            "calculation": calculation,
            "dataset_summary": dataset_summary,
            "run_chemistry": chemistry,
            "software_environment": environment(),
            "execution_type": "local calibration-driven simulation; no IBM QPU used",
        }
        output_path.write_text(
            json.dumps(record, indent=2, allow_nan=False, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"      Saved {output_path}", flush=True)

    print(f"Completed {len(inputs)} calculations in {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
