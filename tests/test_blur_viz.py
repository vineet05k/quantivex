import numpy as np
import pytest
from qiskit.quantum_info import Statevector

from quantivex import viz
from quantivex.blur import blur_circuit, decode, encode, gray, noise_heightmap, quantum_blur
from quantivex.errors import QuantivexError
from quantivex.experiment import energy_curve

PNG = b"\x89PNG\r\n\x1a\n"


def test_gray_code_neighbours_differ_by_one_bit():
    for n in range(63):
        assert bin(gray(n) ^ gray(n + 1)).count("1") == 1


def test_encode_decode_roundtrip():
    img = np.random.default_rng(1).random((4, 8)) + 0.01
    probs = np.abs(encode(img)) ** 2
    assert np.allclose(decode(probs, 8, 4), img / img.max())


@pytest.mark.parametrize("gate", ["rx", "ry"])
def test_numpy_blur_matches_qiskit_circuit(gate):
    img = np.random.default_rng(0).random((8, 16))
    ours = quantum_blur(img, 0.4, gate)
    theirs = decode(np.abs(Statevector(blur_circuit(img, 0.4, gate)).data) ** 2, 16, 8)
    assert np.allclose(ours, theirs)


def test_zero_rotation_is_identity_and_blur_spreads():
    img = np.zeros((8, 8))
    img[4, 4] = 1.0
    assert np.allclose(quantum_blur(img, 0.0), img)
    blurred = quantum_blur(img, 0.5)
    assert blurred[4, 4] == pytest.approx(1.0) and blurred[4, 5] > 0 and blurred[3, 4] > 0


@pytest.mark.parametrize("bad", [np.ones((3, 4)), np.ones(8), -np.ones((4, 4)), np.zeros((4, 4))])
def test_blur_rejects_bad_images(bad):
    with pytest.raises(QuantivexError):
        quantum_blur(bad)


def test_noise_heightmap(torino):
    h = noise_heightmap(torino, "two_qubit_error", 64)
    assert h.shape == (64, 64) and h.min() > 0
    with pytest.raises(QuantivexError):
        noise_heightmap(torino, "vibes")


def test_figures_are_written(tmp_path, chip, torino):
    paths = [
        viz.plot_chip_map(torino, tmp_path / "chip.png", highlight=(81, 80, 92, 99), secondary=(0, 1, 2, 3)),
        viz.plot_noise_blur(chip, tmp_path / "blur.png", size=32),
        viz.plot_energy_curve(energy_curve("H2", [0.5, 0.75, 1.0]), tmp_path / "curve.png", "H2"),
    ]
    for p in paths:
        assert p.read_bytes()[:8] == PNG and p.stat().st_size > 10_000
