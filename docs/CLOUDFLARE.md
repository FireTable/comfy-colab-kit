# Cloudflare Zero Trust & Access Architecture

Comfy Colab Kit utilizes a **Dual-Hostname Zero Trust Architecture** that isolates public user authentication from the private backend compute VM.

---

## 1. Dual-Hostname Request Flow

```mermaid
flowchart LR
    subgraph PublicHost ["1. Public Host (e.g. comfy.yourdomain.com)"]
        User["User / Agent"] -->|HTTPS| PublicAccess["Cloudflare Access\n(Email OTP / GitHub SSO)"]
        PublicAccess -->|Proxied DNS| VPS_Caddy["VPS: Caddy Gateway\n(Verifies Access JWT)"]
    end

    subgraph BackendHost ["2. Backend Host (e.g. comfy-colab.yourdomain.com)"]
        VPS_Gate["comfy-gate Reverse Proxy\n(Injects Service Token)"] -->|HTTPS| BackendAccess["Cloudflare Access\n(Service Token Auth Rule)"]
        BackendAccess -->|Named Tunnel| ColabVM["cloudflared in Colab\n(ComfyUI :8188)"]
    end

    VPS_Caddy --> VPS_Gate
```

---

## 2. Hostname Responsibilities

### Public Host (`PUBLIC_HOST`)
- **DNS**: Proxied `A` or `AAAA` record pointing to your VPS public IPv4/IPv6. SSL/TLS encryption mode set to **Full (strict)**.
- **Access Application**: Self-hosted application covering `PUBLIC_HOST`. Configured with an **Allow** policy restricting access to authorized team emails or identity providers.
- **Audience Tag**: The Application AUD tag is saved in `.env` as `CF_ACCESS_AUD`. The gate cryptographically validates the `Cf-Access-Jwt-Assertion` header on every incoming request against Cloudflare's public JWKS keys.

### Backend Host (`COLAB_TUNNEL_HOST`)
- **DNS**: Proxied `CNAME` pointing to the remotely-managed Cloudflare Named Tunnel.
- **Access Application**: Self-hosted application covering `COLAB_TUNNEL_HOST`. Configured with a single **Service Auth** rule that permits access *only* when valid `CF-Access-Client-Id` and `CF-Access-Client-Secret` headers are present.
- **Isolation**: Nobody can access `COLAB_TUNNEL_HOST` directly through a web browser. Only `comfy-gate` holds the Service Token (`secrets/cf_service.json`), ensuring all traffic must pass through the gateway's state machine, rate limits, and authentication guards.

---

## 3. Automated Configuration with `scripts/cf-setup.sh`

The included setup script is fully idempotent. To run it, create a Cloudflare API Token with the following permissions:
- **Account** > **Cloudflare Tunnel**: `Edit`
- **Account** > **Access: Apps and Policies**: `Edit`
- **Account** > **Access: Service Tokens**: `Edit`
- **Zone** > **DNS**: `Edit`

Execute the script on your VPS:
```bash
ZONE_NAME="yourdomain.com" \
COLAB_TUNNEL_HOST="comfy-colab.yourdomain.com" \
CLOUDFLARE_API_TOKEN="your-api-token" \
scripts/cf-setup.sh
```

The script will automatically configure the tunnel, create the DNS CNAME, generate the service token, and write `secrets/colab_tunnel_token`, `secrets/cf_service.json`, and `secrets/cf_ids.json` with secure `0600` permissions.

---

## 4. How to Bypass Cloudflare Access (Dual-Host Bypass)

If you want to completely disable login prompts (for friends, internal testing, or open access), you **MUST** configure Bypass on **BOTH** Access Applications:

### Why Both Applications Must Be Bypassed:
1. **Frontend App (`PUBLIC_HOST`, e.g. `comfy-colab-kit`)**: Protects the gateway wake card. If not bypassed, users see the email OTP login prompt when first opening the site.
2. **Backend App (`COLAB_TUNNEL_HOST`, e.g. `comfy-colab-kit-backend`)**: Protects the Colab VM tunnel. Once ComfyUI boots (`state: ready`), the gateway embeds ComfyUI via an `<iframe>` pointing to `COLAB_TUNNEL_HOST`. If this backend app is not bypassed, the browser will be blocked from loading ComfyUI inside the iframe!

### Step-by-Step Configuration:
1. **Cloudflare Zero Trust Dashboard**:
   - Go to **Access** > **Applications**.
   - Open **App 1 (`comfy-colab-kit`)** > **Policies** > Add Policy:
     - Name: `Bypass-All`
     - Action: `Bypass`
     - Include: `Everyone` (Order: 1st)
   - Open **App 2 (`comfy-colab-kit-backend`)** > **Policies** > Add Policy:
     - Name: `Bypass-All`
     - Action: `Bypass`
     - Include: `Everyone` (Order: 1st)
2. **VPS Gateway Configuration**:
   - In `.env` on your VPS, enable `AUTH_DISABLED=true`:
     ```bash
     sed -i 's/^# *AUTH_DISABLED=.*/AUTH_DISABLED=true/' .env
     docker compose -f deploy/docker-compose.yml up -d
     ```
   - This instructs `comfy-gate` to accept requests without Cloudflare JWT assertions and grants all visitors owner privileges.

---

## 5. The Symmetry Rule: Restoring / Enabling Protection

Just as **disabling** access requires updating both applications, **enabling (or restoring) protection is strictly symmetrical**:

| Component | Target Host | Required Cloudflare Access Policies | Role in Protected Mode |
|---|---|---|---|
| **App 1: Frontend** | `PUBLIC_HOST`<br>(e.g. `comfy.yourdomain.com`) | • Policy 1: `Allow` for `ALLOWED_EMAILS` | Challenges visiting browsers with Email OTP / SSO. Issues JWT assertion. |
| **App 2: Backend** | `COLAB_TUNNEL_HOST`<br>(e.g. `comfy-colab.yourdomain.com`) | • Policy 1: `Service Auth` for Service Token<br>• Policy 2: `Allow` for `ALLOWED_EMAILS` | • `Service Auth`: lets VPS `comfy-gate` proxy traffic.<br>• `Allow`: lets authenticated browser iframe load ComfyUI. |
| **VPS Gateway** | `comfy-gate:8080` | `.env`: `AUTH_DISABLED=false`<br>(or remove/comment out `AUTH_DISABLED`) | Cryptographically verifies incoming `Cf-Access-Jwt-Assertion` against Cloudflare JWKS. |

> [!IMPORTANT]
> **Common Pitfall when Enabling**: If you forget to add Policy 2 (`Allow` for your email) on the Backend App, Cloudflare will accept the gateway's service token on the backend proxy, but the **browser's iframe will be rejected with 403 Forbidden** because Cloudflare Access will block the browser from loading the iframe! Always ensure the Backend App permits BOTH the Service Token and the authorized user emails.
