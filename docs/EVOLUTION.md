# Project Evolution — Comfy Colab Kit

A chronicle of how Comfy Colab Kit went from a single working commit to a complete
lifecycle gateway, reconstructed from the 113-commit history
(2026-10-23 → 2026-10-25, single author `FireTable`).

Every milestone below cites the commits that created it. The narrative is organised by
*what changed*, not by commit order, because the history interleaves features with the
fixes they generated.

<p align="center">
  <img src="../assets/architecture/flow.svg" width="100%" alt="Comfy Colab Kit request path, 10-state lifecycle, and auto-stop gates" /><br/>
  <i>The system these milestones produced — request path, 10-state lifecycle, and the five auto-stop gates</i>
</p>

---

## Milestone 0 — The premise (10-23 21:12 → 21:48)

`d0d3c94` initial commit: `comfy-gate`, Colab setup script, three profiles, and the full
docs set — including `docs/SECURITY.md`, which from day one defined the three-perimeter
model (Cloudflare Access → gateway → Colab VM) that the project still follows.

The architectural bet was stated immediately and never revised:

> The Gate's Essential Role: `comfy-gate` is fundamentally a **lifecycle, cold-start, and
> auto-shutdown gateway** — it does **NOT** reimplement ComfyUI.

Everything since has been elaboration of that single idea.

| Commit | Change |
|---|---|
| `d0d3c94` | Initial monorepo: gate, Colab runtime, profiles, docs |
| `a96086c` | Container named `comfy-gate`, joins `langgraph-app_default`, adds `hf_token` secret |
| `be2f80f` | Pin compose project name |
| `34fb5f2` | Bind-mount `examples/`, `profiles/`, `colab/` read-only; reload profiles per wake |
| `5e3c69e` | Gitignore local deploy notes |
| `fa4cb93` | Parse colab-cli 0.7 `sessions` format; `usage` for balance; unbuffered exec output |
| `d07d29c` | Drop client `Sec-WebSocket-*` headers on the backend WS handshake |

---

## Milestone 1 — Cold start becomes observable (10-23 22:00 → 23:10)

The first night was spent making a 25 GB download survivable and visible.

**Per-file download supervision.** `f01e89a` gave every HuggingFace file its own child
process with a stall watchdog, the ability to `SIGSTOP` background downloads while a
prompt is running, and byte-based progress. `fc10331` added 429/Xet backoff.
`8641caf` unwrapped `{prompt: ...}` smoke-test bodies and surfaced HTTP error bodies.

The stall timeout was tuned three times — 90 s → 45 s (`f01e89a`) → 30 s
(`9acb1d3`) — as the download pipeline matured.

**Core-first ordering.** `6c294d0` made core models download first while optional ones
wait, and reported bytes-on-disk throughput rather than a fake percentage.

**On-demand model fetching.** `b2f76d9` made the default workflow the only one fetched on
wake; everything else became opt-in via `POST /_gate/fetch`, with `model_aliases` letting
many workflows share one base checkpoint. `afe4d3d` applied this to moss-image: one shared
SFT bf16 base + Qwen3.5 int8 text encoder + Flux2 VAE.

**The wake page.** `f131fba` replaced the placeholder with a centred card, an aurora WebGL
background, a stepper with ETA, and a console. `7a9a459` then wired byte-level fields
(core bytes, rate, ETA, paused, restarts) into the page, and `06706a3` kept identity
across SSE state events with a 10-second status resync.

---

## Milestone 2 — Monorepo restructure (10-24 00:29 → 00:42)

Two commits reshaped the repository:

- `3ff9bb3` organised it into `apps/` + `packages/` + `profiles/` + `docs/`, and added the
  agent runbook.
- `2d11936` flattened `apps/gate` and **eliminated every symlink** for a clean release.

`a7f2b39` then fixed packaging so web assets and `openapi.yaml` ship inside the wheel.

---

## Milestone 3 — The showcase site and the visual system (10-23 23:06 → 10-24 03:07)

The project grew a public face alongside its engine.

| Commit | Change |
|---|---|
| `71b748a` | Static promo website |
| `0d9c244` | shadcn-style select, zero-RTT initial state, i18n, online dedup |
| `0ff7cc6` | Colour palette overhaul, i18n, comparison section, spotlight-fade fix |
| `cddf1a6` | Theme-flash resolution, button asymmetry, nav overlap, asset cache-busting |
| `c70ca58` | Dynamic architecture simulation on the site, benchmark metrics |
| `1ad3bc9` | lenis smooth damping + smart section snap |
| `820b580` | Replaced stepped wheel snapping with Apple-like physical lerp inertia |

Meanwhile the gate UI caught up: `4a76004` eliminated button inner-pill artefacts and
harmonised the light theme across gate and site; `5f8d22e` fixed container repo-root
resolution, pre-injected logs during SSR, and bind-mounted web assets.

---

## Milestone 4 — Agent access becomes a first-class citizen (10-24 02:52 → 10-24 19:26)

This is the milestone that turned the gateway into something an agent could drive.

**MCP server.** `e413ef8` added `packages/cli/mcp_server.py` — stdio JSON-RPC 2.0 exposing
`gate_status`, `gate_wake`, `gate_generate`, `gate_stop` — together with anti-bot controls
and a documentation rewrite.

**Token-based access.** `74f94eb` added query/cookie token support, hardened the proxy
User-Agent, and fixed zero-client auto-stop. `1e2d0bd` made owner-only control the
**default**, with `a799673` restricting shutdown to the session initiator or an owner.

**SQLite tokens and RFC 8628 device flow.** `9174970` introduced `agent_tokens` in SQLite,
the RFC 8628 device-authorisation endpoints, and Colab keepalive. `f9cf6a9` then fixed a
401 auth-lookup bug and added a web token generator.

**The shape of the surface area today:**

```
POST /api/auth/device/code      → device_code + user_code
GET  /auth/device?code=XXXX-XXXX → human approves in browser
POST /api/auth/device/token      → access_token (Bearer)
```

---

## Milestone 5 — Embedding ComfyUI, not replacing it (10-24 11:46 → 13:43)

`32aef6e` embedded ComfyUI in an iframe with a native glassmorphic floating dock;
`0c74eba` adopted the site's header and iframe layout and replaced the floating dock.

The accompanying UI work was unusually fine-grained — `c761f00`, `62669a0`, `28d6f18`,
`88c66b3`, `fcca8cc`, `8de1e96`, `6390a06`, `260b8b0` — each chasing one frame of layout
jank (vertical centring, console expansion shift, card offset). `b85cb42` and `f0f3fe7`
made default-workflow auto-load reliable through the Cloudflare proxy.

`4c64ba8` stripped Cloudflare Access cookies before forwarding to the backend so the
client's `CF_Authorization` cookie was not clobbered.

---

## Milestone 6 — Performance and reliability hardening (10-24 04:16 → 23:43)

| Commit | Change |
|---|---|
| `9acb1d3` | `hf-transfer` Rust chunked downloads, shorter stall timeout |
| `b84cbbe` | 44 px touch targets, DOM-update optimisation, mobile nav |
| `cd7af85` | Monotonic download progress, smooth byte formatting |
| `35c0a36` | Blended download ETA to prevent stall spikes |
| `7bd38da` | Defer workflow auto-load until the canvas is mounted (fixes null-canvas toasts) |
| `944d8b0` | Keep the Colab VM alive: request-touch middleware, Origin header, extended watchdog |
| `eed07b2` | Two-phase watchdog confirmation + CLI-backed heartbeat channel |
| `cd3960b` | Auto-reconcile a disconnected Colab VM on backend probe failure |
| `17fdcc5` | Retry the colab exec handshake, fix container mount sync |

---

## Milestone 7 — Docs, security posture, and deployment (10-24 14:01 → 18:14)

`66de9fc` added `docs/ACCESS.md` and standardised the Docker network. `07a6356` and
`1e31416` documented the **dual-host bypass** requirement and the symmetry rule for it.
`347313f` documented human vs agent Cloudflare Access flows. `d1fc1dc` documented the
unified multi-domain Access application — the pattern that finally let one login cover both
the public host and the Colab tunnel host.

---

## Milestone 8 — Owner panel and metric masking (10-24 22:37 → 10-25 00:15)

`125c2f1` relocated session metrics and the stop action into an owner-only user panel.
`abca61e` added the test that proves a non-owner receives masked `cu` and `woken_by`.
`f296f2b` aligned the panel's metrics with the wake page. `8afb432` set the default
preset overrides for moss-image.

---

## Milestone 9 — The agent skill and one-click onboarding (10-25 01:06 → 02:35)

The most recent phase, and the one that produced the screenshots now in the README.

| Commit | Change |
|---|---|
| `1baace2` | One-click "Copy Prompt for AI Agent", `gate_txt2img` tool, Cloudflare Access bypass for MCP |
| `f743084` | Allow unauthenticated `GET /mcp` for server discovery |
| `ed37fb2` | Explicit User-Agent in the CLI (fixes Cloudflare Error 1010) |
| `eb305a7` | `CF_Authorization` cookie fallback |
| `a0d76da` | Generalise the agent prompt to `~/.agents`; universal MCP config |
| `4aedec7` | Delete agent tokens completely instead of soft-disabling |
| `eb16969` | Reuse an unused agent token; persist the plain token for copy-prompt |
| `192ec62` | Fix App-mode form titles, expand options, add the `/comfy-colab-kit` skill |
| `d64c7d5` | Image-upload passthrough; document workflow queries with `ckit` |

---

## Milestone 10 — Documentation as a product (10-25 02:48 → 03:53)

`6c1e4b1` added `docs/MCP.md` and streamlined the user popover. `caca2b4` and `e6a917a`
added retina-resolution UI preview screenshots. `7562ae5` introduced the AI-agent session
gallery with a comparison table, and `57cf37a` normalised both agent screenshots to an
identical 1400×1400 canvas so the gallery row lines up without depending on CSS.

---

## The shape of the history

```
10-23 21:12  ┃ ■ 0  premise — gateway, not a reimplementation
10-23 22:00  ┃ ■ 1  cold start becomes observable
10-24 00:29  ┃ ■ 2  monorepo restructure, symlinks removed
10-24 02:52  ┃ ■ 3  showcase site + visual system
10-24 04:16  ┃ ■ 4  agent access: MCP, tokens, device flow
10-24 11:46  ┃ ■ 5  ComfyUI embedded in an iframe
10-24 18:01  ┃ ■ 6  performance & reliability hardening
10-24 22:37  ┃ ■ 7  owner panel & metric masking
10-25 01:06  ┃ ■ 8  agent skill & one-click onboarding
10-25 02:48  ┃ ■ 9  documentation as a product
```

**Commit-type distribution:** 42 `fix` · 30 `feat` · 13 `docs` · 3 `deploy` · 3 `chore` ·
2 `style` · 2 `refactor` · 1 `test` · 1 `perf`.

The ratio is telling: **42 fixes against 30 features**. More than a third of the history
is the cost of making a moving target work — layout jitter, Cloudflare error codes,
WebSocket handshake conflicts, HTTP/2 upload races. The features are what the project
*is*; the fixes are what it *cost*.

---

## What the history says about the design

**1. The gateway never became a ComfyUI.** `d0d3c94` committed to the proxy/lifecycle
role and every later commit deepened it rather than diluting it. The gate today is
2,901 lines across nine modules — routing, auth, state machine, budget rules, device
flow — and zero lines of diffusion. `apps/gate/app.py` (762) and `session.py` (645) hold
the bulk; `stopper.py`, the whole auto-stop policy, is 92 lines of pure functions.

**2. Idempotency came early and never had to be re-litigated.** `b2f76d9` (on-demand
models) and `9cb8be7` (zero-client auto-stop) both assume that a second caller joins
rather than duplicates. The `Session.lock` is load-bearing.

**3. Failure modes were treated as first-class.** Stall watchdogs (45 s), probe-failure
reconciliation, two-phase watchdog confirmation, orphan-VM sweeping, mount-sync retries —
the project spends a disproportionate share of its history on *not losing the user's money*
while a VM is half-alive.

**4. Security was layered, not retrofitted.** Zero Trust from hour one, service-token
backend from hour one, owner-only control made the **default** rather than an opt-in
(`1e2d0bd`), and metric masking got its own test (`abca61e`).

**5. The agent surface grew last, and grew into the existing seams.** MCP did not need a
new execution path — it rides `/_gate/*`, which the browser already used. That is why
the gateway could stay small while the agent surface grew to five tools over HTTP
(`gate_status`, `gate_wake`, `gate_txt2img`, `gate_generate`, `gate_stop`) and four over
stdio (`gate_txt2img` is HTTP-only).

---

## Reference

- Architecture: [`docs/ARCHITECTURE.md`](ARCHITECTURE.md)
- Auto-stop rules: [`docs/AUTO_STOP.md`](AUTO_STOP.md)
- Agent integration: [`docs/AGENT_SKILL.md`](AGENT_SKILL.md) · [`docs/MCP.md`](MCP.md)
- Threat model: [`docs/SECURITY.md`](SECURITY.md)
- Live diagram: [`../assets/architecture/flow.svg`](../assets/architecture/flow.svg) ·
  [`../assets/architecture/flow.zh.svg`](../assets/architecture/flow.zh.svg) —
  generated by `scripts/build-architecture-svg.py` (never hand-edit the SVGs)
