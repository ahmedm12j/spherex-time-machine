# Deploying SPHEREx Time Machine on Hugging Face Spaces

Hugging Face Spaces (Docker SDK, CPU Basic) gives this app **16 GB RAM, 8 cores, 24/7** —
16× the memory of Streamlit Community Cloud's free 1 GB tier, with no sleeping. The app code
is unchanged: the same `app.py`, the same validated science pipeline, the same `Dockerfile`.

The Space runs the container as **UID 1000** and routes traffic to the port declared in the
Space's README YAML — this repo's `Dockerfile` already runs as `user` (UID 1000) and listens
on **8501**.

---

## 1. Create the Space

1. Open <https://huggingface.co/new-space> (create a free account first if needed).
2. **Space name:** `spherex-time-machine`.
3. **License:** your choice (the repo is MIT).
4. **SDK:** select **Docker** — not Gradio, not Streamlit.
5. **Docker template:** **Blank**.
6. **Hardware:** **CPU Basic · 2 vCPU · 16 GB RAM · Free**.
7. **Visibility:** **Public**.
8. Click **Create Space**.

You now have an empty Space repository at
`https://huggingface.co/spaces/<username>/spherex-time-machine`.

## 2. Add the YAML metadata to the Space's README.md

Hugging Face reads the Space configuration from the YAML front-matter of the **Space's own**
`README.md`. Create or replace it with exactly this block on top:

```yaml
---
title: SPHEREx Time Machine
emoji: 🔭
colorFrom: purple
colorTo: gray
sdk: docker
app_port: 8501
pinned: false
---
```

`app_port: 8501` must match `EXPOSE 8501` in the `Dockerfile` and `--server.port=8501` in the
container command. If they disagree, the Space builds fine but shows a blank page.

> Note: this file lives in the **Space** repo. In the source repo `README.md` is the project
> documentation, and it is excluded from the image by `.dockerignore`.

## 3. Push the code

Clone the Space repo and copy the project into it. `Dockerfile` and `requirements.txt` must be
at the repository root.

```bash
git clone https://huggingface.co/spaces/<username>/spherex-time-machine
cd spherex-time-machine

# Copy the app into the Space (adjust the source path to your clone)
cp -r "/path/to/your/local/project/." .

# The Space's README.md must keep the YAML block from step 2
git add .
git commit -m "Initial deploy"
git push
```

If the Space repo already contained a `README.md`, keep its YAML block and replace only the
body. Do not commit `.venv/`, `data/cache/`, or other local artefacts — `.dockerignore` keeps
them out of the image, but they should not be in the Space repo either.

Hugging Face also accepts an authenticated git push (username + an access token with **write**
scope from <https://huggingface.co/settings/tokens>) if it prompts for a password.

## 4. Wait for the build

The first build takes roughly **5–10 minutes** (installing astropy, scipy, reproject and the
rest). Open the Space page and watch the **Logs** tab:

- `pip install -r requirements.txt` output, then
- `You can now view your Streamlit app in your browser.`

Every later push rebuilds incrementally. A restarted Space keeps the image, so restarts are
fast; only code changes trigger a rebuild.

## 5. Verify

- Health endpoint: `https://<username>-spherex-time-machine.hf.space/_stcore/health`
  → should return **200** with body `ok`.
- App: `https://<username>-spherex-time-machine.hf.space` — the header, the LIVE IRSA badge
  and the Barnard's Star frame should render within a few seconds.
- If the page is blank but the health check is 200, the port does not match: check
  `app_port` (step 2) against `EXPOSE`/`--server.port` in the `Dockerfile`.

The health check is also declared inside the image, so the Space's Logs tab marks the container
unhealthy if Streamlit fails to start.

## 6. Caveats

- **Ephemeral disk.** The container filesystem is reset on every restart or rebuild, so
  `data/cache/` (the SIA query cache and downloaded FITS cutouts) is wiped. The first visit
  after a restart re-downloads from IRSA and is slow — typically 15–20 s per field — then later
  visits hit the warm cache. That is a cold-start cost, not a crash.
- **No sleep on this tier.** Per the current Hugging Face docs, Docker Spaces on CPU Basic run
  continuously; the Space is not paused for inactivity. If that ever changes, a lightweight
  uptime pinger hitting `/_stcore/health` every few minutes keeps it warm.
- **No persistent volumes on the free tier.** Pre-warming the cache at build time would only
  help until the next restart; this repo does not pre-warm in the image.
- **Anonymous archive access only.** IRSA and the AWS Open Data bucket need no credentials, so
  the Space needs no secrets.
- **Memory headroom, not infinite memory.** 16 GB removes the pressure that made the 1 GB
  Streamlit Cloud build stall, but each epoch is still a few MB of range reads; keep the
  "Epochs to load" slider modest on slow connections.
- **Local Docker runs as UID 1000 too.** A plain `docker run -p 8501:8501` works because the
  image is owned by that user. If you use `docker-compose.yml`, which mounts named volumes over
  `data/cache`, chown the volume once (`docker compose run --rm --user root app chown -R
  user:user /app/data`) or the app cannot write its cache.

---

### Local check before deploying (optional)

```bash
docker build -t spherex-time-machine .
docker run --rm -p 8501:8501 spherex-time-machine
# → http://localhost:8501
```

If this builds and serves locally, the Space build will too — it uses the same Dockerfile.
