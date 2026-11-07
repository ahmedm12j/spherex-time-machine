"""Streamlit UI for the SPHEREx Time Machine.

Three feature tiers live here:
  Tier 1  blink viewer, RA/Dec + FOV, metadata panel
  Tier 2  difference mode, mover ranking, shareable URLs, Gaia/WISE overlays,
          on-the-fly spectra, science-notes modal
  Tier 3  Discovery Mode dashboard with CSV export, precomputed target spectra,
          guided tour, iframe embed documentation
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from . import archive, blink, catalogs, config, coords, fitsdata, photometry, pipeline

DEMO_DIR = config.ROOT / "data" / "demo"
SPECTRA_DIR = config.ROOT / "data" / "spectra"
TARGETS = config.load_targets()

CSS = """
<style>
  .block-container{padding-top:1.4rem;padding-bottom:2rem;max-width:1500px;}
  html,body,[class*="css"]{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
  .stm-title{font-size:clamp(1.5rem,3.4vw,2.35rem);font-weight:800;letter-spacing:-.02em;
             color:#f4f7fd;margin:0 0 .15rem 0;line-height:1.12;}
  .stm-sub{color:#a9b8d0;font-size:.94rem;margin:0 0 .9rem 0;}
  .stm-pill{display:inline-block;font-size:.7rem;font-weight:700;letter-spacing:.09em;
            text-transform:uppercase;padding:.18rem .55rem;border-radius:999px;
            background:#12233b;color:#7ee0ff;border:1px solid #23507a;margin-right:.4rem;}
  .stm-card{background:#0e1420;border:1px solid #26314a;border-radius:12px;padding:.85rem 1rem;
            color:#e9eef7;font-size:.9rem;line-height:1.5;margin-bottom:.7rem;}
  .stm-warn{background:#241d0c;border:1px solid #6b5313;color:#ffdf9b;}
  .stm-err{background:#2a1216;border:1px solid #6d2630;color:#ffc2cb;}
  .stm-ok{background:#0d2019;border:1px solid #1f5c45;color:#a9f0cf;}
  .stm-strip{display:flex;gap:5px;margin:.15rem 0 .55rem 0;}
  .stm-strip div{flex:1;text-align:center;font-size:.63rem;font-weight:700;padding:.34rem .1rem;
                 border-radius:6px;color:#04101d;}
  .stm-strip div.on{outline:2px solid #ffffff;outline-offset:1px;}
  .stm-kpi{font-variant-numeric:tabular-nums;}
  .stm-foot{color:#8797b3;font-size:.76rem;line-height:1.6;border-top:1px solid #1d2537;
            margin-top:1.4rem;padding-top:.8rem;}
  a{color:#8fd8ff;}
  @media (max-width:740px){
    .block-container{padding-left:.7rem;padding-right:.7rem;}
    .stm-title{font-size:1.35rem;}
  }
</style>
"""

WAVELENGTH_COLORS = {
    1: "#9ec9ff", 2: "#7ee0ff", 3: "#8ff0c0",
    4: "#ffe08a", 5: "#ffab6b", 6: "#ff8080",
}


# --------------------------------------------------------------------- caching
def _qp(key: str, default: str = "") -> str:
    try:
        val = st.query_params.get(key)
        return default if val is None else str(val)
    except Exception:
        return default


@st.cache_data(ttl=3600, show_spinner=False)
def cached_epochs(ra: float, dec: float, fov: float) -> archive.QueryResult:
    return archive.get_epochs(ra, dec, fov)


@st.cache_data(max_entries=6, show_spinner=False)
def cached_sequence(ra: float, dec: float, fov: float, mode: str,
                    detector: int, max_frames: int) -> pipeline.Sequence:
    res = archive.get_epochs(ra, dec, fov)
    return pipeline.build_sequence(
        res.epochs, ra, dec, fov, mode=mode,
        detector=detector if detector > 0 else None, max_frames=max_frames,
    )


@st.cache_data(max_entries=10, show_spinner=False)
def cached_pngs(ra: float, dec: float, fov: float, mode: str, detector: int,
                max_frames: int, cmap: str) -> list[str]:
    seq = cached_sequence(ra, dec, fov, mode, detector, max_frames)
    return pipeline.encode_frames(seq, cmap=cmap)


@st.cache_data(ttl=86400, show_spinner=False, max_entries=32)
def cached_spectrum(ra: float, dec: float, fov: float, aperture: float,
                    max_rows: int) -> photometry.SpectrumResult:
    res = archive.get_epochs(ra, dec, fov)
    pm = catalogs.nearest_proper_motion(ra, dec)
    return photometry.extract_spectrum(res.epochs, ra, dec, aperture_factor=aperture,
                                       max_rows=max_rows, pm=pm)


@st.cache_data(ttl=86400, show_spinner=False, max_entries=16)
def cached_overlay(kind: str, ra: float, dec: float, radius: float) -> catalogs.Overlay:
    if kind == "gaia":
        return catalogs.gaia_dr3(ra, dec, radius)
    return catalogs.allwise(ra, dec, radius)


# ---------------------------------------------------------------------- widgets
def _band_strip(active: int) -> str:
    cells = []
    for det, info in config.DETECTOR_INFO.items():
        lo, hi = info["wl_range"]
        on = " on" if det == active else ""
        cells.append(
            f'<div class="{on.strip()}" style="background:{WAVELENGTH_COLORS[det]}">'
            f'D{det}<br>{lo:.2f}–{hi:.2f}µm<br><span style="font-weight:500">17 bands</span></div>'
        )
    return '<div class="stm-strip">' + "".join(cells) + "</div>"


def _card(kind: str, title: str, body: str) -> str:
    cls = {"warn": "stm-card stm-warn", "err": "stm-card stm-err",
           "ok": "stm-card stm-ok"}.get(kind, "stm-card")
    return f'<div class="{cls}"><strong>{title}</strong><br>{body}</div>'


def _image_grid(png_b64: str, caption: str) -> None:
    import base64
    import io

    from PIL import Image

    raw = base64.b64decode(png_b64)
    st.image(Image.open(io.BytesIO(raw)), caption=caption, use_container_width=True)


# ------------------------------------------------------------------ state setup
def _bootstrap_state() -> None:
    if st.session_state.get("_boot"):
        return
    st.session_state["_boot"] = True
    ra_q = _qp("ra")
    dec_q = _qp("dec")
    if ra_q and dec_q:
        st.session_state["ra_in"] = ra_q
        st.session_state["dec_in"] = dec_q
    else:
        st.session_state["ra_in"] = str(config.DEFAULT_TARGET["ra_deg"])
        st.session_state["dec_in"] = str(config.DEFAULT_TARGET["dec_deg"])
    st.session_state["target_pick"] = _qp("target", "Barnard's Star")
    try:
        st.session_state["fov_in"] = float(_qp("fov", str(config.FOV_DEFAULT_DEG)))
    except ValueError:
        st.session_state["fov_in"] = config.FOV_DEFAULT_DEG
    st.session_state["mode_in"] = _qp("mode", "stack")
    try:
        st.session_state["det_in"] = int(_qp("band", "5"))
    except ValueError:
        st.session_state["det_in"] = 5
    st.session_state["cmap_in"] = _qp("cmap", "viridis")
    ov = _qp("overlay", "")
    st.session_state["overlay_in"] = [v for v in ov.split(",") if v] if ov else []


def _target_by_name(name: str) -> dict | None:
    for t in TARGETS:
        if t["name"] == name:
            return t
    return None


def _on_target_change() -> None:
    t = _target_by_name(st.session_state.get("target_pick", ""))
    if t:
        st.session_state["ra_in"] = t["ra_text"]
        st.session_state["dec_in"] = t["dec_text"]


# ------------------------------------------------------------------- data access
def _share_url(ra: float, dec: float, fov: float, mode: str, det: int,
               cmap: str, overlays: list[str]) -> str:
    parts = [f"ra={ra:.5f}", f"dec={dec:.5f}", f"fov={fov:.3f}", f"mode={mode}"]
    if mode == "single":
        parts.append(f"band={det}")
    parts.append(f"cmap={cmap}")
    if overlays:
        parts.append("overlay=" + ",".join(overlays))
    return "?" + "&".join(parts)


def _coverage_card(ra: float, dec: float) -> None:
    link = archive.coverage_deeplink(ra, dec)
    st.markdown(_card(
        "warn", "Coverage pending for this position",
        "No SPHEREx Level-2 epoch covers this exact sky patch yet. The survey is still "
        "being taken, so coverage grows every week. You can inspect the archive directly:"
    ), unsafe_allow_html=True)
    st.link_button("Open this position in IRSA", link, use_container_width=False)
    st.caption(f"Saved query: RA {ra:.5f}°, Dec {dec:.5f}°")


def _demo_frame(ra: float, dec: float) -> bool:
    """Render one real bundled cutout when the network has nothing to give."""
    files = sorted(DEMO_DIR.glob("*.npz"))
    if not files:
        return False
    try:
        blob = np.load(files[0], allow_pickle=False)
        img = blob["image"]
        from astropy.io import fits

        hdr = fits.Header.fromstring(str(blob["header"].item()))
        lo, hi = np.nanpercentile(img, [2, 99.5])
        fig = go.Figure(go.Heatmap(z=img, colorscale="Viridis", zmin=lo, zmax=hi,
                                   colorbar=dict(title="MJy/sr", tickfont=dict(color="#dfe7f5")),
                                   hoverinfo="skip"))
        fig.update_layout(
            height=430, margin=dict(l=8, r=8, t=30, b=8),
            paper_bgcolor="#0b0e14", plot_bgcolor="#0b0e14",
            title=dict(text="Bundled real SPHEREx frame (offline demo)", font=dict(size=13, color="#a9b8d0")),
            xaxis=dict(visible=False), yaxis=dict(visible=False, autorange="reversed"),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(_card(
            "ok", "Offline demo frame",
            f"This is a real SPHEREx Level-2 cutout bundled with the app "
            f"(detector D{int(blob['detector'])} if present, {hdr.get('OBSID','')}). "
            "It is one epoch only, so it cannot blink — it proves the rendering path works "
            "while the archive is unreachable."
        ), unsafe_allow_html=True)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------- main render
def render() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    _bootstrap_state()

    # ---------------------------------------------------------------- sidebar
    with st.sidebar:
        st.markdown("### 🔭 SPHEREx Time Machine")
        st.caption("Blink the infrared sky · real IRSA data")
        names = ["Custom coordinates"] + [t["name"] for t in TARGETS]
        pick = st.selectbox(
            "Test target", names,
            index=names.index(st.session_state["target_pick"])
            if st.session_state.get("target_pick") in names else 0,
            key="target_pick", on_change=_on_target_change,
        )
        target = _target_by_name(pick)
        if target:
            st.caption(f"_{target['note']}_")

        col_a, col_b = st.columns(2)
        with col_a:
            ra_text = st.text_input("Right Ascension", key="ra_in",
                                    help="Decimal degrees or sexagesimal. e.g. 17h57m48.5s / 269.452")
        with col_b:
            dec_text = st.text_input("Declination", key="dec_in",
                                     help="Decimal degrees or sexagesimal. e.g. +04d41m36s / 4.693")

        fov = st.slider("Field of view (degrees)", config.FOV_MIN_DEG, config.FOV_MAX_DEG,
                        key="fov_in", step=0.05,
                        help="Larger FOV tracks faster movers; smaller FOV downloads less data and loads faster.")

        st.markdown("**Band mode**")
        mode_label = st.radio(
            "Band mode", ["All bands stacked", "Single detector"],
            index=0 if st.session_state.get("mode_in", "stack") == "stack" else 1,
            label_visibility="collapsed", key="mode_label",
        )
        mode = "stack" if mode_label == "All bands stacked" else "single"
        st.markdown(_band_strip(st.session_state.get("det_in", 5)), unsafe_allow_html=True)
        detector = 0
        if mode == "single":
            detector = st.select_slider(
                "Detector array", options=list(config.DETECTOR_INFO.keys()),
                key="det_in",
                format_func=lambda d: (f"D{d} · {config.DETECTOR_INFO[d]['wl_center']:.2f} µm"),
            )
        else:
            st.caption("Coadding every detector that covers this position (inverse-variance weighted).")

        with st.expander("Appearance", expanded=False):
            cmap = st.selectbox("Colormap", ["viridis", "magma", "inferno", "gray"],
                                key="cmap_in")
            fps = st.slider("Blink rate (fps)", 4, 20, 12)
            max_frames = st.slider("Epochs to load", 4, 16, 10,
                                   help="More epochs = better movie, more archive reads.")

        overlays = st.multiselect("Catalogue overlays", ["Gaia DR3", "AllWISE"],
                                  key="overlay_in")

        st.divider()
        st.markdown("**Share this view**")
        if st.button("Build shareable URL", use_container_width=True):
            try:
                parsed_tmp = coords.parse_pair(ra_text, dec_text)
                st.session_state["share_url"] = _share_url(
                    parsed_tmp.ra_deg, parsed_tmp.dec_deg, fov, mode, detector, cmap, overlays)
            except coords.CoordError:
                st.session_state["share_url"] = ""
        if st.session_state.get("share_url"):
            st.code(st.session_state["share_url"], language="text")
        st.caption("Append these parameters to the app URL to restore the exact view.")

        st.divider()
        if st.button("▶ 20-second guided tour", use_container_width=True):
            st.session_state["tour_step"] = 0
            st.session_state["target_pick"] = "Barnard's Star"
            _on_target_change()
            st.rerun()

    # ------------------------------------------------------------ parse coords
    try:
        parsed = coords.parse_pair(ra_text, dec_text)
    except coords.CoordError as exc:
        st.markdown(CSS, unsafe_allow_html=True)
        st.markdown(_card("err", "Check your coordinates", str(exc)), unsafe_allow_html=True)
        st.info("Accepted formats: `269.452 / 4.693`, `17h57m48.5s / +04d41m36s`, "
                "`17 57 48.5 / +04 41 36`.")
        return

    ra, dec = parsed.ra_deg, parsed.dec_deg
    title = target["name"] if target else "Custom sky patch"

    # ----------------------------------------------------------------- header
    st.markdown(
        f'<div class="stm-title">SPHEREx Time Machine</div>'
        f'<div class="stm-sub"><span class="stm-pill">live IRSA</span>'
        f'<span class="stm-pill">{config.N_TOTAL_BANDS} bands</span>'
        f'<span class="stm-pill">0.75–5.0 µm</span>'
        f'{title} · RA {parsed.ra_hms} · Dec {parsed.dec_dms} · FOV {fov:.2f}°</div>',
        unsafe_allow_html=True)

    if st.session_state.get("tour_step") is not None:
        _render_tour(ra, dec, fov, mode, detector, cmap, max_frames)

    # ------------------------------------------------------------------ fetch
    with st.spinner("Querying IRSA SIA2 for SPHEREx epochs…"):
        result = cached_epochs(ra, dec, fov)

    if result.empty:
        if not result.ok:
            st.markdown(_card(
                "err", "Archive busy",
                "IRSA did not answer after two retries with exponential backoff. This is usually "
                "temporary load on the archive, not a problem with your query."),
                unsafe_allow_html=True)
            if result.message:
                st.caption(f"Diagnostics: {result.message}")
            if st.button("Retry the archive query", key="retry_archive"):
                st.cache_data.clear()
                st.rerun()
        else:
            _coverage_card(ra, dec)
        _demo_frame(ra, dec)
        _render_science_notes()
        return

    summary = archive.summarise(result.epochs)
    with st.spinner("Reading Level-2 cutouts (flags + variance + wavelength)…"):
        seq = cached_sequence(ra, dec, fov, mode, detector, max_frames)

    if len(seq) == 0:
        st.markdown(_card(
            "warn", "Epochs exist but no cutout was usable",
            f"{summary['n_epochs']} observation(s) intersect this position, but none produced a "
            "valid cutout (network or header problem). Try a different FOV or target."),
            unsafe_allow_html=True)
        _render_science_notes()
        return

    # ----------------------------------------------------------------- viewer
    left, right = st.columns([1.15, 1], gap="large")
    with left:
        with st.spinner("Rendering frames…"):
            pngs = cached_pngs(ra, dec, fov, mode, detector, max_frames, cmap)
        markers = _build_markers(seq, overlays, ra, dec, fov)
        components.html(
            blink.render(
                pngs,
                [f.label for f in seq.frames],
                markers=markers,
                width=config.DISPLAY_PX,
                height=config.DISPLAY_PX,
                fps=fps,
                title=title,
                what=("Same patch of sky, different dates. Fixed stars stay put; anything that "
                      "moves or changes brightness between frames is real astrophysics."),
                note=("; ".join(seq.notes) if seq.notes else ""),
            ),
            height=config.DISPLAY_PX + 150,
            scrolling=False,
        )
        st.caption(f"{len(seq.frames)} epochs · {seq.pixel_scale_arcsec:.2f}\"/pixel · "
                   f"{summary['n_obs']} archive rows · source: {result.source}")

    with right:
        _render_metadata(seq, summary, mode, detector)
        st.divider()
        _render_difference(seq)

    st.divider()
    tab_spec, tab_disc, tab_notes = st.tabs(
        ["🌈 Spectrum (click a source)", "🛰 Discovery Mode", "📚 Science notes"]
    )
    with tab_spec:
        _render_spectrum(seq, ra, dec, fov, target)
    with tab_disc:
        _render_discovery(seq, ra, dec, fov)
    with tab_notes:
        _render_science_notes_body(seq)

    _render_footer(result, summary)


# --------------------------------------------------------------------- sections
def _build_markers(seq: pipeline.Sequence, overlays: list[str], ra: float,
                   dec: float, fov: float) -> list[dict]:
    markers: list[dict] = []
    radius = max(0.1, fov * 0.6)
    if "Gaia DR3" in overlays:
        ov = cached_overlay("gaia", ra, dec, radius)
        if not ov.empty:
            px = catalogs.to_pixels(ov, seq.ref_wcs, seq.shape)
            for _, row in px.head(60).iterrows():
                markers.append({"x": float(row["x"]), "y": float(row["y"]),
                                "color": "#7ee0ff", "label": str(row["label"])})
    if "AllWISE" in overlays:
        ov = cached_overlay("wise", ra, dec, radius)
        if not ov.empty:
            px = catalogs.to_pixels(ov, seq.ref_wcs, seq.shape)
            for _, row in px.head(60).iterrows():
                markers.append({"x": float(row["x"]), "y": float(row["y"]),
                                "color": "#ffb347", "label": str(row["label"])})
    return markers


def _fmt_exptime(value) -> str:
    try:
        v = float(value)
        return f"{v:.1f} s" if np.isfinite(v) else "—"
    except (TypeError, ValueError):
        return "—"


def _render_metadata(seq: pipeline.Sequence, summary: dict, mode: str, detector: int) -> None:
    st.markdown("#### Frame metadata")
    idx = st.select_slider("Inspect epoch", options=list(range(len(seq.frames))),
                           value=len(seq.frames) - 1,
                           format_func=lambda i: seq.frames[i].label)
    frame = seq.frames[idx]
    meta = frame.meta or {}

    c1, c2 = st.columns(2)
    c1.metric("Epoch (UTC)", frame.iso.replace("T", " ")[:16])
    c2.metric("MJD", f"{frame.t_min:.3f}")
    c3, c4 = st.columns(2)
    dets = ", ".join(f"D{d}" for d in frame.detectors) or "—"
    c3.metric("Detector", dets)
    c4.metric("Wavelength", f"{frame.wavelength:.2f} µm" if np.isfinite(frame.wavelength) else "—")

    wl_range = meta.get("wavelength_range")
    good = meta.get("good_fraction", 1.0)
    rows = [
        ("Observation ID", frame.obs_id),
        ("Processing version", meta.get("proc_version", "—")),
        ("Release collection", meta.get("collection", "—")),
        ("Exposure time", _fmt_exptime(meta.get("exptime"))),
        ("PSF FWHM", f"{frame.psf_fwhm_px:.2f} native px"),
        ("LVF range on frame", f"{wl_range[0]:.2f}–{wl_range[1]:.2f} µm" if wl_range else "—"),
        ("Clean pixels", f"{good*100:.1f}%"),
        ("Contaminated pixels", f"{meta.get('n_contaminated_px', 0):,}"),
        ("Mode", "All-bands coadd" if mode == "stack" else f"Single detector D{detector}"),
    ]
    st.dataframe(pd.DataFrame(rows, columns=["Property", "Value"]),
                 hide_index=True, use_container_width=True, height=250)

    flags = meta.get("flag_counts") or {}
    if flags:
        top = list(flags.items())[:8]
        st.caption("Contamination flags present (FLAGS bitmask, per-pixel counts)")
        st.dataframe(
            pd.DataFrame([{"Flag": k, "Pixels": f"{v:,}",
                           "Meaning": config.FLAG_DESCRIPTIONS.get(k, "")} for k, v in top]),
            hide_index=True, use_container_width=True, height=200,
        )
    else:
        st.caption("No contamination flags set in this cutout.")


def _render_difference(seq: pipeline.Sequence) -> None:
    st.markdown("#### Difference mode")
    if len(seq.frames) < 2:
        st.caption("Need at least two epochs.")
        return
    labels = [f.label for f in seq.frames]
    c1, c2 = st.columns(2)
    i_a = c1.selectbox("Epoch A", range(len(seq.frames)), index=len(seq.frames) - 1,
                       format_func=lambda i: labels[i], key="diff_a")
    i_b = c2.selectbox("Epoch B", range(len(seq.frames)), index=0,
                       format_func=lambda i: labels[i], key="diff_b")
    op = st.radio("Operation", ["A − B (subtract)", "A + B (add)"],
                  horizontal=True, key="diff_op")
    psf_match = st.checkbox("PSF-match before subtracting", value=True,
                            help="Convolves the sharper epoch to the broader PSF so a "
                                 "colour difference does not masquerade as structure.")
    if i_a == i_b:
        st.caption("Pick two different epochs.")
        return

    if op.startswith("A − B"):
        res = pipeline.difference(seq.frames[i_a], seq.frames[i_b], psf_match=psf_match)
        snr = res["snr"]
        finite = snr[np.isfinite(snr)]
        if finite.size == 0:
            st.caption("No overlapping pixels between these epochs.")
            return
        fig = go.Figure(go.Heatmap(z=snr, colorscale="RdBu", zmid=0, zmin=-6, zmax=6,
                                   colorbar=dict(title="S/N", tickfont=dict(color="#dfe7f5")),
                                   hovertemplate="S/N %{z:.2f}<extra></extra>"))
        fig.update_layout(height=380, margin=dict(l=6, r=6, t=28, b=6),
                          paper_bgcolor="#0b0e14", plot_bgcolor="#0b0e14",
                          title=dict(text=res["stats"]["label"], font=dict(size=12, color="#a9b8d0")),
                          xaxis=dict(visible=False), yaxis=dict(visible=False, autorange="reversed"))
        st.plotly_chart(fig, use_container_width=True)
        if set(seq.frames[i_a].detectors) != set(seq.frames[i_b].detectors):
            st.caption(
                "These two epochs combine different detector arrays, i.e. different wavelengths. "
                "That colour difference is real but shows up as structure; switch to "
                "**Single detector** and pick one array for a cleaner difference.")
        sigma_cal = res["stats"].get("sigma_cal", 1.0)
        verdict = "interpretable" if abs(sigma_cal - 1.0) < 0.15 else "check normalisation"
        st.markdown(
            _card("ok" if verdict == "interpretable" else "warn",
                  f"Difference statistics — {verdict}",
                  f"Median S/N {res['stats']['median']:.2f} · calibrated σ {sigma_cal:.2f} · "
                  f"|S/N|₉₉ {res['stats']['p99']:.1f} · Δt {res['stats']['dt_days']:.1f} d. "
                  "A well-behaved variance-weighted difference has σ ≈ 1."),
            unsafe_allow_html=True)
    else:
        res = pipeline.add_frames(seq.frames[i_a], seq.frames[i_b])
        img = res["image"]
        finite = img[np.isfinite(img)]
        if finite.size == 0:
            st.caption("No overlapping pixels.")
            return
        lo, hi = np.nanpercentile(finite, [2, 99.5])
        fig = go.Figure(go.Heatmap(z=img, colorscale="Viridis", zmin=lo, zmax=hi,
                                   colorbar=dict(title="MJy/sr", tickfont=dict(color="#dfe7f5")),
                                   hovertemplate="%{z:.3f} MJy/sr<extra></extra>"))
        fig.update_layout(height=380, margin=dict(l=6, r=6, t=28, b=6),
                          paper_bgcolor="#0b0e14", plot_bgcolor="#0b0e14",
                          title=dict(text=res["label"], font=dict(size=12, color="#a9b8d0")),
                          xaxis=dict(visible=False), yaxis=dict(visible=False, autorange="reversed"))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Inverse-variance coadd of two epochs — deeper than either alone.")


def _render_spectrum(seq: pipeline.Sequence, ra: float, dec: float, fov: float,
                     target: dict | None) -> None:
    st.markdown("#### Click any source to extract its spectrum")
    st.caption("Box-select a source on the reference frame. SPHEREx's linear variable filter "
               "gives each visiting epoch a different wavelength at that pixel, so the "
               "revisits assemble a real 0.75–5.0 µm spectrum.")

    frame = seq.frames[-1]
    z = frame.data
    finite = z[np.isfinite(z)]
    lo, hi = np.nanpercentile(finite, [2, 99.5]) if finite.size else (0.0, 1.0)
    fig = go.Figure(go.Heatmap(z=z, colorscale="Viridis", zmin=lo, zmax=hi,
                               hovertemplate="x %{x:.0f} y %{y:.0f}<br>%{z:.3f} MJy/sr<extra></extra>"))
    fig.update_layout(height=420, margin=dict(l=6, r=6, t=24, b=6),
                      paper_bgcolor="#0b0e14", plot_bgcolor="#0b0e14",
                      title=dict(text=f"Reference frame · {frame.label} · drag a box on a source",
                                 font=dict(size=12, color="#a9b8d0")),
                      dragmode="select",
                      xaxis=dict(visible=False), yaxis=dict(visible=False, autorange="reversed"))

    selection = None
    try:
        event = st.plotly_chart(fig, use_container_width=True, on_select="rerun",
                                selection_mode=("box", "points"), key="spectrum_pick")
        if isinstance(event, dict):
            selection = event.get("selection", {}).get("points", [])
        elif hasattr(event, "selection"):
            selection = event.selection.get("points", [])
    except Exception:
        st.plotly_chart(fig, use_container_width=True)

    pick_ra, pick_dec, picked = ra, dec, False
    if selection:
        pt = selection[0]
        x, y = pt.get("x"), pt.get("y")
        if x is not None and y is not None:
            try:
                w_ra, w_dec = seq.ref_wcs.all_pix2world([[float(x), float(y)]], 0)[0]
                pick_ra, pick_dec, picked = float(w_ra), float(w_dec), True
            except Exception:
                pass

    c1, c2, c3 = st.columns([1, 1, 1])
    aperture = c1.slider("Aperture (x PSF FWHM)", 0.6, 2.5, 1.2, 0.1, key="ap_sl",
                         help="SPHEREx's PSF is ~6 arcsec FWHM, so the default aperture is "
                              "1.2 x FWHM. Smaller apertures miss most of the light.")
    max_rows = c2.slider("Epochs sampled", 6, 40, 18, 2, key="spec_rows",
                         help="Each sample is one archive read; higher = fuller spectrum, slower.")
    if c3.button("Extract spectrum at centre", use_container_width=True):
        st.session_state["force_centre"] = True

    if picked:
        st.caption(f"Selected RA {coords.format_ra(pick_ra)} Dec {coords.format_dec(pick_dec)}")
    else:
        st.caption("No selection yet — showing the spectrum at the field centre.")

    with st.spinner("Extracting spectrum from SPHEREx revisits…"):
        spec = cached_spectrum(pick_ra, pick_dec, fov, aperture, max_rows)

    if spec.points:
        pts = spec.sorted()
        wl = [p.wavelength_um for p in pts]
        fl = [p.flux_mjy for p in pts]
        er = [p.err_mjy if np.isfinite(p.err_mjy) else 0.0 for p in pts]
        sfig = go.Figure()
        sfig.add_trace(go.Scatter(
            x=wl, y=fl, mode="markers",
            error_y=dict(type="data", array=er, visible=True, thickness=1, width=2,
                         color="rgba(126,224,255,.55)"),
            marker=dict(size=8, color=[f"{WAVELENGTH_COLORS.get(p.detector, '#7ee0ff')}" for p in pts],
                        line=dict(width=1, color="#0b0e14")),

            text=[f"{p.obs_id} D{p.detector}" for p in pts],
            hovertemplate="λ %{x:.3f} µm<br>%{y:.3f} mJy<br>%{text}<extra></extra>",
            name="SPHEREx",
        ))
        sfig.update_layout(
            height=400, margin=dict(l=10, r=10, t=30, b=10),
            paper_bgcolor="#0b0e14", plot_bgcolor="#0b0e14",
            font=dict(color="#dfe7f5"),
            title=dict(text="Near-infrared spectrum from real SPHEREx revisits",
                       font=dict(size=13, color="#a9b8d0")),
            xaxis=dict(title="Wavelength (µm)", gridcolor="#1e2739", range=[0.7, 5.1]),
            yaxis=dict(title="Flux density (mJy)", gridcolor="#1e2739"),
            showlegend=False,
        )
        st.plotly_chart(sfig, use_container_width=True)
        st.caption(f"{len(pts)} wavelength samples from {spec.n_attempted} archive reads · "
                   f"aperture {aperture:.1f}× FWHM · background from a local annulus. "
                   f"Samples are unevenly spaced, so points are not joined by a line.")
        with st.expander("Spectrum data table"):
            st.dataframe(pd.DataFrame([{
                "λ (µm)": f"{p.wavelength_um:.3f}", "Flux (mJy)": f"{p.flux_mjy:.4f}",
                "σ (mJy)": f"{p.err_mjy:.4f}", "S/N": f"{p.snr:.1f}",
                "Detector": f"D{p.detector}", "Obs": p.obs_id,
            } for p in pts]), hide_index=True, use_container_width=True)
    else:
        st.markdown(_card("warn", "Not enough wavelength samples here",
                          spec.message or "Try a target with more epochs or a larger aperture."),
                    unsafe_allow_html=True)

    # precomputed reference spectra (Tier 3)
    if target:
        path = SPECTRA_DIR / f"{target['slug']}.json"
        if path.exists():
            try:
                blob = json.loads(path.read_text(encoding="utf-8"))
                pts = blob.get("points", [])
                if pts:
                    st.markdown("##### Precomputed reference spectrum")
                    rfig = go.Figure(go.Scatter(
                        x=[p["wavelength_um"] for p in pts], y=[p["flux_mjy"] for p in pts],
                        mode="markers+lines", marker=dict(size=6, color="#ffb347"),
                        line=dict(color="rgba(255,179,71,.5)"),
                        hovertemplate="λ %{x:.3f} µm<br>%{y:.3f} mJy<extra></extra>"))
                    rfig.update_layout(height=300, margin=dict(l=10, r=10, t=20, b=10),
                                       paper_bgcolor="#0b0e14", plot_bgcolor="#0b0e14",
                                       font=dict(color="#dfe7f5"),
                                       xaxis=dict(title="Wavelength (µm)", gridcolor="#1e2739"),
                                       yaxis=dict(title="Flux density (mJy)", gridcolor="#1e2739"))
                    st.plotly_chart(rfig, use_container_width=True)
                    st.caption(f"Cached on {blob.get('generated','—')} from {blob.get('n_points', len(pts))} "
                               f"real epochs. Generated by scripts/prewarm.py.")
            except Exception:
                pass


def _render_discovery(seq: pipeline.Sequence, ra: float, dec: float, fov: float) -> None:
    st.markdown("#### Discovery Mode")
    st.caption("Runs source detection on every epoch, chains detections over time and ranks "
               "them by displacement divided by centroid uncertainty. Real movers survive; "
               "variable stars do not.")
    if len(seq.frames) < 2:
        st.caption("Need at least two epochs.")
        return
    c1, c2 = st.columns([1, 1])
    threshold = c1.slider("Detection threshold (σ)", 3.0, 10.0, 5.0, 0.5, key="disc_thr")
    max_shift = c2.slider("Max frame-to-frame shift (px)", 1.0, 8.0, 2.5, 0.5, key="disc_shift",
                          help="Tighter values stop the tracker hopping between unrelated stars.")

    if st.button("Run discovery scan", type="primary", key="disc_run"):
        with st.spinner("Detecting and chaining sources across epochs…"):
            cands = photometry.detect_movers(seq, max_shift_px=max_shift,
                                             threshold_sigma=threshold)
        st.session_state["cands"] = cands

    cands = st.session_state.get("cands", [])
    if not cands:
        st.markdown(_card("ok", "Nothing ranked yet",
                          "Press **Run discovery scan**. On a quiet field you should get a "
                          "handful of low-score candidates; on Barnard's Star the star itself "
                          "should rank near the top."), unsafe_allow_html=True)
        return

    st.markdown(f"##### Top {min(5, len(cands))} candidate movers")
    cols = st.columns(min(5, len(cands)))
    for i, cand in enumerate(cands[:5]):
        with cols[i]:
            thumb = pipeline.detection_thumbnail(seq, int(cand.x), int(cand.y))
            if thumb:
                _image_grid(thumb, f"#{i+1} · score {cand.score:.0f}")
            st.caption(f"{cand.displacement_arcsec:.1f}\"/{len(seq.frames)} ep · "
                       f"RA {cand.ra:.4f} Dec {cand.dec:.4f}")

    st.dataframe(pd.DataFrame([{
        "Rank": i + 1, "RA (°)": round(c.ra, 5), "Dec (°)": round(c.dec, 5),
        "Displacement (\")": round(c.displacement_arcsec, 2),
        "Score": round(c.score, 1), "Frames": c.n_frames,
    } for i, c in enumerate(cands)]), hide_index=True, use_container_width=True)

    st.download_button(
        "⬇ Download candidates (CSV)",
        data=photometry.candidates_to_csv(cands, seq),
        file_name=f"spherex_candidates_{ra:.3f}_{dec:.3f}.csv",
        mime="text/csv",
    )
    st.caption("Score = displacement / 0.25 px centroid uncertainty. Cross-match with Gaia and "
               "WISE in the overlay to separate known high-proper-motion stars from artefacts.")


def _render_tour(ra: float, dec: float, fov: float, mode: str, detector: int,
                 cmap: str, max_frames: int) -> None:
    step = st.session_state.get("tour_step", 0)
    steps = [
        ("1 · A time machine for the sky",
         "SPHEREx scans the whole sky in 102 infrared colours. Here we stack the same patch of "
         "sky from different dates and flick between them."),
        ("2 · Blink it",
         "Press play in the viewer. Fixed stars stay put. Anything that moves between frames is "
         "a real object with real motion — this is how Pluto was found in 1930."),
        ("3 · Barnard's Star",
         "This is the fastest-moving star relative to the Sun: 10.3 arcsec per year. Across the "
         "epochs loaded here it visibly drifts against the background."),
        ("4 · Ask for its spectrum",
         "Open the Spectrum tab and box-select the star. The revisit wavelengths assemble a live "
         "0.75–5.0 µm spectrum from the archive."),
        ("5 · Go hunting",
         "Discovery Mode ranks every source by movement. Export the candidates as CSV and check "
         "them against Gaia. You are now doing volunteer discovery."),
    ]
    if step >= len(steps):
        st.session_state["tour_step"] = None
        return
    head, body = steps[step]
    st.markdown(_card("ok", head, body), unsafe_allow_html=True)
    c1, c2, _ = st.columns([1, 1, 4])
    if c1.button("Next →", key="tour_next"):
        st.session_state["tour_step"] = step + 1
        st.rerun()
    if c2.button("Skip tour", key="tour_skip"):
        st.session_state["tour_step"] = None
        st.rerun()


def _render_science_notes() -> None:
    with st.expander("📚 Science notes — what am I looking at?", expanded=False):
        _render_science_notes_body(None)


def _render_science_notes_body(seq: pipeline.Sequence | None) -> None:
    st.markdown(
        """
**Why blinking finds movers.** A blink comparator alternates two images of the same field.
Everything fixed on the sky (stars, galaxies) lands on the same pixels; a solar-system body or a
high-proper-motion star lands somewhere else. Your eye is far better at spotting that jump than at
reading two numbers.

**Proper motion and parallax.** Stars are not fixed: Barnard's Star moves 10.3 arcsec/yr, WISE 0855
about 4.8 arcsec/yr. Parallax adds a smaller annual wobble (0.55 arcsec at 1.8 pc). At SPHEREx's
6.15 arcsec pixels a fast star moves ~1–2 pixels per year, which is why a longer epoch baseline
matters more than a finer pixel.

**Why 102 bands.** SPHEREx places a linear variable filter over each of six 2040×2040 detector
arrays. Every array covers one 3.5°-wide strip and 17 wavelength slices, so one exposure is a
spectral image: the wavelength at a pixel is known from the `WCS-WAVE` extension, not from a filter
wheel. Summing the arrays raises signal-to-noise; splitting them gives a spectrum.

**Zodiacal light.** The interplanetary dust cloud is the dominant diffuse foreground in the near
infrared. The `ZODI` extension models it; subtracting it makes difference images cleaner.

**PSF limits.** The PSF is 6.1 arcsec at 4 µm, so two sources closer than that merge. Difference
imaging also leaves residuals wherever the PSF differs between epochs, which is why we PSF-match
and divide by the propagated variance.

**Contamination flags.** `FLAGS` is a bitmask (transient hits, overflow, ghosts, persistence,
non-functional pixels). We mask every contaminated pixel before stacking, differencing or
photometry, and report the counts in the metadata panel.

**Limitations.** SPHEREx is a survey, not a targeted observatory: coverage is sparse and uneven,
epoch spacing is weeks to months, and spectra are assembled from revisits at different scan
offsets. This tool is for exploration and education, not for publication-grade photometry.
        """
    )


def _render_footer(result: archive.QueryResult, summary: dict) -> None:
    st.markdown(
        f'<div class="stm-foot">'
        f'<strong>Data</strong>: IRSA SIA2 (<code>{result.source}</code>) → IRSA TAP fallback → '
        f'AWS Open Data <code>nasa-irsa-spherex</code> (anonymous, us-east-1). '
        f'{summary["n_obs"]} archive rows · {summary["n_epochs"]} observations · '
        f'collections {", ".join(summary.get("collections", [])) or "—"} · '
        f'processing {", ".join(summary.get("proc_versions", [])) or "—"}.<br>'
        f'<strong>Method</strong>: range-request FITS cutouts · reprojection to a common TAN grid · '
        f'inverse-variance coadd · variance-weighted differencing · WCS-WAVE spectral extraction.<br>'
        f'Built with Streamlit, astropy, astroquery, pyvo, photutils, reproject. '
        f'Not affiliated with NASA or Caltech.</div>',
        unsafe_allow_html=True,
    )
