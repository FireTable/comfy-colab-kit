<p align="center">
  <img src="assets/logo/logo-e-fusion-dark-512.png" width="96" height="96" alt="Comfy Colab Kit logo" style="border-radius: 16px;" />
</p>

<h1 align="center">Comfy Colab Kit</h1>

<p align="center">
  <b>An always-available, wake-on-demand ComfyUI on Google Colab — open the URL; if it's asleep, press Wake and watch it boot; then it is your ComfyUI app.</b>
</p>

<p align="center">
  English •
  <a href="README-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-amber.svg" alt="License" /></a>
  <img src="https://img.shields.io/badge/Python-3.11%2B-3b82f6?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/Colab-L4-F9AB00?logo=googlecolab&logoColor=white" alt="Colab L4" />
</p>

---

> [!IMPORTANT]
> **Personal-use tool.** Colab compute belongs to your Google account. Sharing it with others may violate the
> Colab terms/FAQ (e.g. reselling or sharing compute) and can get the account restricted. Keep access to
> yourself or a handful of family/friends. Not affiliated with Google or Comfy Org.

---

## 📖 Overview

A tiny gateway, **comfy-gate** (Python / aiohttp), runs on your VPS behind Cloudflare Access. It behaves like a
sleeping Hugging Face Space: when the GPU is off you see a wake card with a profile picker; press **Wake** and
everyone watching sees the same staged progress and live logs; when ComfyUI is up the same URL becomes ComfyUI
(App mode by default, one tap to the node graph). Idle, over-time or over-budget sessions go back to sleep.

| Pillar | What you see | How |
|---|---|---|
| **1 · Wake on demand** | Sleep card → Wake → stages + logs → ComfyUI | 10-state machine, `colab new` (L4) + setup via `colab exec`, same-host reverse proxy incl. `/ws` |
| **2 · Switchable profiles** | Pick MOSS-Image, SD1.5, … before waking | `profile.yaml` + JSON Schema; zero secrets, models by public URL |
| **3 · Auto-stop** | Overlay shows time left; sleeps on idle / max session / quiet hours / CU budget | every rule in `.env` |

## ✨ Features

- **One shared session** — wake is idempotent; a second visitor (or agent) joins the in-flight boot instead of
  starting another VM. State survives gate restarts (`STATE_DIR/session.json`).
- **Live progress for everyone** — Server-Sent Events broadcast state, download GB / speed and setup logs.
- **Access-verified identity** — `Cf-Access-Jwt-Assertion` is verified (RS256, AUD, issuer); `OWNER_EMAILS` /
  `OWNER_ONLY_ACTIONS` restrict switch / stop / extend.
- **Backend locked to a service token** — the Colab tunnel hostname only admits the gate's Access service token.
- **Fast cold start** — parallel `hf_xet` downloads, `uv` installs, tunnel started first, ComfyUI booted as soon as
  core models land (`FAST_START`).
- **App mode first** — workflows get ComfyUI App-mode metadata; the injected overlay toggles App ⇄ Graph.
- **ComfyUI passthrough** — per-profile CLI args, frontend settings, env vars and `extra_model_paths`.
- **Agent friendly** — OpenAPI spec, `cli/ckit --json`, `docs/AGENT_SKILL.md`, `llms.txt`.

## 🚀 Quick start (VPS + Cloudflare)

```bash
git clone <this repo> comfy-colab-kit && cd comfy-colab-kit
cp .env.example .env                         # set PUBLIC_HOST, COLAB_TUNNEL_HOST, CF_ACCESS_*, emails
mkdir -p secrets data && chmod 700 secrets data
ZONE_NAME=example.com COLAB_TUNNEL_HOST=comfy-colab.example.com \
  CLOUDFLARE_API_TOKEN=… scripts/cf-setup.sh # tunnel + DNS + backend Access app + service token → secrets/
openssl rand -hex 32 > secrets/agent_token && chmod 600 secrets/*
docker compose -f deploy/docker-compose.yml run --rm comfy-gate colab sessions   # one-time colab login
docker compose -f deploy/docker-compose.yml up -d
# add deploy/Caddyfile.snippet to Caddy, reload, open https://$PUBLIC_HOST
```

Full walkthrough: [docs/DEPLOY_VPS.md](docs/DEPLOY_VPS.md) and [docs/CLOUDFLARE.md](docs/CLOUDFLARE.md).

## 🧩 Profiles

| Profile | Purpose | Default download |
|---|---|---|
| [`moss-image`](examples/profiles/moss-image/) (primary) | MOSS-Image: DeBand int8 default, 4-step turbo, official quality / layout, edit (optional) — side by side in App mode | ~26 GB core / ~57 GB full default set |
| [`sd-latest`](examples/profiles/sd-latest/) | Stable Diffusion 3.5: Large fp8 default, TurboX 8-step, Medium turbo (gated, optional) | ~15 GB core / ~22 GB |
| [`qwen-image`](examples/profiles/qwen-image/) | Qwen-Image 2.1: Turbo T2I default, full T2I, Turbo / full Edit | ~27 GB core / ~43 GB |

Workflows are **only** community-published ones, fetched from the author with source and license recorded.
Format and passthrough options: [docs/PROFILES.md](docs/PROFILES.md).

## ⏱️ Auto-stop

`IDLE_TIMEOUT_MIN` · `MAX_SESSION_HOURS` / `EXTEND_MAX_TIMES` · `BOOT_TIMEOUT_MIN` · `STOP_ON_PAGE_CLOSE_MIN` ·
`QUIET_HOURS` · `DAILY_CU_BUDGET` / `MONTHLY_CU_BUDGET` / `MIN_CU_BALANCE` · `ORPHAN_SWEEP_MIN` ·
`WATCHDOG_GATE_LOST_MIN` (Colab-side self-release). Details: [docs/AUTO_STOP.md](docs/AUTO_STOP.md).

## 🔐 Security

No secret ever enters the repo: `.env`, `secrets/`, `data/` are ignored, gitleaks + `scripts/check-secrets.sh`
run in pre-commit and CI, profiles reject credential-like keys. Secrets reach Colab over `colab exec` stdin and
live only in kernel memory. See [docs/SECURITY.md](docs/SECURITY.md).

## 🗂️ Project structure

```
gate/            comfy-gate: app.py session.py state.py auth.py stopper.py budget.py proxy.py colab.py plan.py
gate/web/        wake page (index.html), overlay.js, logo
gate/openapi.yaml
colab/setup.py   runs inside Colab: parallel install/download, App-mode metadata, tunnel, watchdog, smoke
cli/ckit         stdlib client (status|profiles|wake|switch|stop|extend --json)
schemas/         profile.schema.json
examples/profiles/{moss-image,sd-latest,qwen-image}/  (each with README.md + workflows/)
deploy/          Dockerfile, docker-compose.yml, Caddyfile.snippet
scripts/         cf-setup.sh, check-secrets.sh, validate-profile.py
docs/            product documentation (start at docs/README.md)
tests/           pytest suite (state machine, auth, auto-stop config, profiles, shared session, app)
```

## 🛠️ Development

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
python -m pytest -q
AUTH_DISABLED=true STATE_DIR=/tmp/kit COLAB_BIN=/bin/false python -m gate.app   # wake page on :8080
```

## 📄 License

MIT for this repository. Models, LoRAs, custom nodes and workflows referenced by profiles keep their own licenses
(listed in each profile README) and are downloaded from their original sources. Colab is a trademark of Google;
ComfyUI belongs to Comfy Org. This project is not affiliated with either.
