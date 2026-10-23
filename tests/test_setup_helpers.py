import importlib.util
import json
import struct
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("kit_setup", ROOT / "colab/setup.py")
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
