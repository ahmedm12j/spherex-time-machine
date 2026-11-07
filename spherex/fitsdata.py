"""Level-2 MEF handling: IMAGE, FLAGS, VARIANCE, ZODI, PSF, WCS-WAVE.

The two scientific rules enforced here:

1. **Flags are honoured.**  Every pixel whose FLAGS value has any bit in
   ``config.CONTAMINATION_BITS`` set is marked unusable and excluded from
   photometry, stacking, difference imaging and mover detection.
2. **Variance is propagated.**  ``VARIANCE`` is in (MJy/sr)^2; it is carried
   through cuts, reprojection and sums so difference images are expressed as
   true signal-to-noise, not arbitrary stretch.

Wavelength comes from the ``WCS-WAVE`` table: SPHEREx uses a linear variable
filter, so a single detector image is *not* a single wavelength.  The 11x11
control grid is bilinearly interpolated to give the true wavelength of every
pixel.
"""
from __future__ import annotations

import hashlib
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from . import config, rangecut
from .archive import Epoch

warnings.filterwarnings("ignore", category=RuntimeWarning)

CUTOUT_DIR = config.CACHE_DIR / "cutouts"
CUTOUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Cutout:
    """One detector image of one epoch, cut to a sky window."""

    epoch: Epoch
    image: np.ndarray
    flags: np.ndarray
    variance: np.ndarray | None
    zodi: np.ndarray | None
    wcs: WCS
    header: fits.Header
    mask: np.ndarray
    wavelength: np.ndarray
    wl_center: float
    psf_fwhm_px: float
    bounds: tuple[int, int, int, int]
    flag_counts: dict[str, int] = field(default_factory=dict)

    @property
    def shape(self) -> tuple[int, int]:
        return self.image.shape

    @property
    def mjd(self) -> float:
        return self.epoch.t_min

    @property
    def good_fraction(self) -> float:
        return float(1.0 - self.mask.mean()) if self.mask.size else 1.0


WAVE_CACHE: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}


def _wave_table(structure: rangecut.FileStructure) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Read the WCS-WAVE extension (tiny bin table, ~20 KB)."""
    url = structure.url
    if url in WAVE_CACHE:
        return WAVE_CACHE[url]
    info = structure.get("WCS-WAVE")
    raw = rangecut._get_range(url, info.data_offset, info.data_offset + info.data_size - 1)
    # Rebuild a one-row FITS table in memory: header + data.
    hdr_bytes = info.raw_header.encode("ascii", "replace")
    padding = b" " * (rangecut.BLOCK - (len(hdr_bytes) % rangecut.BLOCK or rangecut.BLOCK))
    blob = hdr_bytes + (padding if len(hdr_bytes) % rangecut.BLOCK else b"") + raw
    hdu = fits.BinTableHDU.fromstring(blob)
    row = hdu.data[0]
    x = np.asarray(row["X"], dtype=float)
    y = np.asarray(row["Y"], dtype=float)
    vals = np.asarray(row["VALUES"], dtype=float)  # shape (ny, nx, 2)
    WAVE_CACHE[url] = (x, y, vals)
    return x, y, vals


def _bilinear(grid_x: np.ndarray, grid_y: np.ndarray, values: np.ndarray,
              x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Bilinear interpolation of a coarse control grid at pixel positions."""
    gx = np.asarray(grid_x, dtype=float)
    gy = np.asarray(grid_y, dtype=float)
    x = np.clip(np.asarray(x, dtype=float), gx[0], gx[-1])
    y = np.clip(np.asarray(y, dtype=float), gy[0], gy[-1])

    def interp_axis(grid, pos):
        idx = np.clip(np.searchsorted(grid, pos) - 1, 0, len(grid) - 2)
        w = (pos - grid[idx]) / (grid[idx + 1] - grid[idx])
        return idx, w

    ix, wx = interp_axis(gx, x)
    iy, wy = interp_axis(gy, y)
    # values indexed [row=y, col=x]
    v00 = values[iy, ix]
    v01 = values[iy, ix + 1]
    v10 = values[iy + 1, ix]
    v11 = values[iy + 1, ix + 1]
    wx = wx[..., None] if values.ndim == 3 else wx
    wy = wy[..., None] if values.ndim == 3 else wy
    return (v00 * (1 - wx) * (1 - wy) + v01 * wx * (1 - wy)
            + v10 * (1 - wx) * wy + v11 * wx * wy)


def wavelength_at(structure: rangecut.FileStructure, x: float, y: float) -> tuple[float, float]:
    """Return (wavelength_um, bandwidth_um) at a detector pixel (1-based)."""
    try:
        gx, gy, vals = _wave_table(structure)
        res = _bilinear(gx, gy, vals, np.array([[x]]), np.array([[y]]))
        return float(res[0, 0, 0]), float(res[0, 0, 1])
    except Exception:
        return float("nan"), float("nan")


def wavelength_map(
    structure: rangecut.FileStructure, y0: int, y1: int, x0: int, x1: int
) -> np.ndarray:
    """Per-pixel wavelength (um) for a cutout window."""
    gx, gy, vals = _wave_table(structure)
    xs = np.arange(x0, x1) + 1.0
    ys = np.arange(y0, y1) + 1.0
    XX, YY = np.meshgrid(xs, ys)
    wl = _bilinear(gx, gy, vals[..., 0], XX, YY)
    return np.asarray(wl, dtype=np.float32)


def contamination_mask(flags: np.ndarray) -> np.ndarray:
    """Boolean mask of pixels carrying any contamination bit."""
    mask = np.zeros(flags.shape, dtype=bool)
    for name in config.CONTAMINATION_BITS:
        bit = config.FLAG_BITS.get(name)
        if bit is None:
            continue
        mask |= (np.asarray(flags, dtype=np.int64) & (1 << bit)) != 0
    return mask


def flag_summary(flags: np.ndarray) -> dict[str, int]:
    """Count pixels per flag name, for the metadata panel."""
    out: dict[str, int] = {}
    f = np.asarray(flags, dtype=np.int64)
    for name, bit in config.FLAG_BITS.items():
        n = int(np.count_nonzero(f & (1 << bit)))
        if n:
            out[name] = n
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _cache_path(epoch: Epoch, bounds: tuple[int, int, int, int], tag: str) -> Path:
    key = f"{epoch.obs_id}_D{epoch.detector}_{bounds[0]}_{bounds[1]}_{bounds[2]}_{bounds[3]}_{tag}"
    digest = hashlib.sha1(key.encode()).hexdigest()[:16]
    return CUTOUT_DIR / f"{epoch.obs_id}_D{epoch.detector}_{digest}.npz"


def _read_cached(path: Path, epoch: Epoch) -> Cutout | None:
    try:
        blob = np.load(path, allow_pickle=False)
        raw_hdr = blob["header"]
        text = raw_hdr.item() if getattr(raw_hdr, "ndim", 0) == 0 else str(raw_hdr)
        header = fits.Header.fromstring(str(text))
        wcs = WCS(header)
        return Cutout(
            epoch=epoch,
            image=blob["image"].astype(np.float32),
            flags=blob["flags"].astype(np.int32),
            variance=blob["variance"].astype(np.float32) if "variance" in blob else None,
            zodi=blob["zodi"].astype(np.float32) if "zodi" in blob else None,
            wcs=wcs,
            header=header,
            mask=blob["mask"].astype(bool),
            wavelength=blob["wavelength"].astype(np.float32),
            wl_center=float(blob["wl_center"]),
            psf_fwhm_px=float(blob["psf_fwhm_px"]),
            bounds=tuple(int(v) for v in blob["bounds"]),
            flag_counts=flag_summary(blob["flags"]),
        )
    except Exception:
        return None


def _write_cache(path: Path, cut: Cutout) -> None:
    try:
        np.savez(
            path,
            image=cut.image,
            flags=cut.flags,
            variance=cut.variance if cut.variance is not None else np.zeros((0, 0), np.float32),
            zodi=cut.zodi if cut.zodi is not None else np.zeros((0, 0), np.float32),
            header=np.array(str(cut.header), dtype="U"),
            mask=cut.mask,
            wavelength=cut.wavelength,
            wl_center=np.float64(cut.wl_center),
            psf_fwhm_px=np.float64(cut.psf_fwhm_px),
            bounds=np.array(cut.bounds, dtype=np.int64),
        )
    except Exception:
        pass


def load_cutout(
    epoch: Epoch,
    ra: float,
    dec: float,
    fov_deg: float,
    want_zodi: bool = False,
    use_disk_cache: bool = True,
) -> Cutout | None:
    """Read one epoch's cutout, honouring flags/variance and the wavelength map.

    Returns ``None`` (never raises) when the file is corrupt or unreachable so
    the caller can skip the epoch and continue.
    """
    url = epoch.aws_url or epoch.access_url
    if not url:
        return None
    tag = "z" if want_zodi else "n"
    try:
        structure = rangecut.parse_structure(url)
    except Exception:
        return None

    img_info = structure.get("IMAGE")
    wcs = WCS(img_info.header)
    ny_total = int(img_info.header.get("NAXIS2", 2040))
    nx_total = int(img_info.header.get("NAXIS1", 2040))

    try:
        xc, yc = wcs.all_world2pix([[ra, dec]], 0)[0]
    except Exception:
        return None

    pix = float(epoch.pixscale or config.PIXEL_SCALE_ARCSEC)
    n = int(round(fov_deg * 3600.0 / pix))
    n = max(24, min(n, config.MAX_CUTOUT_PX))

    x0 = int(round(xc - n / 2))
    y0 = int(round(yc - n / 2))
    x0 = max(0, min(x0, nx_total - n))
    y0 = max(0, min(y0, ny_total - n))
    x1, y1 = x0 + n, y0 + n
    bounds = (y0, y1, x0, x1)

    cpath = _cache_path(epoch, bounds, tag)
    if use_disk_cache and cpath.exists():
        cached = _read_cached(cpath, epoch)
        if cached is not None:
            return cached

    planes = ("IMAGE", "FLAGS", "VARIANCE", "ZODI") if want_zodi else ("IMAGE", "FLAGS", "VARIANCE")
    try:
        data = rangecut.read_planes(url, y0, y1, x0, x1, planes, structure=structure)
    except Exception:
        return None
    if "IMAGE" not in data:
        return None

    header = rangecut.slice_header(img_info, y0, y1, x0, x1)
    try:
        sub_wcs = WCS(header)
    except Exception:
        sub_wcs = wcs

    flags = data.get("FLAGS")
    if flags is None:
        flags = np.zeros_like(data["IMAGE"], dtype=np.int32)
    mask = contamination_mask(flags)

    try:
        wl_map = wavelength_map(structure, y0, y1, x0, x1)
        wl_center, _ = wavelength_at(structure, (x0 + x1) / 2.0, (y0 + y1) / 2.0)
    except Exception:
        info = config.DETECTOR_INFO.get(epoch.detector, {})
        wl_center = float(info.get("wl_center", float("nan")))
        wl_map = np.full(data["IMAGE"].shape, wl_center, dtype=np.float32)

    psf_fwhm = float(img_info.header.get("PSF_FWHM", 6.1) or 6.1)

    cut = Cutout(
        epoch=epoch,
        image=data["IMAGE"].astype(np.float32),
        flags=flags.astype(np.int32),
        variance=data.get("VARIANCE", None),
        zodi=data.get("ZODI", None),
        wcs=sub_wcs,
        header=header,
        mask=mask,
        wavelength=wl_map,
        wl_center=wl_center,
        psf_fwhm_px=psf_fwhm,
        bounds=bounds,
        flag_counts=flag_summary(flags),
    )
    if use_disk_cache:
        _write_cache(cpath, cut)
    return cut


def load_psf_plane(epoch: Epoch, wavelength_um: float) -> np.ndarray | None:
    """Nearest-wavelength PSF kernel from the PSF extension (121 planes)."""
    url = epoch.aws_url or epoch.access_url
    try:
        structure = rangecut.parse_structure(url)
        info = structure.get("PSF")
        nplanes = int(info.header.get("NAXIS3", 121))
        # The PSF extension's 3rd axis is ordered by wavelength.
        idx = int(np.clip(round((wavelength_um - 0.75) / (5.0 - 0.75) * (nplanes - 1)), 0, nplanes - 1))
        return rangecut.read_rows(url, "PSF", 0, 101, 0, 101, plane=idx, structure=structure)
    except Exception:
        return None


def psf_gaussian(fwhm_px: float, size: int = 25) -> np.ndarray:
    """Normalised 2-D Gaussian kernel used for PSF-matched subtraction."""
    sigma = max(fwhm_px, 0.8) / 2.3548
    ax = np.arange(size) - (size - 1) / 2.0
    xx, yy = np.meshgrid(ax, ax)
    k = np.exp(-(xx**2 + yy**2) / (2 * sigma**2))
    return (k / k.sum()).astype(np.float32)
