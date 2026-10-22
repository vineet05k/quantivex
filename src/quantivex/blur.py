"""Quantum Blur (Wootton 2020, arXiv:2007.11510; Wootton & Pfaffhauser 2021, arXiv:2112.01646).

An image of width 2^nx and height 2^ny is encoded in one (nx + ny)-qubit state:
the amplitude of basis state |gray(y) gray(x)> is sqrt(pixel value). Gray-code
coordinates make horizontally or vertically neighbouring pixels differ by one
bit, so a small rotation on each qubit mixes neighbouring pixels: a blur with
interference. Reading out probabilities gives the blurred image.

`quantum_blur` simulates this exactly with numpy; `blur_circuit` builds the
equivalent Qiskit circuit (used in tests to prove the two agree, and usable on
real hardware for small images).
"""

from __future__ import annotations

import math

import numpy as np
from qiskit import QuantumCircuit

from .data import Snapshot
from .errors import QuantivexError
from .hardware import coordinates

ROTATIONS = ("rx", "ry")


def gray(n: int) -> int:
    return n ^ (n >> 1)


def _check(image: np.ndarray) -> tuple[int, int]:
    if not np.isfinite(image).all():
        raise QuantivexError("Height map values must be finite")
    if image.size > 65536:
        raise QuantivexError("Height map exceeds the 65536-pixel workload limit")
    if image.ndim != 2:
        raise QuantivexError("Quantum Blur needs a 2-D height map")
    h, w = image.shape
    if h & (h - 1) or w & (w - 1) or h < 2 or w < 2:
        raise QuantivexError(f"Height map sides must be powers of two (got {w}x{h})")
    if np.any(image < 0) or not np.any(image > 0):
        raise QuantivexError("Height map must be non-negative with at least one positive value")
    return int(math.log2(w)), int(math.log2(h))


def encode(image: np.ndarray) -> np.ndarray:
    """State vector for a height map (index = gray(y) << nx | gray(x))."""
    nx, ny = _check(image)
    h, w = image.shape
    state = np.zeros(2 ** (nx + ny), dtype=complex)
    for y in range(h):
        for x in range(w):
            state[(gray(y) << nx) | gray(x)] = math.sqrt(image[y, x])
    return state / np.linalg.norm(state)


def decode(probabilities: np.ndarray, width: int, height: int) -> np.ndarray:
    nx = int(math.log2(width))
    out = np.empty((height, width))
    for y in range(height):
        for x in range(width):
            out[y, x] = probabilities[(gray(y) << nx) | gray(x)]
    return out / float(np.max(out))


def _rotation(theta: float, gate: str) -> np.ndarray:
    if not math.isfinite(theta):
        raise QuantivexError("Rotation angle must be finite")
    c, s = math.cos(theta / 2), math.sin(theta / 2)
    if gate == "rx":
        return np.array([[c, -1j * s], [-1j * s, c]])
    if gate == "ry":
        return np.array([[c, -s], [s, c]], dtype=complex)
    raise QuantivexError(f"rotation must be one of {ROTATIONS}")


def quantum_blur(image: np.ndarray, theta: float = 0.15, gate: str = "rx") -> np.ndarray:
    """Blurred copy of `image` (normalised to max 1). theta is the rotation angle per qubit."""
    nx, ny = _check(image)
    n = nx + ny
    state = encode(image).reshape([2] * n)
    u = _rotation(theta, gate)
    for axis in range(n):  # numpy axis k is qubit n - 1 - k; every qubit gets the same rotation
        state = np.moveaxis(np.tensordot(u, state, axes=([1], [axis])), 0, axis)
    probs = np.abs(state.reshape(-1)) ** 2
    return decode(probs, image.shape[1], image.shape[0])


def blur_circuit(image: np.ndarray, theta: float = 0.15, gate: str = "rx") -> QuantumCircuit:
    nx, ny = _check(image)
    _rotation(theta, gate)
    qc = QuantumCircuit(nx + ny, name="quantum_blur")
    qc.initialize(encode(image), range(nx + ny))
    for q in range(nx + ny):
        getattr(qc, gate)(theta, q)
    return qc


def noise_heightmap(snapshot: Snapshot, metric: str = "readout_error", size: int = 64) -> np.ndarray:
    """Rasterise per-qubit error onto a size x size grid at the chip's drawing coordinates.

    Brighter = worse. Pixels with no qubit get a small floor so the state is well defined.
    """
    if size & (size - 1) or size < 8:
        raise QuantivexError("size must be a power of two >= 8")
    metrics = {
        "readout_error": lambda q: q.readout_error,
        "sx_error": lambda q: q.sx_error or 0.0,
        "t1_decay": lambda q: 0.0 if not q.t1_us else 1.0 / q.t1_us,
    }
    if metric == "two_qubit_error":
        values = {q.id: 0.0 for q in snapshot.qubits}
        for link in snapshot.links:
            for end in (link.source, link.target):
                values[end] = max(values[end], min(link.gate_error, 0.5))
    elif metric in metrics:
        values = {q.id: float(metrics[metric](q)) for q in snapshot.qubits}
    else:
        raise QuantivexError(f"metric must be one of {sorted([*metrics, 'two_qubit_error'])}")
    pos = coordinates(snapshot)
    xs = np.array([p[0] for p in pos.values()])
    ys = np.array([p[1] for p in pos.values()])
    pad = 1.0
    span = max(xs.max() - xs.min(), ys.max() - ys.min()) + 2 * pad
    img = np.zeros((size, size))
    radius = max(1, int(size / span / 2.5))
    for q, (x, y) in pos.items():
        px = int((x - xs.min() + pad) / span * (size - 1))
        py = int((ys.max() - y + pad) / span * (size - 1))
        img[max(0, py - radius) : py + radius + 1, max(0, px - radius) : px + radius + 1] = values[q]
    top = float(np.max(img))
    return (img / top if top > 0 else img) + 1e-3
