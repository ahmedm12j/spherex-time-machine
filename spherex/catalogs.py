"""Gaia DR3 and WISE (AllWISE) catalogue overlays via astroquery + Vizier."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config

_CACHE = config.CACHE_DIR / "catalogs.json"
_TTL = 7 * 24 * 3600


@dataclass
class Overlay:
    name: str
    table: pd.DataFrame
    ok: bool = True
    message: str = ""
    color: str = "#7ee0ff"

    @property
    def empty(self) -> bool:
        return self.table is None or self.table.empty


def _load_cache() -> dict:
    try:
        with open(_CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        tmp = _CACHE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        tmp.replace(_CACHE)
    except Exception:
        pass


def _key(kind: str, ra: float, dec: float, radius: float) -> str:
    return f"{kind}_{ra:.4f}_{dec:.4f}_{radius:.4f}"


def _records_from_df(df: pd.DataFrame, ra_col: str, dec_col: str, mag_col: str,
                     name_prefix: str, color: str, extra_cols: dict[str, str]) -> Overlay:
    out = pd.DataFrame({
        "ra": pd.to_numeric(df[ra_col], errors="coerce"),
        "dec": pd.to_numeric(df[dec_col], errors="coerce"),
        "mag": pd.to_numeric(df.get(mag_col), errors="coerce"),
    })
    for src, dst in extra_cols.items():
        if src in df.columns:
            out[dst] = df[src]
    out = out.dropna(subset=["ra", "dec"]).sort_values("mag").head(600).reset_index(drop=True)
    out["label"] = [f"{name_prefix} {m:.1f}" if np.isfinite(m) else name_prefix for m in out["mag"]]
    return Overlay(name_prefix, out, color=color)


def gaia_dr3(ra: float, dec: float, radius_deg: float, use_cache: bool = True) -> Overlay:
    """Gaia DR3 sources (positions + proper motion) from Vizier I/355/gaiadr3."""
    key = _key("gaia", ra, dec, radius_deg)
    cache = _load_cache() if use_cache else {}
    if use_cache and key in cache and time.time() - cache[key]["t"] < _TTL:
        df = pd.DataFrame(cache[key]["rows"])
        return Overlay("Gaia DR3", df, color="#7ee0ff")
    try:
        from astroquery.vizier import Vizier
        import astropy.units as u
        from astropy.coordinates import SkyCoord

        Vizier.ROW_LIMIT = 400
        res = Vizier.query_region(
            SkyCoord(ra, dec, unit="deg"),
            radius=radius_deg * u.deg,
            catalog=["I/355/gaiadr3"],
        )
        if not res:
            return Overlay("Gaia DR3", pd.DataFrame(), ok=True, message="no Gaia sources")
        tbl = res[0]
        df = tbl.to_pandas()
        overlay = _records_from_df(
            df, "RA_ICRS", "DE_ICRS", "Gmag", "Gaia",
            "#7ee0ff", {"pmRA": "pmra", "pmDE": "pmdec", "Plx": "plx"},
        )
    except Exception as exc:  # noqa: BLE001
        return Overlay("Gaia DR3", pd.DataFrame(), ok=False, message=f"{type(exc).__name__}")

    if use_cache:
        cache[key] = {"t": time.time(), "rows": overlay.table.to_dict("records")}
        _save_cache(cache)
    return overlay


def allwise(ra: float, dec: float, radius_deg: float, use_cache: bool = True) -> Overlay:
    """AllWISE sources from Vizier II/328/allwise (W1 magnitudes)."""
    key = _key("wise", ra, dec, radius_deg)
    cache = _load_cache() if use_cache else {}
    if use_cache and key in cache and time.time() - cache[key]["t"] < _TTL:
        df = pd.DataFrame(cache[key]["rows"])
        return Overlay("AllWISE", df, color="#ffb347")
    try:
        from astroquery.vizier import Vizier
        import astropy.units as u
        from astropy.coordinates import SkyCoord

        Vizier.ROW_LIMIT = 400
        res = Vizier.query_region(
            SkyCoord(ra, dec, unit="deg"),
            radius=radius_deg * u.deg,
            catalog=["II/328/allwise"],
        )
        if not res:
            return Overlay("AllWISE", pd.DataFrame(), ok=True, message="no WISE sources")
        df = res[0].to_pandas()
        overlay = _records_from_df(df, "RAJ2000", "DEJ2000", "W1mag", "WISE", "#ffb347", {})
    except Exception as exc:  # noqa: BLE001
        return Overlay("AllWISE", pd.DataFrame(), ok=False, message=f"{type(exc).__name__}")

    if use_cache:
        cache[key] = {"t": time.time(), "rows": overlay.table.to_dict("records")}
        _save_cache(cache)
    return overlay


def _bundled_pm(ra: float, dec: float, max_sep_arcsec: float = 90.0):
    """Proper motion from DEMO_TARGETS.json for the five named targets.

    Used first for the demo targets so the app never blocks on Vizier, and as a
    fallback whenever the Gaia query is unavailable.
    """
    from . import config

    best = None
    for t in config.load_targets():
        pm = t.get("proper_motion")
        if not pm:
            continue
        d_ra = (ra - t["ra_deg"]) * np.cos(np.radians(dec))
        d_dec = dec - t["dec_deg"]
        sep = float(np.hypot(d_ra, d_dec) * 3600.0)
        if sep <= max_sep_arcsec and (best is None or sep < best[0]):
            best = (sep, pm)
    if best is None:
        return None
    pm = best[1]
    return float(pm["pmra"]), float(pm["pmdec"]), float(pm.get("epoch", 2000.0))


def nearest_proper_motion(ra: float, dec: float, radius_deg: float = 0.02) -> tuple[float, float, float] | None:
    """Proper motion of the closest source, as (pmra*cos(dec), pmdec, epoch_year).

    High-proper-motion stars are tens of pixels away from their J2000 position in
    2026 data.  Without this correction a fixed aperture measures empty sky -- and
    the whole point of this tool is stars that move.

    Order: bundled catalogue values for the five NASA demo targets (instant,
    offline-safe), then Gaia DR3 via Vizier for everything else.
    """
    bundled = _bundled_pm(ra, dec)
    if bundled is not None:
        return bundled
    ov = gaia_dr3(ra, dec, radius_deg)
    if ov.empty:
        return None
    df = ov.table
    if "pmra" not in df.columns or "pmdec" not in df.columns:
        return None
    try:
        sep = ((df["ra"] - ra) * np.cos(np.radians(dec)))**2 + (df["dec"] - dec)**2
        row = df.loc[sep.idxmin()]
        pmra = float(row["pmra"])
        pmdec = float(row["pmdec"])
        if not (np.isfinite(pmra) and np.isfinite(pmdec)):
            return None
        return pmra, pmdec, 2016.0
    except Exception:
        return None


def to_pixels(overlay: Overlay, wcs, shape: tuple[int, int]) -> pd.DataFrame:
    """Project catalogue RA/Dec onto the sequence reference pixel grid."""
    if overlay.empty:
        return pd.DataFrame(columns=["x", "y", "mag", "label"])
    try:
        x, y = wcs.all_world2pix(overlay.table["ra"].values, overlay.table["dec"].values, 0)
    except Exception:
        return pd.DataFrame(columns=["x", "y", "mag", "label"])
    out = overlay.table.copy()
    out["x"] = np.asarray(x, dtype=float)
    out["y"] = np.asarray(y, dtype=float)
    ny, nx = shape
    inside = (out["x"] >= 0) & (out["x"] < nx) & (out["y"] >= 0) & (out["y"] < ny)
    return out[inside].reset_index(drop=True)
