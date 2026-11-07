"""Epoch assembly, WCS reprojection, inverse-variance coaddition, differencing.

Design notes
------------
* Every cutout is reprojected onto one common tangent-plane grid so that the
  background stars sit still and only real motion (proper motion, parallax,
  variability) shows up when blinking.
* Coaddition of the six detector arrays uses inverse-variance weights, so the
  stacked frame really is the maximum-S/N combination rather than a plain mean.
* Difference images divide by sqrt(var_A + var_B), giving a signal-to-noise
  map instead of an arbitrary stretch.
* Contaminated pixels (FLAGS) are excluded before anything is combined.
"""
from __future__ import annotations

import io
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import numpy as np
from astropy.wcs import WCS
from numpy.lib.stride_tricks import sliding_window_view

from . import config, fitsdata
from .archive import Epoch
from .fitsdata import Cutout

COLORMAPS = {
    "viridis": "viridis",
    "magma": "magma",
    "inferno": "inferno",
    "gray": "gray",
    "RdBu": "RdBu_r",
}


@dataclass
class Frame:
    data: np.ndarray
    variance: np.ndarray
    coverage: np.ndarray
    valid: np.ndarray
    t_min: float
    label: str
    obs_id: str
    detectors: list[int]
    wavelength: float
    psf_fwhm_px: float
    meta: dict = field(default_factory=dict)

    @property
    def iso(self) -> str:
        from astropy.time import Time

        return Time(self.t_min, format="mjd").iso[:19]

    @property
    def snr(self) -> np.ndarray:
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(self.variance > 0, self.data / np.sqrt(self.variance), np.nan)


@dataclass
class Sequence:
    frames: list[Frame]
    ref_wcs: WCS
    shape: tuple[int, int]
    fov_deg: float
    ra: float
    dec: float
    mode: str
    vmin: float = 0.0
    vmax: float = 1.0
    notes: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.frames)

    @property
    def pixel_scale_arcsec(self) -> float:
        return self.fov_deg * 3600.0 / self.shape[1]

    def extent(self) -> tuple[float, float, float, float]:
        n = self.shape[1]
        half = self.fov_deg * 3600.0 / 2.0
        px = self.pixel_scale_arcsec
        ny = self.shape[0]
        return (-half - (n / 2 - 0.5) * px + n * px / 2 - half,
                half - (n / 2 - 0.5) * px + n * px / 2 - half,
                -half, half)


def reference_wcs(ra: float, dec: float, fov_deg: float, n: int = config.DISPLAY_PX) -> WCS:
    scale = fov_deg * 3600.0 / n
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = [(n + 1) / 2.0, (n + 1) / 2.0]
    w.wcs.cdelt = [-scale / 3600.0, scale / 3600.0]
    w.wcs.radesys = "ICRS"
    w.array_shape = (n, n)
    return w


def _reproject(arr: np.ndarray, wcs: WCS, ref: WCS, shape: tuple[int, int]):
    from reproject import reproject_interp

    out, footprint = reproject_interp(
        (arr.astype(np.float32), wcs), ref, shape_out=shape, return_footprint=True
    )
    return np.asarray(out, dtype=np.float32), np.asarray(footprint, dtype=np.float32)


def _combine_epoch(cuts: list[Cutout], ref: WCS, shape: tuple[int, int]) -> Frame | None:
    """Inverse-variance coadd of every detector covering this sky position."""
    num = np.zeros(shape, dtype=np.float64)
    wsum = np.zeros(shape, dtype=np.float64)
    var_num = np.zeros(shape, dtype=np.float64)
    good_any = np.zeros(shape, dtype=bool)
    fwhms: list[float] = []
    wls: list[float] = []
    used: list[int] = []

    for cut in cuts:
        img = cut.image.astype(np.float32)
        img = np.where(cut.mask, np.nan, img)
        data, foot = _reproject(img, cut.wcs, ref, shape)
        mask_f, _ = _reproject(cut.mask.astype(np.float32), cut.wcs, ref, shape)
        var = cut.variance if cut.variance is not None else np.ones_like(img)
        var = np.where(np.isfinite(var) & (var > 0), var, np.nan)
        var_f, _ = _reproject(var.astype(np.float32), cut.wcs, ref, shape)

        ok = (foot > 0) & np.isfinite(data) & (mask_f < 0.5)
        if not np.any(ok):
            continue
        weight = np.where(ok & np.isfinite(var_f) & (var_f > 0), 1.0 / var_f, 0.0)
        if not np.any(weight > 0):
            weight = np.where(ok, 1.0, 0.0)
        num += np.where(ok, data, 0.0) * weight
        wsum += weight
        var_safe = np.where(np.isfinite(var_f), var_f, 0.0)
        var_num += np.where(ok, var_safe, 0.0) * weight**2
        good_any |= ok
        fwhms.append(cut.psf_fwhm_px)
        wls.append(cut.wl_center)
        used.append(cut.epoch.detector)

    if not used:
        return None

    with np.errstate(divide="ignore", invalid="ignore"):
        data = np.where(wsum > 0, num / wsum, np.nan)
        var = np.where(wsum > 0, var_num / wsum**2, np.nan)
    data = np.where(good_any, data, np.nan).astype(np.float32)
    var = np.where(good_any, var, np.nan).astype(np.float32)

    first = cuts[0]
    dets = sorted(set(used))
    flag_totals: dict[str, int] = {}
    for cut in cuts:
        for key, val in cut.flag_counts.items():
            flag_totals[key] = flag_totals.get(key, 0) + int(val)
    wl_lo = float(np.nanmin([c.wavelength.min() for c in cuts]))
    wl_hi = float(np.nanmax([c.wavelength.max() for c in cuts]))
    return Frame(
        data=data,
        variance=var,
        coverage=good_any.astype(np.float32),
        valid=good_any,
        t_min=float(np.median([c.epoch.t_min for c in cuts])),
        label="",
        obs_id=first.epoch.obs_id,
        detectors=dets,
        wavelength=float(np.nanmean(wls)) if wls else float("nan"),
        psf_fwhm_px=float(np.nanmedian(fwhms)) if fwhms else 6.1,
        meta={
            "proc_version": first.epoch.proc_version,
            "collection": first.epoch.collection,
            "n_detectors": len(dets),
            "exptime": first.epoch.exptime,
            "flag_counts": dict(sorted(flag_totals.items(), key=lambda kv: -kv[1])),
            "good_fraction": float(np.mean([c.good_fraction for c in cuts])),
            "wavelength_range": (wl_lo, wl_hi),
            "n_contaminated_px": int(sum(c.mask.sum() for c in cuts)),
        },
    )


def _global_stretch(frames: list[Frame]) -> tuple[float, float]:
    vals = np.concatenate([f.data[np.isfinite(f.data)].ravel()[::7] for f in frames if np.any(np.isfinite(f.data))])
    if vals.size < 10:
        return 0.0, 1.0
    lo, hi = np.nanpercentile(vals, [2.0, 99.5])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo, hi = float(np.nanmin(vals)), float(np.nanmax(vals))
    if hi <= lo:
        hi = lo + 1e-6
    return float(lo), float(hi)


def build_sequence(
    epochs: list[Epoch],
    ra: float,
    dec: float,
    fov_deg: float,
    mode: str = "stack",
    detector: int | None = None,
    max_frames: int = 10,
    want_zodi: bool = False,
    progress=None,
) -> Sequence:
    """Assemble a blink sequence.  Returns an empty Sequence instead of raising."""
    ref = reference_wcs(ra, dec, fov_deg)
    shape = (config.DISPLAY_PX, config.DISPLAY_PX)
    notes: list[str] = []

    cand = [e for e in epochs if e.covers(ra, dec)]
    if mode == "single" and detector:
        cand = [e for e in cand if e.detector == detector]
    if not cand:
        return Sequence([], ref, shape, fov_deg, ra, dec, mode, notes=["no epochs cover this position"])

    by_obs: dict[str, list[Epoch]] = {}
    for ep in sorted(cand, key=lambda e: e.t_min):
        by_obs.setdefault(ep.obs_id, []).append(ep)

    # A blink is only interesting if the frames are far apart in time, so cluster
    # observations into visits (>= 0.5 d apart) and sample evenly across the whole
    # baseline instead of taking the first N exposures of one night.
    ordered = sorted(
        by_obs.items(), key=lambda kv: float(np.median([e.t_min for e in kv[1]]))
    )
    clusters: list[list[tuple[float, tuple[str, list[Epoch]]]]] = []
    for obs_id, eps in ordered:
        t = float(np.median([e.t_min for e in eps]))
        if clusters and (t - clusters[-1][-1][0]) < 0.5:
            clusters[-1].append((t, (obs_id, eps)))
        else:
            clusters.append([(t, (obs_id, eps))])
    reps = [max(cluster, key=lambda x: len(x[1][1]))[1] for cluster in clusters]
    if len(reps) > max_frames:
        idxs = np.linspace(0, len(reps) - 1, max_frames).round().astype(int)
        chosen = [reps[i] for i in sorted(set(idxs.tolist()))]
    else:
        chosen = reps

    tasks = [(obs_id, ep) for obs_id, eps in chosen for ep in eps]
    loaded: dict[str, list[Cutout]] = {}
    errors = 0

    def _one(item):
        obs_id, ep = item
        return obs_id, fitsdata.load_cutout(ep, ra, dec, fov_deg, want_zodi=want_zodi)

    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(_one, t) for t in tasks]
        done = 0
        for fut in as_completed(futs):
            done += 1
            if progress:
                progress(done / max(1, len(futs)))
            try:
                obs_id, cut = fut.result()
            except Exception:
                errors += 1
                continue
            if cut is None:
                errors += 1
                continue
            loaded.setdefault(obs_id, []).append(cut)

    frames: list[Frame] = []
    for obs_id, _eps in chosen:
        cuts = loaded.get(obs_id, [])
        if not cuts:
            continue
        frame = _combine_epoch(cuts, ref, shape)
        if frame is None:
            continue
        frames.append(frame)

    frames.sort(key=lambda f: f.t_min)
    for i, f in enumerate(frames):
        dets = "+".join(f"D{d}" for d in f.detectors)
        f.label = f"{f.iso}  {dets}"

    if errors:
        notes.append(f"{errors} file(s) skipped (unreachable or corrupt)")
    if len(frames) < 2:
        notes.append("fewer than two usable epochs at this position")
    elif len({round(f.t_min) for f in frames}) < 2:
        notes.append("all usable epochs are from the same night")

    seq = Sequence(frames, ref, shape, fov_deg, ra, dec, mode, notes=notes)
    if frames:
        seq.vmin, seq.vmax = _global_stretch(frames)
    return seq


# --------------------------------------------------------------- differencing
def _gauss_smooth(arr: np.ndarray, fwhm_px: float) -> np.ndarray:
    sigma = max(fwhm_px, 0.5) / 2.3548
    r = max(1, int(math.ceil(3 * sigma)))
    ax = np.arange(-r, r + 1)
    k = np.exp(-(ax**2) / (2 * sigma**2))
    k /= k.sum()
    out = np.where(np.isfinite(arr), arr, 0.0)
    pad = np.pad(out, r, mode="edge")
    tmp = np.apply_along_axis(lambda m: np.convolve(m, k, mode="valid"), 0, pad)
    sm = np.apply_along_axis(lambda m: np.convolve(m, k, mode="valid"), 1, tmp)
    return np.where(np.isfinite(arr), sm, np.nan)


def difference(
    a: Frame, b: Frame, psf_match: bool = True, subtract_zodi: bool = False
) -> dict:
    """Variance-weighted A - B with optional PSF matching."""
    da, db = a.data.copy(), b.data.copy()
    if psf_match:
        fwhm_a, fwhm_b = a.psf_fwhm_px, b.psf_fwhm_px
        # frames share the reference pixel grid, so FWHM values are comparable
        if fwhm_a > fwhm_b * 1.05:
            db = _gauss_smooth(db, math.sqrt(max(fwhm_a**2 - fwhm_b**2, 0.1)))
        elif fwhm_b > fwhm_a * 1.05:
            da = _gauss_smooth(da, math.sqrt(max(fwhm_b**2 - fwhm_a**2, 0.1)))

    diff = da - db
    va = np.where(np.isfinite(a.variance) & (a.variance > 0), a.variance, np.nan)
    vb = np.where(np.isfinite(b.variance) & (b.variance > 0), b.variance, np.nan)
    if psf_match:
        va = _gauss_smooth(va, math.sqrt(max(a.psf_fwhm_px, 1.0)))
        vb = _gauss_smooth(vb, math.sqrt(max(b.psf_fwhm_px, 1.0)))
    var = np.nansum(np.dstack([va, vb]), axis=2)
    with np.errstate(divide="ignore", invalid="ignore"):
        snr = np.where(var > 0, diff / np.sqrt(var), np.nan)
    both = a.valid & b.valid
    diff = np.where(both, diff, np.nan)
    snr = np.where(both, snr, np.nan)

    finite = snr[np.isfinite(snr)]
    # Empirical noise rescaling: propagated variance is correct per pixel but does
    # not include pixel-to-pixel PSF correlation, so we measure the robust width
    # of the residual distribution and divide by it.  Relative weights are kept.
    if finite.size > 100:
        med = float(np.median(finite))
        sigma_emp = float(1.4826 * np.median(np.abs(finite - med)))
    else:
        med, sigma_emp = 0.0, 1.0
    sigma_emp = sigma_emp if np.isfinite(sigma_emp) and 0.05 < sigma_emp < 100 else 1.0
    snr_cal = snr / sigma_emp
    stats = {
        "median": med,
        "std": float(np.std(finite)) if finite.size else 0.0,
        "p99": float(np.percentile(np.abs(snr_cal[np.isfinite(snr_cal)]), 99))
        if finite.size else 0.0,
        "n_pix": int(finite.size),
        "sigma_emp": sigma_emp,
        "label": f"{a.iso}  −  {b.iso}",
        "dt_days": (a.t_min - b.t_min),
    }
    return {"diff": diff, "snr": snr_cal, "snr_raw": snr, "var": var, "stats": stats}


def add_frames(a: Frame, b: Frame) -> dict:
    """Sum two epochs (inverse-variance coadd of the same sky area)."""
    va = np.where(np.isfinite(a.variance) & (a.variance > 0), a.variance, np.nan)
    vb = np.where(np.isfinite(b.variance) & (b.variance > 0), b.variance, np.nan)
    w = np.nansum(np.dstack([np.where(np.isfinite(va), 1 / va, 0), np.where(np.isfinite(vb), 1 / vb, 0)]), axis=2)
    num = (np.nan_to_num(a.data) * np.nan_to_num(1 / va)) + (np.nan_to_num(b.data) * np.nan_to_num(1 / vb))
    with np.errstate(divide="ignore", invalid="ignore"):
        stacked = np.where(w > 0, num / w, np.nan)
        var = np.where(w > 0, 1 / w, np.nan)
    both = a.valid | b.valid
    return {"image": np.where(both, stacked, np.nan), "variance": np.where(both, var, np.nan),
            "label": f"{a.iso}  +  {b.iso}"}


# ------------------------------------------------------------------- encoding
def to_u8(
    arr: np.ndarray,
    vmin: float | None = None,
    vmax: float | None = None,
    cmap: str = "viridis",
    symmetric: bool = False,
) -> np.ndarray:
    """Map a float image to RGBA uint8 using a perceptually uniform colormap."""
    from matplotlib import colormaps

    finite = arr[np.isfinite(arr)]
    if symmetric:
        lim = np.nanpercentile(np.abs(finite), 99) if finite.size else 1.0
        lim = float(lim) if np.isfinite(lim) and lim > 0 else 1.0
        vmin, vmax = -lim, lim
    else:
        if vmin is None or vmax is None:
            lo, hi = (np.nanpercentile(finite, [2, 99.5]) if finite.size else (0.0, 1.0))
            vmin, vmax = float(lo), float(hi)
    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
        vmax = vmin + 1e-6
    norm = np.clip((arr - vmin) / (vmax - vmin), 0.0, 1.0)
    norm = np.where(np.isfinite(arr), norm, 0.0)
    lut = (colormaps.get_cmap(cmap)(np.arange(256)) * 255).astype(np.uint8)
    idx = (norm * 255).astype(np.uint8)
    rgba = lut[idx]
    rgba[~np.isfinite(arr)] = (10, 12, 18, 255)
    return rgba


def png_b64(rgba: np.ndarray) -> str:
    import base64

    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def encode_frames(seq: Sequence, cmap: str = "viridis", gamma: float = 1.0) -> list[str]:
    """PNG data-URIs for the JS blink viewer."""
    out: list[str] = []
    for f in seq.frames:
        arr = f.data.astype(np.float64)
        if gamma != 1.0:
            lo, hi = seq.vmin, seq.vmax
            norm = np.clip((arr - lo) / (hi - lo + 1e-12), 1e-6, 1.0) ** gamma
            arr = lo + norm * (hi - lo)
        out.append(png_b64(to_u8(arr, seq.vmin, seq.vmax, cmap)))
    return out


def detection_thumbnail(seq: Sequence, x: int, y: int, half: int = 28,
                        cmap: str = "magma") -> str:
    if not seq.frames:
        return ""
    f = seq.frames[len(seq.frames) // 2]
    y0, y1 = max(0, y - half), min(f.data.shape[0], y + half)
    x0, x1 = max(0, x - half), min(f.data.shape[1], x + half)
    patch = f.data[y0:y1, x0:x1]
    return png_b64(to_u8(patch, cmap=cmap))
