"""Pre-warm the disk cache and precompute reference spectra for demo targets.

Usage:
    python scripts/prewarm.py [--targets slug1,slug2] [--frames 8] [--rows 20]

Writes:
    data/cache/            SIA results + cutout .npz files
    data/spectra/*.json    precomputed spectra (Tier 3 deliverable)
    data/validation/prewarm.json   timings
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spherex import archive, catalogs, config, photometry, pipeline  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", default="", help="comma-separated slugs (default: all)")
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--rows", type=int, default=20)
    ap.add_argument("--fov", type=float, default=0.5)
    args = ap.parse_args()

    targets = config.load_targets()
    if args.targets:
        wanted = {s.strip() for s in args.targets.split(",") if s.strip()}
        targets = [t for t in targets if t["slug"] in wanted]
    if not targets:
        print("no targets selected")
        return 1

    out_dir = config.ROOT / "data" / "spectra"
    out_dir.mkdir(parents=True, exist_ok=True)
    val_dir = config.ROOT / "data" / "validation"
    val_dir.mkdir(parents=True, exist_ok=True)

    report = {}
    for t in targets:
        ra, dec = t["ra_deg"], t["dec_deg"]
        print(f"=== {t['name']}  RA {ra:.4f} Dec {dec:.4f}")
        t0 = time.time()
        res = archive.get_epochs(ra, dec, args.fov)
        q_time = time.time() - t0
        print(f"  epochs={len(res.epochs)} rows source={res.source} in {q_time:.1f}s")

        t0 = time.time()
        seq = pipeline.build_sequence(res.epochs, ra, dec, args.fov, max_frames=args.frames)
        seq_time = time.time() - t0
        print(f"  frames={len(seq)} in {seq_time:.1f}s notes={seq.notes}")

        pm = catalogs.nearest_proper_motion(ra, dec)
        print(f"  proper motion: {pm}")
        t0 = time.time()
        spec = photometry.extract_spectrum(res.epochs, ra, dec, max_rows=args.rows, pm=pm)
        spec_time = time.time() - t0
        print(f"  spectrum points={len(spec.points)} in {spec_time:.1f}s")

        if spec.points:
            pts = spec.sorted()
            payload = {
                "target": t["name"],
                "slug": t["slug"],
                "ra_deg": ra,
                "dec_deg": dec,
                "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "n_points": len(pts),
                "points": [
                    {
                        "wavelength_um": round(p.wavelength_um, 5),
                        "flux_mjy": round(p.flux_mjy, 6),
                        "err_mjy": round(p.err_mjy, 6) if p.err_mjy == p.err_mjy else None,
                        "snr": round(p.snr, 2) if p.snr == p.snr else None,
                        "detector": p.detector,
                        "obs_id": p.obs_id,
                        "mjd": round(p.t_min, 4),
                    }
                    for p in pts
                ],
            }
            (out_dir / f"{t['slug']}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(f"  wrote {out_dir / (t['slug'] + '.json')}")

        report[t["slug"]] = {
            "name": t["name"],
            "n_rows": len(res.epochs),
            "n_epochs": len({e.obs_id for e in res.epochs}),
            "n_frames": len(seq),
            "n_spectrum_points": len(spec.points),
            "query_seconds": round(q_time, 2),
            "sequence_seconds": round(seq_time, 2),
            "spectrum_seconds": round(spec_time, 2),
            "notes": seq.notes,
            "message": res.message,
        }

    (val_dir / "prewarm.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\nwrote", val_dir / "prewarm.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
