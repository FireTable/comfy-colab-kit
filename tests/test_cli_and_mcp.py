import json
from pathlib import Path
import pytest
from aiohttp.test_utils import TestClient, TestServer
from ckit.moss_template import (
    PRESETS,
    make_img2img_graph,
    make_moss_img2img_graph,
    make_moss_txt2img_graph,
    make_sd35_img2img_graph,
    make_sd35_txt2img_graph,
    make_txt2img_graph,
    resolve_preset,
)
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


def test_make_moss_img2img_graph_and_multi_profile():
    g = make_moss_img2img_graph(
        prompt="a beach variation",
        image="ref_upload.png",
        preset="Quality",
        denoise=0.65,
        seed=42,
    )
    assert g["30"]["class_type"] == "LoadImage"
    assert g["30"]["inputs"]["image"] == "ref_upload.png"
    assert g["23"]["class_type"] == "VAEEncode"
    assert g["20"]["inputs"]["denoise"] == 0.65
    assert g["22"]["inputs"]["value"] == 42

    sd_t2i = make_txt2img_graph(profile="sd-latest", prompt="a mountain lake", seed=99, width=1024, height=1024)
    assert sd_t2i["4"]["class_type"] == "CheckpointLoaderSimple"
    assert sd_t2i["16"]["inputs"]["text"] == "a mountain lake"
    assert sd_t2i["3"]["inputs"]["seed"] == 99

    sd_i2i = make_img2img_graph(profile="sd-latest", prompt="oil painting style", image="input.png", denoise=0.5, seed=77)
    assert sd_i2i["54"]["inputs"]["image"] == "input.png"
    assert sd_i2i["53"]["class_type"] == "VAEEncode"
    assert sd_i2i["3"]["inputs"]["denoise"] == 0.5

    flux_t2i = make_txt2img_graph(profile="flux-klein", prompt="a neon cyber cat", seed=101, width=1024, height=1024)
    assert flux_t2i["70"]["class_type"] == "UNETLoader"
    assert flux_t2i["74"]["inputs"]["text"] == "a neon cyber cat"
    assert flux_t2i["73"]["inputs"]["noise_seed"] == 101

    flux_i2i = make_img2img_graph(profile="flux-klein", prompt="turn the background into mars", image="cat.png", seed=202)
    assert flux_i2i["76"]["inputs"]["image"] == "cat.png"
    assert flux_i2i["123"]["class_type"] == "ReferenceLatent"
    assert flux_i2i["74"]["inputs"]["text"] == "turn the background into mars"

    wan_t2v = make_txt2img_graph(profile="wan-ltx-video", prompt="waves crashing on black sand beach", seed=303)
    assert wan_t2v["37"]["class_type"] == "UNETLoader"
    assert wan_t2v["55"]["class_type"] == "Wan22ImageToVideoLatent"
    assert "start_image" not in wan_t2v["55"]["inputs"]
    assert wan_t2v["58"]["class_type"] == "SaveVideo"

    wan_i2v = make_img2img_graph(profile="wan-ltx-video", prompt="camera pans left slowly", image="beach.png", seed=404)
    assert wan_i2v["56"]["class_type"] == "LoadImage"
    assert wan_i2v["56"]["inputs"]["image"] == "beach.png"
    assert wan_i2v["55"]["inputs"]["start_image"] == ["56", 0]
    assert wan_i2v["58"]["class_type"] == "SaveVideo"


def test_mcp_server_tools_list():
    tool_names = [t["name"] for t in TOOLS]
    assert "gate_status" in tool_names
    assert "gate_wake" in tool_names
    assert "gate_txt2img" in tool_names
    assert "gate_img2img" in tool_names
    assert "gate_generate" in tool_names
    assert "gate_stop" in tool_names

    txt2img = next(t for t in TOOLS if t["name"] == "gate_txt2img")
    assert "prompt" in txt2img["inputSchema"]["required"]
    assert "preset" in txt2img["inputSchema"]["properties"]
    assert "seed" in txt2img["inputSchema"]["properties"]

    img2img = next(t for t in TOOLS if t["name"] == "gate_img2img")
    assert set(img2img["inputSchema"]["required"]) == {"image", "prompt"}
    assert "denoise" in img2img["inputSchema"]["properties"]


@pytest.mark.asyncio
async def test_gate_mcp_endpoint_tools_list(tmp_path):
    s = Settings.from_env({"AUTH_DISABLED": "true", "STATE_DIR": str(tmp_path / "state"), "DEFAULT_PROFILE": "tiny"})
    prof = {"tiny": load_profile(ROOT / "tests/fixtures/profiles/tiny")}
    app = build_app(s, FakeColab(), prof, run_background=False)
    c = TestClient(TestServer(app))
    await c.start_server()

    expected_tools = {
        "gate_status",
        "gate_wake",
        "gate_txt2img",
        "gate_img2img",
        "gate_generate",
        "gate_stop",
        "gate_queue_job",
        "gate_list_jobs",
        "gate_get_job",
        "gate_manage_job",
        "gate_storage_status",
    }

    async def call_mcp(tool_name: str, arguments: dict) -> dict:
        resp = await c.post("/mcp", json={
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        })
        assert resp.status == 200
        body = await resp.json()
        assert body["result"].get("isError") is not True, body
        return json.loads(body["result"]["content"][0]["text"])

    try:
        # 1. GET /mcp
        r = await c.get("/mcp")
        assert r.status == 200
        data = await r.json()
        assert len(data["tools"]) == 11
        assert set(data["tools"]) == expected_tools

        # 2. initialize includes instructions
        r_init = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 0, "method": "initialize"})
        assert r_init.status == 200
        init_res = await r_init.json()
        assert "gate_queue_job" in init_res["result"]["instructions"]

        # 3. POST /mcp tools/list
        r2 = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r2.status == 200
        data_list = await r2.json()
        tools = [t["name"] for t in data_list["result"]["tools"]]
        assert len(tools) == 11
        assert set(tools) == expected_tools

        # 4. Call all 5 offline queue/archive tools while GPU is sleeping
        prompt = {
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "sleeping mcp queue"}},
            "3": {"class_type": "KSampler", "inputs": {"seed": 11}},
        }
        q_res = await call_mcp("gate_queue_job", {"prompt": prompt, "profile": "tiny"})
        jid = q_res["job_id"]
        assert q_res["status"] == "queued"
        assert q_res["profile_id"] == "tiny"

        list_res = await call_mcp("gate_list_jobs", {"status": "queued", "query": "sleeping mcp", "limit": 10})
        assert list_res["total"] == 1
        assert list_res["jobs"][0]["id"] == jid

        get_res = await call_mcp("gate_get_job", {"job_id": jid, "include_prompt": False})
        assert get_res["id"] == jid
        assert "prompt" not in get_res

        pin_res = await call_mcp("gate_manage_job", {"job_id": jid, "action": "pin"})
        assert pin_res["pinned"] is True
        unpin_res = await call_mcp("gate_manage_job", {"job_id": jid, "action": "unpin"})
        assert unpin_res["pinned"] is False

        cancel_res = await call_mcp("gate_manage_job", {"job_id": jid, "action": "cancel"})
        assert cancel_res["status"] == "canceled"

        retry_res = await call_mcp("gate_manage_job", {"job_id": jid, "action": "retry"})
        assert retry_res["status"] == "queued"

        st_res = await call_mcp("gate_storage_status", {"backup_now": True})
        assert st_res["backend"] == "local"
        assert st_res["backup"]["ok"] is True
        assert st_res["counts"]["queued"] == 1
        assert st_res["profiles"][0]["profile_id"] == "tiny"

        del_res = await call_mcp("gate_manage_job", {"job_id": jid, "action": "delete", "delete_files": True})
        assert del_res["deleted"] is True
        list_after = await call_mcp("gate_list_jobs", {})
        assert list_after["total"] == 0
    finally:
        await c.close()


def test_cli_gate_url_flag(monkeypatch):
    import argparse
    import ckit.cli as cli_mod
    ap = argparse.ArgumentParser(prog="ckit")
    ap.add_argument("cmd", choices=["status", "mcp"])
    ap.add_argument("--gate-url", "--url", dest="gate_url")
    args = ap.parse_args(["--gate-url", "http://localhost:8080", "status"])
    assert args.gate_url == "http://localhost:8080"
    assert args.cmd == "status"

    args2 = ap.parse_args(["mcp", "--url", "http://127.0.0.1:9090"])
    assert args2.gate_url == "http://127.0.0.1:9090"
    assert args2.cmd == "mcp"

