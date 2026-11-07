"""Self-contained HTML/JS blink viewer.

Streamlit re-runs the whole script for every interaction, which caps image
animation far below 10 fps.  Frames are therefore shipped as base64 PNGs into
one HTML component and animated with requestAnimationFrame on a canvas, which
holds 12 fps comfortably on a phone.
"""
from __future__ import annotations

import json

_TEMPLATE = r"""
<div class="stm-root" tabindex="0" role="group" aria-label="SPHEREx blink comparator">
  <div class="stm-stage">
    <canvas id="stm-canvas" width="__WIDTH__" height="__HEIGHT__"
            aria-label="Animated blink sequence of __WIDTH__ by __HEIGHT__ pixels"></canvas>
    <div class="stm-badge" id="stm-badge">__TITLE__</div>
    <div class="stm-help" id="stm-help">__WHAT__</div>
    <div class="stm-note" id="stm-note"></div>
  </div>
  <div class="stm-controls">
    <button id="stm-play" class="stm-btn" aria-label="Play or pause the blink">&#9654;</button>
    <input id="stm-scrub" type="range" min="0" max="__MAX__" value="0" step="1"
           aria-label="Frame scrubber" />
    <span class="stm-fps" id="stm-fps">-- fps</span>
  </div>
  <div class="stm-meta">
    <span id="stm-label">__FIRST__</span>
    <span id="stm-count">1 / __N__</span>
  </div>
</div>
<style>
  .stm-root{
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    background:#0b0e14; border:1px solid #2a3142; border-radius:14px;
    padding:12px; color:#e9eef7; max-width:100%;
  }
  .stm-root:focus-visible{outline:3px solid #7ee0ff; outline-offset:3px;}
  .stm-stage{position:relative; width:100%; aspect-ratio:1/1; background:#05070c;
             border-radius:10px; overflow:hidden;}
  #stm-canvas{width:100%; height:100%; display:block; image-rendering:pixelated;}
  .stm-badge{position:absolute; top:10px; left:12px; font-size:13px; font-weight:600;
             color:#ffffff; background:rgba(8,10,16,.72); padding:4px 9px;
             border-radius:7px; letter-spacing:.2px;}
  .stm-help{position:absolute; left:12px; right:12px; bottom:10px; font-size:12.5px;
            line-height:1.35; color:#f2f6ff; background:rgba(8,10,16,.78);
            padding:7px 10px; border-radius:8px;}
  .stm-note{position:absolute; top:10px; right:12px; font-size:11.5px; color:#ffd479;
            background:rgba(8,10,16,.75); padding:4px 8px; border-radius:6px; display:none;}
  .stm-controls{display:flex; align-items:center; gap:10px; margin-top:10px;}
  .stm-btn{background:#1b2233; color:#e9eef7; border:1px solid #33405c; border-radius:9px;
           width:44px; height:34px; font-size:15px; cursor:pointer;}
  .stm-btn:hover{background:#25304a;}
  .stm-btn:focus-visible{outline:3px solid #7ee0ff; outline-offset:2px;}
  #stm-scrub{flex:1; accent-color:#7ee0ff; height:22px;}
  .stm-fps{font-variant-numeric:tabular-nums; font-size:12px; color:#9fb0cc; min-width:52px; text-align:right;}
  .stm-meta{display:flex; justify-content:space-between; margin-top:6px;
            font-size:12px; color:#9fb0cc; font-variant-numeric:tabular-nums;}
  @media (max-width:640px){ .stm-help{font-size:11px;} .stm-badge{font-size:11px;} }
</style>
<script>
(function(){
  const images = __FRAMES__;
  const labels = __LABELS__;
  const markers = __MARKERS__;
  const targetFps = __FPS__;
  const cvs = document.getElementById('stm-canvas');
  const ctx = cvs.getContext('2d');
  const playBtn = document.getElementById('stm-play');
  const scrub = document.getElementById('stm-scrub');
  const fpsEl = document.getElementById('stm-fps');
  const labelEl = document.getElementById('stm-label');
  const countEl = document.getElementById('stm-count');
  const noteEl = document.getElementById('stm-note');
  const helpEl = document.getElementById('stm-help');

  const loaded = new Array(images.length).fill(null);
  images.forEach(function(src, i){
    const im = new Image();
    im.onload = function(){ loaded[i] = im; if(i === idx) draw(); };
    im.src = src;
  });

  let idx = 0, playing = false, timer = null, lastTs = 0, frames = 0, fpsWindow = 0;
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function draw(){
    ctx.clearRect(0,0,cvs.width,cvs.height);
    const im = loaded[idx];
    if(im){ ctx.drawImage(im, 0, 0, cvs.width, cvs.height); }
    else { ctx.fillStyle = '#05070c'; ctx.fillRect(0,0,cvs.width,cvs.height); }
    if(markers && markers.length){
      const sx = cvs.width / cvs.clientWidth;
      ctx.lineWidth = 1.6 * sx;
      ctx.font = (11*sx) + 'px sans-serif';
      markers.forEach(function(m){
        ctx.strokeStyle = m.color || '#7ee0ff';
        ctx.beginPath();
        ctx.arc(m.x, m.y, 9*sx, 0, Math.PI*2);
        ctx.stroke();
        if(m.label && m.showlabel){
          ctx.fillStyle = m.color || '#7ee0ff';
          ctx.fillText(m.label, m.x + 12*sx, m.y - 8*sx);
        }
      });
    }
    labelEl.textContent = labels[idx] || '';
    countEl.textContent = (idx+1) + ' / ' + images.length;
    scrub.value = idx;
  }

  function step(){
    const now = performance.now();
    if(lastTs){
      const dt = now - lastTs;
      if(dt < 1000/targetFps) return;
      frames++;
      if(now - fpsWindow > 500){
        fpsEl.textContent = (frames * 1000 / (now - fpsWindow)).toFixed(0) + ' fps';
        frames = 0; fpsWindow = now;
      }
      lastTs = now;
      idx = (idx + 1) % images.length;
      draw();
    } else { lastTs = now; fpsWindow = now; }
    timer = requestAnimationFrame(step);
  }

  function play(){
    if(playing) return;
    playing = true; lastTs = 0; playBtn.innerHTML = '&#10073;&#10073;';
    playBtn.setAttribute('aria-label','Pause');
    timer = requestAnimationFrame(step);
  }
  function pause(){
    playing = false; playBtn.innerHTML = '&#9654;';
    playBtn.setAttribute('aria-label','Play');
    if(timer) cancelAnimationFrame(timer);
    timer = null;
  }
  playBtn.addEventListener('click', function(){ playing ? pause() : play(); });
  scrub.addEventListener('input', function(){ idx = parseInt(scrub.value,10)||0; draw(); });

  const root = document.querySelector('.stm-root');
  root.addEventListener('keydown', function(e){
    if(e.key === ' ' || e.key === 'Enter'){ e.preventDefault(); playing ? pause() : play(); }
    else if(e.key === 'ArrowRight'){ e.preventDefault(); idx = (idx+1)%images.length; draw(); }
    else if(e.key === 'ArrowLeft'){ e.preventDefault(); idx = (idx-1+images.length)%images.length; draw(); }
  });

  if(helpEl && helpEl.dataset.seen === undefined){
    setTimeout(function(){ helpEl.style.transition='opacity .8s'; helpEl.style.opacity='0.0';
      setTimeout(function(){ helpEl.style.display='none'; }, 900); }, 7000);
  }
  if(reduce){ draw(); fpsEl.textContent = 'reduced motion'; }
  else { draw(); play(); }
})();
</script>
"""


def render(
    frames: list[str],
    labels: list[str],
    markers: list[dict] | None = None,
    width: int = 384,
    height: int = 384,
    fps: int = 12,
    title: str = "",
    what: str = "You are looking at the same patch of sky at different times — anything that moves is real.",
    note: str = "",
) -> str:
    """Return the HTML for the blink component."""
    frames = [f if str(f).startswith("data:") else f"data:image/png;base64,{f}" for f in frames]
    html = (
        _TEMPLATE.replace("__FRAMES__", json.dumps(frames))
        .replace("__LABELS__", json.dumps(labels))
        .replace("__MARKERS__", json.dumps(markers or []))
        .replace("__FPS__", str(int(fps)))
        .replace("__WIDTH__", str(int(width)))
        .replace("__HEIGHT__", str(int(height)))
        .replace("__MAX__", str(max(0, len(frames) - 1)))
        .replace("__N__", str(len(frames)))
        .replace("__FIRST__", labels[0] if labels else "")
        .replace("__TITLE__", title)
        .replace("__WHAT__", what)
    )
    if note:
        html = html.replace(
            '<div class="stm-note" id="stm-note"></div>',
            f'<div class="stm-note" id="stm-note" style="display:block">{note}</div>',
        )
    return html
