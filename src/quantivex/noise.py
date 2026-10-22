"""Device model built from a calibration snapshot: a Qiskit Target and an Aer noise model.

The same snapshot drives qubit selection, compilation and noisy simulation, so
every number in a run traces back to one data file (and its SHA-256).

Noise model per operation (the standard recipe also used by Aer's from_backend):
* sx / x: depolarising error from sx_error, then thermal relaxation (T1, T2) over the gate duration;
* cz: two-qubit depolarising error from gate_error, then thermal relaxation on each qubit that has
  coherence data (missing T2 -> 2*T1; missing T1 -> pure dephasing; see coherence_notes);
* measure: symmetric readout error from readout_error;
* rz: virtual, error-free.
Unlike Aer, the depolarising part is not reduced by the relaxation part, so the
model is slightly pessimistic. Links with error >= 0.5 are left out of the Target.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from qiskit.circuit import Parameter
from qiskit.circuit.library import CZGate, Measure, RZGate, SXGate, XGate
from qiskit.transpiler import InstructionProperties, Target
from qiskit_aer.noise import NoiseModel, ReadoutError, depolarizing_error, thermal_relaxation_error
from qiskit_aer.noise.errors.quantum_error import QuantumError

from .data import DEAD_LINK_THRESHOLD, Snapshot

NS = 1e-9


def _identity_error() -> QuantumError:
    return depolarizing_error(0.0, 1)


def _depolarizing_param(error: float, n_qubits: int) -> float:
    """Convert average gate infidelity to the depolarising parameter, capped at its maximum."""
    d = 2**n_qubits
    return min(error * d / (d - 1), d * d / (d * d - 1))


def build_target(snapshot: Snapshot) -> Target:
    size = snapshot.max_qubit_id + 1
    target = Target(num_qubits=size, description=snapshot.label)
    dur = snapshot.durations_ns
    sx_props, x_props, rz_props, meas_props = {}, {}, {}, {}
    for q in snapshot.qubits:
        sx_err = q.sx_error or 0.0
        sx_props[(q.id,)] = InstructionProperties(duration=dur["sx"] * NS, error=sx_err)
        x_props[(q.id,)] = InstructionProperties(duration=dur["sx"] * NS, error=sx_err)
        rz_props[(q.id,)] = InstructionProperties(duration=0.0, error=0.0)
        meas_props[(q.id,)] = InstructionProperties(duration=dur["measure"] * NS, error=q.readout_error)
    cz_props = {}
    for link in snapshot.links:
        if link.gate_error < DEAD_LINK_THRESHOLD:
            props = InstructionProperties(duration=dur["cz"] * NS, error=link.gate_error)
            cz_props[(link.source, link.target)] = props
            cz_props[(link.target, link.source)] = props
    target.add_instruction(SXGate(), sx_props)
    target.add_instruction(XGate(), x_props)
    target.add_instruction(RZGate(Parameter("theta")), rz_props)
    target.add_instruction(CZGate(), cz_props)
    target.add_instruction(Measure(), meas_props)
    return target


def _relaxation(t1_us: float | None, t2_us: float | None, duration_ns: float) -> QuantumError | None:
    """Thermal relaxation from whatever coherence data exists.

    Both known: standard channel (T2 capped at 2 T1). Only T1 known: T2 = 2 T1, i.e. amplitude
    damping with no extra pure dephasing (the least noise consistent with the known T1). Only T2
    known: pure dephasing (T1 = infinity). Neither: no relaxation channel.
    """
    if t1_us is None and t2_us is None:
        return None
    t1 = math.inf if t1_us is None else t1_us * 1e3  # ns
    t2 = 2 * t1 if t2_us is None else t2_us * 1e3
    t2 = min(t2, 2 * t1)  # Aer requires T2 <= 2 T1
    return thermal_relaxation_error(t1, t2, duration_ns)


def coherence_notes(snapshot: Snapshot, qubits: Iterable[int] | None = None) -> list[str]:
    """Plain statements of how missing T1/T2 data were treated for the given qubits."""
    chosen = snapshot.qubits if qubits is None else [snapshot.qubit_map[q] for q in qubits]
    notes = []
    for q in chosen:
        if q.t1_us is None and q.t2_us is None:
            notes.append(f"qubit {q.id}: no T1/T2 data, relaxation omitted")
        elif q.t2_us is None:
            notes.append(f"qubit {q.id}: T2 missing, modelled as 2*T1 (amplitude damping only)")
        elif q.t1_us is None:
            notes.append(f"qubit {q.id}: T1 missing, modelled as pure dephasing")
    return notes


def build_noise_model(snapshot: Snapshot) -> NoiseModel:
    model = NoiseModel(basis_gates=["cz", "rz", "sx", "x", "measure"])
    dur = snapshot.durations_ns
    for q in snapshot.qubits:
        err: QuantumError | None = None
        if q.sx_error:
            err = depolarizing_error(_depolarizing_param(q.sx_error, 1), 1)
        relax = _relaxation(q.t1_us, q.t2_us, dur["sx"])
        if relax is not None:
            err = relax if err is None else err.compose(relax)
        if err is not None:
            model.add_quantum_error(err, ["sx", "x"], [q.id])
        if q.readout_error > 0:
            e = q.readout_error
            model.add_readout_error(ReadoutError([[1 - e, e], [e, 1 - e]]), [q.id])
    for link in snapshot.links:
        if link.gate_error >= DEAD_LINK_THRESHOLD:
            continue
        a, b = link.source, link.target
        depol = depolarizing_error(_depolarizing_param(link.gate_error, 2), 2)
        ra = _relaxation(snapshot.qubit_map[a].t1_us, snapshot.qubit_map[a].t2_us, dur["cz"])
        rb = _relaxation(snapshot.qubit_map[b].t1_us, snapshot.qubit_map[b].t2_us, dur["cz"])
        for first, second, r_first, r_second in ((a, b, ra, rb), (b, a, rb, ra)):
            err2 = depol
            if r_first is not None or r_second is not None:
                # expand: r_first acts on the error's qubit 0 (= `first`), r_second on qubit 1.
                # A qubit without coherence data gets the identity, so its partner keeps its channel.
                one = r_first if r_first is not None else _identity_error()
                two = r_second if r_second is not None else _identity_error()
                err2 = depol.compose(one.expand(two))
            model.add_quantum_error(err2, ["cz"], [first, second])
    return model
