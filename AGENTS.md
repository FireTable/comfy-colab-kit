# AGENTS.md — Comfy Colab Kit

Guide for AI coding agents working **on** this repository. To **use** a deployed gate (wake, generate, stop),
read [docs/AGENT_SKILL.md](docs/AGENT_SKILL.md) instead.

---

## 🧭 Monorepo Module Map

The repository is structured as an engineering monorepo with root compatibility symlinks:

| Path | Primary Module | Role | Notes |
|---|---|---|---|
| `apps/gate/` (`gate/`) | Gateway App | aiohttp gateway, auth middleware, background loop, state machine | `build_app(settings, colab, profiles, run_background)` is the test seam |
| `apps/gate/gate/web/` | Web UI | Wake page (`index.html`), `bg.js`, `overlay.js` (App ⇄ Graph via `Comfy.ToggleLinear`) | Zero-RTT SSR-like injection, no build step |
| `apps/site/` (`site/`) | Landing App | Promo marketing & architecture documentation showcase | Pure static HTML/CSS/JS (Cloudflare Pages) |
| `packages/colab/` (`colab/`) | VM Runtime | Runs in Colab kernel; prints `KIT_STAGE/KIT_PROGRESS/KIT_READY/KIT_FAILED` | stdlib + huggingface_hub only |
| `packages/cli/` (`cli/`) | CLI Tool | `ckit` command line utility for operators and agents | stdlib CLI |
| `packages/schemas/` (`schemas/`) | Schema Package | JSON schema (`profile.schema.json`) | Validates all profiles |
| `profiles/examples/` (`examples/`) | Community Profiles | `moss-image` (primary), `sd-latest`, `qwen-image` | Community workflows only |
| `profiles/custom/` | User Profiles | User-defined custom model profiles mount | Re-read dynamically on wake |
| `deploy/` | Deployment | Dockerfile, compose, Caddyfile snippet | Container run as uid 10001 |
| `docs/` | Documentation | Product & architecture guides, including `docs/AGENT.md` | English only, no secrets |
| `scripts/` | Tooling | `check-secrets.sh`, `validate-profile.py`, `cf-setup.sh` | Run before commit |
| `tests/` | Tests | Pytest test suite | Run with `python3 -m pytest -q` |

## 🔁 State machine

```
sleeping → allocating → installing → downloading → booting → smoke_testing → ready
   ▲            └──────────── any boot state ─── fail ──→ failed ──→ stopping ─┐
   └──────────────────────────────── stopping ←── stop (any non-sleeping) ─────┘
ready → switching → (downloading | booting | ready)          failed → allocating (retry)
installing|downloading → ready   (setup found ComfyUI already running on a reused VM)
```

Billing states (CU accrues): every boot state, `ready`, `switching`, `failed`.

## 🚨 Rules that must not be broken

1. **Zero secrets in git.** No tokens, keys, cookies, real e-mails or private hostnames in code, docs, tests or
   profiles. Secrets are `*_FILE` paths only. Run `scripts/check-secrets.sh` before every commit.
2. **The gate is the only holder of the Colab token** and the only caller of `colab new/stop`.
3. **One VM per gate.** All wakes go through `Session.wake()` under `Session.lock`; joining must stay the default.
4. **Verify the Access JWT** (RS256, AUD, issuer) before trusting any identity; owner checks use the JWT e-mail.
5. **Profiles reference, never vendor, models.** Workflows must be community-published with `source` + `license`;
   do not author workflows. Vendor a workflow file (unchanged, with `upstream_url` + `upstream_sha256`) only
   when it is Apache-2.0 or MIT; anything else (no license, GPL, non-OSI, gated) → reference by URL.
6. Keep `schemas/profile.schema.json`, `gate/profiles.py`, `docs/PROFILES.md` and tests consistent.
7. `docs/` contains finished product documentation only — no scratch notes, no TODO dumps.

## ⚠️ Known pitfalls

- ComfyUI rejects requests whose `Origin` host differs from `Host`; the proxy rewrites both to the backend host.
- SSE and `/ws` need streaming through Caddy (`flush_interval -1`, no read timeout).
- `colab exec` output stops when the gate restarts; the session re-attaches by probing the backend.
- App-mode metadata keys (`extra.linearData`, `extra.linearMode`) come from the ComfyUI frontend ≥ 1.41.13;
  pin `frontend.package` in profiles.
- macOS ships bash 3.2: shell scripts must avoid `mapfile`, `${var,,}` etc.

## 🧪 Tests & release

```bash
pip install -e ".[dev]"
python -m pytest -q                 # state machine, auth, auto-stop config, profiles/schema, shared session, app
python scripts/validate-profile.py  # all example profiles
bash scripts/check-secrets.sh       # must print "secret scan: clean"
```

Tests never call Google or Cloudflare: `tests/fakes.py::FakeColab` replaces the CLI.

## 📝 Commit conventions

English-only Conventional Commits: `<type>(<scope>): <imperative summary>` — e.g. `feat(gate): add extend endpoint`.
Stage files by explicit path (never `git add -A`).
