"""Byte-level verification that HTTP range cutouts match the on-disk FITS."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
from astropy.io import fits

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spherex import archive, rangecut  # noqa: E402

AWS = (
    "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/qr2/level2/2026W12_1A/"
    "l2b-v24-2026-082/5/level2_2026W12_1A_0424_3D5_spx_l2b-v24-2026-082.fits"
)
LOCAL = Path("data/cache/probe_d5.fits")


def main() -> int:
    t0 = time.time()
    fs = rangecut.parse_structure(AWS)
    print(f"structure parsed in {time.time()-t0:.2f}s -> {fs.names}")

    with fits.open(LOCAL, memmap=True) as local:
        ok_offsets = True
        for info in fs.hdus:
            lo = local[info.index]
            lh = lo.header
            dh = 2880 * ((len(lh.cards) + 1 + 35) // 36) if info.index >= 0 else 0
            if info.name.upper() != str(lh.get("EXTNAME", "PRIMARY")).upper():
                print(f"  MISMATCH name: remote {info.name} local {lh.get('EXTNAME')}")
                ok_offsets = False
        print("HDU names agree:", ok_offsets)

        y0, y1, x0, x1 = 900, 1300, 850, 1250
        for ext in ("IMAGE", "FLAGS", "VARIANCE", "ZODI"):
            t0 = time.time()
            remote = rangecut.read_image(AWS, ext, y0, y1, x0, x1, structure=fs)
            dt = time.time() - t0
            ref = local[ext].data[y0:y1, x0:x1]
            if ext == "FLAGS":
                same = np.array_equal(remote.astype(np.int32), ref.astype(np.int32))
            else:
                same = np.allclose(remote, ref, rtol=0, atol=0, equal_nan=True)
            print(f"  {ext:9s} shape={remote.shape} identical={same} in {dt:.2f}s")
            assert same, f"{ext} cutout mismatch"

        psf = rangecut.read_rows(AWS, "PSF", 0, 101, 0, 101, plane=60, structure=fs)
        print("  PSF plane identical:", np.allclose(psf, local[5].data[60]))
        assert np.allclose(psf, local[5].data[60])

    t0 = time.time()
    res = archive.get_epochs(269.452083, 4.693333, 0.5, use_cache=False)
    print(f"\nSIA query: {len(res.epochs)} epochs, source={res.source} in {time.time()-t0:.2f}s")
    print("  msg:", res.message)
    if res.epochs:
        e = res.epochs[0]
        print(f"  first: {e.obs_id} D{e.detector} MJD {e.t_min:.3f} proc {e.proc_version}")
        print(f"  aws: {e.aws_url[:110]}")
        s = archive.summarise(res.epochs)
        print("  summary:", s["n_epochs"], "obs, detectors", s["detectors"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
