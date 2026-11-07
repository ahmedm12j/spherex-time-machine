"""Photometry: on-the-fly spectra, source detection, mover ranking.

Spectra are built from the linear variable filter: for every epoch whose
footprint contains the requested sky position we read the wavelength of that
exact pixel from WCS-WAVE and the flux from a small aperture.  Each epoch
therefore contributes one (lambda, flux) point -- SPHEREx builds a spectrum of
a fixed position by revisiting it at many scan offsets.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from . import config, fitsdata, rangecut
from .archive import Epoch

# 1 MJy/sr integrated over one 6.15" SPHEREx pixel == 0.889 mJy (see SCIENCE.md)
MJY_SR_TO_MJY_PER_PIXEL = 1e6 * ((config.PIXEL_SCALE_ARCSEC / 206264.806) ** 2) * 1e3


@dataclass
class SpectrumPoint:
    wavelength_um: float
    flux_mjy: float
    err_mjy: float
    snr: float
    obs_id: str
    detector: int
    t_min: float
    n_pix: int
    flags: int = 0


@dataclass
class SpectrumResult:
    points: list[SpectrumPoint] = field(default_factory=list)
    ra: float = 0.0
    dec: float = 0.0
    message: str = ""
    n_attempted: int = 0
    n_used: int = 0

    def sorted(self) -> list[SpectrumPoint]:
        return sorted(self.points, key=lambda p: p.wavelength_um)

    @property
    def ok(self) -> bool:
        return len(self.points) >= 2


def _aperture_flux(
    image: np.ndarray, variance: np.ndarray | None, flags: np.ndarray | None,
    cx: float, cy: float, r_in: float, r_out: float,
) -> tuple[float, float, int, int]:
    """Aperture sum with a local annulus background, in MJy/sr x pixels.

    Contaminated pixels inside the aperture are excluded and the sum is scaled
    by the inverse of the surviving area fraction (standard aperture correction),
    so a bright star whose core is flagged OVERFLOW still yields a flux estimate
    instead of a hole.
    """
    ny, nx = image.shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    rr = np.hypot(xx - cx, yy - cy)
    good = np.isfinite(image)
    if flags is not None:
        good &= fitsdata.contamination_mask(flags) == False  # noqa: E712
    ap_all = rr <= r_in
    ap = ap_all & good
    an = (rr >= r_in) & (rr <= r_out) & good
    n_all = int(ap_all.sum())
    npix_ap = int(ap.sum())
    if n_all < 1 or npix_ap < 1 or an.sum() < 5:
        return float("nan"), float("nan"), npix_ap, 0
    bkg = float(np.median(image[an]))
    frac = max(npix_ap / n_all, 0.25)
    total = float(np.nansum(image[ap] - bkg)) / frac
    if variance is not None:
        v = np.where(np.isfinite(variance) & (variance > 0), variance, np.nan)
        err = float(np.sqrt(np.nansum(np.where(ap, v, 0.0)))) / frac
    else:
        err = float(np.nanstd(image[an]) * math.sqrt(npix_ap)) / frac
    return total, err, npix_ap, int(an.sum())


def epoch_position(ra: float, dec: float, t_mjd: float,
                   pm: tuple[float, float, float] | None) -> tuple[float, float]:
    """Propagate (ra, dec) from its reference epoch to an observation date.

    pm = (pmRA*cos(dec) mas/yr, pmDec mas/yr, reference_jyear).  Barnard's Star
    moves 10.3 arcsec/yr, i.e. ~45 SPHEREx pixels between J2000 and 2026.
    """
    if not pm:
        return ra, dec
    pmra, pmdec, ref_year = pm
    year = 2000.0 + (t_mjd - 51544.5) / 365.25
    dt = year - ref_year
    cosd = max(abs(np.cos(np.radians(dec))), 1e-3)
    ra2 = ra + (pmra * dt / 1000.0 / 3600.0) / cosd
    dec2 = dec + (pmdec * dt / 1000.0 / 3600.0)
    return ra2 % 360.0, float(np.clip(dec2, -89.999, 89.999))


def _one_epoch_spectrum(epoch: Epoch, ra: float, dec: float, aperture_factor: float,
                        pm: tuple[float, float, float] | None = None) -> SpectrumPoint | None:
    url = epoch.aws_url or epoch.access_url
    if not url:
        return None
    try:
        structure = rangecut.parse_structure(url)
        img_info = structure.get("IMAGE")
        from astropy.wcs import WCS

        wcs = WCS(img_info.header)
        era, edec = epoch_position(ra, dec, epoch.t_min, pm)
        xc, yc = wcs.all_world2pix([[era, edec]], 0)[0]
        nx = int(img_info.header.get("NAXIS1", 2040))
        ny = int(img_info.header.get("NAXIS2", 2040))
        # PSF_FWHM is in arcseconds; SPHEREx's ~6" PSF is about one 6.15" pixel.
        # Apertures follow the PSF (with a floor of 2 px for centroiding) rather
        # than a fixed pixel size.
        psf = float(img_info.header.get("PSF_FWHM", 6.1) or 6.1) / config.PIXEL_SCALE_ARCSEC
        # The PSF is ~1 pixel wide, so a pure PSF-scaled aperture would be smaller
        # than the astrometric scatter; floor it at 3 px and use a 6 px annulus.
        r_in = max(3.0, max(aperture_factor, 1.0) * 3.0 * psf)
        r_out = max(r_in + 3.0, 6.0 * psf)
        half = int(max(10, min(40, r_out * 1.25)))
        x0 = int(max(0, min(xc - half, nx - 2 * half)))
        y0 = int(max(0, min(yc - half, ny - 2 * half)))
        x1, y1 = x0 + 2 * half, y0 + 2 * half
        planes = rangecut.read_planes(url, y0, y1, x0, x1,
                                      ("IMAGE", "FLAGS", "VARIANCE"), structure=structure)
        if "IMAGE" not in planes:
            return None
        image = planes["IMAGE"]
        wl, _bw = fitsdata.wavelength_at(structure, float(xc + 1), float(yc + 1))
        if not np.isfinite(wl):
            return None
        # Refine the centroid on the brightest pixel within +-3 px of the
        # predicted (proper-motion propagated) position.  Without this, a 1 px
        # astrometric scatter plus a 3 px aperture throws away whole epochs.
        cx, cy = float(xc - x0), float(yc - y0)
        ix, iy = int(round(cx)), int(round(cy))
        ys0, ys1 = max(0, iy - 3), min(image.shape[0], iy + 4)
        xs0, xs1 = max(0, ix - 3), min(image.shape[1], ix + 4)
        sub = image[ys0:ys1, xs0:xs1]
        if sub.size and np.any(np.isfinite(sub)):
            ry, rx = np.unravel_index(int(np.nanargmax(sub)), sub.shape)
            cx, cy = float(xs0 + rx), float(ys0 + ry)

        flux, err, npix, _ = _aperture_flux(
            image, planes.get("VARIANCE"), planes.get("FLAGS"),
            cx, cy, r_in=r_in, r_out=r_out,
        )
        if not np.isfinite(flux):
            return None
        # Reject epochs where nothing is actually detected at that position: an
        # unflagged background aperture would otherwise inject pure noise into
        # the spectrum and look like a measurement.
        yy, xx = np.mgrid[0:image.shape[0], 0:image.shape[1]]
        rr = np.hypot(xx - cx, yy - cy)
        core = image[rr <= max(1.5, aperture_factor * psf * 0.6)]
        if core.size == 0 or not np.isfinite(np.nanmax(core)):
            return None
        bkg_level = float(np.nanmedian(image[rr > r_in])) if np.any(rr > r_in) else 0.0
        peak_snr = (float(np.nanmax(core)) - bkg_level) / max(abs(float(err)) / max(npix, 1), 1e-6)
        if peak_snr < 3.0:
            return None
        flux_mjy = flux * MJY_SR_TO_MJY_PER_PIXEL
        err_mjy = err * MJY_SR_TO_MJY_PER_PIXEL if np.isfinite(err) else float("nan")
        snr = float(flux_mjy / err_mjy) if np.isfinite(err_mjy) and err_mjy > 0 else float("nan")
        return SpectrumPoint(
            wavelength_um=float(wl),
            flux_mjy=float(flux_mjy),
            err_mjy=float(err_mjy),
            snr=snr,
            obs_id=epoch.obs_id,
            detector=epoch.detector,
            t_min=epoch.t_min,
            n_pix=npix,
        )
    except Exception:
        return None


def extract_spectrum(
    epochs: list[Epoch],
    ra: float,
    dec: float,
    aperture_factor: float = 1.2,
    max_rows: int = 24,
    pm: tuple[float, float, float] | None = None,
    progress=None,
) -> SpectrumResult:
    """Build a NIR spectrum at (ra, dec) from the epochs that cover it."""
    cand = [e for e in epochs if e.covers(ra, dec)]
    if not cand:
        return SpectrumResult([], ra, dec, message="No epoch footprint covers this position.")

    # Sample epochs evenly in time across detectors to span the wavelength range.
    cand = sorted(cand, key=lambda e: (e.detector, e.t_min))
    by_det: dict[int, list[Epoch]] = {}
    for e in cand:
        by_det.setdefault(e.detector, []).append(e)
    picked: list[Epoch] = []
    per_det = max(1, max_rows // max(1, len(by_det)))
    for det, eps in sorted(by_det.items()):
        idx = np.linspace(0, len(eps) - 1, min(per_det, len(eps))).astype(int)
        picked.extend(eps[i] for i in sorted(set(idx.tolist())))
    picked = picked[: max_rows]

    pts: list[SpectrumPoint] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(_one_epoch_spectrum, e, ra, dec, aperture_factor, pm) for e in picked]
        done = 0
        for fut in as_completed(futs):
            done += 1
            if progress:
                progress(done / max(1, len(futs)))
            try:
                p = fut.result()
            except Exception:
                p = None
            if p is not None and np.isfinite(p.flux_mjy):
                pts.append(p)

    res = SpectrumResult(pts, ra, dec, n_attempted=len(picked), n_used=len(pts))
    if len(pts) < 2:
        res.message = (
            f"Only {len(pts)} usable wavelength sample(s) at this position. "
            "SPHEREx builds spectra over multiple visits; try a target with more epochs."
        )
    return res


# --------------------------------------------------------------- mover search
@dataclass
class Candidate:
    x: float
    y: float
    ra: float
    dec: float
    displacement_px: float
    displacement_arcsec: float
    uncertainty_px: float
    score: float
    n_frames: int
    peak_flux: float
    label: str = ""


def detect_sources(frame, fwhm_px: float | None = None, threshold_sigma: float = 5.0):
    """DAOStarFinder detections on one reprojected frame."""
    from astropy.stats import sigma_clipped_stats
    from photutils.detection import DAOStarFinder

    data = np.asarray(frame.data, dtype=float)
    finite = np.isfinite(data)
    if finite.sum() < 100:
        return None
    _, median, std = sigma_clipped_stats(data[finite], sigma=3.0, maxiters=5)
    if not np.isfinite(std) or std <= 0:
        return None
    fwhm = float(fwhm_px or max(frame.psf_fwhm_px, 1.6))
    finder = DAOStarFinder(fwhm=fwhm, threshold=threshold_sigma * std, exclude_border=True)
    try:
        tbl = finder(np.nan_to_num(data - median, nan=0.0))
    except Exception:
        return None
    if tbl is None or len(tbl) == 0:
        return None
    if "flux" in tbl.colnames:
        tbl.sort("flux")
        tbl.reverse()
    return tbl[:120]


def _col(tbl, *names) -> np.ndarray:
    """Fetch the first available column (photutils renamed some across versions)."""
    for name in names:
        if name in tbl.colnames:
            return np.asarray(tbl[name], dtype=float)
    raise KeyError(f"none of {names} in {tbl.colnames}")


def _upsample_for_detection(frame, factor: int):
    """Zoom a frame so the PSF spans >= ~1.8 pixels before running DAOStarFinder.

    At the native 6.15" pixel scale the SPHEREx PSF is barely one pixel wide, which
    makes centroiding unstable; detection is therefore done on an interpolated
    grid and the coordinates are scaled back.
    """
    from scipy.ndimage import zoom

    data = np.asarray(frame.data, dtype=np.float32)
    if factor <= 1:
        return data
    filled = np.where(np.isfinite(data), data, np.nanmedian(data[np.isfinite(data)]) if np.any(np.isfinite(data)) else 0.0)
    return zoom(filled, factor, order=1)


def detect_movers(seq, max_shift_px: float = 2.5, threshold_sigma: float = 5.0) -> list[Candidate]:
    """Chain detections across epochs and rank by displacement / uncertainty.

    This is deliberately a *difference-blind* second opinion to the differencing
    view: it works even when two epochs have very different depths.
    """
    if len(seq.frames) < 2:
        return []

    pix_scale_seq = seq.pixel_scale_arcsec
    fwhm_seq = max(seq.frames[0].psf_fwhm_px * config.PIXEL_SCALE_ARCSEC / pix_scale_seq, 0.5)
    factor = max(1, int(np.ceil(1.8 / fwhm_seq)))

    class _Zoomed:
        __slots__ = ("data", "psf_fwhm_px")

        def __init__(self, data, psf):
            self.data = data
            self.psf_fwhm_px = psf

    zoomed = [
        _Zoomed(_upsample_for_detection(f, factor), fwhm_seq * factor) for f in seq.frames
    ]
    dets = [detect_sources(f, threshold_sigma=threshold_sigma) for f in zoomed]
    if factor > 1:
        for tbl in dets:
            if tbl is None:
                continue
            for col in ("x_centroid", "y_centroid"):
                if col in tbl.colnames:
                    tbl[col] /= factor
    if not any(d is not None for d in dets):
        return []

    first_idx = next(i for i, d in enumerate(dets) if d is not None)
    first = dets[first_idx]
    tracks = []
    try:
        fx = _col(first, "xcentroid", "x_centroid", "xcent", "x")
        fy = _col(first, "ycentroid", "y_centroid", "ycent", "y")
        ff = _col(first, "flux", "sum", "peak") if any(
            c in first.colnames for c in ("flux", "sum", "peak")) else np.ones_like(fx)
    except KeyError:
        return []
    for i in range(len(fx)):
        tracks.append({
            "x0": float(fx[i]), "y0": float(fy[i]),
            "xn": float(fx[i]), "yn": float(fy[i]),
            "n": 1, "flux": float(ff[i]),
        })

    for i, tbl in enumerate(dets):
        if i == first_idx or tbl is None:
            continue
        try:
            px = _col(tbl, "xcentroid", "x_centroid", "xcent", "x")
            py = _col(tbl, "ycentroid", "y_centroid", "ycent", "y")
        except KeyError:
            continue
        for tr in tracks:
            d = np.hypot(px - tr["xn"], py - tr["yn"])
            if d.size == 0:
                continue
            j = int(np.argmin(d))
            if d[j] <= max_shift_px:
                tr["xn"], tr["yn"] = float(px[j]), float(py[j])
                tr["n"] += 1

    cands: list[Candidate] = []
    pix_scale = pix_scale_seq
    for tr in tracks:
        dx = tr["xn"] - tr["x0"]
        dy = tr["yn"] - tr["y0"]
        disp = float(np.hypot(dx, dy))
        if tr["n"] < 2 and len(seq.frames) > 2:
            continue
        unc = 0.25
        score = disp / unc
        if disp >= 1.0:
            ra, dec = seq.ref_wcs.all_pix2world([[tr["xn"], tr["yn"]]], 0)[0]
            cands.append(Candidate(
                x=tr["xn"], y=tr["yn"], ra=float(ra), dec=float(dec),
                displacement_px=disp, displacement_arcsec=disp * pix_scale,
                uncertainty_px=unc, score=float(score), n_frames=tr["n"],
                peak_flux=tr["flux"],
                label=f"{disp:.1f}px / {disp*pix_scale:.1f}\"",
            ))
    cands.sort(key=lambda c: c.score, reverse=True)
    return cands[:10]


def candidates_to_csv(cands: list[Candidate], seq) -> str:
    lines = [
        "rank,ra_deg,dec_deg,displacement_px,displacement_arcsec,uncertainty_px,score,n_frames,peak_flux_MJysr,target_ra,target_dec,fov_deg,epochs"
    ]
    for i, c in enumerate(cands, 1):
        lines.append(
            f"{i},{c.ra:.6f},{c.dec:.6f},{c.displacement_px:.3f},{c.displacement_arcsec:.3f},"
            f"{c.uncertainty_px:.3f},{c.score:.2f},{c.n_frames},{c.peak_flux:.4f},"
            f"{seq.ra:.6f},{seq.dec:.6f},{seq.fov_deg},{len(seq.frames)}"
        )
    return "\n".join(lines) + "\n"
