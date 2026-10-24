from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from gate.app import SESSION, build_app
from gate.config import Settings
from gate.profiles import load_profile
from gate.state import State
from tests.fakes import FakeColab

ROOT = Path(__file__).resolve().parent.parent


async def client(tmp_path, **env):
    s = Settings.from_env({"AUTH_DISABLED": "true", "STATE_DIR": str(tmp_path), "DEFAULT_PROFILE": "tiny",
                           "AGENT_TOKEN_FILE": "", **env})
    prof = {"tiny": load_profile(ROOT / "tests/fixtures/profiles/tiny")}
    app = build_app(s, FakeColab(), prof, run_background=False)
    c = TestClient(TestServer(app))
    await c.start_server()
    return c, app


async def test_wake_page_and_api(tmp_path):
    c, app = await client(tmp_path)
    try:
        r = await c.get("/")
        assert r.status == 200 and "Comfy Colab Kit" in await r.text()
        r = await c.get("/_gate/profiles")
        assert (await r.json())["profiles"][0]["id"] == "tiny"
        r = await c.post("/_gate/wake", json={"profile": "tiny"})
        assert r.status == 202
        r2 = await c.post("/_gate/wake", json={})
        assert r2.status == 200 and (await r2.json())["joined"] is True
        r = await c.post("/_gate/wake", json={"profile": "nope"})
        assert r.status in (200, 404)
        r = await c.get("/api/queue")
        assert r.status in (503, 502)
        r = await c.get("/_gate/healthz")
        assert r.status == 200
    finally:
        await c.close()


async def test_requires_access_when_auth_enabled(tmp_path):
    s = Settings.from_env({"CF_ACCESS_TEAM_DOMAIN": "t.cloudflareaccess.com", "CF_ACCESS_AUD": "x",
                           "STATE_DIR": str(tmp_path)})
    app = build_app(s, FakeColab(), {"tiny": load_profile(ROOT / "tests/fixtures/profiles/tiny")},
                    run_background=False)
    c = TestClient(TestServer(app))
    await c.start_server()
    try:
        assert (await c.get("/_gate/status")).status == 401
        assert (await c.post("/_gate/wake")).status == 401
        assert (await c.get("/_gate/healthz")).status == 200
        assert (await c.get("/_gate/static/overlay.js")).status == 200
    finally:
        await c.close()


async def test_stop_endpoint(tmp_path):
    c, app = await client(tmp_path)
    try:
        await c.post("/_gate/wake", json={})
        sess = app[SESSION]
        import asyncio
        for _ in range(200):
            if sess.machine.state == State.READY:
                break
            await asyncio.sleep(0.01)
        r = await c.post("/_gate/stop")
        assert (await r.json())["state"] == "sleeping"
    finally:
        await c.close()


async def test_events_sse_replays_logs_and_progress(tmp_path):
    c, app = await client(tmp_path)
    try:
        sess = app[SESSION]
        sess.progress = {"download": {"bytes_done": 100, "bytes_total": 200}}
        sess.bus.publish("log", "historical line 1")
        sess.bus.publish("log", "historical line 2")

        res = await c.get("/_gate/events")
        assert res.status == 200
        body = b""
        for _ in range(10):
            chunk = await res.content.read(1024)
            body += chunk
            if b"historical line 2" in body and b"bytes_done" in body:
                break
        assert b"historical line 1" in body
        assert b"historical line 2" in body
        assert b"bytes_done" in body
    finally:
        await c.close()


async def test_wake_page_inlines_initial_data(tmp_path):
    c, app = await client(tmp_path)
    try:
        r = await c.get("/")
        assert r.status == 200
        text = await r.text()
        assert "window.__INIT__" in text
        assert '"profiles":' in text
        assert '"status":' in text
    finally:
        await c.close()


async def test_client_deduplication_by_cid(tmp_path):
    c, app = await client(tmp_path)
    sess = app[SESSION]
    try:
        r1 = await c.get("/_gate/events?cid=tab-1")
        assert sess.open_clients == 1
        r2 = await c.get("/_gate/events?cid=tab-1")
        assert sess.open_clients == 1

        r3 = await c.get("/_gate/events?cid=tab-2")
        assert sess.open_clients == 2

        r1.close()
        assert sess.open_clients == 2
        r2.close()
        import asyncio
        await asyncio.sleep(0.05)
        assert sess.open_clients == 1
    finally:
        await c.close()


async def test_robots_txt_and_crawler_blocking(tmp_path):
    c, app = await client(tmp_path)
    try:
        r = await c.get("/robots.txt")
        assert r.status == 200
        text = await r.text()
        assert "User-agent: *" in text
        assert "Disallow: /" in text
        assert "noindex" in r.headers.get("X-Robots-Tag", "")

        r = await c.get("/")
        assert r.headers.get("X-Robots-Tag") == "noindex, nofollow, noarchive, nosnippet"

        r_bot = await c.get("/", headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"})
        assert r_bot.status == 403
        assert "forbidden" in (await r_bot.text()).lower()
    finally:
        await c.close()


async def test_mcp_endpoint(tmp_path):
    c, app = await client(tmp_path)
    try:
        r = await c.get("/mcp")
        assert r.status == 200
        info = await r.json()
        assert info["name"] == "comfy-colab-kit"

        r = await c.post("/_gate/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert r.status == 200
        init_res = await r.json()
        assert init_res["result"]["serverInfo"]["name"] == "comfy-colab-kit"

        r = await c.post("/_gate/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert r.status == 200
        tools_res = await r.json()
        tool_names = [t["name"] for t in tools_res["result"]["tools"]]
        assert "gate_status" in tool_names
        assert "gate_wake" in tool_names
        assert "gate_stop" in tool_names

        r = await c.post("/_gate/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "gate_status"}})
        assert r.status == 200
        call_res = await r.json()
        assert "state" in call_res["result"]["content"][0]["text"]
    finally:
        await c.close()


async def test_stop_restricted_to_initiator_and_owner(tmp_path):
    c, app = await client(tmp_path, OWNER_EMAILS="boss@firetable.tech")
    try:
        # Wake from IP 1.1.1.1
        r = await c.post("/_gate/wake", json={}, headers={"CF-Connecting-IP": "1.1.1.1"})
        assert r.status == 202

        sess = app[SESSION]
        import asyncio
        for _ in range(200):
            if sess.machine.state == State.READY:
                break
            await asyncio.sleep(0.01)

        # Another user from IP 2.2.2.2 checks status: can_stop is False
        r = await c.get("/_gate/status", headers={"CF-Connecting-IP": "2.2.2.2"})
        status_data = await r.json()
        assert status_data["can_stop"] is False
        assert status_data["you"]["can_stop"] is False

        # Another user from IP 2.2.2.2 attempts stop: 403 Forbidden
        r = await c.post("/_gate/stop", headers={"CF-Connecting-IP": "2.2.2.2"})
        assert r.status == 403
        err_data = await r.json()
        assert err_data["error"] == "not_initiator"

        # Initiator from IP 1.1.1.1 checks status: can_stop is True
        r = await c.get("/_gate/status", headers={"CF-Connecting-IP": "1.1.1.1"})
        assert (await r.json())["can_stop"] is True

        # Initiator from IP 1.1.1.1 calls stop: 200 OK
        r = await c.post("/_gate/stop", headers={"CF-Connecting-IP": "1.1.1.1"})
        assert r.status == 200
        assert (await r.json())["state"] == "sleeping"
    finally:
        await c.close()


async def test_owner_only_control_mode(tmp_path):
    # Mode where only owner can wake AND only owner can stop
    c, app = await client(tmp_path, OWNER_ONLY_CONTROL="true", OWNER_EMAILS="boss@firetable.tech")
    try:
        # Non-owner checks status: can_wake and can_stop are False
        r = await c.get("/_gate/status", headers={"CF-Connecting-IP": "2.2.2.2"})
        status_data = await r.json()
        assert status_data["can_wake"] is False
        assert status_data["can_stop"] is False

        # Non-owner attempts wake: 403 owner_only
        r = await c.post("/_gate/wake", json={}, headers={"CF-Connecting-IP": "2.2.2.2"})
        assert r.status == 403
        assert (await r.json())["error"] == "owner_only"
    finally:
        await c.close()


