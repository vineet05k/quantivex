"""The plain-English summary copies tool numbers verbatim; the draft checker flags invented ones."""

from quantivex.narrative import check_draft, compose_answer, plain_summary

ROW = {
    "role": "quantivex_best",
    "layout": [18, 31, 32, 33],
    "e_noisy_hartree": -106.917512,
    "verdict": "OUTSIDE_TOLERANCE_95",
    "error_mha": 39.473,
    "confidence_radius_mha": 36.666,
    "beats_hartree_fock": True,
}
PROBLEM = {
    "molecule": "N2",
    "bond_length_angstrom": 2.0,
    "basis": "sto-3g",
    "active_space": "CAS(2e,2o)",
    "qubits": 4,
    "e_hf_hartree": -106.87150405,
    "e_exact_hartree": -106.95698556,
    "correlation_energy_mha": -85.4815,
}
RESULTS = [
    {"tool": "run_chemistry", "output": {"problem": PROBLEM, "vqe": {}, "noisy": ROW}},
    {
        "tool": "compare_layouts",
        "output": {
            "problem": PROBLEM,
            "rows": [ROW, dict(ROW, role="qiskit_default", layout=[99, 92, 80, 81], error_mha=52.507)],
            "quantivex_vs_qiskit_error_mha": {"difference_mha": 13.034, "combined_two_sigma_mha": 11.118},
            "cost_vs_error_spearman_rho": 0.9,
            "cost_vs_error_p_value": 0.0001,
            "correlation_sample_size": 15,
            "correlation_reliable": True,
        },
    },
    {"tool": "render_chip_map", "output": {"figure": "x.png"}},
    {"tool": "run_chemistry", "output": {"error": "bad"}},
]


def test_summary_states_verdict_and_numbers_verbatim():
    text = plain_summary(RESULTS)
    assert "-106.917512 Ha" in text and "39.473 mHa ± 36.666 mHa (95%)" in text
    assert "misses chemical accuracy" in text and "lower than Hartree–Fock" in text
    assert "ρ = 0.9 (positive means" in text and "permutation p = 0.0001" in text and "reliable (p < 0.05" in text
    assert "13.034 mHa in this one run (for scale, the combined two-sigma shot noise is 11.118 mHa)" in text
    assert "not a significance test" in text
    assert check_draft(text, RESULTS) == []  # the summary itself passes its own check


def test_draft_checker_accepts_rounding_and_unit_conversion_and_flags_inventions():
    draft = (
        "The best chain [18, 31, 32, 33] has an error of 39.5 mHa (0.0395 Ha) with radius about 37 mHa, "
        "versus 52.5 mHa for Qiskit, so ρ=0.90. The ideal result would be 123.4 mHa better. N2 at 2.0 Å."
    )
    assert check_draft(draft, RESULTS, "N2 at 2.0 Å") == ["123.4"]


def test_compose_labels_model_text_and_handles_missing_draft():
    answer, flagged = compose_answer(RESULTS, "Error is 777.7 mHa", "q")
    assert flagged == ["777.7"]
    assert answer.index("Summary (computed") < answer.index("(unverified)") < answer.index("777.7")
    clean, none = compose_answer(RESULTS, None)
    assert none == [] and "unverified" not in clean
    assert plain_summary([{"tool": "render_chip_map", "output": {}}]).startswith("The tools produced figures only")
