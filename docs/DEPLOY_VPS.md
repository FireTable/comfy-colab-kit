# Deploying on a VPS

The gate is one small container behind your existing Caddy. Cloudflare terminates TLS and Access at the
edge; Caddy uses a Cloudflare Origin certificate.

## Requirements

- A VPS with Docker + Compose and a running Caddy container on a Docker network (default name `caddy`,
  override with `CADDY_NETWORK`).
- A Cloudflare zone, Zero Trust enabled ([CLOUDFLARE.md](CLOUDFLARE.md)).
- A Google account with Colab compute units (Colab Pro / Pay-as-you-go) for L4.

## Steps

```bash
git clone <your fork> comfy-colab-kit && cd comfy-colab-kit
cp .env.example .env            # edit hostnames, CF_ACCESS_*, ALLOWED_EMAILS, OWNER_EMAILS, budgets
mkdir -p secrets data && chmod 700 secrets data && sudo chown 10001 data

# Cloudflare: tunnel + DNS + backend Access app + service token (writes secrets/, prints nothing secret)
ZONE_NAME=example.com COLAB_TUNNEL_HOST=comfy-colab.example.com CLOUDFLARE_API_TOKEN=… scripts/cf-setup.sh

openssl rand -hex 32 > secrets/agent_token
chmod 600 secrets/*

# one-time Google login for the colab CLI (state is kept in data/ → COLAB_TOKEN_DIR)
docker compose -f deploy/docker-compose.yml run --rm comfy-gate colab sessions

docker compose -f deploy/docker-compose.yml up -d --build
```

Add [`deploy/Caddyfile.snippet`](../deploy/Caddyfile.snippet) to your Caddyfile (replace the host with your
`PUBLIC_HOST`), reload Caddy, then open `https://PUBLIC_HOST`.

The snippet disables response buffering (`flush_interval -1`) and read timeouts so SSE progress and the
ComfyUI WebSocket stream correctly.

### Optional: Hugging Face token

Needed only for gated models (e.g. SD 3.5 Medium). Put a **read-only** token in `secrets/hf_token` (0600),
add it to the `secrets:` lists in `deploy/docker-compose.yml`, and set `HF_TOKEN_FILE=/run/secrets/hf_token`.

## Container hardening

Non-root user (uid 10001), read-only root filesystem with `/tmp` tmpfs, `no-new-privileges`, all
capabilities dropped, profiles mounted read-only, only port 8080 exposed to the Caddy network.

## Data

`data/` holds `state/session.json` (shared session), `state/cu_ledger.json` and the colab CLI login. Back it up
if you care about the ledger; deleting `session.json` while a VM runs is safe (the orphan sweep stops it).

## Upgrades

```bash
git pull && docker compose -f deploy/docker-compose.yml up -d --build
```

A restart during a boot re-attaches to the VM ([ARCHITECTURE.md](ARCHITECTURE.md#the-shared-global-session)).

## Local development

```bash
uv venv && uv pip install -e '.[dev]'
AUTH_DISABLED=true STATE_DIR=./data/state python -m gate.app   # no Cloudflare; never on a public host
pytest -q
```
