# comfy-colab-kit Documentation & Agent Navigation Guide

This folder documents the **finished product**: how comfy-colab-kit works, how to deploy it and how to extend it.
It is written for operators and for **AI coding agents** that change or drive the kit.

---

## 🗺️ Documentation map

```
docs/
├── README.md        # [this file] map + agent fast path
├── ARCHITECTURE.md  # components, request/data flow, shared global session, 10-state machine, reverse proxy, Colab bootstrap
├── WAKE_PAGE.md     # HF-Space-style wake page, live SSE progress, profile selector, in-ComfyUI overlay (App ⇄ Graph, +1h, stop)
├── PROFILES.md      # profile.yaml format, workflows (vendored vs URL-only), App-mode metadata, `comfyui:` passthrough
├── AUTO_STOP.md     # every auto-stop / admission rule and its .env variable, CU ledger, Colab-side watchdog
├── DEPLOY_VPS.md    # Docker Compose + Caddy deployment, colab CLI login, secrets layout, upgrades
├── CLOUDFLARE.md    # two hostnames, Access apps, service token, tunnel, scripts/cf-setup.sh
├── SECURITY.md      # threat model, secret handling, identity checks, scanning
├── API.md           # HTTP API summary (full spec: gate/openapi.yaml)
└── AGENT_SKILL.md   # skill spec for autonomous agents that wake / use / stop a deployed gate
```

---

## 🧭 Task-oriented fast path for coding agents

| Task | Primary guide | Core invariants |
| :--- | :--- | :--- |
| Change the boot sequence / add a state | [`ARCHITECTURE.md`](ARCHITECTURE.md) | All transitions go through `gate/state.py` `TRANSITIONS`; Colab stages map via `STAGE_TO_STATE`; `wake()` stays idempotent under `Session.lock`; state is persisted after every transition. |
| Add or change a profile / workflow | [`PROFILES.md`](PROFILES.md) | Never author workflows; vendor only Apache-2.0/MIT files unchanged with `upstream_sha256`; models are URL-only; run `scripts/validate-profile.py`. |
| Pass flags / settings / env to ComfyUI | [`PROFILES.md#comfyui-passthrough`](PROFILES.md#comfyui-passthrough) | `--listen`, `--port`, `--output-directory`, `--disable-auto-launch`, `--extra-model-paths-config` are reserved; no secrets in profiles. |
| Change when the VM stops | [`AUTO_STOP.md`](AUTO_STOP.md) | Pure rules in `gate/stopper.py`; idle never fires while the ComfyUI queue is busy; config only from `.env`. |
| Wake page / overlay UI | [`WAKE_PAGE.md`](WAKE_PAGE.md) | Everyone sees the same shared session; the Wake button becomes "Starting…" for all viewers. |
| Auth / Access / owners | [`SECURITY.md`](SECURITY.md), [`CLOUDFLARE.md`](CLOUDFLARE.md) | Verify `Cf-Access-Jwt-Assertion` (RS256, aud, iss) on every request; strip user Access headers before proxying. |
| Deploy / upgrade | [`DEPLOY_VPS.md`](DEPLOY_VPS.md) | Secrets are files under `secrets/` (0600), referenced by `*_FILE`; never in `.env` or the image. |
| Drive the gate from an agent | [`AGENT_SKILL.md`](AGENT_SKILL.md), [`API.md`](API.md) | Bearer agent token; poll `/_gate/status` or stream `/_gate/events`; stop when done. |

## Rules for this folder

- Docs describe shipped behaviour only — no plans, research notes or experiments.
- When code changes behaviour, update the matching doc in the same commit.
- No secrets, tokens, real account ids or demo links in any doc.
