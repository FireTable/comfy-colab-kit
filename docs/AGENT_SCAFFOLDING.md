# AI Agent Scaffolding: Custom Profiles & Forking for Any Colab Workload

Comfy Colab Kit is designed to be **agent-extensible**. If the built-in profiles (`moss-image`, `sd-latest`, `qwen-image`) do not cover the model you want — or if you want to reuse this wake-on-demand gateway architecture for **another Google Colab GPU workload** (such as vLLM, Ollama, SD-WebUI Forge, Fooocus, Kohya, Whisper, CosyVoice, or a custom FastAPI/Gradio server) — you do not need to write boilerplate by hand.

You can **fork this repository** (or use `profiles/custom/`) and point an AI coding agent (Claude Code, Cursor, Codex, Antigravity, OpenClaw) at this guide.

---

## Part 1 — No Matching ComfyUI Profile? Let an AI Agent Build One

A profile is a declarative directory (`profile.yaml` + optional MIT/Apache-2.0 `workflows/*.json`) that tells the gateway and Colab runtime:
- Which **ComfyUI commit** and launch flags to run.
- Which **model weights** (`hf://` or `https://`) to stream into the RAM-backed `tmpfs` at T0 via the parallel HTTP Range downloader.
- Which **custom nodes** to clone and install via `uv`.
- Which **community workflows** to install and how to expose their inputs/outputs in **ComfyUI App Mode** (Linear Mode).

### Copy-Paste Prompt for Your AI Agent (New ComfyUI Profile)

```text
Read AGENTS.md, docs/PROFILES.md, docs/APP_MODE.md, and docs/AGENT_SCAFFOLDING.md.
Create a new ComfyUI profile at profiles/custom/<profile-id>/ (or profiles/examples/<profile-id>/) for:
- Target Model / Pipeline: <e.g., Flux.1 Dev fp8 / Wan 2.1 Video / SDXL Lightning>
- Hugging Face / Upstream Workflow URL: <link to official or community workflow JSON and model repo>
- Target GPU: Colab L4 (24 GB VRAM)

Follow all repository invariants:
1. Never put secrets or query-string tokens in profile.yaml.
2. Vendor workflow JSON under workflows/ ONLY if its license is MIT or Apache-2.0 (recording upstream_url and upstream_sha256); otherwise reference it via url:.
3. Configure model_aliases if multiple workflows can share the same base checkpoint / text encoder / VAE.
4. Wire app.inputs ([node_id, widget_name, label]) and app.outputs ([node_id]) for ComfyUI App Mode, respecting the backing-slot rules in docs/APP_MODE.md.
5. Run `python3 scripts/validate-profile.py` and `bash scripts/check-secrets.sh` until both pass cleanly.
```

### Agent Checklist for Building a ComfyUI Profile

When an AI agent executes the prompt above, it must follow this 6-step pipeline:

1. **Inspect the Upstream Workflow Graph**:
   - Download the upstream workflow JSON and inspect its loader nodes (`UNETLoader`, `CheckpointLoaderSimple`, `CLIPLoader`, `VAELoader`, `LoraLoader`, etc.) and any non-core node `type` names.
   - Identify required custom node repositories and pin them under `custom_nodes:` with `id`, `url`, and `license`.
2. **Declare Models & Core Tiering**:
   - Map every weight file to a valid ComfyUI subdirectory (`checkpoints/`, `diffusion_models/`, `text_encoders/`, `clip/`, `vae/`, `loras/`, `controlnet/`, `upscale_models/`, `clip_vision/`, `unet/`, `embeddings/`, `model_patches/`).
   - Mark the default workflow's models with `core: true` so they stream at T0 before ComfyUI finishes booting. Leave secondary workflows as on-demand (`optional: true` or fetched via `POST /_gate/fetch`) so cold starts stay around **~90 s (~1.5 min)**.
3. **Deduplicate Weights with `model_aliases`**:
   - If community workflows reference different quantization filenames for the same component, map them in `model_aliases: { "<workflow_filename.safetensors>": "<model_id>" }` so `packages/colab/setup.py` rewrites the loader widgets at install time.
4. **Configure Colab L4 Hardware Acceleration**:
   - For 24 GB L4 instances, include the battle-tested launch flags and environment variables:
     ```yaml
     comfyui:
       repo: https://github.com/comfyanonymous/ComfyUI
       commit: bc5f880
       args:
         - --preview-method
         - auto
         - --highvram
         - --reserve-vram
         - '0.2'
       env:
         PYTORCH_CUDA_ALLOC_CONF: expandable_segments:True
         CUDA_MODULE_LOADING: LAZY
         TORCH_CUDNN_V8_API: '1'
         TORCH_ALLOW_TF32_CUBLAS_OVERRIDE: '1'
     frontend:
       package: comfyui-frontend-package==1.57.1
     ```
5. **Wire App Mode (`app.inputs` & `app.outputs`)**:
   - Identify the prompt, seed, resolution, or reference-image input nodes and the `SaveImage` / `PreviewImage` output node.
   - Declare `app.inputs` using 3-tuples `[node_id, widget_name, "Human Label"]` and `app.outputs: [output_node_id]`.
   - If a custom node lacks a backing input slot for a promoted widget, follow [`docs/APP_MODE.md`](APP_MODE.md) (`skills/comfy-workflow-ui/SKILL.md`).
6. **Validate & Register**:
   - Run `python3 scripts/validate-profile.py` to verify schema compliance, SHA-256 format, App-mode node references, and wake download size.
   - Run `bash scripts/check-secrets.sh` and `python3 -m pytest -q`.
   - Add the new profile directory to `PROFILES` (and optionally `DEFAULT_PROFILE`) in `.env`.

---

## Part 2 — Forking to Run *Any* Colab GPU Workload (Beyond ComfyUI)

Although this repository ships with ComfyUI as its default backend, **the gateway architecture itself is workload-agnostic**.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                     Reusable Colab GPU Gateway Core                          │
│  • Cloudflare Zero Trust JWT Auth + RFC 8628 Device Flow + Bearer Tokens     │
│  • 10-State Lifecycle Machine + Idempotent Shared Session (Session.lock)     │
│  • Real-Time SSE Progress & Console Broadcast + Wake Web UI (WebGL Aurora)   │
│  • 5 Auto-Stop Cost Guards (Idle, Max Session, Quiet Hours, CU Budget, etc.) │
│  • Parallel T0 HTTP Range Downloader -> RAM tmpfs (/mnt/ram, up to ~1 GB/s)  │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                         Stdout Protocol + Local Port
                    (KIT_STAGE / KIT_PROGRESS / KIT_READY)
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        ▼                              ▼                              ▼
┌───────────────┐              ┌───────────────┐              ┌───────────────┐
│    ComfyUI    │              │ vLLM / Ollama │              │ Gradio / API  │
│  (Port 8188)  │              │ (Port 8000/   │              │ Forge / Audio │
│  Default Repo │              │  11434)       │              │ (Any Port)    │
└───────────────┘              └───────────────┘              └───────────────┘
```

### Why Fork Instead of Building from Scratch?

1. **Zero-Idle Cost Governance Is Already Solved**: `apps/gate/stopper.py`, CU ledger tracking, orphan VM sweeping, and the Colab-side two-phase self-release watchdog (`KIT_WATCHDOG_ONLY=1`) work identically regardless of what process runs on the GPU.
2. **T0 High-Speed Weight Streaming Is Built In**: The multi-connection HTTP Range downloader in `packages/colab/setup.py` (`DL_SCRIPT`) streams multi-gigabyte Hugging Face weights straight into a RAM-backed `tmpfs` (`/mnt/ram`) at up to 1,096 MB/s while system packages install in parallel.
3. **Authentication, Streaming Proxy & MCP Are Ready**: Browser SSO (Cloudflare Access), shareable token links, WebSocket/SSE reverse proxying, and the MCP server (`ckit mcp` / `/mcp`) require only minimal endpoint adjustments.

---

### The 4 Architectural Seams for Adapting a Fork

When instructing an AI coding agent to adapt a forked repository for a non-ComfyUI Colab workload, the agent only needs to touch **four well-defined seams**:

| Seam | Files | What Stays Unchanged | What the Agent Adapts |
| :--- | :--- | :--- | :--- |
| **1. Profile & Plan** | [`packages/schemas/profile.schema.json`](../packages/schemas/profile.schema.json)<br>[`apps/gate/profiles.py`](../apps/gate/profiles.py)<br>[`apps/gate/plan.py`](../apps/gate/plan.py) | Secret scanning, `models:` list (`hf://` URLs, `sha256`, `core`), accelerator selection (`L4`, `A100`, `T4`). | Expand `MODEL_DIRS` if needed; replace or generalize `comfyui:` launch args/env with your target runtime's command and flags. |
| **2. Colab VM Bootstrap** | [`packages/colab/setup.py`](../packages/colab/setup.py) | `Stage` context manager, `DL_SCRIPT` parallel Range downloader, `setup_model_store()` (`/mnt/ram`), `start_tunnel()`, and `KIT_WATCHDOG_ONLY` cost guard. | Change `PORT` (default `8188`), replace ComfyUI clone/boot in `bootstrap` / `comfy_boot` with your server startup command, and point `up()` at your server's health route (e.g. `/v1/models` or `/health`). |
| **3. Proxy & Busy Probe** | [`apps/gate/proxy.py`](../apps/gate/proxy.py)<br>[`apps/gate/app.py`](../apps/gate/app.py) | HTTP + WebSocket streaming proxy, Cloudflare service token injection, `Origin`/`Host` rewriting, `overlay.js` injection. | Update `Proxy.probe()` (default `/system_stats`) and `Proxy.queue_busy()` (default `/queue`) so idle auto-stop knows when your server is actively processing a request; update `api_prefixes` in `catch_all()`. |
| **4. CLI & MCP Tools** | [`packages/cli/cli.py`](../packages/cli/cli.py)<br>[`packages/cli/mcp_server.py`](../packages/cli/mcp_server.py)<br>[`apps/gate/app.py`](../apps/gate/app.py) | `ckit auth`, `ckit status`, `ckit wake`, `ckit stop`, `ckit extend`, and MCP `gate_status` / `gate_wake` / `gate_stop`. | Replace `gate_txt2img` / `gate_generate` with your workload's domain tool (e.g., `gate_chat_completion`, `gate_transcribe`, `gate_tts`, or `gate_infer`). |

---

### Protocol Contract Between Gate and Colab Runtime

If your agent rewrites [`packages/colab/setup.py`](../packages/colab/setup.py) for a custom workload, it **must preserve** the stdout line protocol parsed by [`apps/gate/session.py`](../apps/gate/session.py):

```text
KIT_STAGE bootstrap start
KIT_STAGE bootstrap done 14.2
KIT_STAGE deps start
KIT_STAGE deps done 8.5
KIT_STAGE models start
KIT_PROGRESS download {"done": 10737418240, "total": 26738688000, "rate": 850000000, "eta": 18}
KIT_PARALLEL booting start
KIT_STAGE models done 24.1
KIT_STAGE comfy_boot start
KIT_STAGE comfy_boot done 9.4
KIT_STAGE tunnel start
KIT_STAGE tunnel done 2.1
KIT_READY {"bootstrap": 14.2, "deps": 8.5, "models": 24.1, "comfy_boot": 9.4, "tunnel": 2.1}
```

- **Stage to State Mapping (`STAGE_TO_STATE` in `apps/gate/state.py`)**:
  - `bootstrap` / `deps` / `custom_nodes` → `installing`
  - `models` / `workflows` → `downloading`
  - `comfy_boot` / `tunnel` → `booting`
  - `smoke` → `smoke_testing`
  - `KIT_READY <json>` → transitions the gateway to `ready` and activates the reverse proxy.
  - `KIT_FAILED stage=<name> error=<repr>` → transitions the gateway to `failed` and triggers immediate VM cleanup.
- **Kernel Freedom Requirement**:
  `setup.py` **must exit cleanly** after printing `KIT_READY` (spawning the background watchdog as a detached daemon with `KIT_WATCHDOG_ONLY=1`). Returning from `setup.py` frees the Colab Jupyter kernel so the gateway can execute periodic `colab exec` heartbeats every ~90 s to prevent Colab's idle runtime reaper from disconnecting the VM.

---

### Copy-Paste Prompt for Your AI Agent (Forking for a New Colab Workload)

```text
Read AGENTS.md, docs/ARCHITECTURE.md, and docs/AGENT_SCAFFOLDING.md.
I have forked Comfy Colab Kit and want to adapt this wake-on-demand Colab gateway to run:
- Target Workload: <e.g., vLLM OpenAI-compatible server / Gradio Audio TTS / SD-WebUI Forge>
- Local Port on Colab VM: <e.g., 8000>
- Healthcheck Endpoint: <e.g., GET /health or GET /v1/models>
- Busy-Check Endpoint (for idle auto-stop protection): <e.g., GET /metrics or active request counter>
- Models to Download at T0: <Hugging Face repo/files>

Please:
1. Keep the 10-state machine, Cloudflare Zero Trust auth, SSE wake page, RAM tmpfs parallel Range downloader, and auto-stop rules intact.
2. Adapt the 4 architectural seams documented in docs/AGENT_SCAFFOLDING.md (profile schema/loader, packages/colab/setup.py bootstrap & healthcheck, apps/gate/proxy.py probe/busy check, and MCP/CLI domain tools).
3. Update tests in tests/ to cover the new probe endpoints and verify that `python3 -m pytest -q` and `bash scripts/check-secrets.sh` pass.
```
