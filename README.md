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

A tiny gateway, **comfy-gate** (Python / aiohttp), runs on your VPS behind Cloudflare Access.

> [!NOTE]
> **The Gate's Essential Role**: `comfy-gate` is fundamentally a **lifecycle, cold-start, and auto-shutdown gateway** — it does **NOT** reimplement ComfyUI. When the GPU is asleep, it serves a lightweight, zero-compute wake card with real-time SSE progress. When booted, it transparently reverse-proxies directly into the **authentic, full-featured ComfyUI Web Application** (including node graph, App mode, custom nodes, extensions, and WebSocket streaming), injecting only a non-intrusive floating overlay to switch between App mode and the Node Graph.

| Pillar | What you see | How |
|---|---|---|
| **1 · Wake on demand** | Sleep card → Wake → stages + logs → ComfyUI Web App | 10-state machine, `colab new` (L4) + setup via `colab exec`, streaming reverse proxy (HTTP + `/ws`) |
| **2 · Switchable profiles** | Pick MOSS-Image, SD3.5, … before waking | `profile.yaml` + JSON Schema; zero secrets, models by public URL |
| **3 · Auto-stop** | Overlay shows time left; sleeps on idle / max session / quiet hours / CU budget | every rule configured in `.env` |

## 💡 Why Comfy Colab Kit? (The Paradigm Shift)

Traditional Colab forces you to sit in front of a computer, manually run notebook cells, and baby-sit browser tabs that disconnect when your laptop sleeps. Comfy Colab Kit completely decouples compute execution from human attendance:

| Dimension | ❌ Traditional Colab Notebooks | ⚡ Comfy Colab Kit |
|---|---|---|
| **Trigger Method** | Sit at PC, open browser, manually execute cells | **Anywhere & Autonomous Agents**: 1-click wake on mobile, tablet, or headless AI agent (REST / MCP) |
| **Session Reliability** | Dies if laptop sleeps, tab closes, or Wi-Fi blips | **Decoupled Cloud Gateway**: VPS gateway persists state, auto-reconnects, survives local disconnects |
| **Access & Security** | Ephemeral random `*.trycloudflare.com` URLs with zero auth | **Single Custom Domain + Cloudflare Access**: Enterprise Zero-Trust SSO / OTP, service tokens for agents |
| **Collaboration** | Teammates accidentally spin up duplicate VMs, burning CU | **Global Idempotent Shared Session**: All visitors join the running instance with live streaming logs |
| **Budget Safety** | Leaving notebooks running overnight drains entire CU quotas | **Smart Auto-Shutdown**: Automatic spin-down on idle (e.g. 20m), max session time, or CU hard budget |
| **Automation & MCP** | Inaccessible to headless AI coding agents | **AI Agent Native**: OpenAPI spec, `cli/ckit --json`, upcoming `/mcp` server for Claude / OpenClaw |

## ✨ Features

- **Remote & Mobile Wake** — Wake ComfyUI on Colab L4 from your smartphone, tablet, or terminal. No desktop browser or manual cell execution required.
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
- **Autonomous Agent friendly** — OpenAPI spec, `cli/ckit --json`, `docs/AGENT_SKILL.md`, `llms.txt`, and upcoming `/mcp` server.

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

Full walkthrough: [docs/DEPLOY_VPS.md](docs/DEPLOY_VPS.md), [docs/CLOUDFLARE.md](docs/CLOUDFLARE.md), and [docs/AGENT.md](docs/AGENT.md) (comprehensive credential guide).

## 🤖 For AI Agents & Automation (Agent Skill Protocol)

Autonomous coding agents (Claude Code, Cursor, OpenClaw, Codex, LangGraph) can wake, monitor, use, and release the ComfyUI GPU backend without manual browser interaction:

```
[Agent] ──Bearer / Service Token──► comfy-gate (/_gate/status)
                                          │ If sleeping: POST /_gate/wake
                                          ▼
                                    Colab VM boots L4 ComfyUI
                                          ▼
[Agent] ◄──standard ComfyUI API───► Proxied /prompt, /history, /view
                                          ▼
[Agent] ──POST /_gate/stop────────► Releases GPU (or auto-sleep on idle)
```

1. **Authentication**: Provide `Authorization: Bearer <agent_token>` (configured in `secrets/agent_token`) or Cloudflare Access Service Token headers (`CF-Access-Client-Id` & `CF-Access-Client-Secret`).
2. **Status Check**: Call `GET /_gate/status` (or run `packages/cli/ckit status --json`). If `state == "ready"`, reuse the already running instance.
3. **Idempotent Wake**: Call `POST /_gate/wake` (or run `packages/cli/ckit wake --profile <name> --wait`). Multiple concurrent wakes safely join the in-flight boot.
4. **ComfyUI API Passthrough**: Once `ready`, call standard ComfyUI endpoints directly through the gate (`POST /prompt`, `GET /history/{prompt_id}`, `GET /view?filename=...`, and WebSocket `/ws`).
5. **Clean Shutdown**: When execution completes and no humans are active (`clients == 0`), call `POST /_gate/stop`, or simply disconnect and allow the idle auto-stop rule to reclaim the VM.

📖 **Agent References & Protocols**:
- [docs/AGENT_SKILL.md](docs/AGENT_SKILL.md) — Formal Agent Skill specification (`comfy_colab_gate`), credential handling, and tool patterns.
- [docs/AGENT.md](docs/AGENT.md) — Autonomous VPS deployment guide, credential procurement, and Cloudflare Access setup.
- [apps/gate/openapi.yaml](apps/gate/openapi.yaml) — OpenAPI 3.1 specification for all `/_gate/*` and passthrough routes.
- [llms.txt](llms.txt) — LLM context index for AI systems.

## 🧩 Profiles

| Profile | Purpose | Default download |
|---|---|---|
| [`moss-image`](profiles/examples/moss-image/) (primary) | MOSS-Image: DeBand int8 default, 4-step turbo, official quality / layout, edit (optional) — side by side in App mode | ~26 GB core / ~57 GB full default set |
| [`sd-latest`](profiles/examples/sd-latest/) | Stable Diffusion 3.5: Large fp8 default, TurboX 8-step, Medium turbo (gated, optional) | ~15 GB core / ~22 GB |
| [`qwen-image`](profiles/examples/qwen-image/) | Qwen-Image 2.1: Turbo T2I default, full T2I, Turbo / full Edit | ~27 GB core / ~43 GB |

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

## 🏗️ Monorepo Architecture

Comfy Colab Kit is structured as an engineering monorepo separating server applications, runtime packages, model profiles, and deployment automation:

```
comfy-colab-kit/
├── apps/
│   ├── gate/                 # [Application] Gateway & Reverse Proxy (backend Python + web UI)
│   │   ├── app.py            # aiohttp entrypoint, routes, middleware, auto-stop loop
│   │   ├── session.py        # Central single-instance Session orchestrator
│   │   ├── proxy.py          # Reverse proxy with SSE/WS streaming & overlay injection
│   │   ├── openapi.yaml      # OpenAPI 3.1 specification
│   │   └── web/              # Wake page static UI (index.html, bg.js, logo.svg)
│   └── site/                 # [Application] Showcase / Landing Site
│       ├── index.html        # Showcase documentation & promo page
│       ├── styles.css        # Aligned theme styles
│       └── main.js           # Client interactions
├── packages/
│   ├── colab/                # [Package] Colab VM agent & runtime (setup.py)
│   ├── cli/                  # [Package] Host & developer CLI tool (ckit)
│   └── schemas/              # [Package] JSON Schema definitions (profile.schema.json)
├── profiles/                 # Profiles & Model Configurations
│   ├── examples/             # Built-in community profiles (moss-image, sd-latest, qwen-image)
│   └── custom/               # Mount point for user custom profiles
├── deploy/                   # Infrastructure & deployment configuration
│   ├── Dockerfile            # Container build recipe
│   ├── docker-compose.yml    # VPS orchestration
│   └── Caddyfile.snippet     # Reverse proxy routing
├── docs/                     # Comprehensive product & engineering documentation
│   ├── AGENT.md              # VPS deployment & credential acquisition runbook
│   ├── AGENT_SKILL.md        # AI agent usage guide (wake, generate, stop)
│   └── ...
├── scripts/                  # CI, validation and secret check utilities
└── tests/                    # Pytest test suite (108+ tests)
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
