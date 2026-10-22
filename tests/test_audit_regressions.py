"""Counterexamples from the forensic audit, with independently derived expectations."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from qiskit.quantum_info import SparsePauliOp
from scipy.stats import binom
from streamlit.testing.v1 import AppTest

from quantivex.agent import Session, ask
from quantivex.chemistry import get_problem, sector_ground_energy, spin_squared_operator
from quantivex.data import from_dict
from quantivex.errors import AgentError, ChemistryError, DataValidationError, HardwareError
from quantivex.experiment import _expectation, bernstein_radius, confidence_radius, sampling_radius, verdict
from quantivex.ibm_import import fake_backend, live_backend, snapshot_from_backend
from quantivex.vqe import run_vqe
from quantivex.worker import bounded_tool

from .conftest import FakeLLM, line_chip, reply, tool_call


@pytest.mark.parametrize("counts,variance", [({"00": 50, "11": 50}, 4 / 99), ({"01": 50, "10": 50}, 0)])
def test_group_covariance_counterexamples(counts, variance):
    mean, var = _expectation(SparsePauliOp.from_list([("IZ", 1), ("ZI", 1)]), counts, 100)
    assert mean == 0 and var == pytest.approx(variance)
    # Scaling both terms by 1/0.8 multiplies variance by 1/0.8**2.
    assert _expectation(SparsePauliOp.from_list([("IZ", 1), ("ZI", 1)]), counts, 100, [0.8, 0.8])[1] == pytest.approx(
        variance / 0.8**2
    )


def test_rare_outcome_does_not_gain_certainty_from_zero_observed_variance():
    group = SparsePauliOp.from_list([("Z", 1)])
    mean, var = _expectation(group, {"1": 1000}, 1000)
    radius = sampling_radius([group], [1000])
    assert mean == -1 and var == 0 and radius > 0.002
    assert verdict(mean + 1, 0, radius)["verdict"] == "INCONCLUSIVE_95"
    assert verdict(0, 0)["verdict"] == "UNASSESSED"


@pytest.mark.parametrize("shots", [2, 20, 100, 1000])
def test_confidence_coverage_by_exact_binomial_enumeration(shots):
    group = SparsePauliOp.from_list([("Z", 2.5), ("I", -8)])
    radius = sampling_radius([group], [shots], [0.8])
    for probability in [0.001, 0.02, 0.2, 0.5, 0.98, 0.999]:
        k = np.arange(shots + 1)
        observed = 2.5 * (1 - 2 * k / shots) / 0.8 - 8
        true = 2.5 * (1 - 2 * probability) / 0.8 - 8
        miss = binom.pmf(k, shots, probability)[np.abs(observed - true) > radius].sum()
        assert miss <= 0.05 + 1e-12


def test_combined_radius_positive_for_unobserved_rare_outcomes():
    group = SparsePauliOp.from_list([("Z", 1)])
    radius = confidence_radius([group], [{"1": 1000}])
    assert 0.002 < radius <= sampling_radius([group], [1000], alpha=0.025)
    assert verdict(0.0, 0, radius)["verdict"] == "INCONCLUSIVE_95"


@pytest.mark.parametrize("shots", [2, 20, 100, 1000, 4000])
def test_combined_radius_coverage_by_exact_binomial_enumeration(shots):
    # Every possible outcome of one attenuated two-term group, weighted by its exact binomial probability.
    group = SparsePauliOp.from_list([("Z", 2.5), ("I", -8)])
    k = np.arange(shots + 1)
    radii = np.array([confidence_radius([group], [{"0": int(shots - j), "1": int(j)}], [[0.8]]) for j in k])
    observed = 2.5 * (1 - 2 * k / shots) / 0.8 - 8
    for probability in [0.0005, 0.001, 0.02, 0.2, 0.5, 0.98, 0.999]:
        true = 2.5 * (1 - 2 * probability) / 0.8 - 8
        miss = binom.pmf(k, shots, probability)[np.abs(observed - true) > radii].sum()
        assert miss <= 0.05 + 1e-12


def test_bernstein_adapts_to_variance_and_beats_hoeffding_at_scale():
    group = SparsePauliOp.from_list([("Z", 1)])
    hoeffding = sampling_radius([group], [4000])
    mixed = confidence_radius([group], [{"0": 2000, "1": 2000}])
    skewed = confidence_radius([group], [{"0": 3960, "1": 40}])
    # Low observed variance gives a much tighter bound; at maximal variance the price of
    # splitting alpha between the two bounds is small (Hoeffding at alpha/2).
    assert skewed < 0.5 * hoeffding
    assert mixed == pytest.approx(sampling_radius([group], [4000], alpha=0.025)) and mixed < 1.1 * hoeffding
    assert bernstein_radius([group], [4000], [0.0]) > 0
    with pytest.raises(ValueError):
        bernstein_radius([group], [1], [0.0])
    with pytest.raises(ValueError):
        bernstein_radius([group], [100], [-1.0])


def test_snapshot_is_deeply_immutable_and_defensive(chip):
    raw = line_chip()
    snapshot = from_dict(raw)
    raw["qubits"][0]["readout_error"] = 0.9
    assert snapshot.qubits[0].readout_error != 0.9
    for mapping, key, value in [
        (snapshot.durations_ns, "sx", 32000),
        (snapshot.qubit_map, 0, None),
        (snapshot.link_map, (0, 1), None),
        (snapshot.adjacency, 0, frozenset()),
    ]:
        with pytest.raises(TypeError):
            mapping[key] = value
    copy = snapshot.to_dict()
    copy["durations_ns"]["sx"] = 32000
    assert from_dict(copy).sha256 != snapshot.sha256


def test_sparse_huge_id_rejected_before_target_construction():
    with pytest.raises(DataValidationError, match="511"):
        from_dict({"qubits": [{"id": 1000000000, "readout_error": 0.1}], "edges": []})


def test_nonfinite_tools_and_images_rejected(chip, settings):
    from quantivex.blur import quantum_blur
    from quantivex.errors import QuantivexError

    session = Session(chip, settings)
    for x in [float("nan"), float("inf"), -float("inf")]:
        assert "error" in session.dispatch("render_noise_blur", {"theta": x})
        with pytest.raises(QuantivexError, match="finite"):
            quantum_blur(np.array([[1, x], [1, 1]]))


def test_failed_tools_cannot_promote_invented_scientific_prose(chip, settings):
    llm = FakeLLM([reply(calls=[tool_call("unknown", {})]), reply("IBM proved energy -999 Ha on 9999 qubits")])
    with pytest.raises(AgentError, match="No tool succeeded"):
        ask(chip, "Compute chemistry", settings, llm)
    record = json.loads(next(settings.output_dir.rglob("record.json")).read_text())
    assert record["status"] == "failed"
    assert "-999" not in record["answer"]
    assert record["model_draft_unverified"].startswith("IBM")


def test_even_successful_irrelevant_tool_does_not_certify_model_claim(chip, settings):
    llm = FakeLLM([reply(calls=[tool_call("get_dataset_summary", {})]), reply("Real QPU energy -999 Ha")])
    result = ask(chip, "Calculate energy", settings, llm)
    computed, explanation = result.summary.split("Explanation from the language model (unverified)")
    assert "-999" not in computed and "Task completion and relevance require review" in computed
    assert "-999" in explanation and result.unverified_numbers == ["-999"]
    assert "Check these numbers" in explanation
    record = json.loads(Path(result.record_path).read_text())
    assert record["task_completion_verified"] is False


def test_successful_tool_survives_later_provider_failure(chip, settings):
    def create(**kwargs):
        if not any(m["role"] == "tool" for m in kwargs["messages"]):
            return reply(calls=[tool_call("get_dataset_summary", {})])
        raise ConnectionError("SYNTHETIC_SECRET_DO_NOT_COPY")

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(AgentError):
        ask(chip, "summary", settings, client)
    path = next(settings.output_dir.rglob("record.json"))
    record = json.loads(path.read_text())
    assert record["status"] == "failed" and record["tool_calls"][0]["ok"]
    assert "num_qubits" in record["tool_results"][0]["output"]
    assert "SYNTHETIC_SECRET" not in path.read_text()
    assert (path.parent / "report.md").exists() and (path.parent / "events.jsonl").exists()


def test_wall_timeout_kills_actual_worker(chip, settings):
    output = bounded_tool(Session(chip, settings), "get_dataset_summary", {}, timeout=0.001)
    assert "budget and was stopped" in output["error"] and "QUANTIVEX_TOOL_TIMEOUT" in output["error"]


def test_vqe_twelve_qubits_rejected_before_optimization():
    with pytest.raises(ChemistryError, match="8 qubits"):
        run_vqe(get_problem("N2", 1.1, 6, 6))


@pytest.mark.parametrize("name", ["manila", "brisbane"])
def test_cx_ecr_calibration_not_silently_recast_as_cz(name):
    with pytest.raises(DataValidationError, match="CZ calibration only"):
        snapshot_from_backend(fake_backend(name))


def test_export_uses_same_explicit_authentication(monkeypatch):
    monkeypatch.setenv("QUANTIVEX_IBM_TOKEN", "SYNTHETIC_IBM_TOKEN")
    monkeypatch.setenv("QUANTIVEX_IBM_INSTANCE", "TEST_INSTANCE")
    monkeypatch.delenv("QUANTIVEX_IBM_ACCOUNT", raising=False)
    with patch("qiskit_ibm_runtime.QiskitRuntimeService") as service:
        live_backend("example")
        assert service.call_args.kwargs["instance"] == "TEST_INSTANCE"
        assert service.call_args.kwargs["token"] == "SYNTHETIC_IBM_TOKEN"


def test_lih_singlet_reference_is_not_replaced_by_triplet():
    problem = get_problem("LiH", 3.5)
    assert problem.e_exact - sector_ground_energy(problem) == pytest.approx(0.00233892735404, abs=1e-9)
    assert problem.describe()["target_spin_s"] == 0
    assert run_vqe(problem).spin_squared == pytest.approx(0, abs=1e-8)
    with pytest.raises(ValueError):
        problem.h1.setflags(write=True)


def test_spin_operator_distinguishes_singlet_triplet():
    # Two singly occupied spatial orbitals: alpha-alpha triplet has S(S+1)=2.
    matrix = spin_squared_operator(2).to_matrix()
    psi = np.zeros(16)
    psi[3] = 1
    assert np.vdot(psi, matrix @ psi).real == pytest.approx(2)


def test_fresh_gui_sessions_do_not_receive_server_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "SERVER_SENTINEL_NEVER_SEND")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "OTHER_SERVER_SENTINEL")
    entry = Path(__file__).parents[1] / "launch_gui.py"
    for _ in range(2):
        app = AppTest.from_file(str(entry), default_timeout=60).run(timeout=30)
        assert not app.exception
        assert all(t.value != "SERVER_SENTINEL_NEVER_SEND" for t in app.text_input)
        for element in app:
            proto = getattr(element, "proto", None)
            if proto is not None:
                assert b"SERVER_SENTINEL" not in proto.SerializeToString()


def test_oidc_unauthenticated_stops_before_workspace(monkeypatch):
    monkeypatch.setenv("QUANTIVEX_GUI_AUTH", "oidc")
    entry = Path(__file__).parents[1] / "launch_gui.py"
    with patch("streamlit.user", SimpleNamespace(is_logged_in=False)), patch("streamlit.login") as login:
        app = AppTest.from_file(str(entry), default_timeout=60).run(timeout=30)
        login.assert_called_once()
        assert not app.radio and not app.text_input


@pytest.mark.parametrize("field", ["groups", "reference_energy_hartree", "integrals"])
def test_qpy_binding_rejects_changed_experiment_semantics(tmp_path, field):
    from quantivex import ibm_execution as ibm

    folder = tmp_path / "prepared"
    plan = ibm.prepare(run_vqe(get_problem("H2", 0.75)), fake_backend("manila"), folder, shots=16)
    if field == "groups":
        plan[field][0] = [["IIII", -999]]
    elif field == "integrals":
        plan[field]["e_core"] = -999
    else:
        plan[field] = -999
    (folder / "plan.json").write_text(json.dumps(plan))
    with pytest.raises(HardwareError, match="Invalid") as failure:
        ibm.load_plan(folder)
    assert "match prepared circuit" in str(failure.value.__cause__)


def test_instance_mismatch_blocks_before_submission(tmp_path, monkeypatch):
    from quantivex import ibm_execution as ibm

    folder = tmp_path / "prepared"
    backend = fake_backend("manila")
    plan = ibm.prepare(run_vqe(get_problem("H2", 0.75)), backend, folder, shots=16)
    plan.update(execution="ibm_qpu", instance="REVIEWED_INSTANCE")
    (folder / "plan.json").write_text(json.dumps(plan))
    backend._instance = "OTHER_INSTANCE"
    monkeypatch.setattr(ibm, "_kind", lambda _: "ibm_qpu")
    with patch("qiskit_ibm_runtime.SamplerV2") as sampler:
        with pytest.raises(HardwareError, match="instance does not match"):
            ibm.submit(
                folder,
                confirm=True,
                max_total_shots=plan["total_shots"],
                service=SimpleNamespace(backend=lambda *a, **k: backend),
            )
        sampler.assert_not_called()
    assert not (folder / "job.json").exists()


def test_each_measurement_circuit_uses_its_own_final_readout_map():
    from quantivex.experiment import readout_factors

    raw = line_chip()
    raw["qubits"][0]["readout_error"] = 0.01
    raw["qubits"][1]["readout_error"] = 0.2
    snapshot = from_dict(raw)
    circuits = [
        SimpleNamespace(layout=SimpleNamespace(final_index_layout=lambda: [0, 1])),
        SimpleNamespace(layout=SimpleNamespace(final_index_layout=lambda: [1, 0])),
    ]
    assert readout_factors(circuits, snapshot, 2) == [[0.98, 0.6], [0.6, 0.98]]


def test_oidc_unverified_email_is_denied(monkeypatch):
    monkeypatch.setenv("QUANTIVEX_GUI_AUTH", "oidc")
    monkeypatch.setenv("QUANTIVEX_GUI_ALLOWED_EMAILS", "allowed@example.org")
    user = SimpleNamespace(is_logged_in=True, get={"email": "allowed@example.org", "email_verified": False}.get)
    with patch("streamlit.user", user):
        app = AppTest.from_file(str(Path(__file__).parents[1] / "launch_gui.py"), default_timeout=30).run(timeout=30)
        assert app.error and not app.radio


def test_authorized_gui_never_forwards_server_key_to_changed_endpoint(monkeypatch):
    monkeypatch.setenv("QUANTIVEX_GUI_AUTH", "oidc")
    monkeypatch.setenv("QUANTIVEX_GUI_ALLOWED_EMAILS", "allowed@example.org")
    monkeypatch.setenv("OPENAI_API_KEY", "SERVER_KEY_DO_NOT_FORWARD")
    user = SimpleNamespace(is_logged_in=True, get={"email": "allowed@example.org", "email_verified": True}.get)
    with patch("streamlit.user", user), patch("quantivex.providers.check_compatibility") as check:
        app = AppTest.from_file(str(Path(__file__).parents[1] / "launch_gui.py"), default_timeout=30).run(timeout=30)
        assert not app.exception
        endpoint = next(t for t in app.text_input if t.label == "API base URL")
        endpoint.set_value("https://example.invalid/v1").run()
        assert app.error and "explicit API key override" in app.error[0].value
        check.assert_not_called()


def test_cached_quantum_results_cannot_be_mutated_by_callers():
    from quantivex.chemistry import qubit_hamiltonian

    problem = get_problem("H2", 0.75)
    result = run_vqe(problem)
    with pytest.raises(ValueError):
        result.parameters.setflags(write=True)
    expected = qubit_hamiltonian(problem).coeffs.copy()
    changed = qubit_hamiltonian(problem)
    changed.coeffs[0] = -999
    assert np.array_equal(qubit_hamiltonian(problem).coeffs, expected)
    spin = spin_squared_operator(2)
    expected_spin = spin.coeffs.copy()
    spin.coeffs[0] = -999
    assert np.array_equal(spin_squared_operator(2).coeffs, expected_spin)


@pytest.mark.parametrize("name", ["backend", "calibration_timestamp"])
def test_mutable_snapshot_metadata_rejected(name):
    raw = line_chip()
    raw[name] = {"mutable": True}
    with pytest.raises(DataValidationError, match="string or null"):
        from_dict(raw)


@pytest.mark.parametrize("molecule,bond,ne,no", [("H2", 0.75, 2, 2), ("N2", 1.1, 4, 4)])
def test_analytic_ucc_gradient_matches_independent_central_difference(molecule, bond, ne, no):
    from scipy.sparse import csr_matrix
    from scipy.sparse.linalg import expm_multiply

    from quantivex.chemistry import qubit_hamiltonian
    from quantivex.vqe import _energy_gradient, _generators

    p = get_problem(molecule, bond, ne, no)
    h = csr_matrix(qubit_hamiltonian(p).to_matrix(sparse=True).real)
    generators = _generators(p)
    initial = np.zeros(2**p.n_qubits)
    initial[p.hf_index] = 1
    x = np.random.default_rng(1827).normal(0, 0.1, len(generators))
    value, gradient = _energy_gradient(h, generators, initial, x)

    def independent_energy(parameters):
        state = initial.copy()
        for theta, generator in zip(parameters, generators, strict=True):
            state = expm_multiply(theta * generator, state)
        return float(state @ (h @ state))

    assert value == pytest.approx(independent_energy(x), abs=1e-11)
    for j in sorted({0, len(x) // 2, len(x) - 1}):
        plus = x.copy()
        minus = x.copy()
        plus[j] += 1e-5
        minus[j] -= 1e-5
        difference = (independent_energy(plus) - independent_energy(minus)) / (2e-5)
        assert gradient[j] == pytest.approx(difference, abs=2e-7, rel=1e-5)
