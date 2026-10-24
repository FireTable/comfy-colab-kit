"""RFC 8628 Device Authorization endpoints & HTML approval UI."""
from __future__ import annotations

import json
from pathlib import Path
from aiohttp import web

from .db import GateDB, DB_KEY


def _get_base_url(request: web.Request) -> str:
    proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host", request.headers.get("Host", request.host))
    return f"{proto}://{host}"


async def device_code_endpoint(request: web.Request) -> web.Response:
    """RFC 8628 Step 1: Device requests a verification code."""
    db: GateDB = request.app[DB_KEY]
    body = {}
    if request.can_read_body:
        try:
            body = await request.json()
        except Exception:
            body = {}
    client_name = str(body.get("client_name") or "AI Agent").strip()

    res = db.create_device_code(client_name=client_name)
    base = _get_base_url(request)
    user_code = res["user_code"]
    res["verification_uri"] = f"{base}/auth/device"
    res["verification_uri_complete"] = f"{base}/auth/device?code={user_code}"
    return web.json_response(res)


async def device_token_endpoint(request: web.Request) -> web.Response:
    """RFC 8628 Step 3: Device polls for access token."""
    db: GateDB = request.app[DB_KEY]
    body = {}
    if request.can_read_body:
        try:
            body = await request.json()
        except Exception:
            body = {}
    device_code = str(body.get("device_code") or "").strip()
    if not device_code:
        return web.json_response({"error": "invalid_request", "error_description": "missing device_code"}, status=400)

    res = db.poll_device_token(device_code)
    st = res.get("status")
    if st == "pending":
        return web.json_response({"error": "authorization_pending"}, status=400)
    elif st == "expired":
        return web.json_response({"error": "expired_token"}, status=400)
    elif st == "denied":
        return web.json_response({"error": "access_denied"}, status=400)
    elif st == "approved":
        return web.json_response({
            "access_token": res.get("access_token"),
            "token_type": "Bearer",
            "token_id": res.get("token_id"),
            "email": res.get("email"),
        })
    return web.json_response({"error": "invalid_grant"}, status=400)


async def device_page_endpoint(request: web.Request) -> web.Response:
    """RFC 8628 Step 2: User browser visits /auth/device to approve/deny."""
    ident = request.get("identity")
    email = ident.email if ident else "unknown"
    is_owner = ident.is_owner if ident else False
    code = (request.query.get("code") or "").strip().upper()

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>授权 AI Agent · Comfy Colab Kit</title>
<style>
:root {{
  --bg: #090a0f;
  --card: #12141c;
  --border: rgba(255, 255, 255, 0.08);
  --text: #f3f4f6;
  --muted: #9ca3af;
  --primary: #3b82f6;
  --primary-hover: #2563eb;
  --danger: #ef4444;
  --danger-hover: #dc2626;
  --badge-owner: #10b981;
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: var(--bg);
  color: var(--text);
  font-family: var(--font);
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.5rem;
}}
.card {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 16px;
  width: 100%;
  max-width: 440px;
  padding: 2rem;
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(12px);
}}
.icon {{
  font-size: 2.5rem;
  margin-bottom: 1rem;
  text-align: center;
}}
h1 {{
  font-size: 1.35rem;
  font-weight: 700;
  text-align: center;
  margin-bottom: 0.5rem;
  letter-spacing: -0.02em;
}}
p.subtitle {{
  text-align: center;
  font-size: 0.88rem;
  color: var(--muted);
  margin-bottom: 1.5rem;
  line-height: 1.4;
}}
.user-box {{
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.85rem 1rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1.5rem;
}}
.user-email {{
  font-size: 0.9rem;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}}
.badge {{
  font-size: 0.72rem;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 9999px;
  background: {"rgba(16, 185, 129, 0.15)" if is_owner else "rgba(156, 163, 175, 0.15)"};
  color: {"#10b981" if is_owner else "#9ca3af"};
  border: 1px solid {"rgba(16, 185, 129, 0.3)" if is_owner else "rgba(156, 163, 175, 0.3)"};
}}
.input-label {{
  display: block;
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--muted);
  margin-bottom: 0.4rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}}
.code-input {{
  width: 100%;
  background: rgba(0, 0, 0, 0.4);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.85rem 1rem;
  color: #fff;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 1.25rem;
  font-weight: 700;
  letter-spacing: 0.15em;
  text-align: center;
  text-transform: uppercase;
  outline: none;
  transition: border-color 0.2s;
}}
.code-input:focus {{
  border-color: var(--primary);
}}
.scope-list {{
  margin: 1.25rem 0;
  border-left: 2px solid var(--primary);
  padding-left: 0.85rem;
}}
.scope-item {{
  font-size: 0.82rem;
  color: var(--muted);
  margin-bottom: 0.35rem;
  display: flex;
  align-items: center;
  gap: 0.4rem;
}}
.actions {{
  display: flex;
  gap: 0.75rem;
  margin-top: 1.5rem;
}}
.btn {{
  flex: 1;
  padding: 0.75rem 1rem;
  border-radius: 10px;
  font-size: 0.92rem;
  font-weight: 600;
  cursor: pointer;
  border: none;
  transition: all 0.15s ease;
}}
.btn-primary {{
  background: var(--primary);
  color: #fff;
}}
.btn-primary:hover {{ background: var(--primary-hover); }}
.btn-danger {{
  background: rgba(239, 68, 68, 0.15);
  color: #f87171;
  border: 1px solid rgba(239, 68, 68, 0.3);
}}
.btn-danger:hover {{ background: rgba(239, 68, 68, 0.25); }}
.status-msg {{
  margin-top: 1rem;
  font-size: 0.85rem;
  text-align: center;
  display: none;
}}
.status-msg.error {{ color: #f87171; display: block; }}
.status-msg.success {{ color: #10b981; display: block; }}
</style>
</head>
<body>
<div class="card">
  <div class="icon">🤖</div>
  <h1>授权 AI Agent 连接</h1>
  <p class="subtitle">允许外部设备（Cursor / Claude / OpenClaw）调用此 Comfy Colab 实例。</p>

  <div class="user-box">
    <div class="user-email">👤 {email}</div>
    <div class="badge">{"👑 管理员" if is_owner else "👤 协作者"}</div>
  </div>

  <form id="auth-form" onsubmit="event.preventDefault(); submitAuth(true);">
    <label class="input-label" for="user-code">设备配对码</label>
    <input id="user-code" class="code-input" maxlength="9" placeholder="XXXX-XXXX" value="{code}" required autocomplete="off" autocorrect="off" autocapitalize="characters" />

    <div class="scope-list">
      <div class="scope-item">✓ 唤醒 GPU 实例与查询状态</div>
      <div class="scope-item">✓ 提交 ComfyUI 工作流绘图 Prompt</div>
      {"<div class='scope-item' style='color:#10b981;'>✓ 具备停止机器释放算力权限 (Owner)</div>" if is_owner else "<div class='scope-item'>✕ 无权关机释放算力 (需管理员)</div>"}
    </div>

    <div class="actions">
      <button type="button" class="btn btn-danger" onclick="submitAuth(false)">拒绝 (Deny)</button>
      <button type="submit" class="btn btn-primary" id="btn-approve">确认授权 (Approve)</button>
    </div>
  </form>

  <div id="status-box" class="status-msg"></div>
</div>

<script>
async function submitAuth(approve) {{
  const code = document.getElementById("user-code").value.trim().toUpperCase();
  const statusBox = document.getElementById("status-box");
  const btn = document.getElementById("btn-approve");
  if (!code) {{
    statusBox.className = "status-msg error";
    statusBox.textContent = "请输入设备配对码";
    return;
  }}
  btn.disabled = true;
  statusBox.className = "status-msg";
  statusBox.textContent = "处理中...";
  statusBox.style.display = "block";

  try {{
    const endpoint = approve ? "/api/auth/device/approve" : "/api/auth/device/deny";
    const res = await fetch(endpoint, {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ user_code: code }})
    }});
    const data = await res.json();
    if (res.ok && data.ok) {{
      statusBox.className = "status-msg success";
      statusBox.innerHTML = approve 
        ? "✅ <b>授权成功！</b><br>AI Agent 客户端已自动连接并获取凭证，您可以关闭此标签页。"
        : "🚫 已拒绝该设备授权请求。";
      document.getElementById("auth-form").style.display = "none";
    }} else {{
      statusBox.className = "status-msg error";
      statusBox.textContent = "操作失败: " + (data.error || "未知错误或配对码已失效");
      btn.disabled = false;
    }}
  }} catch (e) {{
    statusBox.className = "status-msg error";
    statusBox.textContent = "网络错误，请稍后重试";
    btn.disabled = false;
  }}
}}
</script>
</body>
</html>
"""
    return web.Response(text=html, content_type="text/html")


async def device_approve_endpoint(request: web.Request) -> web.Response:
    """Approves a device code (gated by auth)."""
    ident = request.get("identity")
    if not ident:
        return web.json_response({"error": "unauthorized"}, status=401)
    db: GateDB = request.app[DB_KEY]
    body = {}
    if request.can_read_body:
        try:
            body = await request.json()
        except Exception:
            body = {}
    user_code = str(body.get("user_code") or "").strip().upper()
    if not user_code:
        return web.json_response({"error": "missing_user_code"}, status=400)

    try:
        res = db.approve_device_code(user_code, email=ident.email)
        return web.json_response({"ok": True, "token_id": res["token_id"]})
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)


async def device_deny_endpoint(request: web.Request) -> web.Response:
    """Denies a device code (gated by auth)."""
    db: GateDB = request.app[DB_KEY]
    body = {}
    if request.can_read_body:
        try:
            body = await request.json()
        except Exception:
            body = {}
    user_code = str(body.get("user_code") or "").strip().upper()
    ok = db.deny_device_code(user_code)
    return web.json_response({"ok": ok})


# ── Token Management Endpoints ───────────────────────────────────────────────
async def list_agent_tokens_endpoint(request: web.Request) -> web.Response:
    """Lists tokens for current user (or all if owner)."""
    ident = request.get("identity")
    if not ident:
        return web.json_response({"error": "unauthorized"}, status=401)
    db: GateDB = request.app[DB_KEY]
    tokens = db.list_tokens(email=ident.email, is_owner=ident.is_owner)
    return web.json_response({
        "tokens": tokens,
        "email": ident.email,
        "is_owner": ident.is_owner,
    })


async def revoke_agent_token_endpoint(request: web.Request) -> web.Response:
    """Revokes a specific token with secondary confirmation on client."""
    ident = request.get("identity")
    if not ident:
        return web.json_response({"error": "unauthorized"}, status=401)
    token_id = request.match_info.get("id", "").strip()
    db: GateDB = request.app[DB_KEY]
    ok = db.revoke_token(token_id, email=ident.email, is_owner=ident.is_owner)
    if not ok:
        return web.json_response({"error": "token_not_found_or_forbidden"}, status=404)
    return web.json_response({"ok": True, "revoked_id": token_id})
