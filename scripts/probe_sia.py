"""Probe IRSA SIA2 + AWS for the five NASA demo targets.

Run:  python scripts/probe_sia.py
Prints, for each target: number of rows in spherex_qr2 / spherex_qr3,
distinct epochs, and the first access_url + AWS cloud_access entry.
"""
from __future__ import annotations

import json
import sys
import traceback

from astropy.coordinates import SkyCoord
import astropy.units as u
from astroquery.ipac.irsa import Irsa

TARGETS = [
    ("Barnard's Star", 17, 57, 48.5, 4, 41, 36),
    ("Epsilon Indi B", 22, 3, 21.7, -56, 47, 10),
    ("WISE 0855", 8, 55, 10.831, -7, 14, 42.53),
    ("RW Aurigae", 5, 7, 49.5, 30, 24, 8),
    ("R Draconis", 16, 32, 40.2, 66, 45, 18),
]

COLLECTIONS = ["spherex_qr2", "spherex_qr3"]


def sexa(h, m, s, dh, dm, ds):
    sign = "-" if (dh < 0 or (dh == 0 and (dm < 0 or ds < 0))) else "+"
    return f"{h:02d}h{m:02d}m{s:04.1f}s {sign}{abs(dh):02d}d{abs(dm):02d}m{abs(ds):04.1f}s"


def main() -> int:
    any_fail = False
    for name, h, m, s, dh, dm, ds in TARGETS:
        coord = SkyCoord(
            f"{h}h{m}m{s}s {dh}d{dm}m{ds}s", unit=(u.hourangle, u.deg), frame="icrs"
        )
        print("=" * 78)
        print(f"{name}: RA={coord.ra.deg:.5f} Dec={coord.dec.deg:.5f}")
        for coll in COLLECTIONS:
            try:
                table = Irsa.query_sia(
                    pos=(coord.ra, coord.dec, 0.25 * u.deg), collection=coll
                )
                if table is None or len(table) == 0:
                    print(f"  {coll}: 0 rows")
                    continue
                t = table.to_pandas()
                obs = t["obs_id"].astype(str)
                epochs = sorted(obs.str.split("_").str[-2].unique())
                print(f"  {coll}: {len(t)} rows | {obs.nunique()} obs_ids | "
                      f"bands={sorted(t['energy_bandpassname'].astype(str).unique())}")
                print(f"    t_min span: {t['t_min'].min():.3f} .. {t['t_min'].max():.3f} MJD")
                print(f"    s_pixel_scale={t['s_pixel_scale'].iloc[0]} arcsec "
                      f"size={t['s_xel1'].iloc[0]}x{t['s_xel2'].iloc[0]}")
                print(f"    access_url={str(t['access_url'].iloc[0])[:120]}")
                ca = t["cloud_access"].iloc[0]
                print(f"    cloud_access={str(ca)[:160]}")
            except Exception as exc:  # noqa: BLE001
                any_fail = True
                print(f"  {coll}: FAILED {type(exc).__name__}: {exc}")
                traceback.print_exc(limit=1)
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
