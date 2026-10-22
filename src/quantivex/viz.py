"""Static report figures (PNG). Matplotlib with the Agg backend: headless and deterministic.

Colour roles follow a validated categorical palette: a sequential blue ramp for
error magnitude, fixed categorical slots for layout identity, the status red only
for dead links (always with a legend label), and one y-axis per panel.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.lines import Line2D

from .blur import noise_heightmap, quantum_blur
from .chemistry import CHEMICAL_ACCURACY_HA
from .data import DEAD_LINK_THRESHOLD, Snapshot
from .experiment import CurvePoint, LayoutComparison
from .hardware import coordinates

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
NEUTRAL = "#b9b8b1"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
CRITICAL = "#d03b3b"
BLUE_RAMP = LinearSegmentedColormap.from_list(
    "quantivex_blue", ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
)

plt.rcParams.update(
    {
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "axes.edgecolor": NEUTRAL,
        "axes.labelcolor": INK_2,
        "xtick.color": INK_2,
        "ytick.color": INK_2,
        "text.color": INK,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "legend.frameon": False,
    }
)


def _save(fig: plt.Figure, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(p, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return p


def plot_chip_map(
    snapshot: Snapshot,
    path: str | Path,
    highlight: Sequence[int] | None = None,
    highlight_label: str = "Selected chain",
    secondary: Sequence[int] | None = None,
    secondary_label: str = "Qiskit default",
) -> Path:
    """Chip map: qubits shaded by readout error, dead links marked, chosen chain(s) highlighted."""
    pos = coordinates(snapshot)
    xs = [p[0] for p in pos.values()]
    width = max(xs) - min(xs) + 2
    fig, ax = plt.subplots(figsize=(min(16, 2.0 + width * 0.75), 7.5))
    ax.grid(False)
    ax.set_axis_off()
    for link in snapshot.links:
        (x1, y1), (x2, y2) = pos[link.source], pos[link.target]
        if link.gate_error >= DEAD_LINK_THRESHOLD:
            ax.plot([x1, x2], [y1, y2], color=CRITICAL, lw=1.6, ls=(0, (2, 2)), zorder=1)
        else:
            ax.plot([x1, x2], [y1, y2], color=NEUTRAL, lw=1.2, zorder=1)
    readout = np.array([snapshot.qubit_map[q].readout_error for q in pos])
    norm = Normalize(vmin=0, vmax=max(0.05, float(np.quantile(readout, 0.95))))
    for chain, color, z in ((secondary, SERIES[1], 2), (highlight, SERIES[0], 3)):
        if chain:
            cx = [pos[q][0] for q in chain]
            cy = [pos[q][1] for q in chain]
            ax.plot(cx, cy, color=color, lw=5, alpha=0.85, solid_capstyle="round", zorder=z)
    sc = ax.scatter(
        xs,
        [p[1] for p in pos.values()],
        c=readout,
        cmap=BLUE_RAMP,
        norm=norm,
        s=260,
        edgecolors=SURFACE,
        linewidths=2,
        zorder=4,
    )
    chosen = set(highlight or []) | set(secondary or [])
    for q, (x, y) in pos.items():
        dark = norm(snapshot.qubit_map[q].readout_error) > 0.55
        ax.text(
            x,
            y,
            str(q),
            ha="center",
            va="center",
            fontsize=6.5,
            zorder=5,
            color=SURFACE if dark else INK,
            fontweight="bold" if q in chosen else "normal",
        )
    cbar = fig.colorbar(sc, ax=ax, fraction=0.025, pad=0.01)
    cbar.set_label("Readout error (darker = worse)")
    handles = [
        Line2D([], [], color=NEUTRAL, lw=1.2, label="Working link"),
        Line2D([], [], color=CRITICAL, lw=1.6, ls=(0, (2, 2)), label=f"Dead link (error ≥ {DEAD_LINK_THRESHOLD})"),
    ]
    if highlight:
        handles.append(Line2D([], [], color=SERIES[0], lw=5, label=f"{highlight_label}: {list(highlight)}"))
    if secondary:
        handles.append(Line2D([], [], color=SERIES[1], lw=5, label=f"{secondary_label}: {list(secondary)}"))
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.01), ncol=len(handles))
    when = snapshot.calibration_timestamp or "unknown date"
    ax.set_title(f"{snapshot.label}\ncalibration {when}", loc="left")
    return _save(fig, path)


def plot_energy_curve(points: Sequence[CurvePoint], path: str | Path, title: str) -> Path:
    """Two stacked panels sharing the x-axis: energies, then error vs exact (no dual axis)."""
    r = np.array([p.bond_length for p in points])
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(8, 7), sharex=True, gridspec_kw={"height_ratios": [3, 2]})
    top.plot(r, [p.e_exact for p in points], color=INK, lw=2, label="Exact (CASCI)")
    top.plot(r, [p.e_hf for p in points], color=INK_2, lw=2, ls="--", label="Hartree-Fock")
    top.plot(r, [p.e_vqe for p in points], color=SERIES[0], lw=0, marker="o", ms=8, label="VQE, noiseless")
    noisy = [p for p in points if p.noisy is not None]
    if noisy:
        top.errorbar(
            [p.bond_length for p in noisy],
            [p.noisy.energy for p in noisy if p.noisy],
            yerr=[2 * p.noisy.stderr for p in noisy if p.noisy],
            color=SERIES[1],
            lw=0,
            marker="s",
            ms=8,
            elinewidth=2,
            capsize=4,
            label="VQE state on noisy chip (±2σ)",
        )
    top.set_ylabel("Energy (hartree)")
    top.set_title(title, loc="left")
    top.legend(loc="best")
    bottom.axhspan(-1000 * CHEMICAL_ACCURACY_HA, 1000 * CHEMICAL_ACCURACY_HA, color=GRID, label="Chemical accuracy")
    bottom.plot(r, [1000 * (p.e_hf - p.e_exact) for p in points], color=INK_2, lw=2, ls="--", label="Hartree-Fock")
    bottom.plot(
        r,
        [1000 * (p.e_vqe - p.e_exact) for p in points],
        color=SERIES[0],
        lw=0,
        marker="o",
        ms=8,
        label="VQE, noiseless",
    )
    if noisy:
        bottom.errorbar(
            [p.bond_length for p in noisy],
            [1000 * (p.noisy.energy - p.e_exact) for p in noisy if p.noisy],
            yerr=[2000 * p.noisy.stderr for p in noisy if p.noisy],
            color=SERIES[1],
            lw=0,
            marker="s",
            ms=8,
            elinewidth=2,
            capsize=4,
            label="Noisy chip",
        )
    bottom.set_ylabel("Error vs exact (mHa)")
    bottom.set_xlabel("Bond length (Å)")
    bottom.legend(loc="best", fontsize=8)
    return _save(fig, path)


ROLE_COLORS = {"quantivex_best": SERIES[0], "qiskit_default": SERIES[1]}


def plot_layout_comparison(comparison: LayoutComparison, path: str | Path) -> Path:
    """Left: |energy error| per layout. Right: does the chain score predict the error?"""
    rows = sorted(comparison.rows, key=lambda r: r.abs_error_ha)
    fig, (left, right) = plt.subplots(1, 2, figsize=(13, 5.2), gridspec_kw={"width_ratios": [3, 2]})
    names = [f"{r.role.replace('_', ' ')}  {list(r.estimate.layout)}" for r in rows]
    errs = [1000 * r.abs_error_ha for r in rows]
    sig = [2000 * r.estimate.stderr for r in rows]
    colors = [ROLE_COLORS.get(r.role, NEUTRAL) for r in rows]
    y = np.arange(len(rows))
    left.barh(y, errs, xerr=sig, color=colors, height=0.6, error_kw={"ecolor": INK_2, "elinewidth": 1.2, "capsize": 3})
    left.axvline(1000 * CHEMICAL_ACCURACY_HA, color=INK, lw=1.2, ls=":")
    left.text(1000 * CHEMICAL_ACCURACY_HA, len(rows) - 0.4, " chemical accuracy", fontsize=8, color=INK_2, va="bottom")
    left.set_yticks(y, names, fontsize=8)
    left.invert_yaxis()
    left.grid(axis="y", visible=False)
    left.set_xlabel("|Energy error| vs exact (mHa), ±2σ shot noise")
    p = comparison.problem
    left.set_title(
        f"Same circuit, different qubits: {p.molecule} at {p.bond_length} Å, CAS({p.n_electrons},{p.n_orbitals})",
        loc="left",
    )
    for yi, e, s2 in zip(y, errs, sig, strict=True):
        left.text(e + s2, yi, f"  {e:.0f} mHa", va="center", fontsize=8, color=INK)
    left.set_xlim(0, max(e + s2 for e, s2 in zip(errs, sig, strict=True)) * 1.18)
    scored = [r for r in comparison.rows if r.chain is not None]
    for r in scored:
        if r.chain is None:
            continue
        if r.role == "quantivex_best":  # drawn as a ring so a coinciding Qiskit point stays visible
            right.scatter(
                r.chain.esp,
                1000 * r.abs_error_ha,
                s=220,
                facecolors="none",
                edgecolors=SERIES[0],
                linewidths=2.5,
                zorder=4,
            )
        else:
            right.scatter(
                r.chain.esp,
                1000 * r.abs_error_ha,
                s=70,
                c=ROLE_COLORS.get(r.role, NEUTRAL),
                edgecolors=SURFACE,
                linewidths=2,
                zorder=3,
            )
    right.set_xlabel("Chain estimated success probability (calibration)")
    right.set_ylabel("|Energy error| (mHa)")
    rho = -comparison.spearman_rho if comparison.spearman_rho is not None else None
    if rho is None:
        right.set_title("Score vs error", loc="left")
    else:
        headline = "Random chains only: ESP vs error"
        right.set_title(f"{headline}\nSpearman ρ = {rho:.2f}, p = {comparison.spearman_p:.2g}", loc="left")
    handles = [
        Line2D([], [], color=SERIES[0], lw=0, marker="o", ms=11, mfc="none", mew=2.5, label="Quantivex best chain"),
        Line2D([], [], color=SERIES[1], lw=0, marker="s", ms=9, label="Qiskit default layout"),
        Line2D([], [], color=NEUTRAL, lw=0, marker="s", ms=9, label="Other chains"),
    ]
    right.legend(handles=handles, loc="upper right", fontsize=8)
    fig.tight_layout()
    return _save(fig, path)


def plot_noise_blur(
    snapshot: Snapshot,
    path: str | Path,
    metric: str = "readout_error",
    theta: float = 0.3,
    gate: str = "rx",
    size: int = 64,
) -> Path:
    original = noise_heightmap(snapshot, metric, size)
    blurred = quantum_blur(original, theta, gate)
    fig, axes = plt.subplots(1, 2, figsize=(11, 5.4))
    for ax, img, title in (
        (axes[0], original, f"Chip noise map ({metric.replace('_', ' ')})"),
        (axes[1], blurred, f"Quantum Blur ({gate}, θ = {theta})"),
    ):
        im = ax.imshow(img, cmap=BLUE_RAMP, vmin=0, vmax=1, interpolation="nearest")
        ax.set_title(title, loc="left")
        ax.set_axis_off()
    cbar = fig.colorbar(im, ax=axes, fraction=0.025, pad=0.02)
    cbar.set_label("Relative error (darker = worse)")
    fig.suptitle(
        f"{snapshot.label}: every pixel interferes through one {int(np.log2(size)) * 2}-qubit state",
        x=0.02,
        ha="left",
        fontsize=10,
        color=INK_2,
    )
    return _save(fig, path)
