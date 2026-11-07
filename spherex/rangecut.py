"""Read small cutouts out of 71 MB remote FITS files using HTTP range requests.

A full SPHEREx Level-2 file is ~71 MB (2040x2040 float32 x 4 planes + PSF).
Downloading one per epoch is impossible inside the 10 s cold-load budget, so
this module parses the FITS header structure over the network, then pulls only
the byte ranges covering the requested rows of each plane.  A 400x400 cutout
costs ~1.5 MB per plane instead of 16 MB.

Everything here is read-only and anonymous: AWS Open Data and IRSA IBE both
honour ``Range`` headers without credentials.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field

import numpy as np
import requests
from astropy.io import fits

from . import config

BLOCK = 2880
CARD = 80
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "spherex-time-machine/1.0 (public research tool)"})
_STRUCT_CACHE: dict[str, "FileStructure"] = {}
_SIZES: dict[str, int] = {}
_LOCK = threading.Lock()


def file_size(url: str) -> int:
    """Remote object size in bytes (-1 when unknown)."""
    if url in _SIZES:
        return _SIZES[url]
    size = -1
    try:
        resp = _SESSION.head(url, timeout=config.HTTP_TIMEOUT)
        if resp.ok and resp.headers.get("Content-Length"):
            size = int(resp.headers["Content-Length"])
    except Exception:
        size = -1
    _SIZES[url] = size
    return size


class RangeError(RuntimeError):
    """Raised when a remote read fails after retries."""


@dataclass
class HDUInfo:
    index: int
    name: str
    header: fits.Header
    raw_header: str
    data_offset: int
    data_size: int
    ndim: int
    shape: tuple[int, ...]


@dataclass
class FileStructure:
    url: str
    hdus: list[HDUInfo] = field(default_factory=list)
    parsed_at: float = 0.0

    def get(self, name: str) -> HDUInfo:
        want = name.upper()
        for h in self.hdus:
            if h.name.upper() == want:
                return h
        raise KeyError(f"{name} extension not found in {self.url}")

    @property
    def names(self) -> list[str]:
        return [h.name for h in self.hdus]


def _get_range(url: str, start: int, end: int, retries: int | None = None) -> bytes:
    """Fetch an inclusive byte range with retry + exponential backoff."""
    retries = config.HTTP_RETRIES if retries is None else retries
    total = file_size(url)
    if total > 0:
        if start >= total:
            raise RangeError("EOF")
        end = min(end, total - 1)
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = _SESSION.get(
                url,
                headers={"Range": f"bytes={start}-{end}"},
                timeout=config.HTTP_TIMEOUT,
            )
            if resp.status_code in (200, 206):
                if resp.status_code == 200 and len(resp.content) > (end - start + 1) * 1.5:
                    # Server ignored Range and sent the whole file: slice locally.
                    return resp.content[start : end + 1]
                return resp.content
            if resp.status_code == 416:
                raise RangeError("EOF")
            if resp.status_code in (403, 404):
                raise RangeError(f"HTTP {resp.status_code} for {url}")
            last = RangeError(f"HTTP {resp.status_code}")
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(0.6 * (2**attempt))
    raise RangeError(f"Range read failed after {retries + 1} attempts: {last}")


def _header_bytes(url: str, offset: int) -> tuple[str, int]:
    """Return (header text, byte length) for the header starting at ``offset``."""
    chunk = 32 * BLOCK
    text_parts: list[str] = []
    length = 0
    while length < 200 * BLOCK:
        buf = _get_range(url, offset, offset + chunk - 1)
        text_parts.append(buf.decode("ascii", "replace"))
        text = "".join(text_parts)
        end = text.find("END" + " " * 77)
        if end >= 0:
            stop = end + CARD
            padded = math.ceil(stop / BLOCK) * BLOCK
            need = padded - len(text)
            if need > 0:
                more = _get_range(url, offset + len(text), offset + padded - 1)
                text += more.decode("ascii", "replace")
            return text[:padded], padded
        length += chunk
        chunk *= 2
    raise RangeError(f"Could not find END card in header at {offset} of {url}")


def _data_size(hdr: fits.Header) -> int:
    naxis = int(hdr.get("NAXIS", 0) or 0)
    if naxis == 0:
        return 0
    nelem = 1
    for i in range(1, naxis + 1):
        nelem *= int(hdr.get(f"NAXIS{i}", 0) or 0)
    xt = str(hdr.get("XTENSION", "")).strip().upper()
    if xt == "BINTABLE":
        rows = int(hdr.get("NAXIS2", 0) or 0)
        rowbytes = int(hdr.get("NAXIS1", 0) or 0)
        return rows * rowbytes + int(hdr.get("PCOUNT", 0) or 0)
    bitpix = abs(int(hdr.get("BITPIX", 8) or 8))
    gcount = int(hdr.get("GCOUNT", 1) or 1)
    pcount = int(hdr.get("PCOUNT", 0) or 0)
    return nelem * bitpix // 8 * gcount + pcount


def parse_structure(url: str, use_cache: bool = True) -> FileStructure:
    """Walk the HDU chain over HTTP and record data offsets."""
    with _LOCK:
        if use_cache and url in _STRUCT_CACHE:
            return _STRUCT_CACHE[url]

    offset = 0
    hdus: list[HDUInfo] = []
    total = file_size(url)
    for index in range(16):
        if total > 0 and offset >= total:
            break
        text, hlen = _header_bytes(url, offset)
        hdr = fits.Header.fromstring(text)
        data_offset = offset + hlen
        size = _data_size(hdr)
        naxis = int(hdr.get("NAXIS", 0) or 0)
        shape = tuple(int(hdr.get(f"NAXIS{i}", 0) or 0) for i in range(naxis, 0, -1))
        name = str(hdr.get("EXTNAME", "PRIMARY")).strip() or "PRIMARY"
        hdus.append(HDUInfo(index, name, hdr, text, data_offset, size, naxis, shape))
        if size == 0 and naxis == 0 and index > 0:
            break
        offset = data_offset + math.ceil(size / BLOCK) * BLOCK
    struct = FileStructure(url=url, hdus=hdus, parsed_at=time.time())
    with _LOCK:
        _STRUCT_CACHE[url] = struct
    return struct


def read_rows(
    url: str,
    extname: str,
    y0: int,
    y1: int,
    x0: int = 0,
    x1: int | None = None,
    plane: int | None = None,
    structure: FileStructure | None = None,
) -> np.ndarray:
    """Read rows ``y0..y1`` (exclusive) of a 2-D plane, then slice columns.

    One HTTP request covers the full-width row slab; column slicing is local.
    """
    fs = structure or parse_structure(url)
    info = fs.get(extname)
    hdr = info.header
    ny_total, nx_total = int(hdr.get("NAXIS2", 0)), int(hdr.get("NAXIS1", 0))
    if info.ndim == 3 and plane is not None:
        plane_stride = int(ny_total) * int(nx_total)
        base = info.data_offset + plane * plane_stride * 4
    elif info.ndim == 2:
        base = info.data_offset
    else:
        raise RangeError(f"{extname} has unsupported dimensionality {info.ndim}")
    y0 = max(0, y0)
    y1 = min(ny_total, y1)
    x1 = nx_total if x1 is None else min(nx_total, x1)
    x0 = max(0, x0)
    if y1 <= y0 or x1 <= x0:
        raise RangeError(f"Empty cutout for {extname}: rows {y0}:{y1} cols {x0}:{x1}")

    nrows = y1 - y0
    start = base + y0 * nx_total * 4
    end = start + nrows * nx_total * 4 - 1
    raw = _get_range(url, start, end)
    bitpix = int(hdr.get("BITPIX", -32))
    dtype = {-32: ">f4", -64: ">f8", 32: ">i4", 16: ">i2"}.get(bitpix, ">f4")
    arr = np.frombuffer(raw, dtype=np.dtype(dtype), count=nrows * nx_total)
    arr = arr.reshape(nrows, nx_total)[:, x0:x1]
    return arr.astype(np.float32) if bitpix < 0 else arr.astype(np.int32)


def read_image(
    url: str,
    extname: str,
    y0: int,
    y1: int,
    x0: int,
    x1: int,
    structure: FileStructure | None = None,
    plane: int | None = None,
) -> np.ndarray:
    return read_rows(url, extname, y0, y1, x0, x1, plane=plane, structure=structure)


def read_planes(
    url: str,
    y0: int,
    y1: int,
    x0: int,
    x1: int,
    planes: tuple[str, ...] = ("IMAGE", "FLAGS", "VARIANCE"),
    structure: FileStructure | None = None,
    max_workers: int = 4,
) -> dict[str, np.ndarray]:
    """Fetch several planes concurrently (independent HTTP range requests)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    fs = structure or parse_structure(url)
    out: dict[str, np.ndarray] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(read_image, url, name, y0, y1, x0, x1, fs): name
            for name in planes
            if name in fs.names
        }
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                out[name] = fut.result()
            except Exception:  # noqa: BLE001 - a missing plane must not kill the epoch
                continue
    return out


def slice_header(info: HDUInfo, y0: int, y1: int, x0: int, x1: int) -> fits.Header:
    """Build a header for a sub-image, adjusting NAXIS/CRPIX and SIP ref point."""
    hdr = info.header.copy()
    hdr["NAXIS1"] = x1 - x0
    hdr["NAXIS2"] = y1 - y0
    hdr["CRPIX1"] = float(hdr.get("CRPIX1", 1.0)) - x0
    hdr["CRPIX2"] = float(hdr.get("CRPIX2", 1.0)) - y0
    for key in ("CRPIX1A", "CRPIX2A", "CRPIX1W", "CRPIX2W"):
        if key in hdr:
            pass  # alternate WCS keep absolute pixel reference
    return hdr


def health_check(url: str) -> tuple[bool, float, str]:
    """Time a 1-byte range request; returns (ok, seconds, detail)."""
    t0 = time.time()
    try:
        _get_range(url, 0, 2879)
        return True, time.time() - t0, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, time.time() - t0, str(exc)
