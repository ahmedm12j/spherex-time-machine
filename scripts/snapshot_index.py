"""Freeze the real IRSA epoch index into ``data/snapshot/epochs.json``.

Streamlit Community Cloud clones the repository with an empty, ephemeral cache
(``data/cache/`` is gitignored), so a query-time IRSA outage would leave the app
with nothing to show.  This script exports the *real* SIA2/TAP rows that were
already fetched from IRSA into a small read-only file that ships with the app.

The rows are not synthesised: every one carries the observing ID, time window,
footprint polygon and AWS object URL that IRSA returned.  Pixels always stream
live from NASA's public S3 bucket; only the (slow to fetch) epoch list is frozen.

Usage:
    python scripts/snapshot_index.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spherex import archive, config  # noqa: E402

OUT = ROOT / "data" / "snapshot" / "epochs.json"
# Fields Epoch needs; keeping s_region is what lets any FOV be served from one cone.
KEEP = (
    "obs_id", "detector", "band", "t_min", "t_max", "proc_version", "collection",
    "exptime", "pixscale", "s_region", "aws_url", "wl_center", "access_url",
)


def main() -> int:
    cache_path = config.CACHE_DIR / "sia_cache.json"
    if not cache_path.exists():
        print(f"no cache at {cache_path} - run scripts/prewarm.py first")
        return 1
    cache = json.loads(cache_path.read_text(encoding="utf-8"))

    seen: dict[tuple[str, int], dict] = {}
    for entry in cache.values():
        for raw in entry.get("epochs", []) or []:
            if not raw.get("aws_url"):
                continue
            key = (str(raw.get("obs_id")), int(raw.get("detector") or 0))
            seen.setdefault(key, {k: raw.get(k) for k in KEEP})

    epochs = sorted(seen.values(), key=lambda e: (e.get("t_min") or 0, e.get("detector") or 0))
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
        "generated_unix": int(time.time()),
        "source": "IRSA SIA2 / TAP obscore (spherex_qr2, spherex_qr3)",
        "note": (
            "Real archive rows. Pixels are fetched live from the NASA IRSA SPHEREx "
            "S3 bucket; only the epoch list is frozen so the app still works when "
            "the IRSA metadata services are unavailable."
        ),
        "n_epochs": len(epochs),
        "n_obs": len({e["obs_id"] for e in epochs}),
        "epochs": epochs,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    size = OUT.stat().st_size / 1024
    print(f"wrote {OUT}: {len(epochs)} epochs, {payload['n_obs']} observations, {size:.0f} KiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
