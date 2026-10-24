# Security & Threat Model

The gateway fronts a cloud GPU instance that incurs Compute Unit costs and executes user-defined diffusion graphs. Comfy Colab Kit enforces security across three strictly isolated perimeters:

---

## 1. 3-Tier Defense-in-Depth Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│ 1. EDGE PERIMETER: Cloudflare Zero Trust Access                        │
│    • Public domain protected by mandatory Email OTP / SSO              │
│    • Gateway cryptographically verifies RS256 JWT, AUD tag, and issuer │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Strips incoming user credentials
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2. GATEWAY PERIMETER: comfy-gate (Linux UID 10001, Read-Only FS)       │
│    • Anti-crawler shield: Blocks scrapers with 403 & X-Robots-Tag      │
│    • Enforces single-instance concurrency lock (Session.lock)          │
│    • Evaluates auto-stop, quiet hours, and CU budgets pure functions   │
│    • Only holder of Cloudflare Service Token (secrets/cf_service.json) │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Stdin JSON pipe over encrypted tunnel
┌───────────────────────────────────▼────────────────────────────────────┐
│ 3. VM PERIMETER: Google Colab L4 Runtime Kernel                        │
│    • Tunnel host accepts ONLY the Gateway's Service Token              │
│    • Tokens received on stdin; live in memory only (never on disk)     │
│    • Kernel watchdog self-terminates if heartbeat lost for 20 minutes  │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Attack Vector & Mitigation Matrix

| Potential Attack Vector | Severity | Architectural Mitigation |
| :--- | :--- | :--- |
| **Bypassing Gateway to reach ComfyUI directly** | High | `COLAB_TUNNEL_HOST` is protected by a separate Cloudflare Access Application requiring a Service Token. Only `comfy-gate` possesses this token. Direct browser access is rejected with HTTP 403 at Cloudflare's edge. |
| **Malicious or Unauthorized Wake Spamming** | High | In-flight wake operations are strictly serialized under `Session.lock`. Concurrent wake calls safely join the existing session without spinning up additional VMs. Non-owners cannot trigger cold profile switches. |
| **Web Crawlers / Search Engines Scraping Gateway** | Medium | `apps/gate/app.py` blocks known web crawlers with 403 Forbidden, serves `Disallow: /` on `/robots.txt`, and attaches `X-Robots-Tag: noindex, nofollow, noarchive, nosnippet` to every response. |
| **Exfiltration of Tokens via Colab VM Disk or Logs** | High | Bootstrap secrets (`CF_TUNNEL_TOKEN`, `HF_TOKEN`) are passed into `colab exec` via **stdin as JSON**. They are never written to disk, never placed in command-line arguments (`argv`), and never printed in logs. |
| **Runaway GPU Spend from VPS Host Crash** | High | The in-kernel watchdog (`packages/colab/setup.py`) monitors heartbeat pings. If the gate becomes unreachable for `WATCHDOG_GATE_LOST_MIN`, the VM triggers `runtime.unassign()` autonomously. |
| **Accidental Credential Commit to Git** | Critical | `.env`, `secrets/*`, and `data/*` are ignored in `.gitignore`. Secrets must use `*_FILE` pointers. Pre-commit hooks execute `scripts/check-secrets.sh` and CI runs Gitleaks on every commit. |

---

## 3. Secret Management Rules

1. **Files only, never environment strings**: All credentials reside in `secrets/` with strict `0600` permissions (directory `0700`). Environment variables only hold path pointers ending in `_FILE`.
2. **Never vendor secrets**: `.env`, `secrets/`, and `data/` are strictly ignored by Git.
3. **Automated Scanning**:
   - `scripts/check-secrets.sh` scans tracked and staged files for token patterns and private keys.
   - Gitleaks runs automatically in GitHub Actions CI on every commit and pull request.

---

## 4. Development & Disabling Authentication (`AUTH_DISABLED`)

For local testing or private networks where the gateway is protected by an alternative security layer (e.g. Tailscale or private LAN):
- Setting `AUTH_DISABLED=true` in `.env` bypasses Cloudflare Access JWT validation entirely and treats all incoming requests as an authorized owner (`dev@localhost`).
- **Warning**: Do NOT expose a gate with `AUTH_DISABLED=true` directly to the public internet without an external authentication barrier, as anyone with the URL could wake the Colab VM and consume compute units.
- In production, always leave `AUTH_DISABLED=false` (the default) and secure `PUBLIC_HOST` behind Cloudflare Access, or use Bearer token authentication (`secrets/agent_token`).

---

## 5. Vulnerability Reporting

Please report security issues privately to the repository maintainers via GitHub Security Advisories rather than opening a public issue.
