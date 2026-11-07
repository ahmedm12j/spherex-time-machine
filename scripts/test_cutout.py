"""Exercise the Level-2 cutout path against live AWS data."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spherex import archive, fitsdata  # noqa: E402

RA, DEC = 269.452083, 4.693333


def main() -> int:
    t0 = time.time()
    res = archive.get_epochs(RA, DEC, fov_deg=0.5)
    print(f"epochs: {len(res.epochs)} source={res.source} in {time.time()-t0:.1f}s")
    if not res.epochs:
        print("NO EPOCHS")
        return 1

    covering = [e for e in res.epochs if e.covers(RA, DEC)]
    print(f"rows whose footprint covers the target: {len(covering)} / {len(res.epochs)}")

    first_obs = min(covering, key=lambda e: e.t_min).obs_id
    same = [e for e in covering if e.obs_id == first_obs]
    print(f"obs {first_obs}: detectors covering target -> {[e.detector for e in same]}")

    ep = sorted(same, key=lambda e: e.detector)[0]
    t0 = time.time()
    cut = fitsdata.load_cutout(ep, RA, DEC, 0.5)
    if cut is None:
        print("CUTOUT FAILED")
        return 1
    print(f"cutout D{ep.detector} {cut.shape} in {time.time()-t0:.2f}s")
    print(f"  wl_center={cut.wl_center:.4f} um  psf={cut.psf_fwhm_px:.2f} px")
    print(f"  wl map range: {cut.wavelength.min():.3f} .. {cut.wavelength.max():.3f} um")
    print(f"  good pixel fraction: {cut.good_fraction:.3f}")
    print(f"  IMAGE median {float(__import__('numpy').nanmedian(cut.image)):.4f} MJy/sr")
    print(f"  variance present: {cut.variance is not None}")
    print("  top flags:", dict(list(cut.flag_counts.items())[:6]))

    t0 = time.time()
    cut2 = fitsdata.load_cutout(ep, RA, DEC, 0.5)
    print(f"second load (disk cache) in {time.time()-t0:.3f}s, equal="
          f"{cut2 is not None and cut2.image.shape == cut.image.shape}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
