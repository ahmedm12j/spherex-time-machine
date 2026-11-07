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
