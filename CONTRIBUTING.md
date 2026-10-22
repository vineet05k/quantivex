# Contributing

```bash
pip install -e ".[dev]"
pre-commit install          # optional: ruff on every commit
pytest -m "not slow"        # fast suite, no API key needed (the LLM is faked)
pytest                      # full suite, including the Torino layout study
ruff check src tests scripts && ruff format --check src tests scripts && mypy
```

Rules of the road:
- Every number the user sees must come from Python, never from the LLM.
- New physics needs a test against an independent reference (PySCF, a Qiskit simulation, or an analytic case).
- New agent tools need a strict schema (`additionalProperties: false`), bounds, and a fake-LLM test that includes an error path.
- Regenerate `docs/benchmark.md` with `python scripts/benchmark_layouts.py` when results-affecting code changes, and update the README table from it.
- Regenerate the integral cache with `python scripts/build_integral_cache.py` (Linux or macOS) when chemistry definitions change.
