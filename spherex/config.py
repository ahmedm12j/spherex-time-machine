"""Static configuration: demo targets, archive endpoints, detector geometry.

Detector wavelength ranges are *nominal* and are only used before a real
WCS-WAVE solution has been read for a file.  Once a file is open the true
per-pixel wavelength from the WCS-WAVE extension always wins.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- endpoints
IRSA_SIA_URL = "https://irsa.ipac.caltech.edu/SIA"
IRSA_TAP_URL = "https://irsa.ipac.caltech.edu/TAP"
IRSA_IBE = "https://irsa.ipac.caltech.edu/ibe/data/spherex"
AWS_BUCKET = "nasa-irsa-spherex"
AWS_REGION = "us-east-1"
AWS_BASE = f"https://{AWS_BUCKET}.s3.{AWS_REGION}.amazonaws.com"

# Quick-look release 2 ships the first ~2 all-sky passes; QR3 adds new epochs.
COLLECTIONS = ("spherex_qr2", "spherex_qr3")

IRSA_SEARCH = "https://irsa.ipac.caltech.edu/applications/Spitzer/SHA/split.html"
IRSA_DEEPLINK = "https://irsa.ipac.caltech.edu/frontpage/"

# ---------------------------------------------------------------- geometry
PIXEL_SCALE_ARCSEC = 6.15          # native SPHEREx pixel scale
FOV_MIN_DEG, FOV_MAX_DEG = 0.1, 2.0
FOV_DEFAULT_DEG = 0.5
MAX_CUTOUT_PX = 768                # hard cap on cutout side in native pixels
DISPLAY_PX = 384                   # blink viewer frame size

# Nominal central wavelength (um) and bandpass edges per detector array.
DETECTOR_INFO = {
    1: {"label": "D1", "wl_center": 0.93, "wl_range": (0.75, 1.11)},
    2: {"label": "D2", "wl_center": 1.34, "wl_range": (1.11, 1.60)},
    3: {"label": "D3", "wl_center": 1.98, "wl_range": (1.60, 2.42)},
    4: {"label": "D4", "wl_center": 2.90, "wl_range": (2.42, 3.42)},
    5: {"label": "D5", "wl_center": 4.10, "wl_range": (3.42, 4.62)},
    6: {"label": "D6", "wl_center": 4.80, "wl_range": (4.62, 5.00)},
}
BANDS_PER_DETECTOR = 17
N_DETECTORS = 6
N_TOTAL_BANDS = N_DETECTORS * BANDS_PER_DETECTOR  # 102

# FLAGS bitmask, taken from the L2 MP_* header keywords (authoritative).
FLAG_BITS = {
    "TRANSIENT": 0,
    "OVERFLOW": 1,
    "SUR_ERROR": 2,
    "PHANTOM": 4,
    "REFERENCE": 5,
    "NONFUNC": 6,
    "DICHROIC": 7,
    "MISSING_DATA": 9,
    "HOT": 10,
    "COLD": 11,
    "FULLSAMPLE": 12,
    "PHANMISS": 14,
    "NONLINEAR": 15,
    "PERSIST": 17,
    "OUTLIER": 19,
    "SOURCE": 21,
    "GHOST": 22,
    "GHOST_FPA": 23,
    "GHOST_EXT": 24,
    "BLOOM": 26,
    "SNOWBALL": 27,
    "HALO": 28,
}
FLAG_DESCRIPTIONS = {
    "TRANSIENT": "Transient hit (cosmic ray / glitch)",
    "OVERFLOW": "Pixel overflow / saturation",
    "SUR_ERROR": "Surface-brightness calibration error",
    "PHANTOM": "Phantom signal from multiplexer readout",
    "REFERENCE": "Reference-pixel derived value",
    "NONFUNC": "Non-functional detector pixel",
    "DICHROIC": "Dichroic / filter edge artefact",
    "MISSING_DATA": "No data recorded",
    "HOT": "Hot pixel",
    "COLD": "Cold / dead pixel",
    "FULLSAMPLE": "Full-sample readout pixel (longer integration)",
    "PHANMISS": "Phantom + missing data",
    "NONLINEAR": "Non-linear detector response",
    "PERSIST": "Persistence from a previous bright source",
    "OUTLIER": "Outlier rejected in coadd",
    "SOURCE": "Pixel contained a detected source",
    "GHOST": "Optical ghost",
    "GHOST_FPA": "Focal-plane ghost",
    "GHOST_EXT": "Extended ghost",
    "BLOOM": "Charge blooming from a bright source",
    "SNOWBALL": "Snowball / particle event",
    "HALO": "Scattered-light halo",
}
# Bits that must be treated as "do not trust this pixel" for science.
CONTAMINATION_BITS = (
    "TRANSIENT", "OVERFLOW", "SUR_ERROR", "PHANTOM", "NONFUNC", "DICHROIC",
    "MISSING_DATA", "HOT", "COLD", "PHANMISS", "NONLINEAR", "PERSIST",
    "OUTLIER", "BLOOM", "SNOWBALL", "GHOST", "GHOST_FPA", "GHOST_EXT",
)

# ---------------------------------------------------------------- demo cache
DEMO_TARGETS_PATH = ROOT / "DEMO_TARGETS.json"

DEFAULT_TARGET = {
    "name": "Barnard's Star",
    "ra_deg": 269.452083,
    "dec_deg": 4.693333,
    "note": "High proper motion",
}

CACHE_DIR = Path(os.environ.get("SPHEREX_CACHE_DIR", ROOT / "data" / "cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HTTP_TIMEOUT = float(os.environ.get("SPHEREX_HTTP_TIMEOUT", "25"))
HTTP_RETRIES = 2


def load_targets() -> list[dict]:
    """Load the machine-readable demo target map (best effort)."""
    try:
        with open(DEMO_TARGETS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("targets", [])
    except Exception:
        return []
