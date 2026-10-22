"""Simulation assistant, provider checks, calibration export, and optional IBM jobs."""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .config import PROVIDERS, Settings, default_results_dir, default_simulation_output_dir
from .data import load, save
from .errors import QuantivexError


def _cmd_ask(a: argparse.Namespace) -> int:
    from .agent import ask

    settings = Settings.from_env(
        provider=a.provider,
        base_url=a.base_url,
        model=a.model,
        tool_choice=a.tool_choice,
        shots=a.shots,
        seed=a.seed,
        output_dir=Path(a.out) if a.out else None,
    )
    result = ask(load(a.data), a.question, settings)
    print(result.answer)
    print(f"\nTool calls: {len(result.tool_calls)} | figures: {len(result.artifacts)}")
    print(f"Report: {result.report_path}\nRecord: {result.record_path}")
    return 0


def _cmd_export(a: argparse.Namespace) -> int:
    from .ibm_import import fake_backend, live_backend, snapshot_from_backend

    backend = fake_backend(a.fake) if a.fake else live_backend(a.backend)
    snap = snapshot_from_backend(backend)
    save(snap, a.output)
    print(
        f"Exported {snap.num_qubits} qubits, {len(snap.links)} links "
        f"(calibration {snap.calibration_timestamp}) to {a.output}"
    )
    return 0


def _cmd_check_llm(a: argparse.Namespace) -> int:
    import json

    from .providers import check_compatibility

    settings = Settings.from_env(provider=a.provider, base_url=a.base_url, model=a.model, tool_choice=a.tool_choice)
    print(json.dumps(check_compatibility(settings), indent=2))
    return 0


def _cmd_calibration(a: argparse.Namespace) -> int:
    import json

    from .calibration import builtin_info, export_builtin

    if a.output:
        export_builtin(a.output)
    print(json.dumps(builtin_info(), indent=2))
    return 0


def _cmd_ibm(a: argparse.Namespace) -> int:
    import json

    from . import ibm_execution as ibm

    try:
        if a.action == "prepare":
            from .chemistry import get_problem
            from .ibm_import import fake_backend
            from .vqe import run_vqe

            backend = fake_backend(a.fake) if a.fake else ibm.get_backend(a.backend)
            result = ibm.prepare(
                run_vqe(get_problem(a.molecule, a.bond_length)),
                backend,
                a.directory,
                shots=a.shots,
                mode=a.mode,
                layout=a.layout,
                seed=a.seed,
                max_execution_time=a.max_execution_time,
            )
        elif a.action == "submit":
            result = ibm.submit(a.directory, confirm=a.confirm, max_total_shots=a.max_total_shots)
        elif a.action == "fetch":
            result = ibm.fetch(a.directory)
        elif a.action == "recover":
            result = ibm.recover(a.directory, a.job_id)
        elif a.action == "local-test":
            from .ibm_import import fake_backend

            result = ibm.run_local(a.directory, fake_backend(a.fake), seed=a.seed)
        else:
            result = ibm.load_plan(a.directory)[0]
        print(json.dumps(result, indent=2))
        return 0
    except QuantivexError:
        raise
    except Exception as exc:
        from .errors import HardwareError

        raise HardwareError(
            f"IBM workflow failed ({type(exc).__name__}). Check the job folder, SDK setup and IBM dashboard; "
            "do not repeat a submission with an unresolved receipt."
        ) from exc


def _cmd_doctor(a: argparse.Namespace) -> int:
    settings = Settings.from_env(provider=a.provider, base_url=a.base_url, model=a.model)
    ok = True
    for mod, required in (
        ("qiskit", True),
        ("qiskit_aer", True),
        ("scipy", True),
        ("matplotlib", True),
        ("openai", True),
        ("pyscf", False),
        ("streamlit", False),
        ("qiskit_ibm_runtime", False),
    ):
        try:
            m = importlib.import_module(mod)
            print(f"  ok       {mod} {getattr(m, '__version__', '')}")
        except ImportError:
            ok = ok and not required
            print(f"  {'MISSING ' if required else 'optional'} {mod}")
    if settings.provider == "anthropic":
        try:
            importlib.import_module("anthropic")
        except ImportError:
            ok = False
            print("  MISSING anthropic; install quantivex[anthropic]")
    try:
        settings.require_api_key()
        print("  ok       provider credentials configured (local servers may need none)")
    except QuantivexError as exc:
        ok = False
        print(f"  MISSING  {exc}")
    print(f"  provider {settings.provider} | model {settings.model}")
    print("  note     doctor checks configuration only; check-llm makes two actual model requests")
    return 0 if ok else 1


def _cmd_process(a: argparse.Namespace) -> int:
    from .process import ProcessConfig, process

    config = ProcessConfig(
        molecule=a.molecule,
        bond_length=a.bond_length,
        active_space=a.active_space,
        circuit_mode=a.mode,
        readout_mitigation=not a.no_mitigation,
        shots=a.shots,
        seed=a.seed,
        top_chains=a.top_chains,
        compare_layouts=a.compare_layouts,
        random_chains=a.random_chains,
        predict_hardware=not a.no_prediction,
        report="none" if a.no_report else "llm",
        require_llm=a.require_llm,
    )
    settings = None
    if not a.no_report:
        settings = Settings.from_env(validate=False, provider=a.provider, base_url=a.base_url, model=a.model)
    out = Path(a.out) if a.out else default_results_dir() / datetime.now().strftime("results_%Y%m%d_%H%M%S")
    result = process(a.inputs, out, config, settings)
    print(
        f"\nDone: {len(result.calibrations)} calibration file(s), {len(result.hardware)} hardware run(s), "
        f"{len(result.failed)} failed, {len(result.skipped)} skipped."
    )
    print(f"Language-model report: {result.report_status}")
    print(f"Results: {result.out_dir}\nStart with: {result.out_dir / 'report.md'}")
    return result.exit_code


def _provider_args(parser: argparse.ArgumentParser, tool_choice: bool = False) -> None:
    parser.add_argument("--provider", choices=PROVIDERS)
    parser.add_argument("--base-url", help="API base endpoint (include /v1 for Chat Completions)")
    parser.add_argument("--model", help="exact model ID available on the selected server")
    if tool_choice:
        parser.add_argument("--tool-choice", choices=("auto", "required"))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="quantivex", description="LLM-guided, noise-aware quantum simulation on IBM calibration data"
    )
    p.add_argument("-v", "--verbose", action="store_true", help="log tool calls and timings")
    sub = p.add_subparsers(dest="command", required=True)

    ask_p = sub.add_parser("ask", help="ask the agent a question about a calibration snapshot")
    ask_p.add_argument("data", help="calibration snapshot JSON")
    ask_p.add_argument("question", help='e.g. "Which 4 qubits should I use for N2 and can I trust the result?"')
    _provider_args(ask_p, tool_choice=True)
    ask_p.add_argument("--shots", type=int)
    ask_p.add_argument("--seed", type=int)
    ask_p.add_argument(
        "--out",
        help=f"output directory for run folders (default {default_simulation_output_dir()})",
    )
    ask_p.set_defaults(func=_cmd_ask)

    exp = sub.add_parser("export", help="export a calibration snapshot from an IBM backend")
    src = exp.add_mutually_exclusive_group(required=True)
    src.add_argument("--backend", help="live backend name (needs IBM Quantum credentials)")
    src.add_argument("--fake", help="qiskit-ibm-runtime fake backend with real historical data, e.g. torino")
    exp.add_argument("--output", required=True)
    exp.set_defaults(func=_cmd_export)

    doctor = sub.add_parser("doctor", help="check dependencies and configuration without contacting a model")
    _provider_args(doctor)
    doctor.set_defaults(func=_cmd_doctor)
    check = sub.add_parser("check-llm", help="two small actual model requests to verify tool-call compatibility")
    _provider_args(check, tool_choice=True)
    check.set_defaults(func=_cmd_check_llm)
    bundled = sub.add_parser("calibration", help="inspect/export bundled historical data, offline; no IBM account")
    bundled.add_argument("--output", help="write a byte-identical copy of the bundled Torino JSON")
    bundled.set_defaults(func=_cmd_calibration)
    ibm = sub.add_parser("ibm", help="prepare, explicitly submit, and retrieve optional IBM Runtime jobs")
    actions = ibm.add_subparsers(dest="action", required=True)
    prep = actions.add_parser("prepare", help="optimize locally and compile; never submits a job")
    source = prep.add_mutually_exclusive_group(required=True)
    source.add_argument("--backend", help="accessible live IBM backend name")
    source.add_argument("--fake", help="offline backend, e.g. FakeManilaV2 or torino")
    prep.add_argument("--molecule", choices=("H2", "N2", "LiH"), default="H2")
    prep.add_argument("--bond-length", type=float, default=0.75, help="bond length in angstroms")
    prep.add_argument("--shots", type=int, default=4000, help="shots per measurement group")
    prep.add_argument("--mode", choices=("compact", "uccsd"), default="compact")
    prep.add_argument("--layout", nargs="+", type=int, help="optional explicit physical qubit indices")
    prep.add_argument("--seed", type=int, default=1234)
    prep.add_argument("--max-execution-time", type=int, default=300, help="Runtime execution limit in seconds")
    for action in (prep, actions.add_parser("inspect", help="inspect a prepared job without contacting IBM")):
        action.add_argument("directory", help="job folder (prepare requires a new folder)")
        action.set_defaults(func=_cmd_ibm)
    submit = actions.add_parser("submit", help="submit a reviewed real-hardware plan once; may consume quota/charges")
    submit.add_argument("--confirm", action="store_true", help="explicitly authorize this hardware submission")
    submit.add_argument(
        "--max-total-shots", type=int, required=True, help="explicit budget over all measurement groups"
    )
    fetch = actions.add_parser("fetch", help="poll once and save completed hardware measurements")
    recover = actions.add_parser("recover", help="attach an existing server job after uncertain submission")
    recover.add_argument("--job-id", required=True)
    local = actions.add_parser("local-test", help="execute a prepared fake-backend plan locally with SamplerV2")
    local.add_argument("--fake", required=True, help="same fake backend used at preparation")
    local.add_argument("--seed", type=int, default=1234)
    for action in (submit, fetch, recover, local):
        action.add_argument("directory", help="previously prepared job folder")
        action.set_defaults(func=_cmd_ibm)
    proc = sub.add_parser(
        "process",
        help="process IBM calibration files and hardware-run folders into one results folder",
        description="Inputs: IBM Quantum Platform calibration CSV, IBM BackendProperties JSON, Quantivex "
        "snapshots, Quantivex hardware-run folders, or folders containing them. Nothing is sent to IBM.",
    )
    proc.add_argument("inputs", nargs="+", help="files or folders to process")
    proc.add_argument(
        "--out",
        help=f"new results folder, never overwritten (default: a new timestamped folder under {default_results_dir()})",
    )
    proc.add_argument("--molecule", choices=("H2", "N2", "LiH"), default="H2")
    proc.add_argument("--bond-length", type=float, default=0.75, help="bond length in angstroms")
    proc.add_argument("--active-space", choices=("2e2o", "4e4o"), default="2e2o")
    proc.add_argument("--mode", choices=("compact", "uccsd"), default="compact")
    proc.add_argument("--no-mitigation", action="store_true", help="simulate without read-out correction")
    proc.add_argument("--shots", type=int, default=4000, help="shots per measurement group (simulation)")
    proc.add_argument("--seed", type=int, default=1234)
    proc.add_argument("--top-chains", type=int, default=5, help="ranked qubit chains to list per chip")
    proc.add_argument("--compare-layouts", action="store_true", help="also compare best/worst/Qiskit/random layouts")
    proc.add_argument("--random-chains", type=int, default=15)
    proc.add_argument("--no-prediction", action="store_true", help="skip noise-model prediction for hardware runs")
    proc.add_argument("--no-report", action="store_true", help="skip the language-model explanation")
    proc.add_argument("--require-llm", action="store_true", help="exit with code 3 if the explanation is missing")
    _provider_args(proc)
    proc.set_defaults(func=_cmd_process)
    sub.add_parser("version", help="print the version").set_defaults(func=lambda _: print(__version__) or 0)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.verbose:
        logging.getLogger("quantivex").setLevel(logging.INFO)  # our logs only, not Qiskit's pass timings
    try:
        return int(args.func(args))
    except QuantivexError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
