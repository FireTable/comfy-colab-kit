# HTTP API

Full machine-readable spec: [`apps/gate/openapi.yaml`](../apps/gate/openapi.yaml) (OpenAPI 3.1).
All routes except `/_gate/healthz` and `/_gate/static/*` require a Cloudflare Access session
(`Cf-Access-Jwt-Assertion`) or `Authorization: Bearer <agent token>`.

> [!NOTE]
> **Proxy Passthrough**: The routes listed below are the Gate's control endpoints (`/_gate/*`). When `state == "ready"`, all other HTTP routes (e.g. `POST /prompt`, `GET /history/{id}`, `GET /view`) and WebSocket connections (`/ws`) are proxied directly to the native ComfyUI instance running inside the Colab VM.

| Method & path | Description | Responses |
|---|---|---|
| `GET /_gate/healthz` | liveness, public | `200 {"ok": true, "state": …}` |
| `GET /_gate/status` | shared session status (see below) | `200` |
| `GET /_gate/events` | SSE stream: `state`, `progress`, `log`, `warn` | `200 text/event-stream` |
| `GET /_gate/profiles` | profiles, default, load errors | `200` |
| `POST /_gate/wake` | `{"profile"?: id, "workflows"?: [ids]}` start or join | `202` started, `200 {"joined": true}`, `404 unknown_profile`, `409 <refusal>`, `403` |
| `POST /_gate/fetch` | `{"workflows": [id, ...]}` download those workflows' models onto the running VM (on demand; idempotent, existing files are skipped). Progress in `progress.download`, result in `models.on_demand` / `models.fetch_error` | `202`, `400`, `404` unknown workflow, `409` `not_ready` / `fetch_in_progress`, `403` |
| `POST /_gate/switch` | `{"profile": id}` cold switch (stop → wake) | `202`, `404`, `409`, `403` |
| `POST /_gate/stop` | stop the VM | `200 {"state": …}`, `403` |
| `POST /_gate/extend` | add `EXTEND_HOURS` | `200` status, `409 extend_limit`, `403` |
| `*` anything else | proxied to ComfyUI when `ready`; otherwise `/` → wake page, `/api`, `/ws`, `/prompt`, `/view` → `503 asleep`, rest → redirect `/` | |

Errors are JSON: `{"error": "<code>", "message": "…"}`.

## Status object

```json
{
  "state": "downloading", "profile": "moss-image", "reason": "", "since": 1761200000.0,
  "session_started": 1761199900.0, "extends_used": 0, "busy": true,
  "progress": {"stage": "download", "download": {"done_gb": 8.2, "total_gb": 26.3, "files_done": 3, "files_total": 9, "gbps": 0.41}},
  "remaining_s": 21300, "warn": "", "can_extend": true,
  "cu": {"today": 0.4, "month": 12.1, "balance": 88.0, "per_hour": 1.71},
  "clients": 2, "default_view": "app",
  "you": {"email": "you@example.com", "owner": true}, "owner_only": ["switch", "stop", "extend"], "version": "0.1.0"
}
```

`progress.stage` is the last `KIT_STAGE`; other keys are the latest `KIT_PROGRESS <key> <json>` payloads from `colab/setup.py`.
