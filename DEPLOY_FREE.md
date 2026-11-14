# Deploying outside Streamlit Community Cloud

The app runs unchanged on any host that (a) keeps a real disk and (b) allows a
long-lived Python process. Nothing in `spherex/` or `app.py` needs to change: the
Dockerfile already points the cutout cache at `/app/data/cache` via
`SPHEREX_CACHE_DIR`, and `scripts/prewarm.py` fills that cache ahead of visitors.

## Why the current host is the bottleneck

Measured on this project, not on a vendor blog:

| Stage | Time (warm local disk cache) |
| --- | --- |
| `archive.get_epochs` for a cached field | 0.05 s |
| `pipeline.build_sequence` (7 frames) | 13.6 s |
| `pipeline.encode_frames` | 1.2–1.9 s |

Of the 13.6 s, about 5.6 s is the one-off `import reproject` (scipy-backed) and
about 7 s is the per-epoch reprojection/coaddition. None of that is host CPU.

What a host *does* change is whether the cache and the library imports survive:

| Host | RAM | Disk | Sleeps | Catch |
| --- | --- | --- | --- | --- |
| Streamlit Community Cloud (current) | 690 MB – 2.7 GB | up to 50 GB, **non-persistent** | after 12 h idle | every new process re-downloads cutouts from IRSA; throttling above ~690 MB |
| Hugging Face Spaces | 2 vCPU / 16 GB | 50 GB, non-persistent | after 48 h idle | **Gradio and Docker Spaces now require PRO ($9/mo)**; free accounts get 2 ZeroGPU Gradio Spaces only — no Streamlit SDK option any more |
| Oracle Cloud Always Free | 2 OCPU / 12 GB (halved from 4/24 on 15 Jun 2026) | 200 GB block volume | never | credit card for identity verification; capacity is often exhausted in popular regions; idle instances can be reclaimed |
| Render / Railway / Fly free tiers | 256–512 MB typically | none | yes | below this app's `astropy`+`scipy`+`reproject` footprint |
| Cloudflare Pages / Netlify / Vercel | n/a | n/a | n/a | static or serverless only; Streamlit needs a WebSocket server, so this is a rewrite, not a migration |

**Recommendation: Oracle Cloud Always Free**, because it is the only free tier
that gives a persistent 200 GB disk and 12 GB of RAM without sleeping. That is
the largest free allowance available and it removes the actual cause of the slow
first load. The trade-off is about 20 minutes of setup and a card for
verification; Streamlit Cloud needs neither.

## Oracle Cloud Always Free, step by step

1. Sign up at <https://www.oracle.com/cloud/free/> and verify the card. Create
   the instance in your **home region** (it cannot be changed later).
2. Create a VM: shape `VM.Standard.A1.Flex`, **2 OCPU / 12 GB**, image
   *Ubuntu 22.04* (Arm), boot volume 50 GB, and attach a block volume of
   100–150 GB for the cache. Keep the instance in a public subnet with a public IP.
3. Open the firewall twice — OCI's security list *and* the guest firewall:
   ```bash
   sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
   sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
   sudo netfilter-persistent save
   ```
4. Install Docker and mount the block volume at `/mnt/cache`:
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER && newgrp docker
   sudo mkfs.ext4 /dev/oracleoci/oraclevdb   # the attached block volume
   sudo mkdir -p /mnt/cache && sudo mount /dev/oracleoci/oraclevdb /mnt/cache
   echo '/dev/oracleoci/oraclevdb /mnt/cache ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab
   ```
5. Clone and start:
   ```bash
   git clone https://github.com/ahmedm12j/spherex-time-machine.git
   cd spherex-time-machine
   docker compose up -d --build
   curl -fsS localhost:8501/_stcore/health   # prints "ok"
   ```
   The cache volume lives inside Docker's own volume; to pin it to the block
   volume instead, change the `spherex-cache` volume to a bind mount:
   ```yaml
   volumes:
     spherex-cache:
       driver: local
       driver_opts:
         type: none
         o: bind
         device: /mnt/cache
   ```
6. Serve it on port 80/443 with automatic TLS (needs a domain pointed at the
   instance's IP):
   ```bash
   sudo apt-get install -y caddy
   sudo tee /etc/caddy/Caddyfile >/dev/null <<'EOF'
   spherex.example.org {
       reverse_proxy localhost:8501
   }
   EOF
   sudo systemctl reload caddy
   ```
   Caddy passes WebSocket upgrades by default, which Streamlit requires. Without
   a domain, browse to `http://<public-ip>:8501` and skip this step.

## Things that will bite

- **Do not delete the Streamlit Cloud app yet.** If the hackathon submission form
  already lists the `streamlit.app` URL, that URL is what judges open. Run both
  until the new one is proven, then update the submission.
- Streamlit behind a proxy needs WebSocket support. Caddy/nginx with the standard
  `Upgrade`/`Connection` headers are fine; a proxy that strips them shows grey
  placeholders forever.
- The first boot is slow by design: the health check passes in ~45 s but the
  prewarm job keeps downloading in the background for a few minutes. Watch it
  with `docker compose exec app tail -f /tmp/prewarm.log`.
- `data/cache` on a dev machine grows to ~600 MB for a handful of fields. The
  budget on a 100 GB block volume is not a concern, but do not commit it.
- Always Free idle instances can be reclaimed if CPU, network and memory all stay
  under 20% at the 95th percentile for seven days. A demo that gets traffic is safe.

## If you want the absolute fastest front end instead

That is a different project, not a deploy: precompute the frames, spectra and
candidate tables into static assets and serve them from Cloudflare Pages (free,
global CDN, unlimited bandwidth, effectively no cold start). You would lose the
live IRSA query — the app would show a fixed set of fields chosen at build time.
Realistic effort: days, not minutes, and it contradicts the "any coordinates,
live archive" premise in the README.
