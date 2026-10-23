# Cloudflare setup

Two hostnames, two Access applications:

| Hostname | Points to | Access app | Who gets in |
|---|---|---|---|
| `PUBLIC_HOST` (e.g. `comfy.example.com`) | VPS (proxied A/AAAA record) → Caddy → gate | created in the dashboard | your Access policy (e-mails, IdP) |
| `COLAB_TUNNEL_HOST` (e.g. `comfy-colab.example.com`) | named tunnel → `http://127.0.0.1:8188` inside Colab | `comfy-colab-kit-backend` (by `cf-setup.sh`) | **only** the gate's service token |

## 1. Public host (dashboard)

1. DNS: proxied record for `PUBLIC_HOST` → VPS IP. SSL/TLS mode **Full (strict)**; create an Origin
   certificate and install it in Caddy (`/etc/caddy/certs/origin.pem|key`).
2. Zero Trust → Access → Applications → *Self-hosted* on `PUBLIC_HOST`, policy *Allow* your e-mails.
3. Copy the **Application Audience (AUD) tag** to `CF_ACCESS_AUD` and your team domain
   (`<team>.cloudflareaccess.com`) to `CF_ACCESS_TEAM_DOMAIN`.
4. Optionally restrict the VPS firewall to Cloudflare IP ranges.

## 2. Backend host (`scripts/cf-setup.sh`)

Idempotent; needs an API token with *Cloudflare Tunnel:Edit*, *Access: Apps and Policies:Edit*,
*Access: Service Tokens:Edit* and *Zone DNS:Edit*:

```bash
ZONE_NAME=example.com COLAB_TUNNEL_HOST=comfy-colab.example.com CLOUDFLARE_API_TOKEN=… scripts/cf-setup.sh
```

It creates or updates the remotely-managed tunnel (`TUNNEL_NAME`, default `comfy-colab`), a proxied CNAME (it
refuses to overwrite a record it did not create), a service token (`SVC_NAME`, default `comfy-gate`) and the
backend Access app with a single *Service Auth* policy. It writes `secrets/colab_tunnel_token`,
`secrets/cf_service.json` and `secrets/cf_ids.json` (0600) and never prints them.

## How a request flows

User → Access login on `PUBLIC_HOST` → gate verifies the JWT → gate calls `COLAB_TUNNEL_HOST` with the
service token → tunnel → ComfyUI. ComfyUI is never reachable without the service token, and the service token
never leaves the VPS.

## Rotation

Delete `secrets/cf_service.json` and re-run `cf-setup.sh` to rotate the service token; restart the gate.
Re-running also refreshes the tunnel token (picked up at the next wake).
