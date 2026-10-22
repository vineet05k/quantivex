"""Installed GUI entry point; usable outside an extracted repository."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    from streamlit.web import cli

    sys.argv = [
        "streamlit",
        "run",
        str(Path(__file__).with_name("gui_entry.py")),
        "--server.address=127.0.0.1",
        *sys.argv[1:],
    ]
    raise SystemExit(cli.main())
