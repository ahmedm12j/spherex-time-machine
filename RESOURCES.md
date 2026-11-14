# Resources and attribution

The SPHEREx Time Machine was built for the 2026 NASA Space Apps Challenge
("Planet X and SPHEREx"). The challenge's Resources tab provided eight items.
They are not equal in priority for a deep-space infrared time-series tool, so
they are grouped below into three tiers with an explicit status for each.

Status legend:

- **Integrated** — the app reads from this at runtime.
- **Cited** — linked for users, not read by the app.
- **Skipped** — intentionally out of scope; belongs to a different mission or archive.

## Tier 1 — Core Resources

| Resource | Status | Role |
|---|---|---|
| **NASA/IPAC Infrared Science Archive (IRSA) — SPHEREx** | **Integrated** | Queries SIA2 for Level-2 quick-release epochs; reads FITS cutouts from the anonymous AWS Open Data mirror `nasa-irsa-spherex` (us-east-1). Every science number in this repo comes from this path. Data produced by NASA/JPL-Caltech, served by IRSA (IPAC/Caltech). |
| **SPHEREx Tutorial Notebooks** | **Cited** | Recommended next step for anyone who wants to go beyond the web UI and work against the same archive in Python. The app itself does not execute these notebooks; they are linked for users, not read at runtime. See "How to use these resources" below. |

## Tier 2 — Inspiration and validation

| Resource | Status | Role |
|---|---|---|
| **WiseView** | **Cited — design blueprint** | A public blink tool for the earlier NASA WISE mission, built for the same purpose: blinking historical infrared data to find faint, fast-moving objects like brown dwarfs or planets. The approach to image stretching, frame blinking, and layout is modelled on how WiseView presents the same problem. The SPHEREx Time Machine is not a fork of WiseView and does not read WISE images. |
| **Gaia Mission Data (DR3)** | **Integrated** | Used for filtering and cross-matching. Gaia maps optical star positions with extreme precision; an object bright in the infrared but missing or distinct in Gaia's stellar map is a candidate for a cold, hidden object. The app uses Gaia DR3 via CDS/Vizier catalog `I/355/gaiadr3` as a visual overlay. It is also used as an optional filter that hides candidates with a known optical counterpart. If Vizier is unreachable, the overlay is skipped and the app continues. |

## How to use these resources

For anyone extending this project, the intended use of the Tier 1 and Tier 2
resources is:

> **SPHEREx Tutorial Notebooks — Start here.** If you are using Python (with Streamlit or standard Jupyter Notebooks), these notebooks give you the boilerplate code needed to read, parse, and handle real SPHEREx data structures.

> **WiseView — Do not skip this.** Since WiseView is a tool built for a similar purpose (blinking historical infrared data from the WISE mission to find faint, fast-moving objects like brown dwarfs or planets), it acts as a blueprint. You can look at how WiseView handles image stretching, frame blinking, and layout to help map out your own front-end system or backend logic.

> **Gaia Mission Data (Data Release 3) — Use this exclusively for filtering.** Gaia maps optical star positions with extreme precision. If an object is bright in the infrared data but missing or distinct in Gaia's stellar map, it helps your logic eliminate background stars and isolate cold, hidden objects.

These are recommendations for the next developer. They are not claims about
what this app already does. The tier table above them states what the app
actually ships.

## Tier 3 — Skipped for this challenge

These four resources were provided by the challenge but belong to the
**Canadian Space Agency** and are not part of the SPHEREx data path. Listing
them here confirms the whole Resources tab was considered, not just the subset
that applied.

| Resource | Status | Reason |
|---|---|---|
| **CSA NEOSSat — overview** | **Skipped** | Near-Earth object surveillance. Different mission, different instrument, different archive. |
| **CSA CADC** | **Skipped** | Canadian national archive. Not on the SPHEREx data path. |
| **CSA NEOSSat — data tutorial** | **Skipped** | Tutorial for a different mission. |
| **CSA NEOSSat — dataset** | **Skipped** | Data from a different mission. |

## Why Tier 3 is skipped, not ignored

The challenge asks for a viewer of SPHEREx time-series data. NEOSSat tracks
asteroids, satellites, and orbital debris; CADC is a Canadian archive. Reading
either into this app would mean displaying a different spacecraft's images in a
tool that claims to show SPHEREx — which would misrepresent provenance and
mislead users. The honest choice is to skip them by design and say so.

## Software dependencies

Astropy, Astroquery, reproject, photutils, astroalign, Streamlit — see
`requirements.txt` for pinned versions.
