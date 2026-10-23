# Deploying on a Linux VPS

The gate runs as a lightweight, non-root Docker container behind your existing Caddy reverse proxy. Cloudflare terminates TLS and Zero Trust Access at the edge, while Caddy handles internal routing with a Cloudflare Origin Certificate.

---

## 1. System Requirements

- **VPS**: Debian 12 / Ubuntu 22.04+ (x86_64 or aarch64), 1+ vCPU, 2GB+ RAM.
- **Docker**: Docker Engine 24+ with Docker Compose V2.
- **Reverse Proxy**: A running Caddy container attached to a shared Docker network (default name: `caddy`).
- **Cloudflare Zone**: Domain onboarded to Cloudflare with Zero Trust enabled ([CLOUDFLARE.md](CLOUDFLARE.md)).
- **Google Account**: An active account with Google Colab Pro or Compute Units balance (L4 GPU).

---

## 2. Step-by-Step Deployment

```bash
# 1. Clone repository
git clone https://github.com/FireTable/comfy-colab-kit.git && cd comfy-colab-kit

# 2. Configure environment
cp .env.example .env
# Edit PUBLIC_HOST, COLAB_TUNNEL_HOST, CF_ACCESS_*, and OWNER_EMAILS

# 3. Create secrets and data directories
mkdir -p secrets data && chmod 700 secrets data
sudo chown -R 10001:10001 data secrets

# 4. Automate Cloudflare setup (Tunnel + DNS + Service Token)
ZONE_NAME="yourdomain.com" \
COLAB_TUNNEL_HOST="comfy-colab.yourdomain.com" \
CLOUDFLARE_API_TOKEN="your-cloudflare-token" \
scripts/cf-setup.sh

# 5. Generate agent authentication bearer token
openssl rand -hex 32 > secrets/agent_token
chmod 600 secrets/*

# 6. One-time interactive Google Colab OAuth login
docker compose -f deploy/docker-compose.yml run --rm comfy-gate colab login
# Follow the prompt and paste the verification code

# 7. Start the comfy-gate container
docker compose -f deploy/docker-compose.yml up -d --build
```

---

## 3. Caddy Reverse Proxy Configuration

Add the following block to your `/etc/caddy/Caddyfile`:

```caddy
comfy.yourdomain.com {
    tls /etc/caddy/certs/origin.pem /etc/caddy/certs/origin.key
    encode zstd gzip

    reverse_proxy comfy-gate:8080 {
        # CRITICAL: Disable response buffering for Server-Sent Events (SSE) and WebSocket streams
        flush_interval -1
        transport http {
            read_buffer 8192
        }
    }
}
```

> [!WARNING]
> **Why `flush_interval -1` is Mandatory**:
> Caddy buffers response chunks by default to optimize compression. If `flush_interval -1` is omitted:
> 1. Real-time boot logs and download speed updates on `GET /_gate/events` will buffer and appear frozen, only delivering in bursts every few minutes.
> 2. The ComfyUI WebSocket channel (`/ws`) may experience latency spikes or dropped node execution highlights.

Reload Caddy:
```bash
caddy reload
```

---

## 4. Verification & Troubleshooting Checklist

After deployment, run these checks to ensure all subsystems are functioning:

| Test | Command | Expected Output |
| :--- | :--- | :--- |
| **Container Status** | `docker compose -f deploy/docker-compose.yml ps` | `comfy-gate` status is `Up (healthy)` |
| **Liveness Probe** | `curl -s https://PUBLIC_HOST/_gate/healthz` | `{"ok": true, "state": "sleeping"}` |
| **Colab Auth Check** | `docker compose -f deploy/docker-compose.yml run --rm comfy-gate colab sessions` | `[colab] No active sessions found on server.` |
| **Anti-Bot Shield** | `curl -s https://PUBLIC_HOST/robots.txt` | `User-agent: * \n Disallow: /` |

### Troubleshooting Common Errors

- **Caddy returns `502 Bad Gateway`**:
  Ensure both Caddy and `comfy-gate` share the same Docker network. By default, `docker-compose.yml` expects an external network named `caddy`. Check via `docker network inspect caddy`.
- **Cloudflare returns `403 Forbidden` on tunnel requests**:
  Verify that the Client ID and Secret in `secrets/cf_service.json` match the Service Token authorized in the Cloudflare Access application protecting `COLAB_TUNNEL_HOST`.
- **Colab CLI displays `OAuth token expired or invalid`**:
  Re-authenticate by running `docker compose -f deploy/docker-compose.yml run --rm comfy-gate colab login`. Ensure `data/colab-cli` has proper permissions (`chown -R 10001:10001 data`).

---

## 5. Routine Upgrades

```bash
git pull
docker compose -f deploy/docker-compose.yml up -d --build
```
A restart during an active session cleanly re-attaches to the running Colab VM without terminating your work.
