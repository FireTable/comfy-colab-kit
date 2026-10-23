# Agent Skill: comfy-colab-kit gate (`comfy_colab_gate`)

This document defines an **Agent Skill** for autonomous agents (Claude Code, OpenClaw, Cursor agents, …)
that need a ComfyUI GPU backend on demand: wake the shared Colab L4 session, wait until ComfyUI is ready, use
the standard ComfyUI API through the gate, and stop it when done.

---

## 1. Skill metadata

- **Name**: `comfy_colab_gate`
- **Description**: Wake, monitor, use and stop a ComfyUI-on-Colab session behind a comfy-colab-kit gate.
- **Interface**: HTTPS JSON API ([API.md](API.md), [`gate/openapi.yaml`](../gate/openapi.yaml)) or `cli/ckit`.
- **Machine index**: [`llms.txt`](../llms.txt)

## 2. Credentials (provided by the operator)

| Item | How the agent receives it |
|---|---|
| Gate URL | `CKIT_URL=https://<PUBLIC_HOST>` |
| Agent token | file path in `CKIT_TOKEN_FILE` → sent as `Authorization: Bearer …` |
| Cloudflare Access service token for `PUBLIC_HOST` | `CF_ACCESS_CLIENT_ID` + file path in `CF_ACCESS_CLIENT_SECRET_FILE` (the operator adds a *Service Auth* rule for it to the public Access app) |

Never print, log or commit these values. Agents are treated as owners.

## 3. Tool surface

```bash
cli/ckit status --json                       # state, profile, remaining_s, cu, …
cli/ckit profiles --json                     # available profiles + workflows
cli/ckit wake --profile moss-image --wait    # start or join; blocks until ready/failed
cli/ckit wake --profile moss-image --workflow kijai-4step   # also install an optional workflow
cli/ckit extend                              # +EXTEND_HOURS (limited)
cli/ckit switch qwen-image                   # cold switch (stop → wake)
cli/ckit stop
```

Raw HTTP equivalents: `GET /_gate/status`, `GET /_gate/profiles`, `POST /_gate/wake`, `POST /_gate/extend`,
`POST /_gate/switch`, `POST /_gate/stop`; live progress on `GET /_gate/events` (SSE).

## 4. Invocation strategy

1. `status` first. If `state == "ready"` and the profile fits, use it — the session is shared with humans.
2. Otherwise `wake`. `202` = you started it, `200 {"joined": true}` = someone else did (their profile wins;
   switching would interrupt them — only do it if the operator allows).
3. `409` reasons: `quiet_hours`, `daily_budget`, `monthly_budget`, `low_balance` → do not retry; report to
   the user. `failed` state → read `reason`, wake again once.
4. Cold start typically takes several minutes (VM allocation + downloads); poll `status` every 10–20 s or
   follow the SSE stream. Treat `BOOT_TIMEOUT_MIN` as the upper bound.
5. When `ready`, call ComfyUI's normal API through the gate (`POST /prompt`, `GET /history/<id>`,
   `GET /view?…`, `ws /ws`) with the same headers.
6. When finished and no human is using it (`clients == 0`), `stop`. Otherwise leave it to auto-stop.

## 5. Guarantees

- Concurrent wakes are idempotent: exactly one VM is created.
- The VM stops automatically on idle, session limit, budgets or quiet hours ([AUTO_STOP.md](AUTO_STOP.md)).
- Secrets never appear in API responses.
