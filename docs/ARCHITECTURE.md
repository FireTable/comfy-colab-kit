# Architecture

```
 phone / browser / agent
        │  https://PUBLIC_HOST            (Cloudflare proxy + Access login)
        ▼
 Cloudflare edge ──► VPS: Caddy (TLS, origin cert) ──► comfy-gate :8080 (aiohttp, Docker)
                                                          │  ① verify Cf-Access-Jwt-Assertion / agent Bearer
                                                          │  ② shared Session (state machine, auto-stop, CU ledger)
                                                          │  ③ colab CLI: new / upload / exec / stop
                                                          │  ④ reverse proxy (HTTP + /ws) with CF-Access-Client-Id/Secret
                                                          ▼
                              https://COLAB_TUNNEL_HOST (Access app: service token only)
                                                          │  cloudflared (named tunnel, started inside Colab)
                                                          ▼
                                   Colab L4 VM: ComfyUI :8188 + kit heartbeat node + watchdog
```

## Components (Monorepo Layout)

| Path | Role |
|---|---|
| `apps/gate/app.py` | aiohttp app: auth middleware, `/_gate/*` routes, catch-all (proxy when ready, wake page otherwise), 30 s background loop |
| `apps/gate/session.py` | The **single shared global session**: lock, persistence, SSE broadcaster, wake / switch / stop / extend, boot task |
| `apps/gate/state.py` | 10-state machine and allowed transitions |
| `apps/gate/stopper.py` | Pure auto-stop and wake-admission rules ([AUTO_STOP.md](AUTO_STOP.md)) |
| `apps/gate/budget.py` | Local CU ledger (per day / month, last known balance) |
| `apps/gate/colab.py` | `ColabRunner` protocol + `ColabCLI` (async subprocess around `google-colab-cli`) |
| `apps/gate/proxy.py` | Streaming HTTP + WebSocket reverse proxy, overlay injection, queue / heartbeat probes |
| `apps/gate/auth.py` | Cloudflare Access JWT verification (JWKS cache), agent token, owners |
| `apps/gate/profiles.py`, `apps/gate/plan.py` | Profile parsing/validation and the JSON plan sent to Colab |
| `apps/gate/web/` | Wake page (`index.html`), in-ComfyUI overlay (`overlay.js`), logo |
| `packages/colab/setup.py` | Runs inside the Colab kernel: install, parallel downloads, workflows, ComfyUI boot, tunnel, watchdog, smoke test |
| `packages/cli/ckit` | Python stdlib CLI for operators and autonomous agents |
| `packages/schemas/profile.schema.json` | JSON Schema definitions for profile validation |
| `apps/site/` | Showcase & architecture documentation static website (Cloudflare Pages) |

## Lifecycle Gate vs. ComfyUI Web Application

A key architectural design principle of this project is separation of concerns:

- **What `comfy-gate` IS**:
  An intelligent **lifecycle gatekeeper**. It manages the state machine (`sleeping` ⇄ `ready`), handles authentication (Cloudflare Access JWT & Bearer tokens), boots Colab VMs on-demand via the Colab CLI, streams setup progress via SSE, tracks Compute Unit (CU) consumption, and enforces auto-stop rules (idle timeouts, max session caps, quiet hours).
- **What `comfy-gate` is NOT**:
  It is **not** a reimplementation of ComfyUI's user interface, graph renderer, or execution engine.
- **The Transition**:
  While the VM is booting or asleep, `GET /` serves the zero-compute wake page. The exact millisecond the state reaches `ready`, the gate transitions into a transparent, low-overhead reverse proxy:
  - All standard ComfyUI HTTP endpoints (`/prompt`, `/history`, `/view`, `/system_stats`, `/upload/image`) and the WebSocket stream (`/ws`) pass directly through to ComfyUI running in the Colab VM.
  - The browser loads the **official, full-featured ComfyUI Web Application** (including node canvas, App mode, custom nodes, and extensions).
  - The proxy injects only a minimal floating overlay (`overlay.js`) that provides session countdown, stop button, and toggles between App mode and the Node Graph via ComfyUI's native command `Comfy.ToggleLinear`.

## The shared global session

There is exactly one Colab VM per gate, shared by every signed-in user and agent.

- `POST /_gate/wake` takes `Session.lock`. If the state is not `sleeping`/`failed`, the caller **joins** the
  in-flight session (`200 {"joined": true}`); otherwise it starts the boot task and returns `202`.
  25 concurrent wakes produce exactly one `colab new` (`tests/test_session.py`).
- State, profile, enabled workflows, start time, extensions and progress are written atomically to
  `STATE_DIR/session.json` after every transition. On restart the gate restores it and reconciles with
  `colab sessions`: if the VM is gone it goes to `sleeping`; if it is still booting it re-attaches and probes the
  backend until it answers; an interrupted `stopping` becomes `failed` and the stop is retried.
- Every change is broadcast on `GET /_gate/events` (SSE: `state`, `progress`, `log`, `warn`); new
  subscribers receive the current state and the last 400 log lines first, so every viewer sees the same
  "Starting…" screen.

## State machine

```
sleeping ─wake─► allocating ─► installing ─► downloading ─► booting ─► smoke_testing ─► ready
    ▲                │             │  └──────────► ready (reused VM) ◄──┘                  │
    │                └─────────────┴──── any boot state ──► failed                          │
    └──────────────── stopping ◄── stop / auto-stop / switch (cold: stop → wake new profile) ┘
```

States: `sleeping, allocating, installing, downloading, booting, smoke_testing, ready, switching, stopping, failed`.
Billing states (a VM may exist, CU accrue): all boot states, `ready`, `switching` and `failed`
(`failed` keeps billing until auto-stop releases the VM).
Colab reports progress with marker lines on stdout which `Session.handle_line` consumes:
`KIT_STAGE <name>` (mapped by `STAGE_TO_STATE`), `KIT_PROGRESS {json}`, `KIT_READY`, `KIT_FAILED <reason>`.

## Boot sequence

1. `colab sessions` — reuse an existing VM named `COLAB_SESSION_NAME` if present, else `colab new -s NAME --gpu L4`.
2. Build `plan.json` from the profile (`gate/plan.py`) and `colab upload` a small tarball (`colab/` + plan).
3. `colab exec -s NAME` with a short bootstrap program on **stdin**; secrets (tunnel token, optional HF
   token) are passed as JSON on stdin, never in argv or files on disk.
4. `colab/setup.py` stages: `bootstrap → deps → install_workflows → download (core first) → comfy_boot → smoke`
   then prints `KIT_READY`. With `FAST_START=true` ComfyUI starts once core models are in and the remaining
   downloads continue in the background.

Downloads run in parallel (`DOWNLOAD_CONCURRENCY`) with `hf_xet` high-performance mode, verify SHA-256 for
non-HF URLs, and hard-link duplicate files by hash.

## Reverse proxy

When `ready`, every non-`/_gate/*` path (HTTP and `/ws`) is streamed to `https://COLAB_TUNNEL_HOST`:

- user `Cf-Access-*` headers and `CF_Authorization` cookies are stripped;
- `Host`/`Origin` are rewritten to the backend; `CF-Access-Client-Id` / `CF-Access-Client-Secret` are added
  from `CF_SERVICE_TOKEN_FILE`;
- `GET /` gets the overlay script injected.

While not ready: `/` serves the wake page, API paths answer `503 asleep`, everything else redirects to `/`.

## Background loop (every 30 s)

Queue / heartbeat probe when ready, re-attach probe after restart, auto-stop `tick`, orphan sweep
(`ORPHAN_SWEEP_MIN`: a VM that exists while the gate is sleeping is stopped) and `colab ccu-info` balance refresh.

## Frontend Architecture & Asset Delivery

### Zero-FOUC Theme Initialization
Both `apps/gate/web/index.html` and `apps/site/index.html` execute a synchronous inline `<script>` in `<head>` before any stylesheet or DOM node parses. It checks `localStorage.getItem("kit.theme")` or falls back to `(prefers-color-scheme: dark)`, immediately assigning `data-theme` to `<html>`. This eliminates Flash of Unstyled Content (FOUC).

### Static Asset Cache-Busting (No-Bundler Approach)
Cloudflare Pages caches static files (`.css`, `.js`) for 4 hours by default. To deliver instantaneous updates without the complexity of a heavyweight frontend bundler:
1. **MD5 Query Hashing**: `scripts/deploy-site.sh` calculates the MD5 hash of `styles.css` and `main.js` and injects query strings (`styles.css?v=<hash>`) into `index.html` on every deployment.
2. **Edge Revalidation Headers**: `apps/site/_headers` configures `Cache-Control: public, max-age=0, must-revalidate` for HTML, CSS, and JS, guaranteeing that CDN edge servers and visitor browsers validate ETags instantly.
