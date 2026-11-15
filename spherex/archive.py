"""Archive layer: IRSA SIA2 (primary), IRSA TAP (secondary), AWS Open Data.

Why this module issues the SIA request itself
---------------------------------------------
``astroquery.ipac.irsa.Irsa.query_sia`` fetches ``/SIA/capabilities`` first, to learn
the table columns, before it asks for a single row.  When IRSA's servlet container is
unhealthy that preflight comes back as a proxy 502 and the whole call dies even though
the query endpoint may be fine.  We therefore send the SIA2 request ourselves and parse
the VOTable, which removes the preflight from the critical path and lets us own the
timeout, the retry policy and the error text.

Why TAP needs help on SPHEREx rows
----------------------------------
``ivoa.obscore`` returns real SPHEREx rows, but ``cloud_access`` is whitespace for every
one of them and ``access_url`` is an IRSA datalink resolver rather than a FITS file.
Dropping those rows (the previous behaviour) turned a successful TAP query into "no
epochs".  The S3 object key is nonetheless fully determined by the observation, so one
LIST against the public bucket recovers the true file URL for each detector.

Metadata outages are survivable, pixels are not the problem
-----------------------------------------------------------
SPHEREx *metadata* (SIA/TAP, behind the IRSA proxy) and SPHEREx *pixels*
(``s3://nasa-irsa-spherex``) are separate services.  When metadata is unreachable we
answer the cone search from the on-disk index of earlier real IRSA responses, and the
pixel data is still read live from S3 by :mod:`spherex.rangecut`.  Nothing is
synthesised: every epoch returned here was returned by IRSA.

Every public function degrades instead of raising: the UI shows a coverage or
"archive busy" card rather than a stack trace.
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import requests

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
    as_of: str = ""  # ISO date the epoch list was captured (snapshot/cache provenance)

    @property
    def empty(self) -> bool:
        return not self.epochs


# --------------------------------------------------------------- endpoints / policy
_UA = "spherex-time-machine/1.0 (public research tool)"
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": _UA})

# IRSA's SIA answers are slow even when healthy (a full cone took ~10 min in the field),
# so the per-attempt timeout is deliberately larger than the generic HTTP one.
SIA_TIMEOUT = float(os.environ.get("SPHEREX_SIA_TIMEOUT", "90"))
TAP_TIMEOUT = float(os.environ.get("SPHEREX_TAP_TIMEOUT", "90"))
SIA_ATTEMPTS = 2
TAP_ATTEMPTS = 2
SIA_MAXREC = int(os.environ.get("SPHEREX_SIA_MAXREC", "20000"))
# IRSA refuses/timeouts on very wide obscore scans; a cone this small never needs more.
TAP_MAXREC = int(os.environ.get("SPHEREX_TAP_MAXREC", "5000"))

_CACHE_PATH = config.CACHE_DIR / "sia_cache.json"
_CACHE_TTL = 6 * 3600
_AWS_INDEX_PATH = config.CACHE_DIR / "aws_index.json"
_SNAPSHOT_PATH = config.SNAPSHOT_PATH
_SNAPSHOT: dict | None = None

_TAP_COLUMNS = (
    "s_ra", "s_dec", "obs_id", "obs_collection", "energy_bandpassname",
    "t_min", "t_max", "t_exptime", "s_pixel_scale", "access_url",
    "cloud_access", "s_region",
)


class ArchiveError(RuntimeError):
    """An IRSA endpoint could not be reached or returned an error document."""


# ------------------------------------------------------------------- tiny JSON store
def _load_json(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_json(path: Path, payload: dict) -> None:
    try:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        tmp.replace(path)
    except Exception:
        pass


def _load_cache() -> dict:
    return _load_json(_CACHE_PATH)


def _save_cache(cache: dict) -> None:
    _save_json(_CACHE_PATH, cache)


# ------------------------------------------------------------------------- HTTP core
def _request(method: str, url: str, attempts: int, timeout: float, **kwargs):
    """HTTP with bounded exponential backoff; raises :class:`ArchiveError`."""
    last: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            resp = _SESSION.request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            last = ArchiveError(f"{type(exc).__name__}: {exc}")
        else:
            if resp.status_code == 200:
                return resp
            reason = (resp.reason or "").strip()
            last = ArchiveError(f"HTTP {resp.status_code}{' ' + reason if reason else ''}")
            # A 4xx other than rate limiting will not improve by asking again.
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                break
        if attempt + 1 < max(1, attempts):
            time.sleep(0.9 * (2**attempt))
    raise last or ArchiveError("unreachable")


def _get(url: str, params: dict, timeout: float, attempts: int = 2):
    return _request("GET", url, attempts=attempts, timeout=timeout, params=params)


def _post(url: str, data: dict, timeout: float, attempts: int = 2):
    return _request("POST", url, attempts=attempts, timeout=timeout, data=data)


def _votable_error(payload: bytes) -> str:
    """Return the QUERY_STATUS message when the VOTable reports a failure."""
    head = payload[:8000].decode("utf-8", "replace")
    m = re.search(r'name="QUERY_STATUS"\s+value="([^"]*)"', head)
    if not m or m.group(1).upper() != "ERROR":
        return ""
    body = re.search(
        r'<INFO[^>]*name="QUERY_STATUS"[^>]*>(.*?)</INFO>', head, re.S
    )
    text = re.sub(r"<[^>]+>", " ", body.group(1)) if body else ""
    return " ".join(text.split())[:300] or "query failed"


def _votable_to_df(payload: bytes) -> pd.DataFrame:
    from astropy.table import Table

    err = _votable_error(payload)
    if err:
        raise ArchiveError(f"VOTable error: {err}")
    table = Table.read(io.BytesIO(payload), format="votable")
    df = table.to_pandas()
    df.columns = [str(c).lower() for c in df.columns]
    return df


# ------------------------------------------------------------------- row → Epoch
def _proc_version(url: str) -> str:
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


def _fnum(value, default: float = float("nan")) -> float:
    try:
        out = float(value)
    except Exception:
        return default
    return out if np.isfinite(out) else default


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


def _is_fits_url(url: str) -> bool:
    path = str(url or "").split("?")[0].lower()
    return path.endswith(".fits") or path.endswith(".fits.gz")


def _rows_to_epochs(
    df: pd.DataFrame,
    collection: str,
    aws_map: dict[tuple[str, int], str] | None = None,
) -> list[Epoch]:
    out: list[Epoch] = []
    aws_map = aws_map or {}
    for _, row in df.iterrows():
        url = str(row.get("access_url", "") or "")
        obs_id = str(row.get("obs_id", "") or "")
        det = _detector_of(row)
        aws = _aws_from_cloud_access(str(row.get("cloud_access", "") or ""))
        if not aws:
            aws = aws_map.get((obs_id, det), "")
        if not aws and _is_fits_url(url):
            # Some SIA deployments hand out the FITS URL directly, not a datalink id.
            aws = url
        # A datalink resolver cannot be range-read as FITS, and without a real file
        # URL the epoch could never be rendered, so skip it rather than advertise a
        # frame that cannot load.
        if not aws:
            continue
        info = config.DETECTOR_INFO.get(det, {})
        t_min = _fnum(row.get("t_min"), float("nan"))
        if not np.isfinite(t_min):
            continue
        t_max = _fnum(row.get("t_max"), t_min)
        out.append(
            Epoch(
                obs_id=obs_id,
                detector=det,
                band=str(row.get("energy_bandpassname", "")),
                t_min=t_min,
                t_max=t_max,
                access_url=url,
                aws_url=aws,
                proc_version=_proc_version(aws or url),
                collection=str(row.get("obs_collection", collection) or collection),
                exptime=_fnum(row.get("t_exptime"), float("nan")),
                pixscale=_fnum(
                    row.get("s_pixel_scale"), config.PIXEL_SCALE_ARCSEC
                ) or config.PIXEL_SCALE_ARCSEC,
                s_region=str(row.get("s_region", "") or ""),
                wl_center=info.get("wl_center", float("nan")),
            )
        )
    return out


# --------------------------------------------------------------------- SIA2 (primary)
def _sia_rows(ra: float, dec: float, radius_deg: float, collection: str) -> pd.DataFrame:
    params = {
        "POS": f"{ra:.7f},{dec:.7f}",
        "SIZE": f"{radius_deg:.5f}",
        "COLLECTION": collection,
        "RESPONSEFORMAT": "votable",
        "MAXREC": str(SIA_MAXREC),
    }
    # SIA2 lives at <base>/query; some IRSA deployments also answer on <base> itself.
    last: Exception | None = None
    for base, attempts in (
        (config.IRSA_SIA_URL + "/query", SIA_ATTEMPTS),
        (config.IRSA_SIA_URL, 1),
    ):
        try:
            resp = _get(base, params, SIA_TIMEOUT, attempts=attempts)
        except ArchiveError as exc:
            last = exc
            continue
        return _votable_to_df(resp.content)
    raise last or ArchiveError("SIA unreachable")


def query_sia(
    ra: float, dec: float, radius_deg: float, collections: tuple[str, ...] | None = None
) -> QueryResult:
    """Primary path: IRSA SIA2, queried directly so no capabilities preflight runs."""
    collections = collections or config.COLLECTIONS
    all_epochs: list[Epoch] = []
    rows = 0
    errors: list[str] = []
    for coll in collections:
        try:
            df = _sia_rows(ra, dec, radius_deg, coll)
        except Exception as exc:  # noqa: BLE001 - one collection must not kill the query
            errors.append(f"{coll}: {exc}")
            continue
        if df.empty:
            continue
        rows += len(df)
        all_epochs.extend(_rows_to_epochs(df, coll))
    return QueryResult(
        epochs=all_epochs,
        source="IRSA SIA2",
        ok=bool(all_epochs) or not errors,
        message="; ".join(errors),
        n_rows=rows,
    )


# ------------------------------------------------------- AWS Open Data (data plane)
_S3_KEY_RE = re.compile(
    r"^level2_(?P<obs>\d{4}W\d{2}_\d[A-Z]_\d{4}_\d)D(?P<det>\d)_spx_(?P<ver>l2b-v[\d-]+)\.fits$"
)


def _bucket_prefix(obs_id: str, collection: str) -> str:
    coll = str(collection or "").strip().replace("spherex_", "")
    if not coll:
        return ""
    run = "_".join(str(obs_id).split("_")[:2])
    if "W" not in run:
        return ""
    return f"{coll}/level2/{run}/"


def _s3_list(prefix: str, max_pages: int = 10) -> list[str]:
    """List every object under ``prefix`` in the public SPHEREx bucket."""
    keys: list[str] = []
    token = ""
    for _ in range(max_pages):
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        resp = _get(config.AWS_BASE + "/", params, config.HTTP_TIMEOUT, attempts=2)
        text = resp.text
        for block in re.finditer(r"<Contents>(.*?)</Contents>", text, re.S):
            key = re.search(r"<Key>([^<]+)</Key>", block.group(1))
            if key:
                keys.append(key.group(1))
        token_match = re.search(
            r"<NextContinuationToken>([^<]+)</NextContinuationToken>", text
        )
        if not token_match:
            break
        token = token_match.group(1)
    return keys


def resolve_aws_urls(
    rows: list[tuple[str, str]], max_prefixes: int = 8
) -> dict[tuple[str, int], str]:
    """Map ``(obs_id, detector) -> S3 FITS URL`` for observations TAP could not link.

    SPHEREx TAP rows carry a blank ``cloud_access``, so the file URL has to be rebuilt.
    The object key is fully determined by the observation, and a single LIST per
    observing run returns every detector file for every exposure in that run.  Listings
    are cached on disk, so the cost is paid once per run per install.
    """
    wanted: list[tuple[str, str]] = []
    for obs_id, collection in rows:
        obs_id = str(obs_id or "")
        if obs_id and obs_id not in {w[0] for w in wanted}:
            wanted.append((obs_id, str(collection or "")))
    if not wanted:
        return {}

    cache = _load_json(_AWS_INDEX_PATH)
    urls: dict[str, str] = cache.get("urls", {}) or {}
    listed: dict[str, int] = cache.get("listed", {}) or {}

    prefixes: list[str] = []
    for obs_id, collection in wanted:
        prefix = _bucket_prefix(obs_id, collection)
        if prefix and prefix not in prefixes:
            prefixes.append(prefix)

    dirty = False
    for prefix in prefixes[:max_prefixes]:
        if prefix in listed:
            continue
        try:
            keys = _s3_list(prefix)
        except Exception:  # noqa: BLE001 - a missing run must not abort the rest
            continue
        for key in keys:
            m = _S3_KEY_RE.match(key.rsplit("/", 1)[-1])
            if not m:
                continue
            urls[f"{m.group('obs')}|{m.group('det')}"] = f"{config.AWS_BASE}/{key}"
        listed[prefix] = int(time.time())
        dirty = True

    if dirty:
        _save_json(_AWS_INDEX_PATH, {"urls": urls, "listed": listed})

    found: dict[tuple[str, int], str] = {}
    for obs_id, _collection in wanted:
        for det in range(1, config.N_DETECTORS + 1):
            url = urls.get(f"{obs_id}|{det}")
            if url:
                found[(obs_id, det)] = url
    return found


# ----------------------------------------------------------------------- TAP (backup)
def tap_adql(ra: float, dec: float, radius_deg: float,
             collections: tuple[str, ...] = config.COLLECTIONS) -> str:
    """Valid ADQL cone on obscore, restricted to the SPHEREx quick-release tables."""
    colls = ", ".join(f"'{c}'" for c in collections)
    cols = ", ".join(_TAP_COLUMNS)
    return (
        f"SELECT {cols} FROM ivoa.obscore "
        f"WHERE obs_collection IN ({colls}) "
        "AND 1=CONTAINS(POINT('ICRS', s_ra, s_dec), "
        f"CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius_deg:.6f}))"
    )


def query_tap(
    ra: float, dec: float, radius_deg: float,
    collections: tuple[str, ...] | None = None,
) -> QueryResult:
    """Secondary path: IRSA TAP/obscore, with AWS URLs rebuilt from the bucket."""
    collections = collections or config.COLLECTIONS
    adql = tap_adql(ra, dec, radius_deg, collections)
    try:
        resp = _post(
            config.IRSA_TAP_URL + "/sync",
            {"QUERY": adql, "FORMAT": "votable", "MAXREC": str(TAP_MAXREC)},
            TAP_TIMEOUT,
            attempts=TAP_ATTEMPTS,
        )
        df = _votable_to_df(resp.content)
    except Exception as exc:  # noqa: BLE001
        return QueryResult(source="IRSA TAP", ok=False, message=f"{exc}"[:300])
    if df.empty:
        return QueryResult(source="IRSA TAP", ok=True, n_rows=0)

    need = [
        (str(row.get("obs_id", "") or ""), str(row.get("obs_collection", "") or ""))
        for _, row in df.iterrows()
        if not _aws_from_cloud_access(str(row.get("cloud_access", "") or ""))
    ]
    aws_map = resolve_aws_urls(need) if need else {}
    epochs = _rows_to_epochs(df, "tap", aws_map=aws_map)
    message = ""
    if not epochs:
        message = (
            f"TAP returned {len(df)} row(s) but none could be linked to an AWS file"
        )
    return QueryResult(
        epochs=epochs, source="IRSA TAP", ok=True, message=message, n_rows=len(df)
    )


# ------------------------------------------------------------- on-disk epoch index
def _cache_key(ra: float, dec: float, radius: float) -> str:
    return f"{ra:.5f}_{dec:.5f}_{radius:.4f}"


def _sep_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle separation in degrees."""
    p1, p2 = math.radians(dec1), math.radians(dec2)
    dp = p2 - p1
    dl = math.radians(ra2 - ra1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(a))))


def _entry_center(key: str) -> tuple[float, float, float] | None:
    parts = str(key).split("_")
    if len(parts) != 3:
        return None
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        return None


def _cache_candidates(cache: dict, ra: float, dec: float, radius: float) -> list[dict]:
    """Cache entries whose search cone overlaps the requested one (any age)."""
    hits: list[tuple[float, dict]] = []
    for key, entry in cache.items():
        center = _entry_center(key)
        if center is None or not isinstance(entry, dict):
            continue
        cra, cdec, crad = center
        sep = _sep_deg(ra, dec, cra, cdec)
        if sep <= crad + radius + 0.02:
            hits.append((sep, entry))
    hits.sort(key=lambda item: item[0])
    return [entry for _, entry in hits]


def _epochs_from_entries(entries: list[dict], ra: float, dec: float) -> list[Epoch]:
    """Union of cached epochs that really cover the position, newest row per detector."""
    seen: dict[tuple[str, int], Epoch] = {}
    for entry in entries:
        for raw in entry.get("epochs", []) or []:
            try:
                ep = Epoch(**raw)
            except Exception:
                continue
            if not ep.aws_url or not ep.covers(ra, dec):
                continue
            slot = (ep.obs_id, ep.detector)
            if slot not in seen:
                seen[slot] = ep
    return sorted(seen.values(), key=lambda e: e.t_min)


def _cached_epochs(
    cache: dict, ra: float, dec: float, radius: float, max_age: float | None = None
) -> list[Epoch]:
    entries = _cache_candidates(cache, ra, dec, radius)
    if max_age is not None:
        now = time.time()
        entries = [e for e in entries if now - float(e.get("t", 0) or 0) < max_age]
    return _epochs_from_entries(entries, ra, dec)


def _store_cache(cache: dict, ra: float, dec: float, radius: float,
                 epochs: list[Epoch]) -> None:
    """Merge into the entry for this cone so the index keeps growing, then persist."""
    merged: dict[tuple[str, int], Epoch] = {
        (e.obs_id, e.detector): e
        for e in _epochs_from_entries(_cache_candidates(cache, ra, dec, radius), ra, dec)
    }
    for ep in epochs:
        merged[(ep.obs_id, ep.detector)] = ep
    cache[_cache_key(ra, dec, radius)] = {
        "t": time.time(),
        "epochs": [e.__dict__ for e in merged.values()],
    }
    _save_cache(cache)


def _snapshot_epochs(ra: float, dec: float) -> tuple[list[Epoch], str]:
    """Real IRSA rows bundled with the app, filtered to this position."""
    global _SNAPSHOT
    if _SNAPSHOT is None:
        _SNAPSHOT = _load_json(_SNAPSHOT_PATH)
    seen: dict[tuple[str, int], Epoch] = {}
    for raw in _SNAPSHOT.get("epochs", []) or []:
        try:
            ep = Epoch(**raw)
        except Exception:
            continue
        if not ep.aws_url or not ep.covers(ra, dec):
            continue
        seen.setdefault((ep.obs_id, ep.detector), ep)
    return sorted(seen.values(), key=lambda e: e.t_min), _SNAPSHOT.get("generated_at", "")


def get_epochs(
    ra: float, dec: float, fov_deg: float, use_cache: bool = True
) -> QueryResult:
    """Fresh cache -> bundled snapshot -> live SIA2 -> TAP -> stale cache.

    Every tier except the last resort is *real* IRSA metadata, and the pixel data is
    always fetched live from the AWS Open Data bucket.  The snapshot tier exists because
    the deployed app starts with an empty cache: without it a metadata outage reduces
    the page to a single demo frame even though thousands of real epochs exist.
    """
    radius = max(0.12, fov_deg * 0.6 + 0.05)
    cache = _load_cache() if use_cache else {}

    if use_cache:
        fresh = _cached_epochs(cache, ra, dec, radius, max_age=_CACHE_TTL)
        if fresh:
            return QueryResult(
                epochs=fresh, source="IRSA SIA2 (cached)", ok=True, n_rows=len(fresh)
            )

    # Bundled real IRSA rows. Served before touching the network: it is deterministic,
    # instant, and keeps the deployed app working through a total IRSA metadata outage.
    snap, snap_date = _snapshot_epochs(ra, dec)
    if snap:
        return QueryResult(
            epochs=snap,
            source="IRSA SIA2 snapshot",
            as_of=snap_date,
            ok=True,
            n_rows=len(snap),
        )

    result = query_sia(ra, dec, radius)
    if result.empty:
        tap = query_tap(ra, dec, radius)
        if tap.epochs:
            result = tap
        else:
            result.message = "; ".join(
                part for part in (result.message, tap.message) if part
            )
            result.ok = result.ok and tap.ok

    if result.epochs:
        if use_cache:
            _store_cache(cache, ra, dec, radius, result.epochs)
        return result

    if use_cache:
        cached = _cached_epochs(cache, ra, dec, radius)
        if cached:
            note = "IRSA metadata unreachable - serving real cached epochs from disk"
            return QueryResult(
                epochs=cached,
                source="IRSA SIA2 (cached)",
                ok=True,
                message="; ".join(p for p in (note, result.message) if p),
                n_rows=len(cached),
            )
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
