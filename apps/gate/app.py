"""comfy-gate HTTP server (aiohttp)."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import urllib.request
from pathlib import Path

from aiohttp import web

from . import __version__
from .auth import AuthError, IDENT, Identity, JWKSCache, authorize, identify
from .colab import ColabCLI, ColabError, ColabRunner
from .config import Settings, read_secret_file
from .db import GateDB, DB_KEY
from .device_auth import (
    create_agent_token_endpoint,
    device_approve_endpoint,
    device_code_endpoint,
    device_deny_endpoint,
    device_page_endpoint,
    device_token_endpoint,
    list_agent_tokens_endpoint,
    revoke_agent_token_endpoint,
)
from .profiles import Profile, load_all
from .proxy import Proxy
from .session import REPO_ROOT, Session
from .state import State

log = logging.getLogger("gate")
def _find_web_dir() -> Path:
    app_web = Path("/app/apps/gate/web")
    if app_web.is_dir() and (app_web / "index.html").is_file():
        return app_web
    repo_web = REPO_ROOT / "apps" / "gate" / "web"
    if repo_web.is_dir() and (repo_web / "index.html").is_file():
        return repo_web
    pkg_web = Path(__file__).parent / "web"
    if pkg_web.is_dir() and (pkg_web / "index.html").is_file():
        return pkg_web
    return Path(__file__).parent / "web"


WEB = _find_web_dir()
PUBLIC_PATHS = ("/_gate/healthz", "/robots.txt", "/api/auth/device/code", "/api/auth/device/token")
BOT_UA_RE = re.compile(
    r"(bot|crawler|spider|crawling|slurp|archiver|scraper|yandex|bingbot|googlebot|baiduspider|bytespider|twitterbot|facebookexternalhit|rogerbot|linkedinbot|embedly|quora link preview|showyoubot|outbrain|pinterest|slackbot|vkShare|W3C_Validator)",
    re.IGNORECASE,
)
SESSION = web.AppKey("session", Session)
SETTINGS = web.AppKey("settings", Settings)
PROXY = web.AppKey("proxy", Proxy)
PROFILES = web.AppKey("profiles", dict)
PROFILE_ERRORS = web.AppKey("profile_errors", dict)
JWKS = web.AppKey("jwks", object)
AGENT_TOKEN = web.AppKey("agent_token", str)
BG = web.AppKey("bg", object)


def _fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310 (fixed https URL)
        return json.loads(r.read())


def err(status: int, code: str, msg: str) -> web.Response:
    return web.json_response({"error": code, "message": msg}, status=status)


@web.middleware
async def anti_crawler_mw(request: web.Request, handler):
    if request.path == "/robots.txt":
        return await handler(request)
    ua = request.headers.get("User-Agent", "")
    if BOT_UA_RE.search(ua):
        return web.Response(
            status=403,
            text="Access forbidden for automated bots, crawlers, and scrapers.",
            content_type="text/plain",
            headers={"X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet"},
        )
    resp = await handler(request)
    resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet"
    return resp


@web.middleware
async def auth_mw(request: web.Request, handler):
    if request.path in PUBLIC_PATHS or request.path.startswith("/_gate/static/"):
        return await handler(request)
    app = request.app
    query_token = request.query.get("token", "")
    cookie_token = request.cookies.get("agent_token", "")
    try:
        ident = await asyncio.get_running_loop().run_in_executor(
            None, identify, request.headers, app[SETTINGS], app[JWKS], app[AGENT_TOKEN], query_token, cookie_token, app.get(DB_KEY))
    except AuthError as e:
        accept = request.headers.get("Accept", "")
        if "text/html" in accept and not request.path.startswith("/_gate/") and not request.path.startswith("/api/"):
            html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>访问受限 · Comfy Colab Kit</title>
<style>
:root {{
  --bg: #090a0f; --card: #12141c; --border: rgba(255, 255, 255, 0.08);
  --text: #f3f4f6; --muted: #9ca3af; --primary: #3b82f6; --danger: #f87171;
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: var(--bg); color: var(--text); font-family: var(--font);
  min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 1.5rem;
}}
.card {{
  background: var(--card); border: 1px solid var(--border); border-radius: 16px;
  width: 100%; max-width: 440px; padding: 2rem; box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
  text-align: center;
}}
h1 {{ font-size: 1.35rem; font-weight: 700; margin-bottom: 0.75rem; }}
p {{ font-size: 0.9rem; color: var(--muted); line-height: 1.5; margin-bottom: 1.25rem; }}
.code-box {{
  background: rgba(0, 0, 0, 0.4); border: 1px solid var(--border); border-radius: 8px;
  padding: 0.75rem 1rem; font-family: ui-monospace, Menlo, monospace; font-size: 0.85rem;
  color: var(--danger); margin-bottom: 1.5rem; word-break: break-all;
}}
.btn {{
  display: inline-block; padding: 0.7rem 1.4rem; border-radius: 8px;
  background: var(--primary); color: #fff; text-decoration: none; font-size: 0.9rem; font-weight: 600;
  transition: opacity 0.15s;
}}
.btn:hover {{ opacity: 0.9; }}
</style>
</head>
<body>
<div class="card">
  <h1>访问受限 (Access Denied)</h1>
  <p>您的 Cloudflare 身份认证已通过，但当前账号未在网关允许访问列表中。</p>
  <div class="code-box">{e.msg}</div>
  <p style="font-size:0.82rem;margin-bottom:1.5rem">如需访问，请联系管理员将该邮箱加入允许列表，或点击下方重新登录已授权账号。</p>
  <a class="btn" href="/cdn-cgi/access/logout">重新登录</a>
</div>
</body>
</html>"""
            return web.Response(text=html, content_type="text/html", status=e.status)
        return err(e.status, e.code, e.msg)
    request[IDENT] = ident
    request["identity"] = ident
    resp = await handler(request)
    if query_token and app[AGENT_TOKEN] and hmac.compare_digest(query_token, app[AGENT_TOKEN]):
        resp.set_cookie("agent_token", query_token, path="/", httponly=True, samesite="Lax")
    return resp


def _ident(request: web.Request) -> Identity:
    return request[IDENT]


def get_client_ip(request: web.Request) -> str:
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        client = xff.split(",")[0].strip()
        if client:
            return client
    if request.remote:
        return request.remote.strip()
    return ""


async def _body(request: web.Request) -> dict:
    if not request.can_read_body:
        return {}
    try:
        d = await request.json()
        return d if isinstance(d, dict) else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


async def status(request: web.Request) -> web.Response:
    s = request.app[SESSION]
    ident = _ident(request)
    client_ip = get_client_ip(request)
    can_stop = s.can_stop(email=ident.email, ip=client_ip, is_owner=ident.is_owner)
    can_wake = "wake" not in request.app[SETTINGS].owner_only_actions or ident.is_owner
    return web.json_response({
        **s.status(),
        "can_stop": can_stop,
        "can_wake": can_wake,
        "you": {"email": ident.email, "owner": ident.is_owner, "ip": client_ip, "can_stop": can_stop, "can_wake": can_wake},
        "owner_only": list(request.app[SETTINGS].owner_only_actions),
        "version": __version__,
    })


async def profiles_route(request: web.Request) -> web.Response:
    return web.json_response({
        "default": request.app[SETTINGS].default_profile,
        "profiles": [p.summary() for p in request.app[PROFILES].values()],
        "errors": request.app[PROFILE_ERRORS],
    })


async def wake(request: web.Request) -> web.Response:
    ident = _ident(request)
    try:
        authorize(ident, "wake", request.app[SETTINGS])
    except AuthError as e:
        return err(e.status, e.code, e.msg)
    body = await _body(request)
    s = request.app[SESSION]
    profile = body.get("profile")
    client_ip = get_client_ip(request)
    # if a session is already starting/running, the caller joins it (its profile wins);
    # changing profile is the separate, owner-only /_gate/switch action
    try:
        res = await s.wake(profile, by=ident.email, ip=client_ip, workflows=body.get("workflows"))
    except KeyError:
        return err(404, "unknown_profile", f"unknown profile {profile!r}")
    if res.refused:
        return err(409, res.refused, f"wake refused: {res.refused}")
    return web.json_response({"joined": res.joined, "state": res.state, "profile": res.profile},
                             status=200 if res.joined else 202)


async def fetch(request: web.Request) -> web.Response:
    """POST /_gate/fetch {"workflows": [...]}: download those workflows' models onto the running VM."""
    ident = _ident(request)
    try:
        authorize(ident, "wake", request.app[SETTINGS])
    except AuthError as e:
        return err(e.status, e.code, e.msg)
    body = await _body(request)
    wids = body.get("workflows")
    if not isinstance(wids, list) or not all(isinstance(w, str) for w in wids) or not wids:
        return err(400, "bad_request", "body must be {\"workflows\": [\"<id>\", ...]}")
    why = await request.app[SESSION].fetch(wids)
    if why:
        return err(404 if why == "unknown_workflow" else 409, why, f"fetch refused: {why}")
    return web.json_response({"fetching": wids}, status=202)


async def switch(request: web.Request) -> web.Response:
    ident = _ident(request)
    try:
        authorize(ident, "switch", request.app[SETTINGS])
    except AuthError as e:
        return err(e.status, e.code, e.msg)
    body = await _body(request)
    client_ip = get_client_ip(request)
    try:
        res = await request.app[SESSION].switch(str(body.get("profile", "")), by=ident.email, ip=client_ip)
    except KeyError:
        return err(404, "unknown_profile", "unknown profile")
    if res.refused:
        return err(409, res.refused, f"switch refused: {res.refused}")
    return web.json_response({"state": res.state, "profile": res.profile, "mode": "cold"}, status=202)


async def stop(request: web.Request) -> web.Response:
    ident = _ident(request)
    s = request.app[SESSION]
    client_ip = get_client_ip(request)
    if not s.can_stop(email=ident.email, ip=client_ip, is_owner=ident.is_owner):
        return err(403, "not_initiator", "Only the user who started the session (or owner) can stop it.")
    await s.stop(f"stopped by {ident.email or client_ip or 'unknown'}")
    return web.json_response({"state": request.app[SESSION].machine.state.value})


async def extend(request: web.Request) -> web.Response:
    ident = _ident(request)
    try:
        authorize(ident, "extend", request.app[SETTINGS])
    except AuthError as e:
        return err(e.status, e.code, e.msg)
    if not await request.app[SESSION].extend():
        return err(409, "extend_limit", "no extensions left")
    return web.json_response(request.app[SESSION].status())


async def mcp_endpoint(request: web.Request) -> web.Response:
    """Model Context Protocol (MCP) Streamable/HTTP JSON-RPC 2.0 endpoint."""
    if request.method == "GET":
        return web.json_response({
            "name": "comfy-colab-kit",
            "version": "0.1.0",
            "protocol": "2024-11-05",
            "tools": ["gate_status", "gate_wake", "gate_stop"]
        })
    body = await _body(request)
    req_id = body.get("id")
    method = body.get("method")
    params = body.get("params", {})
    s = request.app[SESSION]

    if method == "initialize":
        return web.json_response({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "comfy-colab-kit", "version": "0.1.0"}
            }
        })
    elif method == "tools/list":
        return web.json_response({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "gate_status",
                        "description": "Inspect the Comfy Colab session state, active profile, and CU balance.",
                        "inputSchema": {"type": "object", "properties": {}}
                    },
                    {
                        "name": "gate_wake",
                        "description": "Wake or join the shared Google Colab L4 GPU instance with a model profile.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "profile": {"type": "string", "description": "Profile ID, e.g. 'moss-image'"}
                            }
                        }
                    },
                    {
                        "name": "gate_stop",
                        "description": "Stop the Colab GPU session immediately to release Compute Units.",
                        "inputSchema": {"type": "object", "properties": {}}
                    }
                ]
            }
        })
    elif method == "tools/call":
        tool_name = params.get("name")
        ident = _ident(request)
        if tool_name == "gate_status":
            return web.json_response({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(s.status(), indent=2)}]}
            })
        elif tool_name == "gate_wake":
            args = params.get("arguments", {})
            prof = args.get("profile") or request.app[SETTINGS].default_profile
            res = await s.wake(prof, by=ident.email, ip=get_client_ip(request))
            if res.refused:
                return web.json_response({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"isError": True, "content": [{"type": "text", "text": f"Wake refused: {res.refused}"}]}
                })
            return web.json_response({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": f"Wake initiated for profile '{prof}'. State: {res.state}"}]}
            })
        elif tool_name == "gate_stop":
            client_ip = get_client_ip(request)
            if not s.can_stop(email=ident.email, ip=client_ip, is_owner=ident.is_owner):
                return web.json_response({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"isError": True, "content": [{"type": "text", "text": "Permission denied: only the session initiator or owner can stop it"}]}
                })
            st = await s.stop("mcp agent requested stop")
            return web.json_response({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": f"Session stopped. State: {st.value}"}]}
            })
        return web.json_response({
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}
        })

    return web.json_response({
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not supported: {method}"}
    })


async def events(request: web.Request) -> web.StreamResponse:
    s = request.app[SESSION]
    cid = (request.query.get("cid") or
           request.headers.get("CF-Connecting-IP") or
           request.headers.get("X-Forwarded-For") or
           request.remote or
           str(id(request))).strip()
    try:
        since = int(request.query.get("since", 0))
    except (ValueError, TypeError):
        since = 0
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                                       "X-Accel-Buffering": "no"})
    await resp.prepare(request)

    async def send(kind: str, data) -> None:
        await resp.write(f"event: {kind}\ndata: {json.dumps(data)}\n\n".encode())

    q = s.bus.subscribe()
    s.client_opened(cid)
    try:
        await send("state", s.status())
        if s.progress:
            await send("progress", s.progress)
        for line in s.bus.logs_since(since):
            await send("log", line)
        while True:
            try:
                kind, data = await asyncio.wait_for(q.get(), timeout=20)
                await send(kind, data)
            except asyncio.TimeoutError:
                await resp.write(b": ping\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        s.bus.unsubscribe(q)
        s.client_closed(cid)
    return resp


async def healthz(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "state": request.app[SESSION].machine.state.value})


async def robots_txt(request: web.Request) -> web.Response:
    return web.Response(
        text="User-agent: *\nDisallow: /\n",
        content_type="text/plain",
        headers={"Cache-Control": "public, max-age=86400", "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet"},
    )


async def wake_page(request: web.Request) -> web.Response:
    html = (WEB / "index.html").read_text()
    s = request.app[SESSION]
    ident = _ident(request)
    settings = request.app[SETTINGS]
    client_ip = get_client_ip(request)
    can_stop = s.can_stop(email=ident.email, ip=client_ip, is_owner=ident.is_owner)
    can_wake = "wake" not in settings.owner_only_actions or ident.is_owner
    status_data = {
        **s.status(),
        "can_stop": can_stop,
        "can_wake": can_wake,
        "you": {"email": ident.email, "owner": ident.is_owner, "ip": client_ip, "can_stop": can_stop, "can_wake": can_wake},
        "owner_only": list(settings.owner_only_actions),
    }
    profiles_data = {
        "profiles": [p.summary() for p in request.app[PROFILES].values()],
        "default": settings.default_profile,
    }
    init_json = json.dumps({
        "status": status_data,
        "profiles": profiles_data,
        "logs": list(s.bus.logs),
    }).replace("</script>", "<\\/script>")
    tag = f'<script id="init-data">window.__INIT__ = {init_json};</script>'
    if "</head>" in html:
        html = html.replace("</head>", f"{tag}\n</head>", 1)
    else:
        html = tag + html
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})


async def catch_all(request: web.Request) -> web.StreamResponse:
    s = request.app[SESSION]
    s.touch()
    if request.path in ("/", "/index.html"):
        if request.app[SETTINGS].auto_stop.wake_on_visit and s.machine.state == State.SLEEPING:
            await s.wake(None, by=_ident(request).email, ip=get_client_ip(request))
        return await wake_page(request)
    if s.machine.state == State.READY:
        try:
            return await request.app[PROXY].handle(request)
        except Exception as e:  # noqa: BLE001
            log.warning("proxy error: %s", e)
            return err(502, "backend_unreachable", "ComfyUI backend unreachable")
    if request.path.startswith(("/api", "/ws", "/prompt", "/view")):
        return err(503, "asleep", f"ComfyUI is {s.machine.state.value}; POST /_gate/wake first")
    raise web.HTTPFound("/")


async def background(app: web.Application) -> None:
    s, proxy, settings = app[SESSION], app[PROXY], app[SETTINGS]
    sweep_every = max(1, settings.auto_stop.orphan_sweep_min) * 60
    last_sweep = 0.0
    loop = asyncio.get_running_loop()
    consecutive_probe_failures = 0
    while True:
        await asyncio.sleep(30)
        try:
            if s.machine.state == State.READY:
                probe_ok = await proxy.probe()
                if not probe_ok:
                    consecutive_probe_failures += 1
                    if consecutive_probe_failures >= 5:
                        log.warning("backend probe failed %d times; reconciling with colab", consecutive_probe_failures)
                        await s.reconcile()
                else:
                    consecutive_probe_failures = 0
                    s.queue_busy = await proxy.queue_busy()
                await proxy.heartbeat()
            elif s.is_boot_state() and not s.boot_task_running() and await proxy.probe():
                # re-attached after a gate restart and the backend answers
                if s.machine.can(State.READY):
                    s.machine.to(State.READY, "backend reachable after restart")
                    s.persist()
            await s.tick()
            if loop.time() - last_sweep > sweep_every:
                last_sweep = loop.time()
                if s.machine.state == State.SLEEPING:
                    names = await s.colab.sessions()
                    if settings.colab_session_name in names:
                        s.bus.publish("log", "orphan VM found while sleeping; stopping it")
                        await s.colab.stop(settings.colab_session_name)
                elif s.machine.is_billing:
                    await s.reconcile()
                bal = await s.colab.ccu_info()
                if bal is not None:
                    s.ledger.balance = bal
        except ColabError as e:
            log.warning("background: %s", e)
        except Exception:  # noqa: BLE001
            log.exception("background loop error")


def build_app(settings: Settings, colab: ColabRunner | None = None,
              profiles: dict[str, Profile] | None = None, run_background: bool = True) -> web.Application:
    app = web.Application(middlewares=[anti_crawler_mw, auth_mw], client_max_size=64 * 1024 * 1024)
    if profiles is None:
        profiles, errors = load_all(settings.profiles, REPO_ROOT)
    else:
        errors = {}
    app[SETTINGS] = settings
    app[PROFILES] = profiles
    app[PROFILE_ERRORS] = errors
    app[JWKS] = (JWKSCache(settings.cf_access_team_domain, _fetch_json)
                   if settings.cf_access_team_domain else None)
    app[AGENT_TOKEN] = read_secret_file(settings.agent_token_file)
    session = Session(settings, profiles, colab or ColabCLI(settings.colab_bin, settings.colab_token_dir))
    session.restore()
    app[SESSION] = session
    app[PROXY] = Proxy(settings.backend_url, settings.cf_service_token_file, session.touch,
                       session.client_opened, session.client_closed)
    db_path = Path(settings.state_dir) / "gate.db"
    db = GateDB(db_path)
    app[DB_KEY] = db
    r = app.router
    r.add_get("/robots.txt", robots_txt)
    r.add_get("/_gate/healthz", healthz)
    r.add_get("/_gate/status", status)
    r.add_get("/_gate/events", events)
    r.add_get("/_gate/profiles", profiles_route)
    r.add_post("/_gate/wake", wake)
    r.add_post("/_gate/switch", switch)
    r.add_post("/_gate/fetch", fetch)
    r.add_post("/_gate/stop", stop)
    r.add_post("/_gate/extend", extend)
    r.add_route("*", "/_gate/mcp", mcp_endpoint)
    r.add_route("*", "/mcp", mcp_endpoint)
    r.add_post("/api/auth/device/code", device_code_endpoint)
    r.add_post("/api/auth/device/token", device_token_endpoint)
    r.add_get("/auth/device", device_page_endpoint)
    r.add_post("/api/auth/device/approve", device_approve_endpoint)
    r.add_post("/api/auth/device/deny", device_deny_endpoint)
    r.add_get("/api/agent-tokens", list_agent_tokens_endpoint)
    r.add_post("/api/agent-tokens", create_agent_token_endpoint)
    r.add_post("/api/agent-tokens/{id}/revoke", revoke_agent_token_endpoint)
    r.add_delete("/api/agent-tokens/{id}", revoke_agent_token_endpoint)
    r.add_get("/_gate/tokens", list_agent_tokens_endpoint)
    r.add_post("/_gate/tokens", create_agent_token_endpoint)
    r.add_post("/_gate/tokens/{id}/revoke", revoke_agent_token_endpoint)
    r.add_static("/_gate/static/", WEB, show_index=False)
    r.add_route("*", "/{tail:.*}", catch_all)

    async def on_startup(app: web.Application) -> None:
        await session.reconcile()
        if run_background:
            app[BG] = asyncio.create_task(background(app))

    async def on_cleanup(app: web.Application) -> None:
        if BG in app:
            app[BG].cancel()
        await app[PROXY].close()
        session.persist()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    web.run_app(build_app(settings), host=settings.listen_host, port=settings.listen_port, access_log=None)


if __name__ == "__main__":
    main()
