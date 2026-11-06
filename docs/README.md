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
  ├─ DEPLOY_LOCAL.md (Run local)   ├─ ARCHITECTURE.md (Deep dive)├─ AGENT_SKILL.md (Skill spec)
  ├─ ARCHITECTURE.md (Concept)     ├─ PROFILES.md (Model schemas) ├─ API.md (REST & SSE endpoints)
  ├─ ACCESS.md (How to access)     ├─ APP_MODE.md (UI & forms)    ├─ ACCESS.md (Agent auth)
  ├─ DEPLOY_VPS.md (VPS deploy)    ├─ AUTO_STOP.md (Rules engine) ├─ DEPLOY_LOCAL.md (Local setup)
  ├─ AGENT.md (Full credential run)├─ SECURITY.md (Threat model)  └─ llms.txt (Context index)
  └─ WAKE_PAGE.md (UI features)
```

- **If you want to run locally with zero VPS & zero Cloudflare**: Read [`DEPLOY_LOCAL.md`](DEPLOY_LOCAL.md) for 1-minute Mac/PC setup.
- **If you want to know how users or agents connect**: Read [`ACCESS.md`](ACCESS.md) for web browser, guest token link, LAN, CLI, and AI agent connection paths.
- **If you are deploying on a cloud VPS**: Start with [`DEPLOY_VPS.md`](DEPLOY_VPS.md) for Docker Compose setup, or consult [`AGENT.md`](AGENT.md) for a complete credentials procurement walkthrough.
- **If you are adding or customizing models**: Read [`PROFILES.md`](PROFILES.md) to understand YAML schemas, model aliasing, and workflow policies, or hand [`AGENT_SCAFFOLDING.md`](AGENT_SCAFFOLDING.md) to your AI coding agent to scaffold a new profile or adapt a fork for any Colab GPU service.
- **If you are maintaining workflow UI or App Mode forms**: Read [`APP_MODE.md`](APP_MODE.md) and inspect [`skills/comfy-workflow-ui/`](../skills/comfy-workflow-ui/) for UI validation invariants.
- **If you are building an AI agent**: Read [`AGENT_SKILL.md`](AGENT_SKILL.md) and [`API.md`](API.md) to integrate automated wake, prompt queuing, and shutdown routines.
- **If you want to configure cost protection**: Read [`AUTO_STOP.md`](AUTO_STOP.md) for idle detection, session limits, and Compute Unit (CU) ledger tracking.

---

## 🗺️ Documentation Directory

| Document | Primary Focus | Target Audience |
| :--- | :--- | :--- |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Dual-phase gateway vs proxy, 10-state machine, Colab bootstrap, shared global session | Developers & Architects |
| [`EVOLUTION.md`](EVOLUTION.md) | Project evolution chronicle: 11 milestones reconstructed from the commit history | Contributors & Maintainers |
| [`DEPLOY_LOCAL.md`](DEPLOY_LOCAL.md) | Run gateway 100% locally on Mac/PC (zero VPS, no Cloudflare, zero auth) | Local Creators & Coding Agents |
| [`DEPLOY_VPS.md`](DEPLOY_VPS.md) | Docker Compose + Caddy production deployment, secrets layout, and upgrades | System Operators |
| [`ACCESS.md`](ACCESS.md) | Browser login, shareable guest links, LAN/Tailscale mode, agent auth, and in-app navigation | All Users & Agents |
| [`AGENT.md`](AGENT.md) | End-to-end VPS deployment runbook and complete credential procurement guide | Operators & Coding Agents |
| [`AGENT_SKILL.md`](AGENT_SKILL.md) | Autonomous agent skill protocol (`comfy_colab_gate`), prompt queuing, and GPU lifecycle | AI Agent Developers |
| [`AGENT_SCAFFOLDING.md`](AGENT_SCAFFOLDING.md) | AI agent runbook to scaffold custom ComfyUI profiles or adapt a fork for any Colab GPU workload | Coding Agents & Fork Maintainers |
| [`MCP.md`](MCP.md) | Model Context Protocol (MCP) guide: stdio (`ckit mcp`), remote HTTP, Cursor/Claude integration | AI Agents & Developers |
| [`API.md`](API.md) | HTTP REST endpoints, Server-Sent Events (SSE) telemetry, and ComfyUI proxy routes | Developers & AI Agents |
| [`PROFILES.md`](PROFILES.md) | Profile YAML specifications, upstream workflow licensing, model aliasing, and App mode | Workflow Engineers |
| [`APP_MODE.md`](APP_MODE.md) | ComfyUI App Mode (Linear Mode) architecture, JSON schema, and node serialization rules | Frontend & Workflow Engineers |
| [`AUTO_STOP.md`](AUTO_STOP.md) | Pure auto-stop rules, wake admission guards, CU budgeting, and Colab watchdog | Operators & Budget Owners |
| [`CLOUDFLARE.md`](CLOUDFLARE.md) | Dual-hostname layout, Cloudflare Access JWT validation, and Named Tunnels | Network & Security Engineers |
| [`SECURITY.md`](SECURITY.md) | Threat model, zero-secret repository guarantee, memory-only credentials, and scanning | Security Engineers |
| [`WAKE_PAGE.md`](WAKE_PAGE.md) | Zero-FOUC design, WebGL Aurora visual stack, real-time SSE progress, and injected overlay | Frontend Developers |

---

## ⚡ Task-Oriented Fast Path for Coding Agents

| Goal | Primary Reference | Core Invariants & Rules |
| :--- | :--- | :--- |
| **Modify State Machine or Boot** | [`ARCHITECTURE.md`](ARCHITECTURE.md) | All transitions must pass through `apps/gate/state.py:TRANSITIONS`. Colab stdout markers map via `STAGE_TO_STATE`. `wake()` remains strictly idempotent under `Session.lock`. State persists after every transition. |
| **Add or Modify a Model Profile** | [`PROFILES.md`](PROFILES.md), [`AGENT_SCAFFOLDING.md`](AGENT_SCAFFOLDING.md) | Never author original workflows in this repo. Vendor only Apache-2.0 or MIT workflows unchanged with `upstream_sha256`. Model weights must be URL-only. Validate via `python scripts/validate-profile.py`. |
| **Adapt a Fork for Any Colab Workload** | [`AGENT_SCAFFOLDING.md`](AGENT_SCAFFOLDING.md) | Preserve the 10-state machine, `KIT_STAGE/KIT_PROGRESS/KIT_READY` stdout protocol, RAM `tmpfs` Range downloader, and `KIT_WATCHDOG_ONLY` kernel exit; adapt the 4 seams (profile, setup.py, proxy probe, MCP/CLI). |
| **Pass Custom Flags to ComfyUI** | [`PROFILES.md#comfyui-passthrough`](PROFILES.md#comfyui-passthrough) | Reserved flags (`--listen`, `--port`, `--output-directory`, etc.) are managed by the runtime and cannot be overridden. No credentials allowed in profiles. |
| **Adjust Shutdown or Cost Guards** | [`AUTO_STOP.md`](AUTO_STOP.md) | Auto-stop logic in `apps/gate/stopper.py` must remain pure functions. Idle timer never trips while ComfyUI prompt queue is busy. All knobs configure strictly via `.env`. |
| **Customize Web UI or Overlay** | [`WAKE_PAGE.md`](WAKE_PAGE.md) | The Gate serves the Wake page only while booting or sleeping. When `ready`, the gateway proxies to the authentic ComfyUI Web App, injecting only the minimal floating overlay. |
| **Maintain Workflow UI or App Mode** | [`APP_MODE.md`](APP_MODE.md), [`skills/comfy-workflow-ui/`](../skills/comfy-workflow-ui/) | Never serialize `widgets` on node JSON dicts (breaks live LiteGraph instances). Widget labels persist through backing slots with `"widget": {"name": wname}`. |
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
