"""Regression tests for the v1.2.0 independent re-audit findings (Q120-01 ... Q120-10)."""

import dataclasses
import json
import math
from pathlib import Path

import numpy as np
import pytest
from qiskit import QuantumCircuit
from qiskit.quantum_info import Statevector
from qiskit_aer import AerSimulator
from streamlit.testing.v1 import AppTest

from quantivex import chemistry as chem
from quantivex.agent import ask
from quantivex.config import Settings
from quantivex.data import from_dict
from quantivex.errors import ChemistryError, HardwareError
from quantivex.experiment import estimate_energy
from quantivex.hardware import rank_chains, readout_mitigable
from quantivex.narrative import check_draft, compose_answer
from quantivex.noise import build_noise_model, coherence_notes
from quantivex.providers import token_limit
from quantivex.vqe import build_circuit, run_vqe

from .conftest import FakeLLM, line_chip, reply, tool_call

ENTRY = Path(__file__).parents[1] / "launch_gui.py"


# Q120-01 -------------------------------------------------------------------------------------------
def test_mutating_returned_excitations_cannot_change_later_circuits():
    problem = chem.get_problem("H2", 0.75)
    vqe = run_vqe(problem)
    for exc in chem.uccsd_excitations(problem):
        exc.operator.coeffs[:] = 0
    for mode in ("compact", "uccsd"):
        state = Statevector(build_circuit(vqe, mode))
        energy = float(np.real(state.expectation_value(chem.qubit_hamiltonian(problem))))
        assert energy == pytest.approx(vqe.energy, abs=1e-9)


def test_circuit_energy_must_match_optimiser_energy():
    vqe = run_vqe(chem.get_problem("H2", 0.75))
    wrong = dataclasses.replace(vqe, energy=vqe.energy + 0.0209656)
    with pytest.raises(ChemistryError, match="mismatch"):
        build_circuit(wrong, "compact")


# Q120-02 -------------------------------------------------------------------------------------------
RESULTS = [
    {
        "tool": "get_dataset_summary",
        "output": {"num_qubits": 133, "median_readout_error": 0.022949, "provenance": "qiskit-ibm-runtime 0.50.0"},
    },
    {"tool": "run_chemistry", "output": {"noisy": {"e_noisy_hartree": -106.917512, "error_mha": 39.473}}},
]


@pytest.mark.parametrize(
    "draft,flag",
    [
        ("The measured energy is 777.7 Ha.", "777.7"),  # only in the question
        ("Energy 106.917512 Ha", "106.917512"),  # wrong sign
        ("Built with version 0.50", "0.50"),  # number from an unrelated prose string
        ("It used 3 qubits", "3"),  # unsupported small integer
        ("About 7.77 × 10−3 Ha", "7.77 × 10−3"),  # scientific notation is parsed, not skipped
        ("Error 39.6 mHa", "39.6"),  # beyond rounding of 39.473 at one decimal
    ],
)
def test_checker_flags_unsupported_numbers(draft, flag):
    assert flag in check_draft(draft, RESULTS, "energy 777.7 Ha?")


def test_checker_accepts_rounding_unit_changes_and_magnitudes():
    draft = "Energy -106.92 Ha; error 39.5 mHa (0.039473 Ha, also -39.5 as a signed miss); readout 2.29%; 133 qubits"
    assert check_draft(draft, RESULTS) == []


def test_no_false_reassurance():
    answer, flagged = compose_answer(RESULTS, "The measured energy is 777.7 Ha.", "about 777.7 Ha?")
    assert flagged == ["777.7"] and "every number in this explanation" not in answer
    clean, _ = compose_answer(RESULTS, "Error 39.5 mHa")
    assert "does not verify" in clean and "every number in this explanation" not in clean


# Q120-03 -------------------------------------------------------------------------------------------
def _two_qubit(t0, t1):
    def q(i, t):
        return {"id": i, "readout_error": 0.0, "sx_error": 0.0, **({"t1_us": t[0], "t2_us": t[1]} if t else {})}

    return from_dict(
        {
            "schema_version": 2,
            "qubits": [q(0, t0), q(1, t1)],
            "edges": [{"source": 0, "target": 1, "gate_error": 0.0}],
            "durations_ns": {"sx": 32, "x": 32, "cz": 1000, "measure": 1000},
        }
    )


def _excited(snapshot):
    qc = QuantumCircuit(2)
    qc.x([0, 1])
    qc.cz(0, 1)
    qc.save_density_matrix()
    sim = AerSimulator(method="density_matrix", noise_model=build_noise_model(snapshot))
    p = np.real(np.diag(np.asarray(sim.run(qc).result().data()["density_matrix"])))
    return p[1] + p[3], p[2] + p[3]


@pytest.mark.parametrize("known", [0, 1])
def test_known_relaxation_survives_missing_partner_data(known):
    data = [None, None]
    data[known] = (1.0, 1.0)
    p = _excited(_two_qubit(*data))
    expected = math.exp(-(1000 + 32) / 1000)  # X gate (32 ns) then CZ (1000 ns), T1 = 1 us
    assert p[known] == pytest.approx(expected, abs=1e-6)
    assert p[1 - known] == pytest.approx(1.0, abs=1e-9)
    assert coherence_notes(_two_qubit(*data)) == [f"qubit {1 - known}: no T1/T2 data, relaxation omitted"]


def test_t1_without_t2_is_amplitude_damping():
    p = _excited(_two_qubit((1.0, None), (1.0, 1.0)))
    assert p[0] == pytest.approx(p[1], abs=1e-6)  # T2 = T1 already gives no extra decay of populations


# Q120-04 -------------------------------------------------------------------------------------------
def test_response_token_cap_reaches_every_openai_style_request(chip, tmp_path):
    settings = Settings(model="m", api_key="k", max_tokens=128, output_dir=tmp_path / "r", shots=2000)
    llm = FakeLLM([reply(calls=[tool_call("get_dataset_summary", {})]), reply("done")])
    ask(chip, "Summarise the chip", settings, llm)
    assert all(r.get("max_completion_tokens") == 128 for r in llm.requests) and len(llm.requests) == 2
    for provider, expected in [
        ("openai", {"max_completion_tokens": 128}),
        ("ollama", {"max_tokens": 128}),
        ("local", {"max_tokens": 128}),
        ("openai-compatible", {"max_tokens": 128}),
        ("anthropic", {}),
    ]:
        assert token_limit(dataclasses.replace(settings, provider=provider)) == expected


# Q120-05 -------------------------------------------------------------------------------------------
def test_gate_only_ranking_excludes_chains_that_cannot_be_mitigated():
    raw = line_chip()
    raw["qubits"][0]["readout_error"] = 0.49
    snapshot = from_dict(raw)
    assert not readout_mitigable(snapshot, [0, 1])
    assert all(0 not in c.qubits for c in rank_chains(snapshot, 3, include_readout=False))
    assert any(0 in c.qubits for c in rank_chains(snapshot, 3, include_readout=True))


def test_mitigation_fallback_is_reported_not_silent():
    raw = line_chip()
    raw["qubits"][0]["readout_error"] = 0.49
    snapshot = from_dict(raw)
    vqe = run_vqe(chem.get_problem("H2", 0.75))
    out = estimate_energy(vqe, snapshot, [0, 1, 2, 3], shots=500, mitigate_readout=True).describe(vqe.energy)
    assert out["readout_mitigation"] is False and out["readout_mitigation_requested"] is True
    assert "not applied" in out["model_notes"][0] and "[0]" in out["model_notes"][0]


def test_all_unmitigable_chip_gives_actionable_error():
    snapshot = from_dict(line_chip(readout=0.49))
    with pytest.raises(HardwareError, match="readout_mitigation=false"):
        rank_chains(snapshot, 3, include_readout=False)


# Q120-06 -------------------------------------------------------------------------------------------
def test_gui_explains_session_key_when_only_environment_key_exists(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-synthetic-not-real")
    monkeypatch.delenv("QUANTIVEX_GUI_AUTH", raising=False)
    monkeypatch.delenv("QUANTIVEX_PROVIDER", raising=False)
    app = AppTest.from_file(str(ENTRY), default_timeout=30).run(timeout=30)
    app.text_area[0].set_value("Which qubits are best?").run()
    next(b for b in app.button if b.label == "Ask Quantivex").click().run()
    assert any("Paste a session API key" in e.value for e in app.error)


# Q120-09 -------------------------------------------------------------------------------------------
def test_record_reports_the_configured_tool_budget(chip, tmp_path):
    settings = Settings(model="m", api_key="k", tool_timeout_s=1234, output_dir=tmp_path / "r", shots=2000)
    result = ask(
        chip, "Summarise", settings, FakeLLM([reply(calls=[tool_call("get_dataset_summary", {})]), reply("x")])
    )
    recorded = json.loads(Path(result.record_path).read_text())["settings"]
    assert recorded["tool_wall_seconds"] == 1234 and recorded["tool_cpu_seconds_posix"] == 1224


# Q120-10 -------------------------------------------------------------------------------------------
def test_correlation_direction_is_explicit():
    from quantivex.narrative import plain_summary

    out = {"rows": [], "cost_vs_error_spearman_rho": 0.95, "cost_vs_error_p_value": 0.0001}
    text = plain_summary([{"tool": "compare_layouts", "output": {"problem": {}, **out}}])
    assert "Chain cost vs error" in text and "higher calibration cost had higher error" in text
