"""comfy-gate HTTP server (aiohttp)."""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from pathlib import Path

from aiohttp import web

from . import __version__
from .auth import AuthError, Identity, JWKSCache, authorize, identify
from .colab import ColabCLI, ColabError, ColabRunner
from .config import Settings, read_secret_file
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
PUBLIC_PATHS = ("/_gate/healthz",)
SESSION = web.AppKey("session", Session)
SETTINGS = web.AppKey("settings", Settings)
PROXY = web.AppKey("proxy", Proxy)
PROFILES = web.AppKey("profiles", dict)
PROFILE_ERRORS = web.AppKey("profile_errors", dict)
JWKS = web.AppKey("jwks", object)
AGENT_TOKEN = web.AppKey("agent_token", str)
BG = web.AppKey("bg", object)
IDENT = web.RequestKey("identity", object) if hasattr(web, "RequestKey") else "identity"


def _fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310 (fixed https URL)
        return json.loads(r.read())


def err(status: int, code: str, msg: str) -> web.Response:
    return web.json_response({"error": code, "message": msg}, status=status)


@web.middleware
async def auth_mw(request: web.Request, handler):
    if request.path in PUBLIC_PATHS or request.path.startswith("/_gate/static/"):
        return await handler(request)
    app = request.app
    try:
        ident = await asyncio.get_running_loop().run_in_executor(
            None, identify, request.headers, app[SETTINGS], app[JWKS], app[AGENT_TOKEN])
    except AuthError as e:
        return err(e.status, e.code, e.msg)
    request[IDENT] = ident
    return await handler(request)


def _ident(request: web.Request) -> Identity:
    return request[IDENT]


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
    return web.json_response({**s.status(), "you": {"email": ident.email, "owner": ident.is_owner},
                              "owner_only": list(request.app[SETTINGS].owner_only_actions), "version": __version__})


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
    # if a session is already starting/running, the caller joins it (its profile wins);
    # changing profile is the separate, owner-only /_gate/switch action
    try:
        res = await s.wake(profile, by=ident.email, workflows=body.get("workflows"))
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
    try:
        res = await request.app[SESSION].switch(str(body.get("profile", "")), by=ident.email)
    except KeyError:
        return err(404, "unknown_profile", "unknown profile")
    if res.refused:
        return err(409, res.refused, f"switch refused: {res.refused}")
    return web.json_response({"state": res.state, "profile": res.profile, "mode": "cold"}, status=202)


async def stop(request: web.Request) -> web.Response:
    ident = _ident(request)
    try:
        authorize(ident, "stop", request.app[SETTINGS])
    except AuthError as e:
        return err(e.status, e.code, e.msg)
    await request.app[SESSION].stop(f"stopped by {ident.email}")
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
        logs = list(s.bus.logs)
        for line in logs[since:]:
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


async def wake_page(request: web.Request) -> web.Response:
    html = (WEB / "index.html").read_text()
    s = request.app[SESSION]
    ident = _ident(request)
    settings = request.app[SETTINGS]
    status_data = {
        **s.status(),
        "you": {"email": ident.email, "owner": ident.is_owner},
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
    if s.machine.state == State.READY:
        try:
            return await request.app[PROXY].handle(request)
        except Exception as e:  # noqa: BLE001
            log.warning("proxy error: %s", e)
            return err(502, "backend_unreachable", "ComfyUI backend unreachable")
    if request.app[SETTINGS].auto_stop.wake_on_visit and s.machine.state == State.SLEEPING and request.path == "/":
        await s.wake(None, by=_ident(request).email)
    if request.path in ("/", "/index.html"):
        return await wake_page(request)
    if request.path.startswith(("/api", "/ws", "/prompt", "/view")):
        return err(503, "asleep", f"ComfyUI is {s.machine.state.value}; POST /_gate/wake first")
    raise web.HTTPFound("/")


async def background(app: web.Application) -> None:
    s, proxy, settings = app[SESSION], app[PROXY], app[SETTINGS]
    sweep_every = max(1, settings.auto_stop.orphan_sweep_min) * 60
    last_sweep = 0.0
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(30)
        try:
            if s.machine.state == State.READY:
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
                bal = await s.colab.ccu_info()
                if bal is not None:
                    s.ledger.balance = bal
        except ColabError as e:
            log.warning("background: %s", e)
        except Exception:  # noqa: BLE001
            log.exception("background loop error")


def build_app(settings: Settings, colab: ColabRunner | None = None,
              profiles: dict[str, Profile] | None = None, run_background: bool = True) -> web.Application:
    app = web.Application(middlewares=[auth_mw], client_max_size=64 * 1024 * 1024)
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
    r = app.router
    r.add_get("/_gate/healthz", healthz)
    r.add_get("/_gate/status", status)
    r.add_get("/_gate/events", events)
    r.add_get("/_gate/profiles", profiles_route)
    r.add_post("/_gate/wake", wake)
    r.add_post("/_gate/switch", switch)
    r.add_post("/_gate/fetch", fetch)
    r.add_post("/_gate/stop", stop)
    r.add_post("/_gate/extend", extend)
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
