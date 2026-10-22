"""Optional IBM Runtime jobs. Preparation never submits; the LLM cannot submit jobs.

The state is optimized locally. SamplerV2 measures that fixed state, with raw
counts retained and covariance within each measurement group included in errors.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
from qiskit import qpy, transpile
from qiskit.quantum_info import SparsePauliOp

from . import __version__
from .errors import ConfigError, HardwareError
from .experiment import _expectation, _measurement_circuits, confidence_radius
from .vqe import VQEResult, build_circuit


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    try:
        from qiskit_ibm_runtime import RuntimeEncoder

        return RuntimeEncoder().default(value)
    except (ImportError, TypeError):
        raise TypeError("Unsupported metadata type: " + type(value).__name__) from None


def _json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False, default=_serialize) + "\n").encode()


def _write(path: Path, value: Any, *, exclusive: bool = False) -> None:
    # Exclusive creation is the submission lock, including across CLI/GUI processes.
    destination = path if exclusive else path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    with destination.open("xb") as stream:
        stream.write(_json(value))
        stream.flush()
        os.fsync(stream.fileno())
    if not exclusive:
        os.replace(destination, path)


def _status_name(status: Any) -> str:
    """Return the stable SDK status name for enums and legacy strings."""
    name = getattr(status, "name", status)
    return str(name).rsplit(".", 1)[-1].upper()


def runtime_service() -> Any:
    """Load an SDK saved account, or IBM-only environment credentials; never save them."""
    try:
        from qiskit_ibm_runtime import QiskitRuntimeService
    except ImportError as exc:
        raise ConfigError('Install the IBM extra: pip install "quantivex[ibm]"') from exc
    kwargs: dict[str, str] = {}
    token = os.environ.get("QUANTIVEX_IBM_TOKEN", "").strip()
    account = os.environ.get("QUANTIVEX_IBM_ACCOUNT", "").strip()
    instance = os.environ.get("QUANTIVEX_IBM_INSTANCE", "").strip()
    if token and account:
        raise ConfigError("Use either QUANTIVEX_IBM_TOKEN or QUANTIVEX_IBM_ACCOUNT, not both")
    if token:
        kwargs.update(channel="ibm_quantum_platform", token=token)
    elif account:
        kwargs["name"] = account
    if instance:
        kwargs["instance"] = instance
    try:
        return QiskitRuntimeService(**kwargs)
    except Exception as exc:
        raise ConfigError(
            "IBM authentication failed. Configure an SDK saved account or QUANTIVEX_IBM_TOKEN "
            "and QUANTIVEX_IBM_INSTANCE. No credentials were written by Quantivex."
        ) from exc


def _kind(backend: Any) -> str:
    try:
        from qiskit_aer import AerSimulator
        from qiskit_ibm_runtime import IBMBackend
        from qiskit_ibm_runtime.fake_provider.fake_backend import FakeBackendV2
    except ImportError as exc:
        raise ConfigError('Install the IBM extra: pip install "quantivex[ibm]"') from exc
    if isinstance(backend, IBMBackend):
        return "ibm_qpu"
    if isinstance(backend, (FakeBackendV2, AerSimulator)):
        return "local_test"
    raise HardwareError("Expected an IBM Runtime backend, IBM fake backend, or AerSimulator")


def get_backend(name: str, service: Any = None, instance: str | None = None) -> Any:
    try:
        return (service if service is not None else runtime_service()).backend(
            name, **({"instance": instance} if instance else {})
        )
    except (ConfigError, HardwareError):
        raise
    except Exception as exc:
        raise HardwareError("Cannot access the requested IBM backend; check its name, account and instance") from exc


def _calibration(backend: Any) -> dict[str, Any]:
    props = backend.properties() if hasattr(backend, "properties") else None
    timestamp = getattr(props, "last_update_date", None)
    return {
        "calibration_id": getattr(backend, "calibration_id", None),
        "properties_updated_at": timestamp.isoformat() if timestamp is not None else None,
        "captured_at": _now(),
        "backend_properties": json.loads(_json(props.to_dict()))
        if props is not None and hasattr(props, "to_dict")
        else None,
    }


def _semantic_context(plan: dict[str, Any]) -> str:
    keys = ("problem", "reference_energy_hartree", "integrals", "state_parameters", "circuit_mode")
    return _sha(_json({key: plan[key] for key in keys}))


def _validate_circuits(circuits: list[Any], plan: dict[str, Any], backend: Any = None) -> None:
    if len(circuits) != len(plan["groups"]) or not circuits:
        raise HardwareError("Circuit count does not match measurement groups")
    for circuit, terms in zip(circuits, plan["groups"], strict=True):
        if (circuit.metadata or {}).get("quantivex_group_sha256") != _sha(_json(terms)):
            raise HardwareError("Measurement-group semantics do not match prepared circuit metadata")
        if (circuit.metadata or {}).get("quantivex_context_sha256") != _semantic_context(plan):
            raise HardwareError("Experiment context does not match prepared circuit metadata")
        if circuit.num_parameters or len(circuit.cregs) != 1 or circuit.cregs[0].name != "meas":
            raise HardwareError("Prepared circuits must be bound and have one meas register")
        if circuit.num_clbits != plan["logical_qubits"]:
            raise HardwareError("Measurement width does not match the logical problem")
        if backend is None:
            continue
        if circuit.num_qubits > backend.num_qubits:
            raise HardwareError("Prepared circuit exceeds backend size; prepare again")
        for instruction in circuit.data:
            op = instruction.operation
            if op.name == "barrier":
                continue
            qargs = tuple(circuit.find_bit(q).index for q in instruction.qubits)
            if not backend.target.instruction_supported(operation_name=op.name, qargs=qargs, parameters=op.params):
                raise HardwareError(f"Backend no longer supports prepared instruction {op.name}{qargs}; prepare again")
        if hasattr(backend, "check_faulty"):
            backend.check_faulty(circuit)


def prepare(
    vqe: VQEResult,
    backend: Any,
    directory: str | Path,
    *,
    shots: int = 4000,
    mode: str = "compact",
    layout: list[int] | None = None,
    seed: int = 1234,
    max_execution_time: int = 300,
) -> dict[str, Any]:
    """Compile to this backend's actual target and write a new, reviewable job folder.

    shots is per QWC group, not per Hamiltonian term. No server job is created.
    max_execution_time is a Runtime job limit, not a price or queue-time estimate.
    """
    from .chemistry import qubit_hamiltonian

    kind = _kind(backend)
    for value, lo, hi, name in ((shots, 2, 100_000, "shots"), (max_execution_time, 1, 10_800, "max_execution_time")):
        if type(value) is not int or not lo <= value <= hi:
            raise HardwareError(f"{name} must be an integer from {lo} to {hi}")
    n = vqe.problem.n_qubits
    if n > 8 or n > backend.num_qubits:
        raise HardwareError(
            "This execution workflow supports up to 8 logical qubits and requires a large enough backend"
        )
    if not vqe.converged:
        raise HardwareError("Local VQE did not converge; do not submit this state")
    if layout is not None and (
        len(layout) != n
        or len(set(layout)) != n
        or any(type(q) is not int or not 0 <= q < backend.num_qubits for q in layout)
    ):
        raise HardwareError(f"Layout must contain {n} distinct physical qubit indices on this backend")
    groups = qubit_hamiltonian(vqe.problem).group_commuting(qubit_wise=True)
    measured = _measurement_circuits(build_circuit(vqe, mode), groups)
    circuits = transpile(measured, backend=backend, initial_layout=layout, optimization_level=3, seed_transpiler=seed)
    group_terms = [list(zip(g.paulis.to_labels(), g.coeffs.real.tolist(), strict=True)) for g in groups]
    for circuit, terms in zip(circuits, group_terms, strict=True):
        circuit.metadata = dict(circuit.metadata or {}) | {"quantivex_group_sha256": _sha(_json(terms))}
    buffer = io.BytesIO()
    qpy.dump(circuits, buffer)
    circuit_bytes = buffer.getvalue()
    plan = {
        "schema_version": 2,
        "quantivex_version": __version__,
        "execution": kind,
        "created_at": _now(),
        "backend": backend.name,
        "instance": getattr(backend, "_instance", None) if kind == "ibm_qpu" else None,
        "account_name": os.environ.get("QUANTIVEX_IBM_ACCOUNT") if kind == "ibm_qpu" else None,
        "backend_qubits": backend.num_qubits,
        "native_operations": sorted(backend.target.operation_names),
        "calibration_at_preparation": _calibration(backend),
        "logical_qubits": n,
        "problem": vqe.problem.describe(),
        "reference_energy_hartree": vqe.problem.e_exact,
        "vqe": vqe.describe(),
        "state_parameters": vqe.parameters.tolist(),
        "integrals": {"h1": vqe.problem.h1.tolist(), "eri": vqe.problem.eri.tolist(), "e_core": vqe.problem.e_core},
        "optimization": "local noiseless; fixed parameters on QPU",
        "circuit_mode": mode,
        "seed_transpiler": seed,
        "shots_per_group": shots,
        "total_shots": shots * len(groups),
        "max_execution_time_seconds": max_execution_time,
        "price_estimate": None,
        "readout_mitigation": False,
        "runtime_primitive": "SamplerV2",
        "job_tag": f"quantivex-{uuid4().hex}",
        "circuits_sha256": _sha(circuit_bytes),
        "groups": group_terms,
        "circuits": [
            {
                "depth": c.depth(),
                "two_qubit_gates": sum(len(i.qubits) == 2 and i.operation.name != "barrier" for i in c.data),
                "operations": dict(c.count_ops()),
                "initial_layout": c.layout.initial_index_layout()[:n] if c.layout is not None else list(range(n)),
                "final_layout": c.layout.final_index_layout()[:n] if c.layout is not None else list(range(n)),
            }
            for c in circuits
        ],
        "versions": {p: version(p) for p in ("qiskit", "qiskit-ibm-runtime", "qiskit-aer", "numpy")},
    }
    for circuit in circuits:
        circuit.metadata["quantivex_context_sha256"] = _semantic_context(plan)
    buffer = io.BytesIO()
    qpy.dump(circuits, buffer)
    circuit_bytes = buffer.getvalue()
    plan["circuits_sha256"] = _sha(circuit_bytes)
    if kind == "ibm_qpu" and not isinstance(plan["instance"], str):
        raise HardwareError("IBM instance could not be resolved; set QUANTIVEX_IBM_INSTANCE explicitly")
    _validate_circuits(circuits, plan, backend)
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "circuits.qpy").write_bytes(circuit_bytes)
    _write(folder / "plan.json", plan)
    return plan


def load_plan(directory: str | Path) -> tuple[dict[str, Any], list[Any], str]:
    """Validate local plan/QPY integrity. Only load job folders from trusted sources."""
    try:
        folder = Path(directory)
        raw = (folder / "plan.json").read_bytes()
        plan = json.loads(raw)
        data = (folder / "circuits.qpy").read_bytes()
        if plan["schema_version"] != 2 or plan["execution"] not in ("ibm_qpu", "local_test"):
            raise ValueError("unsupported plan")
        if _sha(data) != plan["circuits_sha256"]:
            raise ValueError("circuit checksum mismatch")
        shots = plan["shots_per_group"]
        if type(shots) is not int or not 2 <= shots <= 100_000 or plan["total_shots"] != shots * len(plan["groups"]):
            raise ValueError("invalid shot budget")
        if type(plan["logical_qubits"]) is not int or not 1 <= plan["logical_qubits"] <= 8:
            raise ValueError("invalid problem width")
        limit = plan["max_execution_time_seconds"]
        if type(limit) is not int or not 1 <= limit <= 10_800:
            raise ValueError("invalid runtime execution limit")
        for terms in plan["groups"]:
            group = SparsePauliOp.from_list(terms)
            if group.num_qubits != plan["logical_qubits"] or not np.isfinite(group.coeffs).all():
                raise ValueError("invalid measurement group")
            if len(group.group_commuting(qubit_wise=True)) != 1:
                raise ValueError("measurement group is not qubit-wise commuting")
        circuits = qpy.load(io.BytesIO(data))
        _validate_circuits(circuits, plan)
        return plan, circuits, _sha(raw)
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        raise HardwareError("Invalid or incomplete job folder; prepare a new plan") from exc


def _options(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "max_execution_time": plan["max_execution_time_seconds"],
        "environment": {"job_tags": [plan["job_tag"]]},
        "twirling": {"enable_gates": False, "enable_measure": False},
        "dynamical_decoupling": {"enable": False},
    }


def submit(
    directory: str | Path, *, confirm: bool = False, max_total_shots: int, service: Any = None
) -> dict[str, Any]:
    """Submit exactly once from a reviewed plan. Does not wait for QPU results.

    On network failure the receipt stays locked: an accepted remote job may exist.
    Inspect the job tag in IBM's dashboard rather than automatically retrying.
    """
    from qiskit_ibm_runtime import SamplerV2

    if confirm is not True:
        raise HardwareError("Submission requires explicit confirmation after reviewing plan.json")
    plan, circuits, digest = load_plan(directory)
    if type(max_total_shots) is not int or not 0 < plan["total_shots"] <= max_total_shots:
        raise HardwareError(f"Plan requires {plan['total_shots']} shots; increase the explicit total-shot budget")
    if plan["execution"] == "local_test":
        raise HardwareError("This is a local-test plan. Use run_local; it cannot be submitted as an IBM QPU job")
    backend = get_backend(plan["backend"], service, plan.get("instance"))
    if _kind(backend) != "ibm_qpu":
        raise HardwareError("Real submission requires an IBM Runtime IBMBackend")
    if not plan.get("instance") or getattr(backend, "_instance", None) != plan["instance"]:
        raise HardwareError("IBM instance does not match reviewed plan; prepare again")
    status = backend.status()
    if not status.operational or status.status_msg != "active":
        raise HardwareError("Backend is not active/operational; no job was submitted")
    _validate_circuits(circuits, plan, backend)
    sampler = SamplerV2(mode=backend, options=_options(plan))
    receipt = {
        "plan_sha256": digest,
        "backend": plan["backend"],
        "instance": plan.get("instance"),
        "execution": "ibm_qpu",
        "job_tag": plan["job_tag"],
        "state": "submission_started",
        "started_at": _now(),
        "calibration_at_submission": _calibration(backend),
    }
    folder = Path(directory)
    try:
        _write(folder / "job.json", receipt, exclusive=True)
    except FileExistsError as exc:
        raise HardwareError("A submission receipt already exists. Retrieve that job; do not submit again") from exc
    try:
        job = sampler.run(circuits, shots=plan["shots_per_group"])
        receipt.update(state="submitted", job_id=job.job_id(), submitted_at=_now())
        _write(folder / "job.json", receipt)
        return receipt
    except Exception as exc:
        raise HardwareError(
            f"Submission outcome uncertain. Do not retry. Look up tag {plan['job_tag']} in IBM Quantum "
            "and use ibm recover to attach its job ID. The local receipt remains locked."
        ) from exc


def energy_from_counts(groups: list[Any], counts: list[dict[str, int]]) -> tuple[float, float, list[int]]:
    """Energy and standard error using each group's per-shot energy, including covariance.

    Unbiased sample variance / N estimates variance of each group mean. Independent
    group variances are summed. This models sampling error, not device bias or drift.
    """
    if len(groups) != len(counts):
        raise HardwareError("Runtime returned the wrong number of measurement groups")
    energy, variance = 0.0, 0.0
    actual = []
    for terms, frequencies in zip(groups, counts, strict=True):
        group = SparsePauliOp.from_list(terms)
        n = group.num_qubits
        if not frequencies or any(
            len(bits) != n or set(bits) - {"0", "1"} or type(weight) is not int or weight < 0
            for bits, weight in frequencies.items()
        ):
            raise HardwareError("Invalid measurement counts or logical register width")
        shots = sum(frequencies.values())
        if shots < 2:
            raise HardwareError("At least two returned shots per group are required to estimate sampling error")
        mean, var = _expectation(group, frequencies, shots)
        variance += var
        energy += mean
        actual.append(shots)
    return energy, math.sqrt(variance), actual


def _save_result(directory: str | Path, plan: dict[str, Any], digest: str, result: Any, job_id: str) -> dict[str, Any]:
    counts = [pub.data.meas.get_counts() for pub in result]
    energy, stderr, actual = energy_from_counts(plan["groups"], counts)
    record = {
        "execution": plan["execution"],
        "backend": plan["backend"],
        "instance": plan.get("instance"),
        "job_id": job_id,
        "plan_sha256": digest,
        "retrieved_at": _now(),
        "energy_hartree": energy,
        "sampling_stderr_hartree": stderr,
        "actual_shots_per_group": actual,
        "reference_energy_hartree": plan["reference_energy_hartree"],
        "error_vs_small_model_hartree": energy - plan["reference_energy_hartree"],
        "readout_mitigation": False,
        "uncertainty_scope": "shot sampling only, including within-group covariance; excludes bias/drift",
        "counts": counts,
        "sampling_confidence_radius_95_hartree": confidence_radius(
            [SparsePauliOp.from_list(g) for g in plan["groups"]], counts
        ),
        "runtime_metadata": getattr(result, "metadata", {}),
        "publication_metadata": [getattr(pub, "metadata", {}) for pub in result],
    }
    _write(Path(directory) / "result.json", record)
    return json.loads(_json(record))


def _check_job(job: Any, plan: dict[str, Any]) -> None:
    if (
        job.backend().name != plan["backend"]
        or plan["job_tag"] not in (job.tags or [])
        or getattr(job.backend(), "_instance", None) != plan.get("instance")
    ):
        raise HardwareError("Job backend/tag does not match this plan")


def recover(directory: str | Path, job_id: str, service: Any = None) -> dict[str, Any]:
    """Recover a job ID after uncertain submission, checking the unique server tag."""
    plan, _, digest = load_plan(directory)
    receipt_path = Path(directory) / "job.json"
    receipt = json.loads(receipt_path.read_bytes())
    if receipt["plan_sha256"] != digest or receipt.get("job_id") or plan["execution"] != "ibm_qpu":
        raise HardwareError("Only a locked, unresolved submission can be recovered")
    job = (service if service is not None else runtime_service()).job(job_id)
    _check_job(job, plan)
    receipt.update(state="submitted", job_id=job_id, recovered_at=_now())
    _write(receipt_path, receipt)
    return receipt


def fetch(directory: str | Path, service: Any = None) -> dict[str, Any]:
    """Poll once; retrieve counts only when DONE. Never submit or retry a job."""
    plan, _, digest = load_plan(directory)
    receipt = json.loads((Path(directory) / "job.json").read_bytes())
    if receipt["plan_sha256"] != digest or receipt["execution"] != "ibm_qpu":
        raise HardwareError("The plan changed after submission, or this is not a QPU job")
    if not receipt.get("job_id"):
        raise HardwareError("Submission has no job ID; inspect the server tag and use ibm recover")
    job = (service if service is not None else runtime_service()).job(receipt["job_id"])
    _check_job(job, plan)
    status = _status_name(job.status())
    if status != "DONE":
        return {"job_id": receipt["job_id"], "status": status, "result_available": False}
    record = _save_result(directory, plan, digest, job.result(), receipt["job_id"])
    try:
        record["runtime_job_metrics"] = job.metrics()
    except Exception as exc:  # noqa: BLE001 - metadata must not discard returned counts
        record["runtime_job_metrics_unavailable"] = type(exc).__name__
    _write(Path(directory) / "result.json", record)
    return json.loads(_json(record))


def run_local(directory: str | Path, backend: Any, *, seed: int = 1234) -> dict[str, Any]:
    """Test the exact SamplerV2 path on a fake/Aer backend, without IBM credentials."""
    from qiskit_ibm_runtime import SamplerV2

    plan, circuits, digest = load_plan(directory)
    if plan["execution"] != "local_test" or _kind(backend) != "local_test" or backend.name != plan["backend"]:
        raise HardwareError("Local tests require the same fake/Aer backend used during preparation")
    _validate_circuits(circuits, plan, backend)
    options = _options(plan) | {"simulator": {"seed_simulator": seed}}
    job = SamplerV2(mode=backend, options=options).run(circuits, shots=plan["shots_per_group"])
    return _save_result(directory, plan, digest, job.result(), job.job_id())
