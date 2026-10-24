# Comfy Colab Kit Documentation & Navigation Guide

Welcome to the **Comfy Colab Kit** engineering and operator documentation. This repository provides an on-demand, cost-optimized lifecycle gateway that boots and proxies ComfyUI on Google Colab GPU instances behind Cloudflare Zero Trust.

---

## 🧭 Navigation by Role

Choose the reading path tailored to your specific role and objective:

```
┌────────────────────────────────────────────────────────────────────────┐
│                          Comfy Colab Kit Docs                          │
└───────┬────────────────────────────────┬───────────────────────────────┘
        │                                │
 👤 End-User / Operator           🛠️ Core Developer             🤖 AI Agent Operator
  ├─ ARCHITECTURE.md (Concept)     ├─ ARCHITECTURE.md (Deep dive)├─ AGENT_SKILL.md (Skill spec)
  ├─ ACCESS.md (How to access)     ├─ PROFILES.md (Model schemas) ├─ API.md (REST & SSE endpoints)
  ├─ DEPLOY_VPS.md (Quick deploy)  ├─ AUTO_STOP.md (Rules engine) ├─ ACCESS.md (Agent auth)
  ├─ AGENT.md (Full credential run)├─ SECURITY.md (Threat model)  ├─ AGENT.md (VPS automation)
  └─ WAKE_PAGE.md (UI features)    └─ CLOUDFLARE.md (Dual host)   └─ llms.txt (Context index)
```

- **If you want to know how users or agents connect**: Read [`ACCESS.md`](ACCESS.md) for web browser, guest token link, LAN, CLI, and AI agent connection paths.
- **If you are deploying for the first time**: Start with [`DEPLOY_VPS.md`](DEPLOY_VPS.md) for the fast Docker Compose setup, or consult [`AGENT.md`](AGENT.md) for a complete credentials procurement walkthrough.
- **If you are adding or customizing models**: Read [`PROFILES.md`](PROFILES.md) to understand YAML schemas, model aliasing, and workflow policies.
- **If you are building an AI agent**: Read [`AGENT_SKILL.md`](AGENT_SKILL.md) and [`API.md`](API.md) to integrate automated wake, prompt queuing, and shutdown routines.
- **If you want to configure cost protection**: Read [`AUTO_STOP.md`](AUTO_STOP.md) for idle detection, session limits, and Compute Unit (CU) ledger tracking.

---

## 🗺️ Documentation Directory

| Document | Primary Focus | Target Audience |
| :--- | :--- | :--- |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Dual-phase gateway vs proxy, 10-state machine, Colab bootstrap, shared global session | Developers & Architects |
| [`DEPLOY_VPS.md`](DEPLOY_VPS.md) | Docker Compose + Caddy production deployment, secrets layout, and upgrades | System Operators |
| [`ACCESS.md`](ACCESS.md) | Browser login, shareable guest links, LAN/Tailscale mode, agent auth, and in-app navigation | All Users & Agents |
| [`AGENT.md`](AGENT.md) | End-to-end VPS deployment runbook and complete credential procurement guide | Operators & Coding Agents |
| [`AGENT_SKILL.md`](AGENT_SKILL.md) | Autonomous agent skill protocol (`comfy_colab_gate`), prompt queuing, and GPU lifecycle | AI Agent Developers |
| [`MCP.md`](MCP.md) | Model Context Protocol (MCP) guide: stdio (`ckit mcp`), remote HTTP, Cursor/Claude integration | AI Agents & Developers |
| [`API.md`](API.md) | HTTP REST endpoints, Server-Sent Events (SSE) telemetry, and ComfyUI proxy routes | Developers & AI Agents |
| [`PROFILES.md`](PROFILES.md) | Profile YAML specifications, upstream workflow licensing, model aliasing, and App mode | Workflow Engineers |
| [`AUTO_STOP.md`](AUTO_STOP.md) | Pure auto-stop rules, wake admission guards, CU budgeting, and Colab watchdog | Operators & Budget Owners |
| [`CLOUDFLARE.md`](CLOUDFLARE.md) | Dual-hostname layout, Cloudflare Access JWT validation, and Named Tunnels | Network & Security Engineers |
| [`SECURITY.md`](SECURITY.md) | Threat model, zero-secret repository guarantee, memory-only credentials, and scanning | Security Engineers |
| [`WAKE_PAGE.md`](WAKE_PAGE.md) | Zero-FOUC design, WebGL Aurora visual stack, real-time SSE progress, and injected overlay | Frontend Developers |

---

## ⚡ Task-Oriented Fast Path for Coding Agents

| Goal | Primary Reference | Core Invariants & Rules |
| :--- | :--- | :--- |
| **Modify State Machine or Boot** | [`ARCHITECTURE.md`](ARCHITECTURE.md) | All transitions must pass through `apps/gate/state.py:TRANSITIONS`. Colab stdout markers map via `STAGE_TO_STATE`. `wake()` remains strictly idempotent under `Session.lock`. State persists after every transition. |
| **Add or Modify a Model Profile** | [`PROFILES.md`](PROFILES.md) | Never author original workflows in this repo. Vendor only Apache-2.0 or MIT workflows unchanged with `upstream_sha256`. Model weights must be URL-only. Validate via `python scripts/validate-profile.py`. |
| **Pass Custom Flags to ComfyUI** | [`PROFILES.md#comfyui-passthrough`](PROFILES.md#comfyui-passthrough) | Reserved flags (`--listen`, `--port`, `--output-directory`, etc.) are managed by the runtime and cannot be overridden. No credentials allowed in profiles. |
| **Adjust Shutdown or Cost Guards** | [`AUTO_STOP.md`](AUTO_STOP.md) | Auto-stop logic in `apps/gate/stopper.py` must remain pure functions. Idle timer never trips while ComfyUI prompt queue is busy. All knobs configure strictly via `.env`. |
| **Customize Web UI or Overlay** | [`WAKE_PAGE.md`](WAKE_PAGE.md) | The Gate serves the Wake page only while booting or sleeping. When `ready`, the gateway proxies to the authentic ComfyUI Web App, injecting only the minimal floating overlay. |
| **Authenticate & Authorize Users** | [`SECURITY.md`](SECURITY.md), [`CLOUDFLARE.md`](CLOUDFLARE.md) | Cryptographically verify `Cf-Access-Jwt-Assertion` (RS256, AUD, issuer) on every request. Strip incoming user Access headers before reverse-proxying to the backend. |
| **Deploy or Automate Infrastructure** | [`DEPLOY_VPS.md`](DEPLOY_VPS.md), [`AGENT.md`](AGENT.md) | Secret values reside exclusively in `secrets/` with `0600` permissions. Reference them via `*_FILE` environment variables. Never commit secrets to git or Docker images. |

---

## 💖 Upstream Open-Source Credits & Acknowledgments

Comfy Colab Kit stands on the shoulders of giants. We express our sincere gratitude to:
- **[ComfyUI](https://github.com/comfyanonymous/ComfyUI)**: The modular, node-based diffusion graph execution engine and modern Web App interface created by @comfyanonymous and the Comfy Org.
- **[Google Colab](https://colab.research.google.com/)**: Providing accessible cloud GPU compute and the `google-colab-cli` ecosystem.
- **[Cloudflare](https://www.cloudflare.com/)**: Zero Trust Access security edge, Cloudflare Named Tunnels (`cloudflared`), and Cloudflare Pages.
- **[React Bits](https://reactbits.dev)**: The stunning Aurora WebGL shaders, SpotlightCard cursor glow, and ShinyText animations created by **David Haz**.
- **[Hugging Face](https://huggingface.co/)**: Fast model hosting and parallel weight distribution infrastructure (`hf_xet`).
- **[uv by Astral](https://astral.sh/uv)**: Blazing fast Python environment initialization.
- **[MOSS-Image](https://huggingface.co/MATLOWAI/MOSS-Image-SFT-DeBand-int8)**: Breakthrough diffusion models, DeBand LoRAs, and community workflows developed by OpenLMLab and MATLOWAI.
