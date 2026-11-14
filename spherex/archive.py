"""Archive layer: IRSA SIA2 (primary), IRSA TAP (secondary), AWS Open Data.

Every public function degrades instead of raising: the UI shows a coverage or
"archive busy" card rather than a stack trace.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import astropy.units as u
import numpy as np
import pandas as pd
from astropy.coordinates import SkyCoord

from . import config


@dataclass
class Epoch:
    obs_id: str
    detector: int
    band: str
    t_min: float
    t_max: float
    access_url: str
    aws_url: str
    proc_version: str
    collection: str
    exptime: float
    pixscale: float
    s_region: str = ""
    wl_center: float = float("nan")

    def covers(self, ra: float, dec: float) -> bool:
        """Point-in-footprint test using the SIA s_region polygon (no I/O)."""
        poly = _parse_polygon(self.s_region)
        if not poly:
            return True
        lon = np.asarray([p[0] for p in poly], dtype=float)
        lat = np.asarray([p[1] for p in poly], dtype=float)
        # unwrap in RA around the test point so the polygon is continuous
        ref = ra
        lon = ((lon - ref + 180.0) % 360.0) - 180.0 + ref
        test_x = ra
        inside = False
        n = len(lon)
        j = n - 1
        for i in range(n):
            xi, yi, xj, yj = lon[i], lat[i], lon[j], lat[j]
            if (yi > dec) != (yj > dec) and (
                test_x < (xj - xi) * (dec - yi) / (yj - yi + 1e-12) + xi
            ):
                inside = not inside
            j = i
        return bool(inside)

    @property
    def mjd(self) -> float:
        return self.t_min

    @property
    def iso(self) -> str:
        from astropy.time import Time

        return Time(self.t_min, format="mjd").iso[:19]

    @property
    def tag(self) -> str:
        return f"{self.obs_id}·D{self.detector}"


@dataclass
class QueryResult:
    epochs: list[Epoch] = field(default_factory=list)
    source: str = ""
    ok: bool = True
    message: str = ""
    n_rows: int = 0

    @property
    def empty(self) -> bool:
        return not self.epochs


_CACHE_PATH = config.CACHE_DIR / "sia_cache.json"
_CACHE_TTL = 6 * 3600


def _load_cache() -> dict:
    try:
        with open(_CACHE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        tmp = _CACHE_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        tmp.replace(_CACHE_PATH)
    except Exception:
        pass


def _proc_version(url: str) -> str:
    import re

    m = re.search(r"l2b-(v\d+-\d{4}-\d{3})", url or "")
    if m:
        return m.group(1)
    m = re.search(r"l2b-(v\d+)", url or "")
    return m.group(1) if m else "unknown"


def _detector_of(row: pd.Series) -> int:
    try:
        return int(str(row.get("energy_bandpassname", "")).replace("SPHEREx-D", ""))
    except Exception:
        pass
    try:
        return int(row.get("detector", 0))
    except Exception:
        return 0


def _parse_polygon(region: str) -> list[tuple[float, float]]:
    try:
        parts = str(region).strip().split()
        if not parts or parts[0].upper() != "POLYGON":
            return []
        nums = [float(v) for v in parts[2:]]
        return list(zip(nums[0::2], nums[1::2]))
    except Exception:
        return []


def _aws_from_cloud_access(raw: str) -> str:
    try:
        data = json.loads(raw)
        aws = data.get("aws") or {}
        key = aws.get("key")
        bucket = aws.get("bucket_name", config.AWS_BUCKET)
        region = aws.get("region", config.AWS_REGION)
        if key:
            return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
    except Exception:
        pass
    return ""


def _rows_to_epochs(df: pd.DataFrame, collection: str) -> list[Epoch]:
    out: list[Epoch] = []
    for _, row in df.iterrows():
        url = str(row.get("access_url", "") or "")
        aws = _aws_from_cloud_access(str(row.get("cloud_access", "") or ""))
        # TAP's cloud_access is whitespace for every row, so epochs without a usable
        # AWS URL can never be fetched (access_url is an IRSA datalink resolver,
        # not a FITS file). Drop them so the UI shows an honest coverage card
        # instead of "epochs exist but no cutout was usable".
        if not aws:
            continue
        if not url and not aws:
            continue
        det = _detector_of(row)
        info = config.DETECTOR_INFO.get(det, {})
        try:
            t_min = float(row.get("t_min", np.nan))
        except Exception:
            t_min = float("nan")
        if not np.isfinite(t_min):
            continue
        out.append(
            Epoch(
                obs_id=str(row.get("obs_id", "")),
                detector=det,
                band=str(row.get("energy_bandpassname", "")),
                t_min=t_min,
                t_max=float(row.get("t_max", t_min) or t_min),
                access_url=url,
                aws_url=aws,
                proc_version=_proc_version(url or aws),
                collection=str(row.get("obs_collection", collection)),
                exptime=float(row.get("t_exptime", np.nan) or np.nan),
                pixscale=float(row.get("s_pixel_scale", config.PIXEL_SCALE_ARCSEC) or config.PIXEL_SCALE_ARCSEC),
                s_region=str(row.get("s_region", "") or ""),
                wl_center=info.get("wl_center", float("nan")),
            )
        )
    return out


def query_sia(
    ra: float, dec: float, radius_deg: float, collections: tuple[str, ...] | None = None
) -> QueryResult:
    """Primary path: astroquery IRSA SIA2."""
    from astroquery.ipac.irsa import Irsa

    collections = collections or config.COLLECTIONS
    all_epochs: list[Epoch] = []
    rows = 0
    errors: list[str] = []
    for coll in collections:
        for attempt in range(config.HTTP_RETRIES + 1):
            try:
                table = Irsa.query_sia(
                    pos=(ra * u.deg, dec * u.deg, radius_deg * u.deg),
                    collection=coll,
                )
                if table is None or len(table) == 0:
                    break
                df = table.to_pandas()
                df.columns = [c.lower() for c in df.columns]
                rows += len(df)
                all_epochs.extend(_rows_to_epochs(df, coll))
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == config.HTTP_RETRIES:
                    errors.append(f"{coll}: {type(exc).__name__}")
                else:
                    time.sleep(0.8 * (2**attempt))
    return QueryResult(
        epochs=all_epochs,
        source="IRSA SIA2",
        ok=bool(all_epochs) or not errors,
        message="; ".join(errors),
        n_rows=rows,
    )


def query_tap(ra: float, dec: float, radius_deg: float) -> QueryResult:
    """Secondary path: IRSA TAP/obscore, used when SIA2 returns nothing."""
    try:
        import pyvo

        svc = pyvo.dal.TAPService(config.IRSA_TAP_URL)
        adql = f"""
        SELECT s_ra, s_dec, obs_id, obs_collection, energy_bandpassname,
               t_min, t_max, t_exptime, s_pixel_scale, access_url, cloud_access
        FROM ivoa.obscore
        WHERE obs_collection IN ('spherex_qr2','spherex_qr3')
          AND 1=CONTAINS(POINT('ICRS', s_ra, s_dec),
                         CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius_deg:.6f}))
        """
        res = svc.search(adql, maxrec=4000)
        df = res.to_table().to_pandas()
        if df.empty:
            return QueryResult(source="IRSA TAP", ok=True, n_rows=0)
        df.columns = [c.lower() for c in df.columns]
        epochs = _rows_to_epochs(df, "tap")
        return QueryResult(epochs=epochs, source="IRSA TAP", ok=True, n_rows=len(df))
    except Exception as exc:  # noqa: BLE001
        return QueryResult(source="IRSA TAP", ok=False, message=f"{type(exc).__name__}: {exc}")


def _cache_key(ra: float, dec: float, radius: float) -> str:
    return f"{ra:.5f}_{dec:.5f}_{radius:.4f}"


def get_epochs(
    ra: float, dec: float, fov_deg: float, use_cache: bool = True
) -> QueryResult:
    """SIA2 -> TAP -> disk cache. Always returns a QueryResult."""
    radius = max(0.12, fov_deg * 0.6 + 0.05)
    key = _cache_key(ra, dec, radius)
    cache = _load_cache() if use_cache else {}
    if use_cache and key in cache:
        entry = cache[key]
        if time.time() - entry.get("t", 0) < _CACHE_TTL:
            epochs = [Epoch(**e) for e in entry.get("epochs", [])]
            # Invalidate stale entries that were cached before the aws_url filter.
            if epochs and all(getattr(e, "aws_url", "") for e in epochs):
                return QueryResult(epochs=epochs, source="disk cache", ok=True, n_rows=len(epochs))

    result = query_sia(ra, dec, radius)
    if result.empty:
        tap = query_tap(ra, dec, radius)
        if not tap.empty:
            result = tap
        elif not tap.ok:
            result.message = (result.message + "; " if result.message else "") + tap.message

    if result.epochs and use_cache:
        cache[key] = {
            "t": time.time(),
            "epochs": [e.__dict__ for e in result.epochs],
        }
        _save_cache(cache)
    return result


def group_epochs(epochs: list[Epoch]) -> dict[str, list[Epoch]]:
    """Group by obs_id, ordered by time, one entry per distinct observation."""
    groups: dict[str, list[Epoch]] = {}
    for ep in sorted(epochs, key=lambda e: e.t_min):
        groups.setdefault(ep.obs_id, []).append(ep)
    return groups


def coverage_deeplink(ra: float, dec: float, size_arcmin: float = 30.0) -> str:
    return (
        "https://irsa.ipac.caltech.edu/applications/Spitzer/SHA/"
        f"#/?__action=layout.showDropDown&view=Search&spatial=cone&"
        f"spatial_text=&ra={ra:.5f}&dec={dec:.5f}&radius={size_arcmin:.1f}"
    )


def irsa_data_link(url: str) -> str:
    return url or config.IRSA_SEARCH


def summarise(epochs: list[Epoch]) -> dict:
    if not epochs:
        return {"n_epochs": 0, "n_obs": 0, "detectors": [], "t_span": None}
    times = [e.t_min for e in epochs]
    return {
        "n_epochs": len({e.obs_id for e in epochs}),
        "n_obs": len(epochs),
        "detectors": sorted({e.detector for e in epochs}),
        "t_span": (min(times), max(times)),
        "proc_versions": sorted({e.proc_version for e in epochs}),
        "collections": sorted({e.collection for e in epochs}),
    }
