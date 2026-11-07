"""Regenerate the figures in docs/ from real SPHEREx data.

    python scripts/make_figures.py

Produces:
    docs/blink_strip.png     three epochs side by side with the target marked
    docs/difference.png      variance-weighted A-B S/N map
    docs/spectrum_barnard.png  precomputed spectrum
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from spherex import archive, config, pipeline  # noqa: E402

DOCS = config.ROOT / "docs"
DOCS.mkdir(exist_ok=True)
RA, DEC, FOV = 269.452083, 4.693333, 0.5


def fig_blink(seq) -> None:
    n = min(3, len(seq.frames))
    idx = np.linspace(0, len(seq.frames) - 1, n).round().astype(int)
    fig, axes = plt.subplots(1, n, figsize=(4.1 * n, 4.5), facecolor="#0b0e14")
    if n == 1:
        axes = [axes]
    for ax, i in zip(axes, idx):
        f = seq.frames[i]
        im = ax.imshow(f.data, cmap="viridis", origin="lower", vmin=seq.vmin, vmax=seq.vmax)
        ax.set_title(f.label, color="#dfe7f5", fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_facecolor("#05070c")
    fig.suptitle("Same sky, three epochs — SPHEREx real data (MJy/sr)",
                 color="#e9eef7", fontsize=12)
    cb = fig.colorbar(im, ax=axes, fraction=0.02, pad=0.02)
    cb.ax.tick_params(colors="#9fb0cc", labelsize=8)
    fig.savefig(DOCS / "blink_strip.png", dpi=130, facecolor="#0b0e14",
                bbox_inches="tight")
    plt.close(fig)
    print("wrote docs/blink_strip.png")


def fig_diff(seq) -> None:
    if len(seq.frames) < 2:
        return
    res = pipeline.difference(seq.frames[-1], seq.frames[0])
    snr = res["snr"]
    fig, ax = plt.subplots(figsize=(6.2, 5.4), facecolor="#0b0e14")
    im = ax.imshow(snr, cmap="RdBu", origin="lower", vmin=-6, vmax=6)
    ax.set_title(f"Variance-weighted difference\n{res['stats']['label']}",
                 color="#dfe7f5", fontsize=10)
    ax.set_xticks([])
    ax.set_yticks([])
    cb = fig.colorbar(im, ax=ax, fraction=0.045, pad=0.02)
    cb.set_label("S/N", color="#9fb0cc")
    cb.ax.tick_params(colors="#9fb0cc", labelsize=8)
    fig.savefig(DOCS / "difference.png", dpi=130, facecolor="#0b0e14", bbox_inches="tight")
    plt.close(fig)
    print("wrote docs/difference.png")


def fig_spectrum() -> None:
    path = config.ROOT / "data" / "spectra" / "barnards_star.json"
    if not path.exists():
        print("no precomputed spectrum yet; skipping")
        return
    blob = json.loads(path.read_text(encoding="utf-8"))
    pts = blob["points"]
    wl = [p["wavelength_um"] for p in pts]
    fl = [p["flux_mjy"] for p in pts]
    er = [(p["err_mjy"] or 0.0) for p in pts]
    fig, ax = plt.subplots(figsize=(7.4, 4.4), facecolor="#0b0e14")
    ax.set_facecolor("#0b0e14")
    ax.errorbar(wl, fl, yerr=er, fmt="o", color="#7ee0ff", ecolor="#3a6a8a",
                ms=7, capsize=3)
    ax.set_xlabel("Wavelength (µm)", color="#dfe7f5")
    ax.set_ylabel("Flux density (mJy)", color="#dfe7f5")
    ax.set_title(f"Barnard's Star — {blob['n_points']} SPHEREx revisit samples",
                 color="#e9eef7", fontsize=11)
    ax.tick_params(colors="#9fb0cc")
    for spine in ax.spines.values():
        spine.set_color("#26314a")
    ax.grid(color="#1e2739", lw=0.6)
    fig.savefig(DOCS / "spectrum_barnard.png", dpi=130, facecolor="#0b0e14",
                bbox_inches="tight")
    plt.close(fig)
    print("wrote docs/spectrum_barnard.png")


def main() -> int:
    res = archive.get_epochs(RA, DEC, FOV)
    seq = pipeline.build_sequence(res.epochs, RA, DEC, FOV, max_frames=8)
    print(f"frames={len(seq)}")
    if len(seq):
        fig_blink(seq)
        fig_diff(seq)
    fig_spectrum()
    return 0


if __name__ == "__main__":
    sys.exit(main())
