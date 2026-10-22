"""Isolated numerical tool worker. No model or IBM submission entry points."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def bounded_tool(session: Any, name: str, args: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
    """Run one tool in a subprocess with wall-clock, CPU and memory limits (QUANTIVEX_TOOL_TIMEOUT, default 900 s)."""
    if timeout is None:
        timeout = float(getattr(session.settings, "tool_timeout_s", 900.0))
    request = {
        "cpu_seconds": max(10, int(timeout) - 10),
        "snapshot": session.snapshot.to_dict(),
        "name": name,
        "args": args,
        "shots": session.settings.shots,
        "seed": session.settings.seed,
        "run_dir": str(session.recorder.run_dir.resolve()),
        "counter": session.recorder._counter,
    }
    env = {
        k: v
        for k, v in os.environ.items()
        if not any(x in k.upper() for x in ("TOKEN", "API_KEY", "SECRET", "PASSWORD"))
    }
    env.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", MPLBACKEND="Agg")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "quantivex.worker"],
            input=json.dumps(request, allow_nan=False),
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "error": f"Numerical tool exceeded its {timeout:.0f} s budget and was stopped. Use fewer random chains, "
            "fewer curve points or the 2e2o active space, or raise QUANTIVEX_TOOL_TIMEOUT"
        }
    if result.returncode != 0:
        return {"error": "Numerical worker failed or exceeded its resource budget"}
    payload = json.loads(result.stdout)
    from .report import Artifact

    session.recorder._counter = payload["counter"]
    session.recorder.artifacts.extend(Artifact(a["kind"], Path(a["path"]), a["caption"]) for a in payload["artifacts"])
    return payload["output"]


def main() -> None:
    request = json.load(sys.stdin)
    if os.name == "posix":
        import resource

        cpu = int(request.get("cpu_seconds", 290))
        resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    from .agent import Session
    from .config import Settings
    from .data import from_dict
    from .report import RunRecorder

    recorder = object.__new__(RunRecorder)
    recorder.root = Path(request["run_dir"]).parent
    recorder.run_dir = Path(request["run_dir"])
    recorder.artifacts = []
    recorder._counter = request["counter"]
    session = object.__new__(Session)
    session.snapshot = from_dict(request["snapshot"])
    session.settings = Settings(shots=request["shots"], seed=request["seed"])
    session.recorder = recorder
    output = session.dispatch(request["name"], request["args"])
    json.dump(
        {
            "output": output,
            "counter": recorder._counter,
            "artifacts": [{"kind": a.kind, "path": str(a.path), "caption": a.caption} for a in recorder.artifacts],
        },
        sys.stdout,
        allow_nan=False,
    )


if __name__ == "__main__":
    main()
