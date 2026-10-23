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

## Components

| Path | Role |
|---|---|
| `gate/app.py` | aiohttp app: auth middleware, `/_gate/*` routes, catch-all (proxy when ready, wake page otherwise), 30 s background loop |
| `gate/session.py` | The **single shared global session**: lock, persistence, SSE broadcaster, wake / switch / stop / extend, boot task |
| `gate/state.py` | 10-state machine and allowed transitions |
| `gate/stopper.py` | Pure auto-stop and wake-admission rules ([AUTO_STOP.md](AUTO_STOP.md)) |
| `gate/budget.py` | Local CU ledger (per day / month, last known balance) |
| `gate/colab.py` | `ColabRunner` protocol + `ColabCLI` (async subprocess around `google-colab-cli`) |
| `gate/proxy.py` | Streaming HTTP + WebSocket reverse proxy, overlay injection, queue / heartbeat probes |
| `gate/auth.py` | Cloudflare Access JWT verification (JWKS cache), agent token, owners |
| `gate/profiles.py`, `gate/plan.py` | Profile parsing/validation and the JSON plan sent to Colab |
| `colab/setup.py` | Runs inside the Colab kernel: install, parallel downloads, workflows, ComfyUI boot, tunnel, watchdog, smoke test |
| `gate/web/` | Wake page (`index.html`), in-ComfyUI overlay (`overlay.js`), logo |
| `cli/ckit` | Stdlib CLI for humans and agents |

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
