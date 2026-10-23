# AGENT.md — VPS Deployment Runbook & Credential Guide

This runbook guides AI agents and engineers through deploying **Comfy Colab Kit** from scratch on a Linux VPS behind Cloudflare Access, backed by Google Colab GPU instances.

---

## 1. Prerequisites & Credential Procurement

If you or your organization do not yet possess the required accounts and credentials, follow this step-by-step acquisition guide.

### 1.1 Virtual Private Server (VPS)
- **Requirements**: Debian 12 / Ubuntu 22.04+ (x86_64 or aarch64), 1+ vCPU, 2GB+ RAM, Docker Engine with Compose V2.
- **Network**: A static public IPv4 or a private mesh IP (such as Tailscale). No open host GPU is needed.

### 1.2 Cloudflare Zero Trust & Access
Comfy Colab Kit uses Cloudflare Access to provide seamless email one-time passcode (OTP) authentication, and Cloudflare Tunnel to reach the remote Colab VM securely.
1. **Cloudflare Account & DNS**:
   - Register a domain or add an existing domain to Cloudflare.
   - Navigate to **Zero Trust** dashboard (free tier supports up to 50 users).
2. **Cloudflare Tunnel (`colab_tunnel_token`)**:
   - Go to **Networks** > **Tunnels** > **Create a tunnel**.
   - Select **Cloudflared**. Name it `comfy-colab`.
   - Copy the installation token. This value will be saved in `secrets/colab_tunnel_token`.
   - In the tunnel configuration, add a public hostname:
     - Subdomain: `comfy-colab` (e.g., `comfy-colab.yourdomain.com`).
     - Service: `HTTP` -> `localhost:8188` (ComfyUI backend default).
3. **Cloudflare Access Application**:
   - Go to **Access** > **Applications** > **Add an application** > **Self-hosted**.
   - Application Name: `Comfy Colab Gateway`.
   - Domain: e.g. `comfy.yourdomain.com`.
   - Policies: Add an allow rule (e.g. Email Ends With `@yourdomain.com` or specific email addresses).
   - Capture your **Team Domain** (e.g., `https://<team-name>.cloudflareaccess.com`) and **Application Audience (AUD) Tag** (found under Overview / Basic Info).
4. **Cloudflare Service Token (`cf_service.json`)**:
   - Go to **Access** > **Service Auth** > **Create Service Token**.
   - Name: `comfy-gate-to-colab`.
   - Duration: 1 year (or desired).
   - Copy the generated `Client ID` and `Client Secret`.
   - Under Access Applications, protect the `comfy-colab.yourdomain.com` tunnel host with an Access Application requiring this Service Token (or bypass rule for this Service Token).
   - Format saved locally in `secrets/cf_service.json`:
     ```json
     {
       "CF-Access-Client-Id": "YOUR_CLIENT_ID",
       "CF-Access-Client-Secret": "YOUR_CLIENT_SECRET"
     }
     ```
5. **Optional: Cloudflare Access Login Branding**:
   - Navigate to **Settings** > **Custom Pages** > **Login page**.
   - You can customize the logo, background color, and header text.
   - *Note*: This custom branding applies account-wide across all Access applications under your Zero Trust organization.

### 1.3 Google Colab Account & Compute Units (CU)
- **Account**: A standard Google account subscribed to **Google AI Pro**, **Colab Pro**, or **Colab Pro+** containing GPU Compute Units.
- **GPU Accelerator**: `L4` (24GB VRAM) is recommended for modern diffusion models (MOSS-Image, SD 3.5, Qwen-Image).

### 1.4 Hugging Face User Access Token (`hf_token`)
- Create an account at [huggingface.co](https://huggingface.co).
- Navigate to **Settings** > **Access Tokens** > **Create new token**.
- Type: **Read**.
- Save the token string in `secrets/hf_token`. This avoids strict download rate limits and grants access to community models.

### 1.5 Agent Bearer Token (`agent_token`)
- Generate a secure random alphanumeric token:
  ```bash
  openssl rand -hex 32 > secrets/agent_token
  ```
- This token allows CLI tools (`ckit`) and external orchestrators to interact with `/_gate/` endpoints securely without going through interactive browser login.

---

## 2. Directory & Secrets Setup

Comfy Colab Kit is structured as an engineering monorepo:

```
comfy-colab-kit/
├── apps/
│   ├── gate/         # Gateway backend & wake page web UI
│   └── site/         # Static showcase & documentation website
├── packages/
│   ├── colab/        # Setup script shipped to Colab VM (setup.py)
│   ├── cli/          # ckit CLI client
│   └── schemas/      # Profile JSON schema
├── profiles/
│   ├── examples/     # Default community profiles (moss-image, sd-latest, etc.)
│   └── custom/       # Custom user profiles
├── deploy/           # Dockerfile & docker-compose.yml
├── docs/             # Engineering & architecture specifications
├── secrets/          # gitignored secret files (mode 0600)
└── data/             # Persistent runtime state (session.json, cu_ledger.json, colab-cli)
```

### 2.1 File Permissions
The `comfy-gate` container runs under non-root UID `10001`. Ensure the data directory and secrets are accessible:

```bash
mkdir -p data secrets
chmod 700 data
chown -R 10001:10001 data secrets
chmod 600 secrets/*
```

### 2.2 Environment Configuration (`.env`)
Copy `.env.example` to `.env` and fill in your domain and Cloudflare values:

```bash
cp .env.example .env
```

Key fields to configure:
- `PUBLIC_HOST`: e.g. `comfy.yourdomain.com`
- `COLAB_TUNNEL_HOST`: e.g. `comfy-colab.yourdomain.com`
- `CF_ACCESS_TEAM_DOMAIN`: `https://<team-name>.cloudflareaccess.com`
- `CF_ACCESS_AUD`: `<Your Cloudflare Access Audience Tag>`
- `OWNER_EMAILS`: `["admin@yourdomain.com"]`
- `COLAB_ACCELERATOR`: `L4`
- `COLAB_SESSION_NAME`: `comfy`

---

## 3. Deployment & Container Orchestration

### 3.1 Start the Gate Container
The service is orchestrated via Docker Compose:

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

Check logs and health status:
```bash
docker compose -f deploy/docker-compose.yml ps
docker logs -f comfy-gate
```

### 3.2 Caddy Reverse Proxy Configuration
In your VPS reverse proxy (e.g. Caddy), append the gateway upstream:

```caddy
comfy.yourdomain.com {
    tls /etc/caddy/cert.pem /etc/caddy/key.pem
    encode zstd gzip

    reverse_proxy comfy-gate:8080 {
        flush_interval -1
        transport http {
            read_buffer 8192
        }
    }
}
```
*Note*: `flush_interval -1` is essential for Server-Sent Events (`/_gate/events`) and WebSocket streaming (`/ws`).

Reload Caddy:
```bash
caddy reload
```

---

## 4. Colab CLI Authentication

Authenticate `google-colab-cli` once inside the container. State is persisted in `/data/colab-cli`:

```bash
docker exec -it comfy-gate sh -c "XDG_CONFIG_HOME=/data/colab-cli colab login"
```
Follow the OAuth authorization prompt.

Verify authentication:
```bash
docker exec comfy-gate sh -c "XDG_CONFIG_HOME=/data/colab-cli colab sessions"
```
Should output: `[colab] No active sessions found on server.`

---

## 5. First Wake & Operational Verification

1. **Open Gateway in Browser**: Navigate to `https://comfy.yourdomain.com`.
2. **Access Login**: Enter your authorized email and submit the OTP.
3. **Inspect Sleeping State**: The UI loads with zero flicker, showing `ComfyUI is sleeping`, current online count, and profile options.
4. **Wake Session**: Click **⚡ Wake**.
   - Gate provisions an L4 GPU VM via `colab new`.
   - Packages and downloads core models with progress reported by byte count.
   - Runs automated smoke test.
   - Redirects to ComfyUI App mode upon reaching `ready`.
5. **Generate an Image**: Enter a prompt in App mode and click Generate.
6. **Switch Views**: Use the bottom-right toggle to view the node execution graph.
7. **Release Resources**: When finished, click **Stop · release GPU** in the header overlay or wait for auto-stop (idle / budget limit).

---

## 6. Security & Hard Rules

1. **Zero Secrets in Git**: Never commit `.env`, `secrets/*`, tokens, or private emails. Run `scripts/check-secrets.sh` before every commit.
2. **Single Global Session**: All users join the same global session managed by `Session.wake()` with mutual exclusion lock. Never start multiple VMs per gate.
3. **Non-Commercial**: Cloudflare Tunnel & Google Colab terms prohibit commercial multi-tenant resale. Use only for personal workflows, research, and trusted collaborators.
