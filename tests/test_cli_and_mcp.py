import json
from pathlib import Path
import pytest
from aiohttp.test_utils import TestClient, TestServer
from ckit.moss_template import make_moss_txt2img_graph, resolve_preset, PRESETS
from ckit.mcp_server import TOOLS
from gate.app import build_app
from gate.profiles import load_profile
from gate.config import Settings
from tests.fakes import FakeColab

ROOT = Path(__file__).resolve().parent.parent


def test_resolve_preset():
    choice, idx = resolve_preset("Fast alt")
    assert idx == 4
    assert choice == PRESETS[4]

    choice, idx = resolve_preset("quality")
    assert idx == 0

    choice, idx = resolve_preset("unknown")
    assert idx == 4  # default


def test_make_moss_txt2img_graph():
    g = make_moss_txt2img_graph(
        prompt="a stunning cosmic galaxy",
        negative_prompt="blurry, noisy",
        preset="Fast alt",
        seed=12345,
        width=1024,
        height=768
    )
    assert g["1"]["inputs"]["choice"] == PRESETS[4]
    assert g["1"]["inputs"]["index"] == 4
    assert g["6"]["inputs"]["value"] == "a stunning cosmic galaxy"
    assert g["8"]["inputs"]["text"] == "blurry, noisy"
    assert g["22"]["inputs"]["value"] == 12345
    assert g["23"]["inputs"]["width"] == 1024
    assert g["23"]["inputs"]["height"] == 768
    assert g["26"]["class_type"] == "SaveImage"


def test_mcp_server_tools_list():
    tool_names = [t["name"] for t in TOOLS]
    assert "gate_status" in tool_names
    assert "gate_wake" in tool_names
    assert "gate_txt2img" in tool_names
    assert "gate_generate" in tool_names
    assert "gate_stop" in tool_names

    txt2img = next(t for t in TOOLS if t["name"] == "gate_txt2img")
    assert "prompt" in txt2img["inputSchema"]["required"]
    assert "preset" in txt2img["inputSchema"]["properties"]
    assert "seed" in txt2img["inputSchema"]["properties"]


@pytest.mark.asyncio
async def test_gate_mcp_endpoint_tools_list(tmp_path):
    s = Settings.from_env({"AUTH_DISABLED": "true", "STATE_DIR": str(tmp_path), "DEFAULT_PROFILE": "tiny"})
    prof = {"tiny": load_profile(ROOT / "tests/fixtures/profiles/tiny")}
    app = build_app(s, FakeColab(), prof, run_background=False)
    c = TestClient(TestServer(app))
    await c.start_server()

    try:
        # 1. GET /mcp
        r = await c.get("/mcp")
        assert r.status == 200
        data = await r.json()
        assert "gate_txt2img" in data["tools"]

        # 2. POST /mcp tools/list
        r2 = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r2.status == 200
        data_list = await r2.json()
        tools = [t["name"] for t in data_list["result"]["tools"]]
        assert "gate_txt2img" in tools
        assert "gate_generate" in tools
        assert "gate_status" in tools
        assert "gate_wake" in tools
        assert "gate_stop" in tools
    finally:
        await c.close()
