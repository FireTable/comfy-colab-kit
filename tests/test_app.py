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
