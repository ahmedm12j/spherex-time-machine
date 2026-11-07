# 2-minute demo video — narration and shot list

**Total runtime: 2:00.** Screen recording at 1440×900, browser at 100 % zoom.
Have the app already loaded on Barnard's Star (warm cache) before you hit record.

---

### 0:00 – 0:12 · Cold open
**Shot:** The blink viewer already playing, full frame, no browser chrome.
**Narration:**
> "This is the sky, photographed at different times in the infrared, flipped like
> a flipbook. Everything standing still is a star or a galaxy. The thing that
> jumps — that's what we're hunting."

**Action:** click pause at 0:08 so the frame freezes on a visible offset.

---

### 0:12 – 0:30 · What it is
**Shot:** Pull back to the full app. Header reads *SPHEREx Time Machine*, RA/Dec
and FOV visible in the sidebar.
**Narration:**
> "SPHEREx is NASA's all-sky infrared telescope — 102 colours, 0.75 to 5 microns.
> Type any coordinates, pick a field of view, and this tool streams the real
> archive data and blinks it for you."

**Action:** type `17h57m48.5s` / `+04d41m36s`. Leave FOV at 0.5°. Press Enter.

---

### 0:30 – 0:52 · Barnard's Star
**Shot:** Blink resumes; the star visibly drifts against the fixed field.
**Narration:**
> "Barnard's Star is the fastest-moving star relative to the Sun — ten arcseconds
> every year. Across the epochs loaded here it slides across the background.
> Nothing is animated; every frame is a separate observation from the archive."

**Action:** scrub the bar slowly by hand at 0:44 to prove the frames are real.

---

### 0:52 – 1:12 · The money shot — live spectrum
**Shot:** Switch to the *Spectrum* tab. Drag a box over Barnard's Star.
**Narration:**
> "Click a source and the app builds a spectrum on the fly. SPHEREx has no filter
> wheel — a linear variable filter sits on the detector, so every pixel already
> knows its own wavelength. Revisit the same spot at different scan offsets and
> you assemble a near-infrared spectrum from the archive, live."

**Action:** the plot draws with error bars. Hover one point to show λ and flux.

---

### 1:12 – 1:30 · Difference mode
**Shot:** Right column, *Difference mode*. Epoch A = latest, B = earliest.
**Narration:**
> "Subtract two epochs and divide by the propagated noise. Fixed stars vanish;
> anything that moved or changed brightness survives. Noise sigma stays at one,
> which is how you know the image is calibrated and not just a pretty colour map."

**Action:** toggle *PSF-match* off and on to show the residual difference.

---

### 1:30 – 1:48 · Discovery Mode
**Shot:** *Discovery Mode* tab → **Run discovery scan**.
**Narration:**
> "Discovery Mode runs the whole field, chains detections across every epoch and
> ranks candidates by how far they moved relative to the measurement error.
> Export the list as CSV and cross-match it against Gaia. That's volunteer
> discovery — anyone with a browser can do it."

**Action:** top-5 thumbnails appear; click **Download candidates (CSV)**.

---

### 1:48 – 2:00 · Close
**Shot:** Zoom out; the URL bar shows `?ra=269.45208&dec=4.69333&fov=0.5&mode=stack`.
**Narration:**
> "Every view is a URL, so it embeds in a classroom page or a press release.
> Real archive data, real physics, no login. Go blink something."

**Action:** cut to the title card: *SPHEREx Time Machine — real IRSA data, in your browser.*

---

## Shot checklist (tick before recording)

- [ ] App pre-warmed on Barnard's Star (cache warm) so nothing spins on camera
- [ ] Browser zoom 100 %, dark mode, notifications off
- [ ] Blink rate at 12 fps (default)
- [ ] Gaia overlay ON for the spectrum shot (shows cross-match credibility)
- [ ] Spectrum axes legible at 1080p
- [ ] CSV download actually saves a file
- [ ] Close-up fonts ≥ 16 px effective size

## Do-not-say list

Avoid "simulated", "mock", "demo data". Everything on screen is live IRSA/NASA
data. Say "coverage pending" rather than "no data" if a target is empty.
