"""Actual Runtime SDK in local mode; injected remote service for submission contracts."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest
from qiskit import QuantumCircuit
from qiskit.primitives import StatevectorSampler
from qiskit.providers import JobStatus
from qiskit.quantum_info import SparsePauliOp, Statevector

pytest.importorskip("qiskit_ibm_runtime")

from qiskit_ibm_runtime.execution_span import ExecutionSpans, SliceSpan

from quantivex import ibm_execution as ibm
from quantivex.chemistry import get_problem, qubit_hamiltonian
from quantivex.cli import main
from quantivex.errors import ConfigError, HardwareError
from quantivex.experiment import _measurement_circuits
from quantivex.ibm_import import fake_backend
from quantivex.vqe import build_circuit, run_vqe


@pytest.fixture(scope="module")
def h2():
    return run_vqe(get_problem("H2", 0.75))


@pytest.fixture
def prepared(tmp_path, h2):
    backend = fake_backend("FakeManilaV2")
    folder = tmp_path / "job"
    plan = ibm.prepare(h2, backend, folder, shots=256, layout=[4, 2, 0, 1])
    return folder, backend, plan


def test_real_runtime_sdk_fake_backend_end_to_end(prepared):
    folder, backend, plan = prepared
    assert plan["execution"] == "local_test"
    assert "cx" in plan["native_operations"] and "cz" not in plan["native_operations"]
    assert all(c["initial_layout"] == [4, 2, 0, 1] for c in plan["circuits"])
    assert plan["total_shots"] == 256 * len(plan["groups"])
    assert not (folder / "job.json").exists()
    result = ibm.run_local(folder, backend, seed=7)
    assert result["execution"] == "local_test"
    assert result["actual_shots_per_group"] == [256] * len(plan["groups"])
    assert np.isfinite(result["energy_hartree"])
    assert result["sampling_stderr_hartree"] > 0
    assert json.loads((folder / "result.json").read_text()) == result


def test_runtime_execution_spans_are_json_serializable():
    start = datetime(2026, 10, 19, tzinfo=timezone.utc)
    spans = ExecutionSpans([SliceSpan(start, start + timedelta(seconds=1), {0: ((1,), slice(0, 16))})])
    encoded = json.loads(ibm._json({"execution_spans": spans}))
    assert encoded["execution_spans"]["__type__"] == "ExecutionSpans"


def test_group_covariance_positive_negative_and_actual_shots():
    terms = [[("IZ", 1.0), ("ZI", 1.0)]]
    e, se, shots = ibm.energy_from_counts(terms, [{"00": 2, "11": 2}])
    assert e == 0 and se == pytest.approx(np.sqrt(4 / 3)) and shots == [4]
    assert ibm.energy_from_counts(terms, [{"01": 2, "10": 2}])[:2] == (0, 0)
    assert ibm.energy_from_counts([[("II", 3.0)]], [{"00": 9}])[:2] == (3, 0)


def test_bit_order_and_independent_groups():
    e, se, _ = ibm.energy_from_counts([[("IZ", 2)], [("ZI", 3)]], [{"01": 8}, {"01": 5}])
    assert (e, se) == (1, 0)


@pytest.mark.parametrize("counts", [{"0": 2}, {"0x": 2}, {"00": 1}, {"00": -2}, {}, {"00": True}])
def test_reject_bad_counts(counts):
    with pytest.raises(HardwareError):
        ibm.energy_from_counts([[("ZZ", 1)]], [counts])
    with pytest.raises(HardwareError):
        ibm.energy_from_counts([[("ZZ", 1)]], [])


def test_measurement_bases_and_energy_match_exact_for_complex_state():
    qc = QuantumCircuit(2)
    qc.ry(0.7, 0)
    qc.rx(0.9, 1)
    qc.cx(0, 1)
    ham = SparsePauliOp.from_list([("XY", 0.4), ("YZ", -0.8), ("IZ", 0.3), ("II", -1)])
    groups = ham.group_commuting(qubit_wise=True)
    result = StatevectorSampler(seed=21).run(_measurement_circuits(qc, groups), shots=20000).result()
    e, se, _ = ibm.energy_from_counts(
        [list(zip(g.paulis.to_labels(), g.coeffs.real.tolist(), strict=True)) for g in groups],
        [pub.data.meas.get_counts() for pub in result],
    )
    assert e == pytest.approx(Statevector(qc).expectation_value(ham).real, abs=5 * se)


def test_ideal_h2_runtime_path_matches_reference(tmp_path, h2):
    from qiskit_aer import AerSimulator

    backend = AerSimulator(seed_simulator=3)
    folder = tmp_path / "ideal"
    ibm.prepare(h2, backend, folder, shots=20000)
    result = ibm.run_local(folder, backend, seed=3)
    assert result["energy_hartree"] == pytest.approx(h2.problem.e_exact, abs=5 * result["sampling_stderr_hartree"])
    assert Statevector(build_circuit(h2)).expectation_value(qubit_hamiltonian(h2.problem)).real == pytest.approx(
        h2.problem.e_exact, abs=1e-6
    )


@pytest.mark.parametrize(
    "options", [{"shots": 0}, {"shots": True}, {"max_execution_time": 0}, {"layout": [1, 1, 2, 3]}]
)
def test_preparation_rejects_invalid_options(tmp_path, h2, options):
    with pytest.raises(HardwareError):
        ibm.prepare(h2, fake_backend("manila"), tmp_path / "job", **options)
    assert not (tmp_path / "job").exists()


def test_integrity_and_confirmation(prepared):
    folder, _, plan = prepared
    with pytest.raises(HardwareError, match="confirmation"):
        ibm.submit(folder, max_total_shots=plan["total_shots"])
    with pytest.raises(HardwareError, match="budget"):
        ibm.submit(folder, confirm=True, max_total_shots=1)
    with pytest.raises(HardwareError, match="local-test"):
        ibm.submit(folder, confirm=True, max_total_shots=plan["total_shots"])
    data = folder / "circuits.qpy"
    data.write_bytes(data.read_bytes() + b"changed")
    with pytest.raises(HardwareError, match="Invalid"):
        ibm.load_plan(folder)


def remote_setup(prepared, monkeypatch):
    folder, backend, plan = prepared
    plan["execution"] = "ibm_qpu"
    plan["instance"] = "TEST_INSTANCE"
    backend._instance = "TEST_INSTANCE"
    (folder / "plan.json").write_text(json.dumps(plan))
    service = SimpleNamespace(backend=Mock(return_value=backend), job=Mock())
    monkeypatch.setattr(ibm, "_kind", lambda b: "ibm_qpu")
    job = SimpleNamespace(
        job_id=lambda: "remote-job-123",
        backend=lambda: backend,
        tags=[plan["job_tag"]],
        status=Mock(return_value=JobStatus.QUEUED),
        result=Mock(),
    )
    service.job.return_value = job
    return folder, backend, plan, service, job


def test_remote_submission_receipt_gates_and_fetch(prepared, monkeypatch):
    folder, backend, plan, service, job = remote_setup(prepared, monkeypatch)
    with patch("qiskit_ibm_runtime.SamplerV2") as sampler:
        sampler.return_value.run.return_value = job
        receipt = ibm.submit(folder, confirm=True, max_total_shots=plan["total_shots"], service=service)
        assert receipt["job_id"] == "remote-job-123"
        assert sampler.call_args.kwargs["mode"] is backend
        assert sampler.call_args.kwargs["options"]["environment"]["job_tags"] == [plan["job_tag"]]
        assert sampler.return_value.run.call_args.kwargs["shots"] == 256
        with pytest.raises(HardwareError, match="receipt already exists"):
            ibm.submit(folder, confirm=True, max_total_shots=plan["total_shots"], service=service)
        assert sampler.return_value.run.call_count == 1
    assert ibm.fetch(folder, service)["status"] == "QUEUED"
    job.result.assert_not_called()
    job.status.return_value = JobStatus.DONE
    runtime_result = type("RuntimeResult", (list,), {})
    job.result.return_value = runtime_result(
        [
            SimpleNamespace(data=SimpleNamespace(meas=SimpleNamespace(get_counts=lambda: {"0000": 256})))
            for _ in plan["groups"]
        ]
    )
    start = datetime(2026, 10, 19, tzinfo=timezone.utc)
    job.result.return_value.metadata = {
        "execution_spans": ExecutionSpans([SliceSpan(start, start + timedelta(seconds=1), {0: ((1,), slice(0, 256))})])
    }
    result = ibm.fetch(folder, service)
    assert result["execution"] == "ibm_qpu" and result["job_id"] == receipt["job_id"]
    assert result["runtime_metadata"]["execution_spans"]["__type__"] == "ExecutionSpans"
    json.dumps(result)
    plan["shots_per_group"] = 128
    plan["total_shots"] = 128 * len(plan["groups"])
    (folder / "plan.json").write_text(json.dumps(plan))
    with pytest.raises(HardwareError, match="plan changed"):
        ibm.fetch(folder, service)


def test_uncertain_submission_recovery_never_retries(prepared, monkeypatch):
    folder, _, plan, service, job = remote_setup(prepared, monkeypatch)
    with patch("qiskit_ibm_runtime.SamplerV2") as sampler:
        sampler.return_value.run.side_effect = ConnectionError("a transport failure")
        with pytest.raises(HardwareError, match="uncertain"):
            ibm.submit(folder, confirm=True, max_total_shots=plan["total_shots"], service=service)
        assert json.loads((folder / "job.json").read_text())["state"] == "submission_started"
        with pytest.raises(HardwareError, match="receipt already exists"):
            ibm.submit(folder, confirm=True, max_total_shots=plan["total_shots"], service=service)
        assert sampler.return_value.run.call_count == 1
    job.tags = ["unrelated"]
    with pytest.raises(HardwareError, match="backend/tag"):
        ibm.recover(folder, "remote-job-123", service)
    job.tags = [plan["job_tag"]]
    assert ibm.recover(folder, "remote-job-123", service)["job_id"] == "remote-job-123"


def test_authentication_uses_ibm_env_only(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "llm-secret")
    monkeypatch.setenv("QUANTIVEX_IBM_TOKEN", "ibm-secret")
    monkeypatch.setenv("QUANTIVEX_IBM_INSTANCE", "my-instance")
    monkeypatch.delenv("QUANTIVEX_IBM_ACCOUNT", raising=False)
    with patch("qiskit_ibm_runtime.QiskitRuntimeService") as service:
        ibm.runtime_service()
        assert service.call_args.kwargs == {
            "channel": "ibm_quantum_platform",
            "token": "ibm-secret",
            "instance": "my-instance",
        }
        monkeypatch.setenv("QUANTIVEX_IBM_ACCOUNT", "named")
        with pytest.raises(ConfigError):
            ibm.runtime_service()
        monkeypatch.delenv("QUANTIVEX_IBM_TOKEN")
        ibm.runtime_service()
        assert service.call_args.kwargs == {"name": "named", "instance": "my-instance"}


def test_offline_cli(tmp_path, capsys):
    folder = str(tmp_path / "cli")
    assert main(["ibm", "prepare", folder, "--fake", "FakeManilaV2", "--shots", "64"]) == 0
    assert json.loads(capsys.readouterr().out)["execution"] == "local_test"
    assert main(["ibm", "inspect", folder]) == 0
    capsys.readouterr()
    assert main(["ibm", "local-test", folder, "--fake", "manila"]) == 0
    assert json.loads(capsys.readouterr().out)["actual_shots_per_group"]
    assert main(["ibm", "submit", folder, "--max-total-shots", "10000"]) == 2
    assert "confirmation" in capsys.readouterr().err


def test_hardware_gui_needs_no_llm_key(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    entry = Path(__file__).parents[1] / "launch_gui.py"
    app = AppTest.from_file(str(entry), default_timeout=60).run(timeout=60)
    next(r for r in app.radio if r.label == "Workspace").set_value("IBM execution").run()
    assert not app.exception
    next(b for b in app.button if b.label == "Prepare job (no submission)").click().run(timeout=60)
    assert not app.exception and not app.error
    assert next(t for t in app.text_input if t.label == "Prepared job folder").value
    assert any(b.label == "Run this plan locally" for b in app.button)
    assert not any(b.label == "Submit this plan to IBM" for b in app.button)


@pytest.mark.parametrize("device,native", [("torino", "cz"), ("brisbane", "ecr")])
def test_compile_actual_native_targets(tmp_path, h2, device, native):
    backend = fake_backend(device)
    folder = tmp_path / device
    plan = ibm.prepare(h2, backend, folder, shots=16)
    assert native in plan["native_operations"]
    assert any(c["operations"].get(native, 0) > 0 for c in plan["circuits"])
    restored, circuits, _ = ibm.load_plan(folder)
    ibm._validate_circuits(circuits, restored, backend)


def test_routing_keeps_logical_bit_order(tmp_path, h2):
    from qiskit_aer import AerSimulator

    simulator = AerSimulator.from_backend(fake_backend("manila"))
    simulator.set_options(noise_model=None)
    folder = tmp_path / "routed"
    ibm.prepare(h2, simulator, folder, shots=20000, layout=[4, 2, 0, 1])
    result = ibm.run_local(folder, simulator, seed=7)
    assert result["energy_hartree"] == pytest.approx(h2.problem.e_exact, abs=5 * result["sampling_stderr_hartree"])


def test_live_submission_rejects_fake_backend(prepared):
    folder, backend, plan = prepared
    plan["execution"] = "ibm_qpu"
    (folder / "plan.json").write_text(json.dumps(plan))
    service = SimpleNamespace(backend=lambda _: backend)
    with pytest.raises(HardwareError, match="IBMBackend"):
        ibm.submit(folder, confirm=True, max_total_shots=plan["total_shots"], service=service)
    assert not (folder / "job.json").exists()


def test_inactive_backend_and_changed_target_block_submission(prepared, monkeypatch):
    folder, backend, plan, service, _ = remote_setup(prepared, monkeypatch)
    monkeypatch.setattr(backend, "status", lambda: SimpleNamespace(operational=False, status_msg="inactive"))
    with pytest.raises(HardwareError, match="active"):
        ibm.submit(folder, confirm=True, max_total_shots=plan["total_shots"], service=service)
    assert not (folder / "job.json").exists()
    monkeypatch.setattr(backend, "status", lambda: SimpleNamespace(operational=True, status_msg="active"))
    from qiskit.transpiler import Target

    monkeypatch.setattr(backend, "_target", Target(num_qubits=5))
    with pytest.raises(HardwareError, match="no longer supports"):
        ibm.submit(folder, confirm=True, max_total_shots=plan["total_shots"], service=service)
    assert not (folder / "job.json").exists()


def test_gui_rejects_account_backed_plan(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest

    monkeypatch.chdir(tmp_path)
    entry = Path(__file__).parents[1] / "launch_gui.py"
    app = AppTest.from_file(str(entry), default_timeout=60).run(timeout=60)
    next(r for r in app.radio if r.label == "Workspace").set_value("IBM execution").run()
    plan = {"execution": "ibm_qpu", "backend": "test_backend"}
    with patch.object(ibm, "load_plan", return_value=(plan, [], "digest")), patch.object(ibm, "submit") as submit:
        next(t for t in app.text_input if t.label == "Prepared job folder").set_value("reviewed-folder").run()
        assert app.error and "local CLI" in app.error[0].value
        assert not any(b.label == "Submit this plan to IBM" for b in app.button)
        submit.assert_not_called()


def test_missing_or_damaged_plan_is_controlled(tmp_path):
    with pytest.raises(HardwareError, match="Invalid"):
        ibm.load_plan(tmp_path / "nonexistent")
