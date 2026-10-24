"""Tests for SQLite database layer and RFC 8628 Device Authorization flow."""
from pathlib import Path
import pytest
from aiohttp.test_utils import TestClient, TestServer

from gate.app import build_app
from gate.config import Settings
from gate.db import GateDB
from gate.profiles import load_profile
from tests.fakes import FakeColab

ROOT = Path(__file__).resolve().parent.parent


def test_gate_db_device_flow(tmp_path):
    db_file = tmp_path / "test_gate.db"
    db = GateDB(db_file)

    # 1. Create device code
    code_info = db.create_device_code(client_name="Test-Cursor", expires_in_sec=60)
    dev_code = code_info["device_code"]
    user_code = code_info["user_code"]
    assert dev_code.startswith("dev_")
    assert "-" in user_code

    # 2. Poll while pending
    poll1 = db.poll_device_token(dev_code)
    assert poll1["status"] == "pending"

    # 3. Approve device code
    app_res = db.approve_device_code(user_code, email="alice@example.com", token_name="Cursor-Mac")
    raw_token = app_res["token"]
    assert raw_token.startswith("sk_agent_")
    assert app_res["email"] == "alice@example.com"

    # 4. Poll after approved
    poll2 = db.poll_device_token(dev_code)
    assert poll2["status"] == "approved"
    assert poll2["access_token"] == raw_token
    assert poll2["email"] == "alice@example.com"

    # 5. Verify token works
    verified = db.verify_token(raw_token)
    assert verified is not None
    assert verified["email"] == "alice@example.com"
    assert verified["name"] == "Cursor-Mac"

    # 6. List tokens
    tokens = db.list_tokens(email="alice@example.com")
    assert len(tokens) == 1
    assert tokens[0]["name"] == "Cursor-Mac"
    assert not tokens[0]["revoked"]

    # 7. Revoke token
    ok = db.revoke_token(tokens[0]["id"], email="alice@example.com")
    assert ok is True
    # Now verify should fail
    assert db.verify_token(raw_token) is None


@pytest.mark.asyncio
async def test_device_auth_endpoints(tmp_path):
    settings = Settings.from_env({
        "STATE_DIR": str(tmp_path),
        "AUTH_DISABLED": "true",
        "OWNER_EMAILS": "admin@example.com",
        "DEFAULT_PROFILE": "tiny",
    })
    prof = {"tiny": load_profile(ROOT / "tests/fixtures/profiles/tiny")}
    app = build_app(settings, colab=FakeColab(), profiles=prof, run_background=False)
    client = TestClient(TestServer(app))
    await client.start_server()

    try:
        # Step 1: Request device code
        res = await client.post("/api/auth/device/code", json={"client_name": "Cursor"})
        assert res.status == 200
        data = await res.json()
        assert "device_code" in data
        assert "user_code" in data
        user_code = data["user_code"]
        dev_code = data["device_code"]

        # Step 2: Poll pending
        res = await client.post("/api/auth/device/token", json={"device_code": dev_code})
        assert res.status == 400
        assert (await res.json())["error"] == "authorization_pending"

        # Step 3: Approve code (runs as dev@localhost under AUTH_DISABLED)
        res = await client.post("/api/auth/device/approve", json={"user_code": user_code})
        assert res.status == 200
        assert (await res.json())["ok"] is True

        # Step 4: Poll token (should now succeed)
        res = await client.post("/api/auth/device/token", json={"device_code": dev_code})
        assert res.status == 200
        tok_data = await res.json()
        assert "access_token" in tok_data
        token = tok_data["access_token"]
        assert token.startswith("sk_agent_")

        # Step 5: Call status using the bearer token
        res = await client.get("/_gate/status", headers={"Authorization": f"Bearer {token}"})
        assert res.status == 200
        st_data = await res.json()
        assert st_data["you"]["email"] == "dev@localhost"

        # Step 6: List tokens
        res = await client.get("/api/agent-tokens")
        assert res.status == 200
        toks = (await res.json())["tokens"]
        assert len(toks) >= 1
        tid = toks[0]["id"]

        # Step 7: Revoke token
        res = await client.post(f"/api/agent-tokens/{tid}/revoke")
        assert res.status == 200
        assert (await res.json())["ok"] is True

        # Step 8: Token is now rejected
        res = await client.get("/_gate/status", headers={"Authorization": f"Bearer {token}"})
        assert res.status == 401

        # Step 9: Test direct token creation (web UI flow without ckit)
        res = await client.post("/api/agent-tokens", json={"name": "Cursor-Direct"})
        assert res.status == 200
        dir_data = await res.json()
        assert "token" in dir_data
        assert dir_data["token"].startswith("sk_agent_")
        assert dir_data["name"] == "Cursor-Direct"
        assert dir_data["email"] == "dev@localhost"

        # Verify direct token works
        res = await client.get("/_gate/status", headers={"Authorization": f"Bearer {dir_data['token']}"})
        assert res.status == 200
    finally:
        await client.close()
