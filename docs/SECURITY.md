# Security

## Threat model

The gate fronts a GPU VM that costs money and runs arbitrary ComfyUI graphs. Goals: only allow-listed people
and your agents can reach it; nobody can reach ComfyUI directly; no secret ever lands in git, images, logs,
argv or Colab disk; a dead VPS cannot leave a VM billing forever.

## Identity

| Layer | Check |
|---|---|
| Cloudflare Access on `PUBLIC_HOST` | login (OTP / Google / GitHub) against your Access policy |
| Gate (`gate/auth.py`) | verifies `Cf-Access-Jwt-Assertion` on **every** request: RS256 only, signature against `https://<CF_ACCESS_TEAM_DOMAIN>/cdn-cgi/access/certs` (cached JWKS), `aud == CF_ACCESS_AUD`, `iss == https://<team domain>`, expiry; then the e-mail must be in `ALLOWED_EMAILS` |
| Agents | `Authorization: Bearer <token>` compared in constant time with `AGENT_TOKEN_FILE` |
| Owners | `OWNER_EMAILS` (empty = everyone allowed is an owner); actions in `OWNER_ONLY_ACTIONS` need an owner. Agents count as owners |
| `COLAB_TUNNEL_HOST` | separate Access app that admits **only** the gate's service token; the gate adds `CF-Access-Client-Id/Secret` and strips the user's Access headers and cookies |

`AUTH_DISABLED=true` exists for local development only; never set it on a public host.

## Secrets

| Secret | Where it lives | How it is used |
|---|---|---|
| Access service token | `secrets/cf_service.json` → `CF_SERVICE_TOKEN_FILE` | gate → backend headers |
| Tunnel token | `secrets/colab_tunnel_token` → `CF_TUNNEL_TOKEN_FILE` | sent to Colab on stdin, written 0600 for cloudflared |
| Agent token | `secrets/agent_token` → `AGENT_TOKEN_FILE` | bearer auth |
| HF read token (optional) | `HF_TOKEN_FILE` | sent to Colab on stdin for gated / rate-limited downloads |
| colab CLI OAuth | `COLAB_TOKEN_DIR` (data volume) | written by `colab` login |

Rules: secrets are files (0600, folder 0700) mounted as Docker secrets; `.env` only holds `*_FILE` paths;
`secrets/`, `.env` and `data/` are git-ignored; profiles reject secret-like keys and values; secrets go to
Colab as JSON on stdin of `colab exec` and stay in kernel memory, never in argv, the uploaded tarball or logs.

## Scanning

- `scripts/check-secrets.sh` — fast local scan of tracked / staged files (token patterns, private keys,
  `secrets/` paths); used by pre-commit.
- `.gitleaks.toml` + `.github/workflows/secret-scan.yml` — gitleaks on every push / PR.

## Cost safety

Wake admission and auto-stop rules ([AUTO_STOP.md](AUTO_STOP.md)), the orphan sweep, and the Colab-side
watchdog that releases the VM when the gate's heartbeat disappears for `WATCHDOG_GATE_LOST_MIN`.

## Reporting

Please report vulnerabilities privately to the repository owner rather than in a public issue.
