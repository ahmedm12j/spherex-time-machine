"""End-to-end smoke test: epochs -> cutouts -> sequence -> diff -> movers -> spectrum."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from spherex import archive, photometry, pipeline  # noqa: E402

RA, DEC, FOV = 269.452083, 4.693333, 0.3


def main() -> int:
    t0 = time.time()
    res = archive.get_epochs(RA, DEC, FOV)
    print(f"[1] epochs={len(res.epochs)} source={res.source} t={time.time()-t0:.1f}s")

    t0 = time.time()
    seq = pipeline.build_sequence(res.epochs, RA, DEC, FOV, mode="stack", max_frames=3)
    print(f"[2] frames={len(seq)} shape={seq.shape} t={time.time()-t0:.1f}s notes={seq.notes}")
    for f in seq.frames:
        fin = f.data[np.isfinite(f.data)]
        print(f"    {f.label} dets={f.detectors} median={np.median(fin):.3f} "
              f"wl={f.wavelength:.2f} valid={f.valid.mean():.2f}")
    if len(seq) < 2:
        print("NOT ENOUGH FRAMES")
        return 1

    t0 = time.time()
    diff = pipeline.difference(seq.frames[-1], seq.frames[0])
    print(f"[3] diff std={diff['stats']['std']:.2f} median={diff['stats']['median']:.2f} "
          f"dt={diff['stats']['dt_days']:.1f}d t={time.time()-t0:.1f}s")

    t0 = time.time()
    pngs = pipeline.encode_frames(seq, cmap="viridis")
    print(f"[4] pngs={len(pngs)} first={len(pngs[0])//1024}KB t={time.time()-t0:.1f}s")

    t0 = time.time()
    cands = photometry.detect_movers(seq)
    print(f"[5] candidates={len(cands)} t={time.time()-t0:.1f}s")
    for c in cands[:3]:
        print(f"    score={c.score:.1f} disp={c.displacement_arcsec:.1f}\" "
              f"RA={c.ra:.4f} Dec={c.dec:.4f}")

    t0 = time.time()
    spec = photometry.extract_spectrum(res.epochs, RA, DEC, max_rows=6)
    print(f"[6] spectrum points={len(spec.points)} t={time.time()-t0:.1f}s msg={spec.message}")
    for p in spec.sorted()[:6]:
        print(f"    wavelength={p.wavelength_um:.3f} flux={p.flux_mjy:.3f} mJy snr={p.snr:.1f} D{p.detector}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
