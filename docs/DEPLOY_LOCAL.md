# 💻 Local Standalone Deployment (Zero VPS & No Cloudflare Required)

Run the **comfy-gate** gateway directly on your local computer (**macOS**, **Linux**, or **Windows via WSL2/Docker**).

> [!TIP]
> **Why run locally?**
> - **Zero VPS costs ($0/mo)**: No need to rent, patch, or maintain a cloud Linux server.
> - **Zero Cloudflare setup**: No custom domain, DNS records, Cloudflare Tunnel, or Zero Trust Access required.
> - **Zero expensive local hardware**: Your laptop doesn't need an NVIDIA GPU. The local gateway commands an on-demand Google Colab L4 GPU (24GB VRAM) in the cloud, streams ComfyUI directly to `http://localhost:8080`, and shuts it down when you're done.

---

## 🎯 Comparison: Local Mode vs. VPS Mode

| Feature | 💻 Local Mode (`localhost:8080`) | ☁️ VPS Mode (`comfy.yourdomain.com`) |
|---|---|---|
| **Best for** | Solo creators, local AI agents, Mac/laptop users, fast prototyping | Teams, shared access, public webhooks, 24/7 background automations |
| **Server needed?** | **None** (runs on your Mac / PC) | Linux VPS (Debian / Ubuntu) |
| **Domain needed?** | **None** (`http://localhost:8080`) | Custom domain with Cloudflare DNS |
| **Authentication** | `AUTH_DISABLED=true` (Zero-auth for local dev) | Cloudflare Zero Trust (Google OAuth / SSO) + Service Tokens |
| **AI Agent integration** | Direct stdio MCP (`ckit mcp --gate-url http://localhost:8080`) | Remote HTTP MCP with CF Service Token headers |
| **Compute Engine** | Google Colab L4 GPU (ephemeral on-demand) | Google Colab L4 GPU (ephemeral on-demand) |
| **Idle Cost** | **$0.00** | VPS monthly fee |

---

## 🚀 1-Minute Quickstart for Humans

### 1. Prerequisites
- **Computer**: macOS (Apple Silicon / Intel), Linux, or Windows (WSL2 / Docker Desktop).
- **Runtime**: Docker installed OR Python 3.11+.
- **Google Account**: Active Google account with Colab access (free tier or Compute Units).

### 2. Configure Local Environment
```bash
# Clone the repository
git clone https://github.com/FireTable/comfy-colab-kit.git
cd comfy-colab-kit

# Create your local .env configuration
cp .env.example .env

# Set auth bypass and localhost port in .env
# (macOS sed syntax; or edit .env directly with VS Code / cursor)
sed -i '' 's/AUTH_DISABLED=false/AUTH_DISABLED=true/' .env
sed -i '' 's/PUBLIC_HOST=comfy.example.com/PUBLIC_HOST=localhost:8080/' .env
```

### 3. One-Time Google Colab Authentication
The gateway needs permission to call the `colab` CLI. Authenticate once to save credentials into `./data`:
```bash
# Option A: Via Docker (no local Python setup required)
mkdir -p data && chmod 700 data
docker run --rm -it -v $(pwd)/data:/data -e HOME=/data/home ghcr.io/firetable/comfy-colab-kit:latest colab sessions

# Option B: Via local Python / pip
pip install -e ".[dev]"
colab sessions
```
*Follow the terminal prompt to open the Google OAuth URL, grant access, and paste the authorization code.*

### 4. Start the Local Gateway
```bash
# Start with Docker Compose (Recommended)
docker compose -f deploy/docker-compose.local.yml up -d

# OR run directly in Python
python3 -m gate
```

### 5. Open Your Browser
Visit **`http://localhost:8080`**:
1. Click **⚡ 唤醒 GPU (Wake GPU)** and choose your profile (`flux-klein`, `wan-ltx-video`, `moss-image`, etc.).
2. Watch real-time streaming progress logs as the Colab L4 instance boots.
3. Once `ready`, generate images or videos with the built-in **App Mode** form or standard **Graph Mode** canvas!
4. When finished, click **停止 (Stop)** or let the auto-stop timer release compute automatically.

---

## 🤖 Guide for Autonomous AI Agents (Claude Code, Cursor, OpenClaw, Codex)

AI coding agents can autonomously boot, configure, and operate the gateway on `localhost`:

### 1. Health & State Inspection
```bash
# Probe if local gateway is already running
curl -s http://localhost:8080/_gate/healthz
# Returns: {"ok": true, "state": "sleeping"}

# Inspect available profiles
curl -s http://localhost:8080/_gate/profiles
# Returns: ["moss-image", "flux-klein", "wan-ltx-video", "sd-latest", "qwen-image"]
```

### 2. Programmatic Wake & GPU Control
```bash
# Wake with FLUX.2 Klein profile
curl -s -X POST http://localhost:8080/_gate/wake \
  -H "Content-Type: application/json" \
  -d '{"profile": "flux-klein"}'

# Monitor boot state
curl -s http://localhost:8080/_gate/state
```

### 3. Zero-Auth Stdio MCP Integration
Because `AUTH_DISABLED=true` is enabled, the agent requires **zero service tokens and zero cookies**.

Configure Claude Desktop (`claude_desktop_config.json`) or Cursor MCP settings:
```json
{
  "mcpServers": {
    "comfy-local": {
      "command": "ckit",
      "args": ["mcp", "--gate-url", "http://localhost:8080"]
    }
  }
}
```

Now the agent has direct access to tools:
- `gate_status`: Check remaining session seconds and CU balance.
- `gate_wake`: Wake or switch profiles on demand.
- `gate_txt2img`: Generate images directly from natural language prompts.
- `gate_img2img`: Edit images or animate first frames into videos.
- `gate_stop`: Immediately release the cloud GPU to eliminate idle costs.

### 4. CLI Execution via `ckit`
```bash
# Text-to-Image via FLUX.2 Klein
ckit --gate-url http://localhost:8080 generate \
  --prompt "A sleek cyberpunk hacker workstation, neon rim light, 8k octane render" \
  --profile flux-klein

# Video generation via Wan 2.2 TI2V
ckit --gate-url http://localhost:8080 generate \
  --prompt "Cinematic camera orbiting an ancient temple in the clouds, golden hour" \
  --profile wan-ltx-video

# Release compute immediately
ckit --gate-url http://localhost:8080 stop
```

---

## 🛠️ Troubleshooting

- **Port Conflict (8080 in use)**: Set `PORT=8090` in `.env` and `PUBLIC_HOST=localhost:8090`.
- **Permission Denied on `./data`**: Run `chmod -R 777 ./data` if running across mixed host/Docker user permissions.
- **Colab Auth Expired**: Re-run `docker run --rm -it -v $(pwd)/data:/data -e HOME=/data/home ghcr.io/firetable/comfy-colab-kit:latest colab sessions`.
