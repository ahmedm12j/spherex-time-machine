# Deploy on GitHub Codespaces (16 GB RAM, free, public URL)

Use this when Lightning AI signups are closed (`error=signups_disabled`). GitHub
Codespaces gives a personal account **4 cores / 16 GB RAM** with **120 core-hours
per month free** and **no credit card**. That is **30 hours of 4-core runtime per
month** — enough for a demo window, not for 24/7 hosting.

Sources: [Codespaces billing](https://docs.github.com/en/billing/concepts/product-billing/github-codespaces)
(Free: 120 core-hours + 15 GB-month) and
[forwarding ports](https://docs.github.com/en/codespaces/developing-in-a-codespace/forwarding-ports-in-your-codespace)
(public URLs look like `https://<codespace>-8501.app.github.dev`).

## 1. Create the Codespace

- Open the repo on GitHub → **Code** → **Codespaces** → **+**
- `.devcontainer/devcontainer.json` requests **4 cpus / 16 GB RAM**, so Codespaces
  provisions that machine automatically — you do not pick it by hand, and you
  cannot accidentally land on the 2-core / 8 GB default
- Dependencies install themselves (`postCreateCommand`); wait for
  `pip install -r requirements.txt` to finish in the terminal

## 2. Install and run

    pip install -r requirements.txt
    streamlit run app.py --server.port 8501 --server.address 0.0.0.0 --server.headless true

## 3. The URL is already public

Port 8501 is declared in `.devcontainer/devcontainer.json` with
`"visibility": "public"`, so the forwarded URL needs no extra clicks:

    https://<codespace>-8501.app.github.dev

If it is not public on your machine, open the **Ports** tab → right-click
**8501** → **Port Visibility** → **Public**, then copy the forwarded URL.
Anyone with that link can open the app without a GitHub login.

## 4. Verify

- Dark UI loads
- Barnard's Star renders and the blink plays
- "▶ 20-second guided tour" completes with a green card
- Discovery Mode → Run scan returns candidates

## 5. Limits (read before sharing)

**30 hours per month.** 120 core-hours ÷ 4 cores. Running the 4-core machine
continuously burns the whole month's quota in 30 hours, then usage stops until
the next cycle (no card on file). Check the remaining quota under GitHub
**Settings → Billing → Codespaces**.

**It stops on idle.** Default idle timeout is 30 minutes, and a codespace stops
after 12 hours regardless. When it stops, **the public URL stops working**; restart
the codespace and re-run step 2, and the URL returns (same codespace name, same
hostname).

**Ephemeral disk.** Files outside the repo are lost on rebuild. The FITS cache lives
in `SPHEREX_CACHE_DIR`, so a cold start re-downloads.

**Not a hosting service.** GitHub positions Codespaces as a development
environment. It is fine for judging and demos; do not present it as production.

## 6. Keep the always-on link

The Streamlit Community Cloud deployment (1 GB RAM) stays live 24/7 at
<https://spherex-time-machine.streamlit.app>. Give judges that link as the
always-on fallback and the Codespaces link as the 16 GB demo.

## 7. Tuesday runbook (15 minutes before judging)

1. Open the repo → **Code** → **Codespaces** → resume the existing codespace
   (resuming is faster than creating one; the 4-core / 16 GB machine comes back).
2. In the terminal:

       streamlit run app.py --server.port 8501 --server.address 0.0.0.0 --server.headless true

3. Open the port 8501 URL once yourself so the first page render warms the cache.
4. Load Barnard's Star, then rewind and replay the blink — the second load reads
   from `data/cache` and is fast.
5. Paste the `https://<codespace>-8501.app.github.dev` link into the submission.

Budget check: 120 core-hours ÷ 4 cores = **30 hours/month**. A 6-hour judging day
on the 4-core box costs ~24 core-hours, leaving room for one rehearsal. Watch
**Settings → Billing → Codespaces** before the day, not during it.

If the codespace refuses to start because the quota is spent, fall back to the
Streamlit Cloud URL — it is live 24/7 and needs no setup.
