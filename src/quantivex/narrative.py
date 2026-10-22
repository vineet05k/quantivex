"""Plain-English answers that cannot drift from the computed numbers.

`plain_summary` turns tool outputs into sentences with a fixed template, so every number in it is
copied from a tool result. `check_draft` lists the numbers in the model's own explanation that do
not appear in any tool output (or in the question), so a reader knows which claims to check.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any

from .experiment import CHEMICAL_ACCURACY_HA, DEGRADED, TRUSTWORTHY, UNTRUSTWORTHY

_ACCURACY_MHA = round(1000 * CHEMICAL_ACCURACY_HA, 1)

VERDICT_TEXT = {
    TRUSTWORTHY: f"agrees with the reference to chemical accuracy ({_ACCURACY_MHA} mHa) with 95% confidence",
    UNTRUSTWORTHY: f"misses chemical accuracy ({_ACCURACY_MHA} mHa) with 95% confidence, "
    "so it is not reliable chemistry",
    DEGRADED: "cannot be decided: the 95% sampling interval straddles chemical accuracy (more shots would help)",
    "UNASSESSED": "has no finite-sample confidence radius, so no verdict was made",
}


def _pct(x: float) -> str:
    return f"{100 * x:.2f}%"


def _verdict_sentence(row: dict[str, Any]) -> str:
    text = VERDICT_TEXT.get(str(row.get("verdict")), str(row.get("verdict")))
    radius = row.get("confidence_radius_mha")
    margin = f" ± {radius} mHa (95%)" if radius is not None else ""
    hf = row.get("beats_hartree_fock")
    hf_text = (
        "" if hf is None else (" It is lower than Hartree–Fock." if hf else " It is not better than Hartree–Fock.")
    )
    return (
        f"On qubits {row.get('layout')}, the noisy energy is {row.get('e_noisy_hartree')} Ha, an error of "
        f"{row.get('error_mha')} mHa{margin} against the exact small-model value. This result {text}.{hf_text}"
    ) + "".join(
        f" Note: {note}." if not note.endswith(".") else f" Note: {note}" for note in row.get("model_notes", [])
    )


def _problem_sentences(out: dict[str, Any]) -> list[str]:
    p, v = out.get("problem", {}), out.get("vqe", {})
    if not p.get("molecule"):
        return []
    lines = [
        f"{p.get('molecule')} at {p.get('bond_length_angstrom')} Å ({p.get('basis')}, {p.get('active_space')}, "
        f"{p.get('qubits')} qubits): exact energy {p.get('e_exact_hartree')} Ha, "
        f"Hartree–Fock {p.get('e_hf_hartree')} Ha, "
        f"correlation energy {p.get('correlation_energy_mha')} mHa."
    ]
    if v:
        lines.append(
            f"Noiseless VQE ({v.get('ansatz')}) reaches {v.get('e_vqe_ideal_hartree')} Ha "
            f"(ansatz error {v.get('ansatz_error_mha')} mHa)."
        )
    return lines


def _dataset(out: dict[str, Any]) -> list[str]:
    if not out.get("label"):
        return []
    return [
        f"Calibration data: {out.get('label')}, measured {out.get('calibration_timestamp') or 'at an unknown time'}. "
        f"Median readout error {_pct(out.get('median_readout_error', 0))}, median two-qubit error "
        f"{_pct(out.get('median_two_qubit_error', 0))}, median T1 {out.get('median_t1_us')} µs, "
        f"median T2 {out.get('median_t2_us')} µs; {out.get('dead_links')} links are unusable."
    ]


def _chains(out: dict[str, Any]) -> list[str]:
    best = out.get("best") or []
    if not best:
        return [f"No usable chain of {out.get('length')} qubits was found."]
    top, worst = best[0], out.get("worst", {})
    return [
        f"Best {out.get('length')}-qubit chain: {top.get('qubits')} with estimated success probability "
        f"{top.get('esp')} ({top.get('score')}). The worst of {out.get('usable_chains')} usable chains is "
        f"{worst.get('qubits')} with {worst.get('esp')}."
    ]


def _chemistry(out: dict[str, Any]) -> list[str]:
    lines = _problem_sentences(out)
    if out.get("noisy"):
        lines.append(_verdict_sentence(out["noisy"]))
    return lines


def _compare(out: dict[str, Any]) -> list[str]:
    lines = _problem_sentences(out)
    rows = {r.get("role"): r for r in out.get("rows", [])}
    for role, name in [
        ("quantivex_best", "Calibration-best chain"),
        ("qiskit_default", "Qiskit's own layout"),
        ("worst_usable_chain", "Worst usable chain"),
    ]:
        if role in rows:
            r = rows[role]
            lines.append(
                f"{name} {r.get('layout')}: error {r.get('error_mha')} mHa ± {r.get('confidence_radius_mha')} mHa "
                f"(95%), {r.get('verdict')}."
            )
    vs = out.get("quantivex_vs_qiskit_error_mha")
    if vs:
        diff, noise = vs.get("difference_mha"), vs.get("combined_two_sigma_mha")
        if isinstance(diff, (int, float)) and isinstance(noise, (int, float)):
            lines.append(
                f"Error reduction of the calibration-best chain over Qiskit's layout: {diff} mHa in this one run "
                f"(for scale, the combined two-sigma shot noise is {noise} mHa). This is descriptive, not a "
                "significance test; compare layouts with the paired multi-seed benchmark."
            )
    if out.get("rows_with_mitigation_fallback"):
        lines.append(
            "Readout mitigation could not be applied to "
            + ", ".join(out["rows_with_mitigation_fallback"])
            + "; those rows are unmitigated and not directly comparable with the others."
        )
    rho, p, n = (
        out.get("cost_vs_error_spearman_rho"),
        out.get("cost_vs_error_p_value"),
        out.get("correlation_sample_size"),
    )
    if rho is not None:
        status = (
            "reliable (p < 0.05 and at least 15 chains)"
            if out.get("correlation_reliable")
            else "not reliable on its own (needs p < 0.05 and at least 15 chains)"
        )
        lines.append(
            f"Chain cost vs error over {n} random chains: Spearman ρ = {rho} (positive means chains with higher "
            f"calibration cost had higher error, as a good score should), permutation p = {p}; "
            f"this is {status}. Within this simulation only."
        )
    return lines


def _curve(out: dict[str, Any]) -> list[str]:
    pts = out.get("points") or []
    if not pts:
        return []
    noisy = [p for p in pts if "noisy_error_mha" in p]
    lo, hi = pts[0].get("bond_length_angstrom"), pts[-1].get("bond_length_angstrom")
    line = f"Energy curve over {len(pts)} bond lengths ({lo}–{hi} Å)"
    if noisy:
        worst = max(noisy, key=lambda p: abs(p["noisy_error_mha"]))
        best = min(noisy, key=lambda p: abs(p["noisy_error_mha"]))
        line += (
            f" on chain {out.get('layout')}: noisy error ranges from {best['noisy_error_mha']} mHa "
            f"at {best['bond_length_angstrom']} Å to {worst['noisy_error_mha']} mHa "
            f"at {worst['bond_length_angstrom']} Å."
        )
    else:
        line += " (noiseless only)."
    return [line]


_RENDERERS = {
    "get_dataset_summary": _dataset,
    "find_qubit_chains": _chains,
    "run_chemistry": _chemistry,
    "compare_layouts": _compare,
    "energy_curve": _curve,
}


def plain_summary(results: Iterable[dict[str, Any]]) -> str:
    """Deterministic sentences built only from successful tool outputs."""
    lines: list[str] = []
    for result in results:
        output = result.get("output", {})
        render = _RENDERERS.get(result.get("tool", ""))
        if render and isinstance(output, dict) and "error" not in output:
            lines.extend(render(output))
    if not lines:
        return "The tools produced figures only; see below."
    return "\n".join(f"- {line}" for line in lines)


_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")
_NUMBER = re.compile(
    r"(?<![A-Za-z_\d.\-])[-−]?\d+(?:,\d{3}(?!\d))*(?:\.\d+)?(?:[eE][-+]?\d+)?"
    r"(?:\s?[×x*]\s?10\^?\(?[-−⁻]?[\d⁰¹²³⁴⁵⁶⁷⁸⁹]+\)?)?"
)
# String fields whose numbers are data (other strings are prose, e.g. notes, and are not evidence).
_DATA_STRING_KEYS = ("active_space", "calibration_timestamp")
# Keys whose values are often quoted as magnitudes, so a sign-free match is accepted.
_MAGNITUDE_KEYS = ("error", "difference", "radius", "sigma", "stderr", "rho")


def _numbers_in(value: Any, key: str = "") -> list[tuple[float, str]]:
    """(number, key it was found under) for every finite numeric field (and data-like string fields)."""
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [(float(value), key)] if math.isfinite(value) else []
    if isinstance(value, str):
        return [(x, key) for x, _ in _parse_numbers(value)] if key in _DATA_STRING_KEYS else []
    if isinstance(value, dict):
        return [item for k, v in value.items() for item in _numbers_in(v, str(k).lower())]
    if isinstance(value, (list, tuple)):
        return [item for v in value for item in _numbers_in(v, key)]
    return []


def _conversions(key: str) -> tuple[float, ...]:
    """Unit changes a writer may legitimately apply to a number stored under `key`."""
    factors = [1.0]
    if "hartree" in key or key.startswith("e_"):
        factors.append(1000.0)  # Ha -> mHa
    if "mha" in key:
        factors.append(0.001)  # mHa -> Ha
    if any(word in key for word in ("readout_error", "two_qubit_error", "esp", "gate_error")):
        factors.append(100.0)  # fraction -> percent
    return tuple(factors)


# Fixed conventions an answer may quote: 95% confidence, chemical accuracy (mHa), significance level, chain count.
_CONVENTIONS = (95.0, round(1000 * CHEMICAL_ACCURACY_HA, 1), 0.05, 15.0)


def _parse_numbers(text: str) -> list[tuple[float, str]]:
    """(value, original token); scientific notation such as 7.77 × 10⁻³ is converted, not skipped."""
    found = []
    for match in _NUMBER.finditer(text):
        token = match.group(0)
        clean = token.replace("−", "-").replace(",", "").translate(_SUPERSCRIPT)
        try:
            sci = re.fullmatch(r"(-?[\d.]+(?:[eE][-+]?\d+)?)\s?[×x*]\s?10\^?\(?(-?\d+)\)?", clean)
            value = float(sci.group(1)) * 10 ** int(sci.group(2)) if sci else float(clean)
        except (ValueError, OverflowError):
            continue
        found.append((value, token))
    return found


def _decimals(token: str) -> int:
    """Displayed precision of a token, in decimal places of its value."""
    clean = token.replace("−", "-").replace(",", "").translate(_SUPERSCRIPT)
    sci = re.fullmatch(r"-?([\d.]+)\s?[×x*]\s?10\^?\(?(-?\d+)\)?", clean)
    if sci:
        mantissa_decimals = len(sci.group(1).split(".")[1]) if "." in sci.group(1) else 0
        return mantissa_decimals - int(sci.group(2))
    if "e" in clean.lower():
        return 12
    return len(clean.split(".")[1]) if "." in clean else 0


def check_draft(draft: str, results: Iterable[dict[str, Any]], question: str = "") -> list[str]:
    """Numbers in the model draft that equal no tool output, after rounding to the precision written.

    Evidence is numeric tool-output fields only (never the user's question, never prose notes). Signs must
    match, except for error-like fields usually quoted as magnitudes. Allowed unit changes depend on the
    field (Ha <-> mHa for energies, fraction -> percent for error rates). Every number is checked, including
    small integers. This is a heuristic: a flag means "not found", and no flag does not verify the
    explanation's claims, its interpretation, or which quantity a number refers to.
    """
    del question  # deliberately not evidence: a number the user typed is not a computed result
    candidates: list[tuple[float, bool]] = [
        (value * f, any(word in key for word in _MAGNITUDE_KEYS))
        for value, key in _numbers_in([r.get("output") for r in results])
        for f in _conversions(key)
    ]
    candidates += [(c, False) for c in _CONVENTIONS]
    flagged: list[str] = []
    for value, token in _parse_numbers(draft or ""):
        tolerance = 0.5 * 10 ** (-_decimals(token)) + 1e-12
        ok = any(
            abs(value - c) <= tolerance or (magnitude and abs(abs(value) - abs(c)) <= tolerance)
            for c, magnitude in candidates
        )
        if not ok and token not in flagged:
            flagged.append(token)
    return flagged


def compose_answer(results: list[dict[str, Any]], draft: str | None, question: str = "") -> tuple[str, list[str]]:
    """Answer = computed summary, then the model's explanation clearly labelled, with unmatched numbers."""
    flagged = check_draft(draft or "", results, question)
    parts = [
        "### Summary (computed by Quantivex; every number comes from a tool result)",
        "",
        plain_summary(results),
        "",
        "All noisy results are local simulations driven by calibration data; no quantum hardware was used. "
        "Task completion and relevance require review: check that these tools answer your question.",
    ]
    if draft and draft.strip():
        parts += ["", "### Explanation from the language model (unverified)", "", draft.strip(), ""]
        if flagged:
            parts.append(
                "**Check these numbers:** the model wrote "
                + ", ".join(f"`{t}`" for t in flagged)
                + (", which matches" if len(flagged) == 1 else ", which match")
                + " no computed output. Trust the summary above and the evidence below."
            )
        else:
            parts.append(
                "Automatic check: no unmatched numbers were detected by this heuristic. It does not verify "
                "the explanation's claims or interpretation."
            )
    return "\n".join(parts), flagged
