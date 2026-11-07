"""RA/Dec parsing that never raises: decimal degrees and sexagesimal forms.

Acceptance is deliberately generous so a high-school student can type
``17 57 48.5 +04 41 36``, ``17h57m48.5s +04d41m36s`` or ``269.452 4.693``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord


class CoordError(ValueError):
    """Raised with a human-readable message for inline form validation."""


@dataclass(frozen=True)
class ParsedCoord:
    ra_deg: float
    dec_deg: float
    ra_hms: str
    dec_dms: str
    coord: SkyCoord

    @property
    def label(self) -> str:
        return f"{self.ra_hms} {self.dec_dms}"


_SEXA_SPLIT = re.compile(r"[^0-9+\-.]+")


def _clean(token: str) -> str:
    return token.strip().replace("\u2212", "-").replace("\u2013", "-")


def parse_ra(text: str) -> float:
    """Return RA in degrees from decimal degrees or sexagesimal hours."""
    raw = _clean(str(text))
    if not raw:
        raise CoordError("Right Ascension is empty.")
    # h/m/s or d/m/s separated by any non-numeric characters
    parts = [p for p in _SEXA_SPLIT.split(raw) if p not in ("",)]
    try:
        vals = [float(p) for p in parts]
    except ValueError as exc:
        raise CoordError(
            f"Could not read RA '{text}'. Try 269.452 or 17h57m48.5s."
        ) from exc

    is_hours = bool(re.search(r"[hH:]", raw)) or len(vals) == 3
    if len(vals) == 1:
        deg = vals[0]
        if not -0.001 <= deg <= 360.0:
            raise CoordError("RA in decimal degrees must be between 0 and 360.")
        return deg % 360.0
    if len(vals) in (2, 3):
        hh = vals[0]
        mm = vals[1] if len(vals) > 1 else 0.0
        ss = vals[2] if len(vals) > 2 else 0.0
        if not (0 <= hh < 24 and 0 <= mm < 60 and 0 <= ss < 60):
            raise CoordError(f"RA '{text}' is out of range (0-24h, 0-60m, 0-60s).")
        value = hh + mm / 60.0 + ss / 3600.0
        deg = value * 15.0 if is_hours else value
        return deg % 360.0
    raise CoordError(f"Could not read RA '{text}'.")


def parse_dec(text: str) -> float:
    """Return Dec in degrees from decimal degrees or sexagesimal degrees."""
    raw = _clean(str(text))
    if not raw:
        raise CoordError("Declination is empty.")
    sign = -1.0 if raw.lstrip().startswith("-") else 1.0
    parts = [p for p in _SEXA_SPLIT.split(raw) if p not in ("",)]
    try:
        vals = [abs(float(p)) for p in parts]
    except ValueError as exc:
        raise CoordError(
            f"Could not read Dec '{text}'. Try +4.693 or +04d41m36s."
        ) from exc

    if len(vals) == 1:
        deg = sign * vals[0]
        if not -90.0 <= deg <= 90.0:
            raise CoordError("Declination must be between -90 and +90 degrees.")
        return deg
    if len(vals) in (2, 3):
        dd = vals[0]
        mm = vals[1] if len(vals) > 1 else 0.0
        ss = vals[2] if len(vals) > 2 else 0.0
        if not (0 <= dd <= 90 and 0 <= mm < 60 and 0 <= ss < 60):
            raise CoordError(f"Dec '{text}' is out of range (0-90d, 0-60m, 0-60s).")
        deg = sign * (dd + mm / 60.0 + ss / 3600.0)
        if not -90.0 <= deg <= 90.0:
            raise CoordError("Declination must be between -90 and +90 degrees.")
        return deg
    raise CoordError(f"Could not read Dec '{text}'.")


def parse_pair(ra_text: str, dec_text: str) -> ParsedCoord:
    """Parse an RA/Dec pair, raising CoordError with a friendly message."""
    ra = parse_ra(ra_text)
    dec = parse_dec(dec_text)
    coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")
    return ParsedCoord(
        ra_deg=float(ra),
        dec_deg=float(dec),
        ra_hms=coord.ra.to_string(unit=u.hourangle, sep=":", precision=2, pad=True),
        dec_dms=coord.dec.to_string(unit=u.deg, sep=":", precision=1, alwayssign=True, pad=True),
        coord=coord,
    )


def parse_any(text: str) -> ParsedCoord:
    """Parse a single string such as '17h57m48.5s +04d41m36s' or '269.45 4.69'."""
    raw = _clean(text).replace(",", " ").strip()
    if not raw:
        raise CoordError("Please enter coordinates.")
    # split on whitespace if the declination carries an explicit sign
    tokens = raw.split()
    if len(tokens) >= 2:
        for i in range(1, len(tokens)):
            if tokens[i].startswith(("+", "-")):
                return parse_pair(" ".join(tokens[:i]), " ".join(tokens[i:]))
        if len(tokens) == 2:
            return parse_pair(tokens[0], tokens[1])
    # '17:57:48.5 +04:41:36' style gets split by the sign inside one token
    m = re.match(r"^(.+?)\s*([+-]\d.*)$", raw)
    if m:
        return parse_pair(m.group(1), m.group(2))
    raise CoordError("Could not find both RA and Dec.")


def format_ra(deg: float) -> str:
    return SkyCoord(ra=deg * u.deg, dec=0 * u.deg).ra.to_string(
        unit=u.hourangle, sep=":", precision=2, pad=True
    )


def format_dec(deg: float) -> str:
    from astropy.coordinates import Angle

    return Angle(deg, unit=u.deg).to_string(
        unit=u.deg, sep=":", precision=1, alwayssign=True, pad=True
    )


def angular_sep_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    c1 = SkyCoord(ra1 * u.deg, dec1 * u.deg)
    c2 = SkyCoord(ra2 * u.deg, dec2 * u.deg)
    return float(c1.separation(c2).arcsec)


def wrap_angle(deg: np.ndarray | float) -> np.ndarray:
    return (np.asarray(deg) + 180.0) % 360.0 - 180.0
