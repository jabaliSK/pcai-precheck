# pcai-precheck

A Kubernetes-friendly firewall / whitelist **pre-check** for air-gapped or
restricted environments. It runs a battery of connectivity and end-to-end tests
against **Hugging Face**, **NVIDIA NGC**, **Docker Hub**, **GitHub** and
**PyPI**, and serves a live report on **port 18080** with a downloadable
**PDF**.

## What it checks

Everything runs **in a single pod**, orchestrated by a small Python service.

| Category      | What it does                                                     | Tools |
|---------------|------------------------------------------------------------------|-------|
| Connectivity  | HTTPS probe of every configured domain, retried 3 times, with **and** without TLS verification | `curl`, `wget` |
| PyPI          | Reach the simple index, then actually download a tiny package    | `curl`, `wget`, `pip download` |
| Hugging Face  | Download a real file from a tiny model (`hf-internal-testing/tiny-random-gpt2`) | `huggingface_hub` |
| GitHub        | Shallow-clone a tiny public repo (`octocat/Hello-World.git`)     | `git clone --depth 1` |
| Network Speed | Measure download & upload throughput and RTT latency against a public speed test endpoint (Cloudflare by default) | `requests` |

Each connectivity probe distinguishes three outcomes:

- **Pass** — reachable with SSL verification.
- **Warn** — reachable **only without** verification (likely TLS interception / MITM proxy).
- **Fail** — unreachable in every mode.

Set `precheck.failOnSsl=true` in values to make the "warn" case a hard failure.

## Layout

| Path | Purpose |
|------|---------|
| [app/](app/) | Flask service + orchestrator + checks + PDF generator |
| [app/main.py](app/main.py) | HTTP entry point (routes: `/`, `/healthz`, `/api/results`, `/api/run`, `/report.pdf`) |
| [app/orchestrator.py](app/orchestrator.py) | Runs the checks in a background thread, tracks progress |
| [app/checks/](app/checks) | One module per test category |
| [app/report.py](app/report.py) | ReportLab PDF renderer |
| [scripts/precheck.sh](scripts/precheck.sh) | Standalone bash equivalent (curl + wget, retries) |
| [config/domains.txt](config/domains.txt) | Default domain list |
| [Dockerfile](Dockerfile) | Multi-stage build (python:3.12-slim + curl/wget/git) |
| [helm/pcai-precheck](helm/pcai-precheck) | Helm chart (Deployment + Service + optional Ingress) |

## HTTP endpoints

Served by the container on port `18080`:

| Path | Description |
|------|-------------|
| `GET /` | Live HTML report (auto-refreshes while the run is in progress) |
| `GET /report.pdf` | Downloadable PDF of the current results |
| `GET /api/results` | Full state as JSON (progress, config, results, summary) |
| `POST /api/run` | Trigger a fresh run (returns 202, or 409 if already running) |
| `GET /healthz` | Liveness / readiness probe |

## Run locally (Docker)

```bash
docker build -t pcai-precheck:0.2.0 .
docker run --rm -p 18080:18080 pcai-precheck:0.2.0
# then open http://localhost:18080/
```

Behind a corporate proxy:

```bash
docker run --rm -p 18080:18080 \
  -e HTTPS_PROXY=http://proxy.corp.example:3128 \
  -e HTTP_PROXY=http://proxy.corp.example:3128 \
  -e NO_PROXY=localhost,127.0.0.1 \
  pcai-precheck:0.2.0
```

## Run locally (Python)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.main
```

## Run just the bash script

```bash
DOMAINS_FILE=./config/domains.txt ./scripts/precheck.sh
```

Environment variables (used by both the app and the script):

| Variable | Default | Meaning |
|----------|---------|---------|
| `DOMAINS_FILE` | `/etc/precheck/domains.txt` | Newline-separated domains |
| `DOMAINS` | _(empty)_ | Extra whitespace-separated domains |
| `RETRIES` | `3` | Attempts per probe |
| `TIMEOUT` | `10` | Per-request timeout (seconds) |
| `RETRY_DELAY` | `2` | Delay between retries (seconds) |
| `PORT` | `443` | TCP port for connectivity probes |
| `FAIL_ON_SSL` | `false` | Treat "reachable only insecurely" as failure |
| `HF_REPO`, `HF_FILE` | tiny-random-gpt2, config.json | HF end-to-end test target |
| `GITHUB_REPO` | `octocat/Hello-World.git` | GitHub end-to-end test target |
| `RUN_ON_STARTUP` | `true` | Auto-run when the web server boots |
| `MAX_WORKERS` | `0` (auto) | Max concurrent checks (auto = `min(32, num_tasks)`) |
| `SPEEDTEST_ENABLED` | `true` | Run the download/upload speed test |
| `SPEEDTEST_URL` | `https://speed.cloudflare.com` | Base URL exposing `/__down?bytes=N` and `/__up` |
| `SPEEDTEST_DOWNLOAD_BYTES` | `26214400` (25 MiB) | Bytes pulled for the download probe |
| `SPEEDTEST_UPLOAD_BYTES` | `10485760` (10 MiB) | Bytes pushed for the upload probe |
| `SPEEDTEST_MIN_MBPS` | `5` | Warn (don't fail) if either direction is slower than this |

## Deploy with Helm

```bash
helm install precheck ./helm/pcai-precheck \
  --namespace precheck --create-namespace \
  --set image.repository=<your-registry>/pcai-precheck \
  --set image.tag=0.2.0
```

Port-forward the UI locally:

```bash
kubectl -n precheck port-forward svc/precheck-pcai-precheck 18080:18080
# open http://localhost:18080/
```

Expose via NodePort:

```bash
helm upgrade --install precheck ./helm/pcai-precheck \
  --set service.type=NodePort --set service.nodePort=30880
```

Expose via Ingress:

```bash
helm upgrade --install precheck ./helm/pcai-precheck \
  --set ingress.enabled=true \
  --set ingress.className=nginx \
  --set ingress.hosts[0].host=precheck.example.com \
  --set ingress.hosts[0].paths[0].path=/ \
  --set ingress.hosts[0].paths[0].pathType=Prefix
```

Behind a proxy:

```bash
helm upgrade --install precheck ./helm/pcai-precheck \
  --set-string extraEnv[0].name=HTTPS_PROXY \
  --set-string extraEnv[0].value=http://proxy.corp.example:3128 \
  --set-string extraEnv[1].name=HTTP_PROXY \
  --set-string extraEnv[1].value=http://proxy.corp.example:3128 \
  --set-string extraEnv[2].name=NO_PROXY \
  --set-string extraEnv[2].value=localhost,127.0.0.1,.svc,.cluster.local
```

## Notes

- The domain list intentionally covers the common auth/CDN/LFS endpoints for
  each provider. If a real download still fails after this check passes, grab
  the exact hostname from your client logs and add it to `precheck.domains` in
  [helm/pcai-precheck/values.yaml](helm/pcai-precheck/values.yaml).
- The Hugging Face and GitHub tests use very small, publicly stable
  targets — override them via `precheck.hfRepo` / `precheck.githubRepo` if your
  environment prefers a different canary.
