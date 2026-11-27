"""comfy-gate HTTP server (aiohttp)."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import time
import urllib.parse
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
from .queue import JobManager
from .session import REPO_ROOT, Session
from .state import State
from .storage import ArchiveStore, build_store, parse_range_header

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
JOBS = web.AppKey("jobs", JobManager)
STORE = web.AppKey("store", object)
DISPATCHER = web.AppKey("dispatcher", object)


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
    if request.path in PUBLIC_PATHS or request.path.startswith("/_gate/static/") or (request.method == "GET" and request.path in ("/mcp", "/_gate/mcp")):
        return await handler(request)
    app = request.app
    query_token = request.query.get("token", "")
    cookie_token = request.cookies.get("agent_token", "")
    cf_cookie = request.cookies.get("CF_Authorization", "")
    try:
        ident = await asyncio.get_running_loop().run_in_executor(
            None, identify, request.headers, app[SETTINGS], app[JWKS], app[AGENT_TOKEN], query_token, cookie_token, app.get(DB_KEY), cf_cookie)
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
    st = s.status()
    if not ident.is_owner:
        st["cu"] = None
        st["woken_by"] = None
    return web.json_response({
        **st,
        "queue": request.app[JOBS].active_queue_summary(),
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
    accel = body.get("accelerator")
    if accel is not None and not isinstance(accel, str):
        return err(400, "bad_request", "accelerator must be a string")
    client_ip = get_client_ip(request)
    # if a session is already starting/running, the caller joins it (its profile wins);
    # changing profile is the separate, owner-only /_gate/switch action
    try:
        res = await s.wake(profile, by=ident.email, ip=client_ip,
                           workflows=body.get("workflows"), accelerator=accel)
    except KeyError:
        return err(404, "unknown_profile", f"unknown profile {profile!r}")
    except ValueError as e:
        return err(400, "invalid_accelerator", str(e))
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


MCP_INSTRUCTIONS = (
    "Comfy Colab Kit MCP server. Manage the shared Colab GPU session (gate_status, gate_wake, gate_stop), "
    "run foreground generations when ready (gate_txt2img, gate_img2img, gate_generate), "
    "and manage the durable offline queue & Google Drive/local archive even while the GPU is sleeping "
    "(gate_queue_job, gate_list_jobs, gate_get_job, gate_manage_job, gate_storage_status)."
)

MCP_TOOLS = [
    {
        "name": "gate_status",
        "description": "Inspect the Comfy Colab session state, active profile, and CU balance.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "gate_wake",
        "description": "Wake or join the shared Google Colab GPU instance with a model profile (e.g. 'moss-image', 'flux-klein', 'wan-ltx-video', 'sd-latest', 'qwen-image', 'minimax-h3-image', 'minimax-h3-video').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile": {"type": "string", "description": "Profile ID, e.g. 'moss-image', 'flux-klein', 'wan-ltx-video', 'sd-latest', 'qwen-image', 'minimax-h3-image', 'minimax-h3-video'."},
                "accelerator": {"type": "string", "description": "Optional preferred GPU tier: 'G4', 'A100', 'L4', 'T4', or 'H100'."},
            },
        },
    },
    {
        "name": "gate_txt2img",
        "description": "Generate an image or video from a text prompt using the active profile's pipeline (MOSS-Image DeBand, FLUX.2 Klein 4B, Wan 2.2 5B TI2V, or SD 3.5 Large fp8).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Text description of the image or video to generate."},
                "negative_prompt": {"type": "string", "description": "Negative elements to avoid."},
                "preset": {"type": "string", "description": "Preset: 'Fast alt' (default), 'Fast', 'Balanced', 'Quality'."},
                "seed": {"type": "integer", "description": "Random seed (optional)."},
                "width": {"type": "integer", "description": "Output width (default 1024; 832 for wan-ltx-video)."},
                "height": {"type": "integer", "description": "Output height (default 1024; 480 for wan-ltx-video)."},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "gate_img2img",
        "description": "Generate an image edit/variation or image-to-video clip from a reference image (filename in ComfyUI input/ or HTTP/HTTPS image URL) and a text prompt.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "image": {"type": "string", "description": "Reference image filename in ComfyUI input/ or an HTTP/HTTPS URL."},
                "prompt": {"type": "string", "description": "Text prompt guiding the image transformation, edit, or video motion."},
                "denoise": {"type": "number", "description": "Denoising strength (0.05 to 1.0, default 0.75)."},
                "negative_prompt": {"type": "string", "description": "Negative elements to avoid."},
                "preset": {"type": "string", "description": "Preset: 'Fast alt' (default), 'Fast', 'Balanced', 'Quality'."},
                "seed": {"type": "integer", "description": "Random seed (optional)."},
            },
            "required": ["image", "prompt"],
        },
    },
    {
        "name": "gate_generate",
        "description": "Submit a raw ComfyUI prompt graph and return completed image outputs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "object", "description": "The ComfyUI API prompt dictionary."},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "gate_stop",
        "description": "Stop the Colab GPU session immediately to release Compute Units.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "gate_queue_job",
        "description": "Enqueue a ComfyUI prompt graph into the durable persistent queue (works even while GPU is sleeping). Auto-dispatches when the matching profile wakes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "object", "description": "The ComfyUI API prompt dictionary."},
                "profile": {"type": "string", "description": "Target profile ID (optional; defaults to active or default profile)."},
                "workflow_id": {"type": "string", "description": "Optional workflow ID label."},
                "image": {"type": "string", "description": "Optional reference image HTTP/HTTPS URL (downloaded offline to archive) or existing uploaded input filename."},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "gate_list_jobs",
        "description": "List queued, running, interrupted, and archived jobs from persistent storage (works while GPU is sleeping).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile": {"type": "string", "description": "Filter by profile ID."},
                "status": {"type": "string", "description": "Filter by status ('queued', 'running', 'archiving', 'interrupted', 'completed', 'failed', 'canceled')."},
                "query": {"type": "string", "description": "Search text in prompt, job ID, profile, or error."},
                "pinned": {"type": "boolean", "description": "Filter by pinned status."},
                "limit": {"type": "integer", "description": "Max jobs to return (default: 20)."},
            },
        },
    },
    {
        "name": "gate_get_job",
        "description": "Get detailed status, metadata, inputs, and output archive URLs for a single job (works while GPU is sleeping).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job ID or prompt_id."},
                "include_prompt": {"type": "boolean", "description": "Include full prompt JSON graph (default: false)."},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "gate_manage_job",
        "description": "Manage a queued or archived job: retry, cancel, pin, unpin, or delete (works while GPU is sleeping).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Target job ID."},
                "action": {
                    "type": "string",
                    "enum": ["retry", "cancel", "pin", "unpin", "delete"],
                    "description": "Action to perform.",
                },
                "delete_files": {"type": "boolean", "description": "When action is 'delete', also delete unshared output blobs and thumbnails (default: false)."},
            },
            "required": ["job_id", "action"],
        },
    },
    {
        "name": "gate_storage_status",
        "description": "Inspect ArchiveStore backend status (Google Drive or Local Disk), job counts, and per-profile archive stats; optionally trigger an immediate SQLite backup.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "backup_now": {"type": "boolean", "description": "If true, create and upload an immediate DB backup snapshot first (default: false)."},
            },
        },
    },
]


def _mcp_ok(req_id: Any, text: str) -> web.Response:
    return web.json_response({
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {"content": [{"type": "text", "text": text}]},
    })


def _mcp_err(req_id: Any, msg: str) -> web.Response:
    return web.json_response({
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {"isError": True, "content": [{"type": "text", "text": msg}]},
    })


def _enrich_job_urls(job: dict[str, Any], public_base: str) -> dict[str, Any]:
    out = dict(job)
    for key in ("inputs", "outputs"):
        items = []
        for item in job.get(key) or []:
            d = dict(item)
            if d.get("url", "").startswith("/"):
                d["url"] = f"{public_base}{d['url']}"
            if d.get("thumb_url", "").startswith("/"):
                d["thumb_url"] = f"{public_base}{d['thumb_url']}"
            items.append(d)
        out[key] = items
    return out


async def _download_and_store_input_url(client, job_manager: JobManager, image_ref: str) -> dict[str, Any]:
    import aiohttp as _aiohttp
    async with client.get(image_ref, timeout=_aiohttp.ClientTimeout(total=30)) as ir:
        if ir.status >= 400:
            raise ValueError(f"Failed to download image URL ({ir.status})")
        img_bytes = await ir.read()
        img_mime = (ir.headers.get("Content-Type") or "image/png").split(";")[0].strip()
    fn = image_ref.rsplit("/", 1)[-1].split("?")[0] or "reference.png"
    return await job_manager.store_input_bytes(fn, img_bytes, mime=img_mime)


def _set_job_pinned_helper(app: web.Application, job_id: str, pinned: bool | None = None) -> dict[str, Any] | None:
    db: GateDB = app[DB_KEY]
    existing = db.get_job(job_id, include_prompt=False)
    if not existing:
        return None
    new_pinned = (not existing["pinned"]) if pinned is None else bool(pinned)
    updated = db.set_job_pinned(job_id, new_pinned)
    jm: JobManager = app[JOBS]
    jm.mark_db_dirty()
    jm._publish_job_event(updated, "pinned")
    return updated


async def _storage_status_payload(app: web.Application, include_profiles: bool = False) -> dict[str, Any]:
    store: ArchiveStore = app[STORE]
    db: GateDB = app[DB_KEY]
    jm: JobManager = app[JOBS]
    info = await store.status_info()
    payload: dict[str, Any] = {
        **info,
        "counts": db.count_jobs_by_status(),
        "db_dirty": jm.db_dirty,
    }
    if include_profiles:
        payload["profiles"] = db.profile_archive_stats()
    return payload


async def mcp_endpoint(request: web.Request) -> web.Response:
    """Model Context Protocol (MCP) Streamable/HTTP JSON-RPC 2.0 endpoint."""
    if request.method == "GET":
        return web.json_response({
            "name": "comfy-colab-kit",
            "version": __version__,
            "protocol": "2024-11-05",
            "tools": [t["name"] for t in MCP_TOOLS],
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
                "serverInfo": {"name": "comfy-colab-kit", "version": __version__},
                "instructions": MCP_INSTRUCTIONS,
            },
        })
    elif method == "tools/list":
        return web.json_response({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": MCP_TOOLS},
        })
    elif method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments") or {}
        ident = _ident(request)
        public_base = f"{request.scheme}://{request.host}".rstrip("/")
        job_manager: JobManager = request.app[JOBS]
        db: GateDB = request.app[DB_KEY]

        if tool_name == "gate_status":
            return _mcp_ok(req_id, json.dumps({**s.status(), "queue": job_manager.active_queue_summary()}, indent=2))

        elif tool_name == "gate_wake":
            prof = args.get("profile") or request.app[SETTINGS].default_profile
            try:
                res = await s.wake(prof, by=ident.email, ip=get_client_ip(request), accelerator=args.get("accelerator"))
            except (KeyError, ValueError) as e:
                return _mcp_err(req_id, f"Wake error: {e}")
            if res.refused:
                return _mcp_err(req_id, f"Wake refused: {res.refused}")
            return _mcp_ok(req_id, f"Wake initiated for profile '{prof}'. State: {res.state}")

        elif tool_name in ("gate_txt2img", "gate_img2img", "gate_generate"):
            if s.machine.state != State.READY:
                return _mcp_err(req_id, f"Cannot generate: GPU is not ready (state: '{s.machine.state.value}'). Call gate_wake first.")
            proxy = request.app[PROXY]
            client = await proxy.client()
            active_profile = s.machine.profile or request.app[SETTINGS].default_profile
            extra_input_ids: list[str] = []

            if tool_name == "gate_txt2img":
                prompt_text = (args.get("prompt") or "").strip()
                if not prompt_text:
                    return _mcp_err(req_id, "Missing required argument 'prompt'")
                from ckit.moss_template import make_txt2img_graph
                prompt_graph = make_txt2img_graph(
                    profile=active_profile,
                    prompt=prompt_text,
                    negative_prompt=args.get("negative_prompt", "worst quality, bad anatomy, watermark, jpeg artifact"),
                    preset=args.get("preset", "Fast alt · fix + darask0 0.35 · 10 steps"),
                    seed=args.get("seed", -1),
                    width=args.get("width", 1024),
                    height=args.get("height", 1024),
                )
            elif tool_name == "gate_img2img":
                prompt_text = (args.get("prompt") or "").strip()
                image_ref = (args.get("image") or "").strip()
                if not prompt_text or not image_ref:
                    return _mcp_err(req_id, "Missing required arguments 'image' and 'prompt'")
                if image_ref.startswith("http://") or image_ref.startswith("https://"):
                    try:
                        asset = await _download_and_store_input_url(client, job_manager, image_ref)
                        image_ref = asset["filename"]
                        extra_input_ids.append(asset["id"])
                    except Exception as e:
                        return _mcp_err(req_id, f"Error storing reference image: {e}")
                from ckit.moss_template import make_img2img_graph
                prompt_graph = make_img2img_graph(
                    profile=active_profile,
                    prompt=prompt_text,
                    image=image_ref,
                    negative_prompt=args.get("negative_prompt", "worst quality, bad anatomy, watermark, jpeg artifact"),
                    preset=args.get("preset", "Fast alt · fix + darask0 0.35 · 10 steps"),
                    denoise=args.get("denoise", 0.75),
                    seed=args.get("seed", -1),
                )
            else:
                prompt_graph = args.get("prompt")
                if not isinstance(prompt_graph, dict) or not prompt_graph:
                    return _mcp_err(req_id, "Argument 'prompt' must be a non-empty ComfyUI graph dictionary")

            timeout_s = int(args.get("timeout_seconds") or 180)
            job = await job_manager.enqueue_prompt(
                prompt_graph,
                profile_id=active_profile,
                submitted_by=ident.email,
                input_asset_ids=extra_input_ids or None,
            )
            final_job = await job_manager.wait_for_job(job["id"], timeout_s=timeout_s)
            if not final_job or final_job["status"] not in ("completed", "failed", "canceled", "interrupted"):
                return _mcp_err(req_id, f"Generation timed out after {timeout_s}s. Job ID: {job['id']}")
            if final_job["status"] != "completed":
                return _mcp_err(req_id, f"Generation {final_job['status']}: {final_job.get('error_message') or 'unknown error'}")

            images = [
                {
                    "filename": out["filename"],
                    "url": f"{public_base}{out['url']}",
                    "asset_id": out["id"],
                    "node_id": out.get("node_id", ""),
                }
                for out in final_job.get("outputs") or []
            ]
            res_data = {
                "job_id": final_job["id"],
                "prompt_id": final_job["comfy_prompt_id"] or final_job["client_prompt_id"],
                "status": {"status_str": "success", "completed": True},
                "images": images,
            }
            res_text = json.dumps(res_data, indent=2, ensure_ascii=False)
            if images:
                img_md = "\n".join([f"![{img['filename']}]({img['url']})\nLink: {img['url']}" for img in images])
                res_text += f"\n\n### Generated Outputs\n{img_md}"
            return _mcp_ok(req_id, res_text)

        elif tool_name == "gate_stop":
            client_ip = get_client_ip(request)
            if not s.can_stop(email=ident.email, ip=client_ip, is_owner=ident.is_owner):
                return _mcp_err(req_id, "Permission denied: only the session initiator or owner can stop it")
            await s.stop("mcp agent requested stop")
            return _mcp_ok(req_id, f"Session stopped. State: {s.machine.state.value}")

        elif tool_name == "gate_queue_job":
            prompt_graph = args.get("prompt")
            if not isinstance(prompt_graph, dict) or not prompt_graph:
                return _mcp_err(req_id, "Argument 'prompt' must be a non-empty ComfyUI graph dictionary")
            profile = args.get("profile") or args.get("profile_id") or s.machine.profile or request.app[SETTINGS].default_profile
            if profile not in request.app[PROFILES]:
                return _mcp_err(req_id, f"Unknown profile {profile!r}")
            image_ref = (args.get("image") or "").strip()
            input_asset_ids: list[str] = []
            extra_input_filenames: list[str] = []
            if image_ref:
                if image_ref.startswith("http://") or image_ref.startswith("https://"):
                    try:
                        client = await request.app[PROXY].client()
                        asset = await _download_and_store_input_url(client, job_manager, image_ref)
                        input_asset_ids.append(asset["id"])
                        extra_input_filenames.append(asset["filename"])
                    except Exception as e:
                        return _mcp_err(req_id, f"Error downloading reference image: {e}")
                else:
                    extra_input_filenames.append(image_ref)
            job = await job_manager.enqueue_prompt(
                prompt_graph,
                profile_id=str(profile),
                submitted_by=ident.email,
                workflow_id=str(args.get("workflow_id") or ""),
                extra_input_filenames=extra_input_filenames or None,
                input_asset_ids=input_asset_ids or None,
            )
            return _mcp_ok(req_id, json.dumps({
                "job_id": job["id"],
                "prompt_id": job["client_prompt_id"],
                "status": job["status"],
                "profile_id": job["profile_id"],
                "inputs": _enrich_job_urls(job, public_base).get("inputs", []),
            }, indent=2, ensure_ascii=False))

        elif tool_name == "gate_list_jobs":
            profile = (args.get("profile") or "").strip() or None
            status_f = (args.get("status") or "").strip() or None
            q_str = (args.get("query") or "").strip() or None
            pinned_f = args.get("pinned") if isinstance(args.get("pinned"), bool) else None
            try:
                limit = max(1, min(200, int(args.get("limit") or 20)))
            except (TypeError, ValueError):
                limit = 20
            jobs = [_enrich_job_urls(j, public_base) for j in db.list_jobs(
                profile=profile, status=status_f, q=q_str, pinned=pinned_f, limit=limit, include_prompt=False
            )]
            total = db.count_jobs(profile=profile, status=status_f, q=q_str, pinned=pinned_f)
            return _mcp_ok(req_id, json.dumps({
                "jobs": jobs,
                "total": total,
                "counts": db.count_jobs_by_status(profile),
                "limit": limit,
            }, indent=2, ensure_ascii=False))

        elif tool_name == "gate_get_job":
            job_id = str(args.get("job_id") or "").strip()
            if not job_id:
                return _mcp_err(req_id, "Missing required argument 'job_id'")
            include_prompt = bool(args.get("include_prompt", False))
            job = db.get_job(job_id, include_prompt=include_prompt)
            if not job:
                return _mcp_err(req_id, f"Job {job_id!r} not found")
            return _mcp_ok(req_id, json.dumps(_enrich_job_urls(job, public_base), indent=2, ensure_ascii=False))

        elif tool_name == "gate_manage_job":
            job_id = str(args.get("job_id") or "").strip()
            action = str(args.get("action") or "").strip().lower()
            if not job_id or action not in ("retry", "cancel", "pin", "unpin", "delete"):
                return _mcp_err(req_id, "Required arguments: 'job_id' and 'action' in ('retry', 'cancel', 'pin', 'unpin', 'delete')")
            if action == "retry":
                job, code = await job_manager.retry_job(job_id)
                if not job:
                    return _mcp_err(req_id, f"Job {job_id!r} not found")
                if code == "already_active":
                    return _mcp_err(req_id, f"Job {job_id!r} is already active ({job['status']})")
                return _mcp_ok(req_id, json.dumps(_enrich_job_urls(job, public_base), indent=2, ensure_ascii=False))
            elif action == "cancel":
                job, code = await job_manager.cancel_job(job_id)
                if not job:
                    return _mcp_err(req_id, f"Job {job_id!r} not found")
                if code == "already_finished":
                    return _mcp_err(req_id, f"Job {job_id!r} already finished ({job['status']})")
                return _mcp_ok(req_id, json.dumps(_enrich_job_urls(job, public_base), indent=2, ensure_ascii=False))
            elif action in ("pin", "unpin"):
                job = _set_job_pinned_helper(request.app, job_id, pinned=(action == "pin"))
                if not job:
                    return _mcp_err(req_id, f"Job {job_id!r} not found")
                return _mcp_ok(req_id, json.dumps(_enrich_job_urls(job, public_base), indent=2, ensure_ascii=False))
            else:
                delete_files = bool(args.get("delete_files", False))
                ok = await job_manager.delete_job(job_id, delete_files=delete_files)
                if not ok:
                    return _mcp_err(req_id, f"Job {job_id!r} not found")
                return _mcp_ok(req_id, json.dumps({"deleted": True, "id": job_id, "delete_files": delete_files}, indent=2))

        elif tool_name == "gate_storage_status":
            backup_res = None
            if bool(args.get("backup_now", False)):
                backup_res = await job_manager.backup_now()
            payload = await _storage_status_payload(request.app, include_profiles=True)
            if backup_res is not None:
                payload["backup"] = backup_res
            return _mcp_ok(req_id, json.dumps(payload, indent=2, ensure_ascii=False))

        return web.json_response({
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
        })

    return web.json_response({
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not supported: {method}"},
    })


async def events(request: web.Request) -> web.StreamResponse:
    s = request.app[SESSION]
    ident = _ident(request)
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
        init_st = s.status()
        if not ident.is_owner:
            init_st["cu"] = None
            init_st["woken_by"] = None
        await send("state", init_st)
        if s.progress:
            await send("progress", s.progress)
        for line in s.bus.logs_since(since):
            await send("log", line)
        while True:
            try:
                kind, data = await asyncio.wait_for(q.get(), timeout=20)
                if kind == "state" and isinstance(data, dict) and not ident.is_owner:
                    data = {**data, "cu": None, "woken_by": None}
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
    st = s.status()
    if not ident.is_owner:
        st["cu"] = None
        st["woken_by"] = None
    status_data = {
        **st,
        "queue": request.app[JOBS].active_queue_summary(),
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


async def storage_status_route(request: web.Request) -> web.Response:
    return web.json_response(await _storage_status_payload(request.app))


async def storage_backup_route(request: web.Request) -> web.Response:
    jm: JobManager = request.app[JOBS]
    res = await jm.backup_now()
    return web.json_response(res, status=200 if res.get("ok") else 502)


async def archive_profiles_route(request: web.Request) -> web.Response:
    db: GateDB = request.app[DB_KEY]
    profiles = request.app[PROFILES]
    stats = db.profile_archive_stats()
    for row in stats:
        prof = profiles.get(row["profile_id"])
        row["title"] = prof.title if prof else row["profile_id"]
    return web.json_response({
        "profiles": stats,
        "totals": db.count_jobs_by_status(),
    })


async def list_jobs_route(request: web.Request) -> web.Response:
    db: GateDB = request.app[DB_KEY]
    q_params = request.query
    profile = q_params.get("profile") or None
    status_f = q_params.get("status") or None
    q_str = q_params.get("q") or None
    pinned_raw = (q_params.get("pinned") or "").strip().lower()
    pinned_f: bool | None = None
    if pinned_raw in ("1", "true", "yes"):
        pinned_f = True
    elif pinned_raw in ("0", "false", "no"):
        pinned_f = False
    try:
        limit = max(1, min(200, int(q_params.get("limit", "50"))))
    except ValueError:
        limit = 50
    try:
        offset = max(0, int(q_params.get("offset", "0")))
    except ValueError:
        offset = 0
    include_prompt = (q_params.get("include_prompt") or "").strip().lower() in ("1", "true")
    jobs = db.list_jobs(
        profile=profile,
        status=status_f,
        q=q_str,
        pinned=pinned_f,
        limit=limit,
        offset=offset,
        include_prompt=include_prompt,
    )
    total = db.count_jobs(profile=profile, status=status_f, q=q_str, pinned=pinned_f)
    return web.json_response({
        "jobs": jobs,
        "total": total,
        "counts": db.count_jobs_by_status(profile),
        "limit": limit,
        "offset": offset,
    })


async def get_job_route(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    job = request.app[DB_KEY].get_job(job_id, include_prompt=True)
    if not job:
        return err(404, "not_found", f"job {job_id!r} not found")
    return web.json_response(job)


async def create_job_route(request: web.Request) -> web.Response:
    body = await _body(request)
    s = request.app[SESSION]
    settings = request.app[SETTINGS]
    profile = body.get("profile") or body.get("profile_id") or s.machine.profile or settings.default_profile
    if profile not in request.app[PROFILES]:
        return err(404, "unknown_profile", f"unknown profile {profile!r}")
    prompt_dict = body.get("prompt")
    if not isinstance(prompt_dict, dict) or not prompt_dict:
        return err(400, "bad_request", "prompt must be a non-empty JSON object")
    ident = _ident(request)
    extra_data = body.get("extra_data") if isinstance(body.get("extra_data"), dict) else None
    extra_inputs = body.get("inputs") if isinstance(body.get("inputs"), list) else None
    input_asset_ids = body.get("input_asset_ids") if isinstance(body.get("input_asset_ids"), list) else None
    job = await request.app[JOBS].enqueue_prompt(
        prompt_dict,
        profile_id=str(profile),
        submitted_by=ident.email,
        workflow_id=str(body.get("workflow_id") or ""),
        extra_data=extra_data,
        extra_input_filenames=[str(x) for x in extra_inputs] if extra_inputs else None,
        input_asset_ids=[str(x) for x in input_asset_ids] if input_asset_ids else None,
    )
    return web.json_response(job, status=202)


async def offline_upload_route(request: web.Request) -> web.Response:
    return await request.app[JOBS].handle_offline_upload(request)


async def retry_job_route(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    job, code = await request.app[JOBS].retry_job(job_id)
    if not job:
        return err(404, "not_found", f"job {job_id!r} not found")
    if code == "already_active":
        return err(409, "already_active", f"job {job_id!r} is already active ({job['status']})")
    return web.json_response(job, status=202)


async def cancel_job_route(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    job, code = await request.app[JOBS].cancel_job(job_id)
    if not job:
        return err(404, "not_found", f"job {job_id!r} not found")
    if code == "already_finished":
        return err(409, "already_finished", f"job {job_id!r} already finished ({job['status']})")
    return web.json_response(job)


async def pin_job_route(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    body = await _body(request)
    pinned = bool(body["pinned"]) if "pinned" in body else None
    updated = _set_job_pinned_helper(request.app, job_id, pinned=pinned)
    if not updated:
        return err(404, "not_found", f"job {job_id!r} not found")
    return web.json_response(updated)


async def delete_job_route(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    delete_files = (
        request.query.get("delete_files", "").strip().lower() in ("1", "true")
        or request.query.get("delete_unshared_outputs", "").strip().lower() in ("1", "true")
    )
    ok = await request.app[JOBS].delete_job(job_id, delete_files=delete_files)
    if not ok:
        return err(404, "not_found", f"job {job_id!r} not found")
    return web.json_response({"deleted": True, "id": job_id})


async def archive_asset_route(request: web.Request) -> web.StreamResponse:
    asset_id = request.match_info["asset_id"]
    db: GateDB = request.app[DB_KEY]
    store: ArchiveStore = request.app[STORE]
    asset = db.get_asset(asset_id)
    if not asset:
        return err(404, "not_found", f"asset {asset_id!r} not found")

    download = (request.query.get("download") or "").strip().lower() in ("1", "true")
    safe_name = re.sub(r'["\\\r\n]', "_", asset["filename"] or "file")
    range_hdr = request.headers.get("Range")

    if asset["kind"] == "input":
        ext = Path(asset["filename"]).suffix.lower()
        data = await store.get_input(asset["sha256"], ext, asset.get("remote_id", ""))
        if data is None:
            return err(404, "not_found", f"input asset bytes for {asset_id!r} missing")
        total = len(data)
        parsed = parse_range_header(range_hdr, total)
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Type": asset["mime"] or "application/octet-stream",
            "Cache-Control": "private, max-age=3600",
        }
        if download:
            headers["Content-Disposition"] = f'attachment; filename="{safe_name}"'
        if parsed == "invalid":
            return web.Response(status=416, headers={**headers, "Content-Range": f"bytes */{total}"})
        if parsed is None:
            return web.Response(status=200, body=data, headers=headers)
        start, end = parsed
        return web.Response(
            status=206,
            body=data[start : end + 1],
            headers={**headers, "Content-Range": f"bytes {start}-{end}/{total}"},
        )

    try:
        status_code, out_headers, byte_iter = await store.open_output_stream(
            asset["storage_key"], asset.get("remote_id", ""), range_header=range_hdr
        )
    except FileNotFoundError:
        return err(404, "not_found", f"output blob for {asset_id!r} not found")

    headers = {k: v for k, v in out_headers.items() if k.lower() not in ("transfer-encoding", "connection")}
    headers.setdefault("Accept-Ranges", "bytes")
    headers.setdefault("Content-Type", asset["mime"] or "application/octet-stream")
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    resp = web.StreamResponse(status=status_code, headers=headers)
    await resp.prepare(request)
    async for chunk in byte_iter:
        await resp.write(chunk)
    await resp.write_eof()
    return resp


async def archive_thumb_route(request: web.Request) -> web.StreamResponse:
    asset_id = request.match_info["asset_id"]
    asset = request.app[DB_KEY].get_asset(asset_id)
    if not asset or not asset.get("thumb_relpath"):
        return err(404, "not_found", f"thumbnail for {asset_id!r} not found")
    thumbs_root = request.app[JOBS].thumbs_dir.resolve()
    thumb_path = (thumbs_root / asset["thumb_relpath"]).resolve()
    if not thumb_path.is_relative_to(thumbs_root) or not thumb_path.is_file():
        return err(404, "not_found", f"thumbnail file for {asset_id!r} missing")
    return web.FileResponse(thumb_path, headers={"Cache-Control": "public, max-age=86400"})


async def catch_all(request: web.Request) -> web.StreamResponse:
    s = request.app[SESSION]
    s.touch()
    if request.path in ("/", "/index.html"):
        if request.app[SETTINGS].auto_stop.wake_on_visit and s.machine.state == State.SLEEPING:
            await s.wake(None, by=_ident(request).email, ip=get_client_ip(request))
        return await wake_page(request)
    if s.machine.state == State.READY:
        if request.app[SETTINGS].queue_intercept_enabled:
            if request.method == "POST" and request.path in (
                "/upload/image",
                "/upload/mask",
                "/api/upload/image",
                "/api/upload/mask",
            ):
                return await request.app[JOBS].handle_upload_proxy(request)
            if request.method == "POST" and request.path in ("/prompt", "/api/prompt"):
                return await request.app[JOBS].handle_prompt_proxy(request)
            if request.method == "GET" and (
                request.path in ("/history", "/history/", "/api/history", "/api/history/")
                or request.path.startswith("/history/")
                or request.path.startswith("/api/history/")
            ):
                return await request.app[JOBS].handle_history_proxy(request)
        try:
            return await request.app[PROXY].handle(request)
        except Exception as e:  # noqa: BLE001
            log.warning("proxy error: %s", e)
            return err(502, "backend_unreachable", "ComfyUI backend unreachable")
    api_prefixes = (
        "/api", "/ws", "/prompt", "/view", "/view_metadata",
        "/upload", "/object_info", "/embeddings", "/extensions",
        "/system_stats", "/queue", "/interrupt", "/history"
    )
    if any(request.path.startswith(p) for p in api_prefixes):
        return err(503, "asleep", f"ComfyUI is {s.machine.state.value}; POST /_gate/wake first")
    raise web.HTTPFound("/")


async def background(app: web.Application) -> None:
    s, proxy, settings, jm = app[SESSION], app[PROXY], app[SETTINGS], app[JOBS]
    sweep_every = max(1, settings.auto_stop.orphan_sweep_min) * 60
    last_sweep = 0.0
    loop = asyncio.get_running_loop()
    consecutive_probe_failures = 0
    last_hb = 0.0
    hb_task = None
    while True:
        await asyncio.sleep(30)
        try:
            if s.machine.state == State.READY:
                # Touch the Colab kernel regularly: the setup script returns after boot so the
                # kernel is free for execs, and an exec every ~90 s is what keeps the runtime
                # from being reclaimed as idle. It also refreshes the VM-side heartbeat file the
                # watchdog uses to decide the gate is still alive.
                if loop.time() - last_hb > 90 and (hb_task is None or hb_task.done()):
                    last_hb = loop.time()
                    hb_task = asyncio.create_task(s.colab.touch_heartbeat(settings.colab_session_name))
                probe_ok = await proxy.probe()
                if not probe_ok:
                    consecutive_probe_failures += 1
                    if consecutive_probe_failures >= 3:
                        log.warning("backend probe failed %d times; reconciling with colab", consecutive_probe_failures)
                        await s.reconcile()
                else:
                    consecutive_probe_failures = 0
                    s.queue_busy = (await proxy.queue_busy()) or jm.has_active_work_for(s.machine.profile)
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
                elif s.machine.is_billing and not s.boot_task_running():
                    await s.reconcile()
                bal = await s.colab.ccu_info()
                if bal is not None:
                    s.ledger.balance = bal
        except ColabError as e:
            log.warning("background: %s", e)
        except Exception:  # noqa: BLE001
            log.exception("background loop error")


def build_app(settings: Settings, colab: ColabRunner | None = None,
              profiles: dict[str, Profile] | None = None, run_background: bool = True,
              store: ArchiveStore | None = None, run_dispatcher: bool | None = None) -> web.Application:
    if run_dispatcher is None:
        run_dispatcher = run_background
    app = web.Application(middlewares=[anti_crawler_mw, auth_mw], client_max_size=512 * 1024 * 1024)
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
    resolved_store = store or build_store(settings)
    db_path = Path(settings.state_dir) / "gate.db"
    db = GateDB(db_path, defer_init=(not db_path.exists()))
    job_manager = JobManager(settings, session, app[PROXY], db, resolved_store)
    app[DB_KEY] = db
    app[STORE] = resolved_store
    app[JOBS] = job_manager

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
    r.add_get("/_gate/storage/status", storage_status_route)
    r.add_post("/_gate/storage/backup", storage_backup_route)
    r.add_get("/_gate/archive/profiles", archive_profiles_route)
    r.add_get("/_gate/archive/assets/{asset_id}", archive_asset_route)
    r.add_get("/_gate/archive/thumbs/{asset_id}", archive_thumb_route)
    r.add_get("/_gate/jobs", list_jobs_route)
    r.add_post("/_gate/jobs", create_job_route)
    r.add_post("/_gate/jobs/upload", offline_upload_route)
    r.add_get("/_gate/jobs/{job_id}", get_job_route)
    r.add_post("/_gate/jobs/{job_id}/retry", retry_job_route)
    r.add_post("/_gate/jobs/{job_id}/cancel", cancel_job_route)
    r.add_post("/_gate/jobs/{job_id}/pin", pin_job_route)
    r.add_delete("/_gate/jobs/{job_id}", delete_job_route)
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
        if not db_path.exists():
            try:
                await resolved_store.restore_db_if_missing(db_path)
            except Exception as e:  # noqa: BLE001
                log.warning("cold DB restore failed: %s", e)
        db.ensure_initialized()
        db.mark_all_running_interrupted("gate restarted")
        await session.reconcile()
        if run_background:
            app[BG] = asyncio.create_task(background(app))
        if run_dispatcher:
            app[DISPATCHER] = asyncio.create_task(job_manager.run())

    async def on_cleanup(app: web.Application) -> None:
        if BG in app:
            app[BG].cancel()
        job_manager.stop()
        if DISPATCHER in app:
            app[DISPATCHER].cancel()
            try:
                await app[DISPATCHER]
            except asyncio.CancelledError:
                pass
        if job_manager.db_dirty:
            try:
                await job_manager.backup_now()
            except Exception as e:  # noqa: BLE001
                log.warning("final DB backup failed on cleanup: %s", e)
        await resolved_store.close()
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
