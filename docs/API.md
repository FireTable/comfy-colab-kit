# REST API, SSE, and MCP Specification

Full machine-readable OpenAPI 3.1 specification: [`apps/gate/openapi.yaml`](../apps/gate/openapi.yaml).

---

## 1. Authentication

All routes except `GET /_gate/healthz`, `GET /robots.txt`, and `GET /_gate/static/*` require authentication via either:
1. **Cloudflare Access JWT**: Provided automatically by the browser in the `Cf-Access-Jwt-Assertion` header.
2. **Agent Bearer Token**: Provided in the HTTP header: `Authorization: Bearer <agent_token>`.

> [!NOTE]
> **Proxy Passthrough**: The routes listed below are the Gate's control endpoints (`/_gate/*` and `/_gate/mcp`). When `state == "ready"`, all other HTTP routes (e.g. `POST /prompt`, `GET /history/{id}`, `GET /view`) and WebSocket connections (`/ws`) are proxied directly to the native ComfyUI instance running inside the Colab VM.

---

## 2. Endpoint Catalog

| Method & Path | Description | Expected Responses |
|---|---|---|
| `GET /robots.txt` | Crawler policy: disallows all robots | `200 text/plain` |
| `GET /_gate/healthz` | Container health probe (public) | `200 {"ok": true, "state": "..."}` |
| `GET /_gate/status` | Current session state, telemetry, and CU ledger | `200` (see Status Object below) |
| `GET /_gate/events` | Real-time Server-Sent Events (SSE) telemetry stream | `200 text/event-stream` (`state`, `progress`, `log`, `warn`) |
| `GET /_gate/profiles` | Available model profiles, default selection, errors | `200` |
| `POST /_gate/wake` | Wake a profile or join in-flight boot `{"profile": "id"}` | `202` started, `200 {"joined": true}`, `409 <refusal>` |
| `POST /_gate/fetch` | Trigger on-demand download of optional workflows | `202`, `404`, `409` |
| `POST /_gate/switch` | Cold switch to another profile (stop -> wake) | `202`, `404`, `409` |
| `POST /_gate/stop` | Stop the Colab VM and release GPU immediately | `200 {"state": "stopping"}` |
| `POST /_gate/extend` | Extend session duration by `+EXTEND_HOURS` | `200`, `409 extend_limit` |
| `POST /_gate/mcp` / `POST /mcp` | Model Context Protocol JSON-RPC 2.0 tool interface | `200 application/json` |
| `*` (Any other path) | Proxied to ComfyUI when `ready`; otherwise wake card | Passthrough / `503 asleep` |

---

## 3. Status Object (`GET /_gate/status`)

```json
{
  "state": "downloading",
  "profile": "moss-image",
  "reason": "",
  "since": 1761200000.0,
  "session_started": 1761199900.0,
  "extends_used": 0,
  "can_extend": true,
  "busy": true,
  "remaining_s": 21300,
  "warn": "",
  "clients": 2,
  "default_view": "app",
  "cu": {
    "today": 0.4,
    "month": 12.1,
    "balance": 88.0,
    "per_hour": 1.71
  },
  "progress": {
    "stage": "download",
    "download": {
      "done_gb": 16.2,
      "total_gb": 25.4,
      "files_done": 4,
      "files_total": 6,
      "gbps": 0.42
    }
  },
  "models": {
    "on_demand": ["chinoll-edit"],
    "fetching": [],
    "fetch_error": ""
  },
  "you": {
    "email": "developer@example.com",
    "owner": true
  },
  "owner_only": ["switch", "stop", "extend"],
  "version": "0.1.0"
}
```

### Field Dictionary
- `state`: Active phase of the 10-state machine (`sleeping`, `allocating`, `installing`, `downloading`, `booting`, `smoke_testing`, `ready`, `switching`, `stopping`, `failed`).
- `busy`: `true` if ComfyUI's execution queue has active or pending jobs.
- `remaining_s`: Seconds remaining before the session reaches `MAX_SESSION_HOURS` cutoff.
- `cu`: Real-time Compute Unit metrics (`today`, `month`, `balance` from Google, and `per_hour` burn rate).
- `progress`: Telemetry emitted from `packages/colab/setup.py` (download GB, progress bar percentage, transfer rate).
- `owner_only`: Actions restricted to owners defined in `OWNER_EMAILS` (empty means all authenticated users are owners).
