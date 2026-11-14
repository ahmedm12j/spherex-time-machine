# Deploy on Lightning AI Studio (16 GB RAM, free)

> **Status: new signups are currently closed.** `https://platform.lightning.ai`
> returns `error=signups_disabled`, and Lightning's [status page](https://status.lightning.ai/)
> still carries the October 2026 security incident ("Service is down to address a
> security incident", Drive read-only restored 29 Oct). Existing accounts are
> unaffected. If you get this error: try the **GitHub SSO** button or a `.edu`
> address, otherwise use [`deploy/CODESPACES.md`](CODESPACES.md) — same 16 GB class,
> also free, public URL included.

Lightning AI's free Studio gives **4 CPU / 16 GB RAM / $0 / no card**. It is
the only free option in 2026 with enough memory for SPHEREx Time Machine.
**Caveat:** free Studios force-restart every 4 hours and do NOT auto-restart.

## 1. Create the Studio

- https://lightning.ai → sign up (free, no card)
- **New Studio** → **CPU** → name `spherex-time-machine` → Create

## 2. Clone and install

    git clone https://github.com/ahmedm12j/spherex-time-machine.git
    cd spherex-time-machine
    pip install -r requirements.txt

## 3. Run

    streamlit run app.py --server.port 8501 --server.address 0.0.0.0 --server.headless true

## 4. Expose port 8501

Plugins panel → **Port Viewer** → expose **8501** → copy URL:

    https://8501-<studio-id>.lightningapps.ai

## 5. Verify

- Dark UI loads within 30 s
- Barnard's Star renders (blink plays)
- "▶ 20-second guided tour" → green card, no red error
- Discovery Mode → Run scan → candidates appear, no TypeError

## 6. THE 4-HOUR RESTART (bold, must read)

**Free Studios force-restart every 4 hours. No auto-restart.**

When the URL goes dark: reopen the Studio, re-run step 3, same URL returns.

**Do not give this URL to judges unless you will be present to restart it.**

Keep the Streamlit Cloud URL as the always-on fallback.

If you only finish this file, the run succeeded. Everything below is bonus.

---

## Optional: trim memory and payload

`spherex/config.py` exposes `DISPLAY_PX`, the render size of each displayed frame.
It is set to **384** (down from 512) and the change is measured, not assumed:

    DISPLAY_PX = 384   # was 512; 384 cuts frame payload 41%

On identical pixel data: per-frame PNG encode **611 ms → 142 ms** and payload
**777.8 KB → 458.7 KB (−41%)**. The science products (FITS cutouts, photometry,
spectra) are unaffected — only the displayed PNG is smaller. Revert by setting it
back to 512 if you prefer maximum sharpness on a 16 GB box.
