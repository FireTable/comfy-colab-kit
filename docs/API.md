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

### Control & Gateway Endpoints (`/_gate/*`)

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

### Device Flow & Token Management Endpoints
| Method & Path | Description | Expected Responses |
|---|---|---|
| `POST /api/auth/device/code` | RFC 8628 initiate device authorization flow | `200 {"device_code", "user_code", "verification_uri", "expires_in", "interval"}` |
| `POST /api/auth/device/token` | RFC 8628 poll for agent bearer token | `200 {"access_token", "token_type": "bearer"}`, or `400 {"error": "authorization_pending"}` |
| `GET /auth/device` | User-facing browser authorization approval page | `200 text/html` |
| `POST /api/auth/device/approve` | Approve device code request (owner only) | `200 {"ok": true}` |
| `POST /api/auth/device/deny` | Deny device code request (owner only) | `200 {"ok": true}` |
| `GET /api/agent-tokens` | List existing agent tokens (owner only) | `200 {"tokens": [...]}` |
| `POST /api/agent-tokens` | Create or reuse pre-authorized agent token | `200 {"id", "token", "name"}` |
| `DELETE /api/agent-tokens/{id}` | Permanently revoke and delete an agent token | `200 {"ok": true}` |

### Native ComfyUI Proxied Endpoints (Available when `state == "ready"`)
| Method & Path | Description | Expected Responses |
|---|---|---|
| `POST /upload/image` | **Img2Img**: Upload reference image into input folder | `200 {"name": "...", "subfolder": "", "type": "input"}` |
| `POST /upload/mask` | Upload inpainting mask image | `200 {"name": "...", "type": "input"}` |
| `POST /prompt` | Submit ComfyUI prompt graph JSON | `200 {"prompt_id": "...", "number": 1}` |
| `GET /prompt` | Inspect active and pending queue | `200 {"exec_info": {...}}` |
| `GET /history/{prompt_id}` | Query execution status & image filenames | `200 {"<id>": {"outputs": {...}}}` |
| `GET /view` | Retrieve generated output or input image | `200 image/png` (binary stream) |
| `GET /view_metadata` | View prompt metadata embedded in image | `200 application/json` |
| `GET /object_info` | Inspect all installed node definitions & schemas | `200 application/json` |
| `GET /system_stats` | Inspect GPU VRAM and device load | `200 {"system": {...}, "devices": [...]}` |
| `GET /queue` & `POST /queue` | Query or clear execution queue | `200 application/json` |
| `POST /interrupt` | Interrupt active generation immediately | `200 {}` |
| `GET /ws` | WebSocket for real-time progress events | `101 Switching Protocols` |

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
