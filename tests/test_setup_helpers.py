import importlib.util
import json
import struct
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
colab_setup = ROOT / "packages/colab/setup.py" if (ROOT / "packages/colab/setup.py").is_file() else ROOT / "colab/setup.py"
spec = importlib.util.spec_from_file_location("kit_setup", colab_setup)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

GRAPH = {"nodes": [{"id": 12, "type": "LoraLoaderModelOnly", "widgets_values": ["a.safetensors", 1.0]},
                   {"id": 9, "type": "SaveImage", "widgets_values": ["x"]}], "extra": {"ds": {}}}
WF = {"id": "w", "format": "json", "app_inputs": [[3, "text"]], "app_outputs": [9],
      "overrides": [{"node": 12, "widget_index": 0, "value": "b.safetensors"}]}


def test_prepare_workflow_sets_app_mode_and_override():
    g = setup.prepare_workflow(WF, json.dumps(GRAPH).encode(), "app")
    assert g["extra"]["linearMode"] is True
    assert g["extra"]["linearData"] == {"inputs": [[3, "text"]], "outputs": [9]}
    assert g["nodes"][0]["widgets_values"][0] == "b.safetensors"
    assert g["extra"]["ds"] == {}
    g = setup.prepare_workflow(WF, json.dumps(GRAPH).encode(), "graph")
    assert g["extra"]["linearMode"] is False


def test_override_on_missing_node_fails_loudly():
    wf = {**WF, "overrides": [{"node": 99, "widget_index": 0, "value": "x"}]}
    with pytest.raises(RuntimeError, match="upstream workflow changed"):
        setup.prepare_workflow(wf, json.dumps(GRAPH).encode(), "app")


def test_prepare_workflow_preserves_node_cleanliness():
    wf = {
        "id": "w",
        "format": "json",
        "app_inputs": [
            [3, "text"],
            [4, "text", "自定义负向词 (Custom Negative)"]
        ],
        "app_outputs": [9]
    }
    graph = {
        "nodes": [
            {"id": 3, "type": "CLIPTextEncode", "title": "Prompt", "widgets_values": ["prompt"], "inputs": []},
            {"id": 4, "type": "CLIPTextEncode", "title": "Negative", "widgets_values": ["neg"], "inputs": []},
            {"id": 9, "type": "SaveImage", "widgets_values": ["x"]}
        ],
        "extra": {}
    }
    g = setup.prepare_workflow(wf, json.dumps(graph).encode(), "app")
    node3 = g["nodes"][0]
    node4 = g["nodes"][1]

    # Invariant: node["widgets"] MUST NOT be created in serialization JSON
    assert "widgets" not in node3, "Must never serialize 'widgets' key on nodes (clobbers live LiteGraph widgets)"
    assert "widgets" not in node4, "Must never serialize 'widgets' key on nodes"

    # Canonical backing input slots in node["inputs"] for widget labels
    assert len(node3.get("inputs", [])) == 1
    slot3 = node3["inputs"][0]
    assert slot3.get("widget", {}).get("name") == "text"
    assert slot3.get("label") == "正向提示词 (Positive Prompt)"
    assert slot3.get("link") is None

    assert len(node4.get("inputs", [])) == 1
    slot4 = node4["inputs"][0]
    assert slot4.get("widget", {}).get("name") == "text"
    assert slot4.get("label") == "自定义负向词 (Custom Negative)"
    assert slot4.get("link") is None

    # extra.linearData.inputs must stay clean 2-tuples [nid, wname]
    assert g["extra"]["linearData"]["inputs"] == [[3, "text"], [4, "text"]]


def _png_with(text_chunks):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    body = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    for k, v in text_chunks:
        body += chunk(b"tEXt", k.encode() + b"\0" + v.encode())
    return b"\x89PNG\r\n\x1a\n" + body + chunk(b"IEND", b"")


def test_png_embedded_workflow():
    png = _png_with([("prompt", "{}"), ("workflow", json.dumps(GRAPH))])
    assert setup.png_workflow(png)["nodes"][1]["id"] == 9
    with pytest.raises(RuntimeError):
        setup.png_workflow(_png_with([("prompt", "{}")]))


def test_comfy_cmd_uses_profile_args(monkeypatch):
    monkeypatch.setattr(setup, "PLAN", {"comfyui": {"args": ["--fast"], "extra_model_paths": None}})
    cmd = setup.comfy_cmd()
    assert cmd[:2] == ["python", "main.py"] and cmd[-1] == "--fast"
    assert cmd.count("--listen") == 1


def test_smoke_prompt_unwraps_request_body():
    import importlib.util, pathlib
    p = pathlib.Path(__file__).parent.parent / "packages/colab/setup.py"
    if not p.is_file():
        p = pathlib.Path(__file__).parent.parent / "colab/setup.py"
    spec = importlib.util.spec_from_file_location("kit_setup", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    g = {"1": {"class_type": "X", "inputs": {}}}
    assert m.smoke_prompt({"prompt": g, "presets": []}) == g
    assert m.smoke_prompt(g) == g



def test_remap_models_rewrites_nodes_subgraphs_and_api_inputs():
    al = {"moss_image_sft_int8_convrot.safetensors": "moss_image_sft_bf16.safetensors"}
    g = {"nodes": [{"id": 1, "widgets_values": ["moss_image_sft_int8_convrot.safetensors", "default"]}],
         "definitions": {"subgraphs": [{"nodes": [{"id": 2, "widgets_values": ["moss_image_sft_int8_convrot.safetensors"]}]}]}}
    assert setup.remap_models(g, al) == 2
    assert g["nodes"][0]["widgets_values"][0] == "moss_image_sft_bf16.safetensors"
    api = {"3": {"class_type": "UNETLoader", "inputs": {"unet_name": "moss_image_sft_int8_convrot.safetensors"}}}
    setup.remap_models(api, al)
    assert api["3"]["inputs"]["unet_name"] == "moss_image_sft_bf16.safetensors"


def _fake_models(tmp_path, monkeypatch, script):
    monkeypatch.setattr(setup, "COMFY", str(tmp_path / "ComfyUI"))
    monkeypatch.setattr(setup, "HF_DL_SCRIPT", script)
    monkeypatch.setattr(setup, "HF_TMP", str(tmp_path / "hf"))
    return [{"id": "a", "dest": "x/a.bin", "url": "https://huggingface.co/o/r/resolve/main/a.bin",
             "size_gb": 0.000001, "core": True}]


import pathlib  # noqa: E402


def test_stalled_file_is_restarted(tmp_path, monkeypatch):
    # first attempt writes nothing and hangs; the second attempt (marker present) succeeds
    script = r"""
import os, sys, time
d = sys.argv[4]; os.makedirs(d, exist_ok=True)
marker = d + '.tried'
if not os.path.exists(marker):
    open(marker, 'w').close(); time.sleep(60)
p = os.path.join(d, 'a.bin'); open(p, 'wb').write(b'x' * 1000); print(p)
"""
    models = _fake_models(tmp_path, monkeypatch, script)
    dl = setup.Downloads(models, 2, busy=lambda: False, stall_s=4)
    dl.start()
    dl.wait(["a"])
    assert dl.restarts == 1 and dl.files_done == 1
    assert (tmp_path / "ComfyUI/models/x/a.bin").exists()
    p = dl.progress(0)
    assert p["bytes_total"] == 1000 and p["core_done"] is True
