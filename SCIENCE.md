# The science behind SPHEREx Time Machine

*One page on the astrophysics and the data-processing decisions.*

## 1. What a SPHEREx Level-2 file actually contains

Each file is a multi-extension FITS with six scientifically distinct HDUs:

| HDU | Contents | How this app uses it |
|---|---|---|
| `IMAGE` | Calibrated surface brightness, MJy/sr | The pixels you blink |
| `FLAGS` | Per-pixel 32-bit bitmask | Every contaminated pixel is masked before stacking, differencing or photometry |
| `VARIANCE` | (MJy/sr)² per pixel | Propagated through the coadd and the difference, so difference images are true S/N |
| `ZODI` | Zodiacal-light model, MJy/sr | Optional subtraction to clean difference images |
| `PSF` | 121 wavelength planes × 101 × 101 | Used for PSF-matched subtraction and for the measured FWHM |
| `WCS-WAVE` | 11 × 11 control grid of (λ, Δλ) | Gives every pixel its true wavelength |

The `FLAGS` bit meanings come from the file headers themselves (`MP_TRANSIENT`,
`MP_OVERFLOW`, …), not from a guessed table, so they stay correct as the pipeline
version changes.

## 2. Why a single SPHEREx image is not a single wavelength

SPHEREx has no filter wheel. A linear variable filter (LVF) sits directly on each
of the six 2040×2040 detector arrays, so **wavelength varies across the focal
plane**. A detector image is therefore a *spectral* image: the `WCS-WAVE`
extension tabulates the wavelength at an 11×11 grid of detector positions, and we
bilinearly interpolate that grid to any pixel.

Two consequences drive the whole app:

* **A "band" is a position, not a file.** A source's flux in one epoch corresponds
  to one specific wavelength — the wavelength at the pixel where the source
  happened to land. The nominal detector bandpass (0.75–1.11 µm for D1, etc.) is
  only a label; the real wavelength comes from `WCS-WAVE`.
* **Spectra are built from revisits.** Because the scan geometry changes between
  visits, the same sky position lands on different pixels, hence different
  wavelengths. Stitching those epochs together yields the 0.75–5.0 µm spectrum
  this tool plots. The 102 nominal bands correspond to six arrays × 17 LVF
  slices.

## 3. Why blinking detects movers

A blink comparator alternates images of the same field. Everything fixed on the
sky falls on the same pixels; a moving object does not. The human eye detects a
*jump* far better than it reads two positions.

At a 6.15 arcsec pixel scale:

* Barnard's Star moves 10.3 arcsec/yr ≈ **1.7 pixels/yr**.
* WISE 0855 moves ≈ 4.8 arcsec/yr ≈ **0.8 pixels/yr**.
* Annual parallax at 1.8 pc is 0.55 arcsec ≈ 0.09 pixels — sub-pixel, so proper
  motion dominates in a blink.

Motion is therefore a few pixels across a multi-year baseline. That is why the
app **samples epochs evenly across the whole available time span** instead of
taking the first N visits: consecutive exposures from the same night show
nothing.

## 4. Processing decisions and why

**Reprojection.** Every cutout has its own WCS (dithers, scan geometry). All
frames are reprojected onto one TAN grid with `reproject`, so residual motion in
the blink is astrophysical rather than instrumental.

**Inverse-variance coaddition.** When more than one detector array covers the
requested position, they are combined with weights 1/σ², and the combined
variance is 1/Σ(1/σ²). This is the maximum-S/N stack; a plain mean would be
wrong because the arrays have different depths.

**Contamination masking.** Pixels carrying `TRANSIENT`, `OVERFLOW`, `SUR_ERROR`,
`PHANTOM`, `NONFUNC`, `HOT`, `COLD`, `PERSIST`, `BLOOM`, `SNOWBALL`, `GHOST*`,
`NONLINEAR`, `OUTLIER`, `DICHROIC`, `MISSING_DATA` or `PHANMISS` are set to NaN
before the stack. The metadata panel always reports which flags were present and
how many pixels they touched.

**Variance-weighted difference.** D = A − B, with σ² = σ²_A + σ²_B. The S/N map
is D/σ. Because propagated variance does not include pixel-to-pixel PSF
correlation, the map is divided by a single robust noise scale measured from its
own median absolute deviation. Relative weights are untouched; the map becomes
interpretable in units of σ (a clean difference has σ ≈ 1).

**PSF matching.** If epochs A and B have different PSFs, the sharper one is
convolved with a Gaussian of FWHM √(FWHM_A² − FWHM_B²) before subtraction, so a
PSF difference is not mistaken for a brightness change.

**Aperture photometry.** Spectra use a circular aperture with a local annulus
background. The radius is **not** a fixed pixel count: the SPHEREx PSF is ~6″
FWHM, comparable to one 6.15″ pixel, so the default aperture is 1.2 × FWHM with
a 2.4 × FWHM background annulus. A fixed 2.5-pixel aperture misses most of the
light and produced spectra consistent with noise before this was fixed.
Flux conversion: one 6.15″ pixel subtends 8.89×10⁻¹⁰ sr, so 1 MJy/sr integrated
over one pixel equals 0.889 mJy. Errors come from the `VARIANCE` plane summed
over the aperture.

**Proper motion must be applied per epoch.** This is the decision that matters
most for this particular tool. Barnard's Star moves 10.3″/yr, so in 2026 data it
sits ~270″ — about 45 SPHEREx pixels — away from its J2000 coordinates. A fixed
aperture on the catalogue position therefore measures empty sky and returns a
noise spectrum. Before extraction, each target's position is propagated to the
observation date using its proper motion (bundled catalogue values for the five
demo targets, Gaia DR3 for arbitrary coordinates). With the correction applied,
Barnard's Star yields 182 → 579 mJy from 0.75 to 3.8 µm, an ordinary M-dwarf SED,
instead of noise.

**`SOURCE` is not contamination.** The `FLAGS` bit `SOURCE` (21) records that a
pixel contains a detected source. Treating it as contamination — as a naive
"mask every flag" rule does — punches a dark hole through the centre of every
star, corrupting both the blink images and the photometry. It is excluded from
the mask and reported as information instead. Saturated cores (`OVERFLOW`) *are*
masked, and the aperture sum is corrected for the surviving area fraction so a
bright star still yields a flux estimate.

**Mover ranking.** Detections are chained across epochs by nearest neighbour
within a configurable shift. Score = total displacement / 0.25 px centroid
uncertainty. The ranking deliberately does *not* use brightness change, so a
variable star like R Draconis is not promoted to "mover".

## 5. What this tool is not

* It is not a photometric catalogue: SPHEREx is a survey with sparse, uneven
  coverage and a 6.1″ PSF at 4 µm.
* Spectra are assembled from revisits at different scan offsets, so they are
  sampled unevenly in wavelength and are not flux-calibrated to publication
  standard.
* Difference images are dominated by PSF and colour residuals for bright,
  red sources; PSF matching reduces but does not eliminate this.
* Coverage grows weekly. A "coverage pending" card is a real scientific result,
  not a bug.
