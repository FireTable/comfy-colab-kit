# Agent Skill: comfy-colab-kit gate (`comfy_colab_gate`)

This document defines an **Agent Skill** for autonomous agents (Claude Code, OpenClaw, Cursor agents, …)
that need a ComfyUI GPU backend on demand: wake the shared Colab L4 session, wait until ComfyUI is ready, use
the standard ComfyUI API through the gate, and stop it when done.

---

## 1. Skill metadata

- **Name**: `comfy_colab_gate`
- **Description**: Wake, monitor, use and stop a ComfyUI-on-Colab session behind a comfy-colab-kit gate.
- **Interface**: HTTPS JSON API ([API.md](API.md), [`apps/gate/openapi.yaml`](../apps/gate/openapi.yaml)) or `packages/cli/ckit`.
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
packages/cli/ckit status --json                       # state, profile, remaining_s, cu, …
packages/cli/ckit profiles --json                     # available profiles + workflows
packages/cli/ckit wake --profile moss-image --wait    # start or join; blocks until ready/failed
packages/cli/ckit wake --profile moss-image --workflow kijai-4step   # also install an optional workflow
packages/cli/ckit extend                              # +EXTEND_HOURS (limited)
packages/cli/ckit switch qwen-image                   # cold switch (stop → wake)
packages/cli/ckit stop
```

Raw HTTP equivalents: `GET /_gate/status`, `GET /_gate/profiles`, `POST /_gate/wake`, `POST /_gate/extend`,
`POST /_gate/switch`, `POST /_gate/stop`; live progress on `GET /_gate/events` (SSE).

## 4. Invocation strategy & ComfyUI Execution

1. **Check Status**: `GET /_gate/status`. If `state == "ready"` and the profile fits, reuse it — the session is shared with humans and other agents.
2. **Wake**: If `sleeping`, send `POST /_gate/wake {"profile": "moss-image"}`.
   - `202`: You triggered the boot task.
   - `200 {"joined": true}`: A session is already booting or active; you joined it idempotently.
3. **Handle Refusals**: `409` with reasons like `quiet_hours`, `daily_budget`, `monthly_budget`, or `low_balance` mean policy blocks execution — do not retry in a loop; notify the operator.
4. **Wait for Ready**: Poll `GET /_gate/status` every 10–15s or listen to the SSE stream `GET /_gate/events`. Wait until `state == "ready"`.
5. **Execute ComfyUI Workflows**:
   Once `ready`, all standard ComfyUI HTTP endpoints and WebSocket streams are proxied transparently:
   - **Queue a Prompt**:
     ```bash
     curl -s -X POST "https://<PUBLIC_HOST>/prompt" \
       -H "Authorization: Bearer <agent_token>" \
       -H "Content-Type: application/json" \
       -d '{"prompt": { ...standard_comfy_workflow_graph... }}'
     # Returns: {"prompt_id": "...", "number": 1, ...}
     ```
   - **Check Generation History**:
     ```bash
     curl -s "https://<PUBLIC_HOST>/history/<prompt_id>" \
       -H "Authorization: Bearer <agent_token>"
     ```
   - **Retrieve Generated Images**:
     ```bash
     curl -s "https://<PUBLIC_HOST>/view?filename=<filename>&subfolder=&type=output" \
       -H "Authorization: Bearer <agent_token>" -o output.png
     ```
6. **Release GPU**: When finished and no human users are connected (`clients == 0`), call `POST /_gate/stop`. Otherwise, let the gate's auto-stop idle timer (`IDLE_TIMEOUT_MIN`) safely reclaim the Colab VM.

## 5. Guarantees

- Concurrent wakes are idempotent: exactly one VM is created.
- The VM stops automatically on idle, session limit, budgets or quiet hours ([AUTO_STOP.md](AUTO_STOP.md)).
- Secrets never appear in API responses.
