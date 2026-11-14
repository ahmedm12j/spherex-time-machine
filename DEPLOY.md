# Deployment (5-minute playbook)

Verified live on 2026-11-08: **https://spherex-time-machine.streamlit.app/**

An external render from a different network loads the app shell, the LIVE IRSA badge, the
102 BANDS pill and the Barnard's Star frame. The app is public and needs no login.

## What is deployed

The live app is built from `main`. The application-code commit is `6bb8a67` ("Align docs with
the live app; regenerate r_dra; fix validation drift"). It changes one UI default (10 → 6
epochs) and the docs; the science code is otherwise unchanged from `c4ece06`. Streamlit Cloud
rebuilds from `main` on push. Any later documentation-only commit does not change the running
application code.

## The 303 check

`https://spherex-time-machine.streamlit.app/_stcore/health` returns **303** from some datacenter
networks. That is not evidence the app is down: two known-public Streamlit apps
(`llm-examples.streamlit.app` and `streamlit-demo-uber-nyc-pickups.streamlit.app`) return the
identical 303 plus `location: https://share.streamlit.io/-/auth/app?...` from the same network.
The redirect is a network-side auth gate applied to every Streamlit app, including public ones.
Residential and mobile browsers load the app normally, and an external render confirms it.

Do not use a datacenter `curl` alone to declare the app dead. Check it in a normal browser or
through an external render service.

## Step by step (about 5 minutes)

1. Make sure the commit you want deployed is pushed to `main`. This repo is public and owned
   by `ahmedm12j`.
2. Sign in at https://streamlit.io/cloud with the GitHub account that owns the repo.
3. In the Streamlit Cloud dashboard, choose **New app** (or open the existing app).
4. Select the `ahmedm12j/spherex-time-machine` repo, the `main` branch, and `app.py` as the
   main file. Python version: **3.11**.
5. Choose **Deploy**. The first build takes about 3–5 minutes while it installs
   `astropy`/`scipy` wheels.
6. Wait for the build to finish, then open the URL in a normal browser. The app should render
   the blink viewer and the Barnard's Star frame. From a datacenter shell, expect a 303 auth
   redirect even when the app is healthy.
7. (Optional) Run a Lighthouse audit against the live URL from a residential connection and
   record the real numbers. The current 46/100 in `VALIDATION.md` is a local measurement and is
   labelled as such. Do not attribute it to the live URL.
8. (Optional) Activate `deploy.yml`: add `STREAMLIT_CLOUD_PAT` as a repository secret on
   `ahmedm12j/spherex-time-machine` (a PAT with `repo` scope, owned by the repo owner). The
   workflow is documented in `.github/workflows/deploy.yml`.

## Credentials

No credentials are hardcoded in this repo. The Streamlit Cloud deploy needs only a GitHub PAT
with `repo` scope, entered as a repository secret — never committed.

## Free hosts other than Community Cloud

Community Cloud keeps no disk between restarts, so every cold start re-downloads its FITS
cutouts from IRSA. For a host with a persistent disk and more RAM, see **`DEPLOY_FREE.md`**: it
compares the 2026 free tiers, recommends Oracle Cloud Always Free, and includes the exact VM
steps. `docker-compose.yml` builds the image with a persistent cache volume and pre-warms it in
the background on boot.

## Local fallback

If you do not want to use Streamlit Cloud, you can still run the same app locally:

```bash
python -m venv .venv
.venv/Scripts/activate   # Windows CMD/PowerShell
pip install -r requirements.txt
streamlit run app.py
```

Or with Docker (if Docker is installed):

```bash
docker build -t spherex-time-machine .
docker run -p 8501:8501 spherex-time-machine
```

Both produce the same `app.py`, the same IRSA reads, and the same blink/difference/spectrum UI.
The local and Docker paths are independent of the Streamlit Cloud URL.
