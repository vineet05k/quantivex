"""Run directories and reproducible reports (JSON record + Markdown summary)."""

from __future__ import annotations

import json
import os
import platform
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from . import __version__
from .data import Snapshot


@dataclass
class Artifact:
    kind: str
    path: Path
    caption: str

    def describe(self) -> dict[str, str]:
        return {"kind": self.kind, "file": self.path.name, "caption": self.caption}


@dataclass
class RunRecorder:
    """Owns one run directory. Artifact file names are generated here, never taken from the LLM."""

    root: Path
    run_dir: Path = field(init=False)
    artifacts: list[Artifact] = field(default_factory=list)
    _counter: int = 0

    def __post_init__(self) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.run_dir = Path(self.root) / f"run_{stamp}"
        self.run_dir.mkdir(parents=True, exist_ok=False)

    def artifact_path(self, stem: str, suffix: str = ".png") -> Path:
        self._counter += 1
        safe = re.sub(r"[^a-z0-9_]+", "_", stem.lower()).strip("_")[:40] or "artifact"
        return self.run_dir / f"{self._counter:02d}_{safe}{suffix}"

    def add(self, kind: str, path: Path, caption: str) -> Artifact:
        art = Artifact(kind, path, caption)
        self.artifacts.append(art)
        return art


def environment() -> dict[str, str]:
    import numpy
    import qiskit
    import qiskit_aer
    import scipy

    sdk_versions = {}
    for name in ("openai", "anthropic", "pyscf", "qiskit-ibm-runtime", "streamlit"):
        try:
            sdk_versions[name] = version(name)
        except PackageNotFoundError:
            sdk_versions[name] = "not installed"
    return {
        **sdk_versions,
        "quantivex": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "qiskit": qiskit.__version__,
        "qiskit_aer": qiskit_aer.__version__,
        "numpy": numpy.__version__,
        "scipy": scipy.__version__,
    }


def _md_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    head = "| " + " | ".join(columns) + " |\n| " + " | ".join("---" for _ in columns) + " |\n"
    body = "".join("| " + " | ".join(str(r.get(c, "")) for c in columns) + " |\n" for r in rows)
    return head + body


def write_reports(record: dict[str, Any], snapshot: Snapshot, run_dir: Path) -> tuple[Path, Path]:
    """Write record.json (everything) and report.md (human summary) into run_dir."""
    json_path = run_dir / "record.json"
    temporary = run_dir / "record.json.tmp"
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(record, indent=2, default=str, allow_nan=False))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, json_path)

    lines = [
        "# Quantivex run report",
        "",
        f"**Question:** {record['question']}",
        "",
        f"**Status:** {record.get('status', 'unknown')}",
        "",
        "## Answer",
        "",
        str(record.get("answer") or "(no answer)"),
        "",
        "## Data provenance",
        "",
        f"- Dataset: {snapshot.label}",
        f"- Source: {snapshot.provenance}",
        f"- Calibration timestamp: {snapshot.calibration_timestamp or 'unknown'}",
        f"- SHA-256: `{snapshot.sha256}`",
        f"- Provider: {record.get('provider', 'openai')} | Model: {record.get('model')} "
        f"| Quantivex {__version__} | {record.get('created_utc')}",
        "",
    ]
    for result in record.get("tool_results", []):
        if result.get("tool") == "compare_layouts" and "rows" in result.get("output", {}):
            out = result["output"]
            lines += [
                "## Layout comparison",
                "",
                _md_table(
                    out["rows"],
                    ["role", "layout", "two_qubit_gates", "chain_esp", "error_mha", "two_sigma_mha", "verdict"],
                ),
                f"Spearman rho (chain cost vs error): {out.get('cost_vs_error_spearman_rho')} "
                f"(p = {out.get('cost_vs_error_p_value')})",
                "",
            ]
        if result.get("tool") == "energy_curve" and "points" in result.get("output", {}):
            out = result["output"]
            cols = list(out["points"][0].keys()) if out["points"] else []
            lines += ["## Energy curve", "", _md_table(out["points"], cols), ""]
    if record.get("artifacts"):
        lines += ["## Figures", ""]
        for art in record["artifacts"]:
            lines += [f"### {art['caption']}", "", f"![{art['caption']}]({art['file']})", ""]
    lines += [
        "## Agent audit trail",
        "",
        _md_table(record.get("tool_calls", []), ["round", "tool", "ok", "seconds", "error"]),
        "",
        "## Caveats",
        "",
        "- Noisy results are simulations driven by a calibration snapshot, not hardware runs.",
        "- VQE parameters are optimised noiselessly; the noisy study measures how hardware noise "
        "degrades the optimised state on each layout.",
        "- Chain scores are a calibration proxy. Correlations against simulated energy error are exploratory, "
        "use random chains only, and do not establish independent hardware validity.",
        "- STO-3G basis and small active spaces: a methods demonstrator, not production chemistry.",
        "",
    ]
    md_path = run_dir / "report.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path
