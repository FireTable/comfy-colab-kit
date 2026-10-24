#!/usr/bin/env python3
"""Comfy Colab Kit — Model Context Protocol (MCP) Server.

Exposes comfy-gate lifecycle and ComfyUI generation tools to AI agents
(Claude Desktop, Cursor, OpenClaw, Cline, etc.) over JSON-RPC 2.0 stdio.

Tools:
  - gate_status: Query session state, profile, remaining seconds, CU ledger.
  - gate_wake: Wake or join the shared Colab GPU instance and wait until ready.
  - gate_txt2img: High-level text-to-image with MOSS-Image DeBand pipeline and presets.
  - gate_generate: Submit a raw ComfyUI prompt graph, poll for completion, and return image URLs.
  - gate_stop: Release the Colab GPU immediately to prevent Compute Unit waste.

Environment Variables:
  CKIT_URL: Gate endpoint, e.g. https://comfy.example.com (default: http://127.0.0.1:8080)
  CKIT_TOKEN: Bearer token string (or CKIT_TOKEN_FILE)
  CKIT_TOKEN_FILE: Path to agent bearer token file (optional if AUTH_DISABLED)
  CF_ACCESS_CLIENT_ID: Cloudflare Access Service Token Client ID (optional)
  CF_ACCESS_CLIENT_SECRET_FILE: Cloudflare Access Service Token Secret file (optional)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PROTOCOL_VERSION = "2024-11-05"


DEFAULT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 (ckit/0.1.0)"


def _read_file(path: str) -> str:
    if not path:
        return ""
    try:
        with open(os.path.expanduser(path), "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def call_gate(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    cfg = {}
    cfg_path = os.path.expanduser("~/.config/ckit/config.json")
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass

    default_base = cfg.get("default_endpoint") or "http://127.0.0.1:8080"
    base = os.environ.get("CKIT_URL", default_base).rstrip("/")
    url = base + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data, headers={"Content-Type": "application/json", "User-Agent": DEFAULT_UA})

    endpoint_cfg = cfg.get("endpoints", {}).get(base, {})
    tok = os.environ.get("CKIT_TOKEN") or _read_file(os.environ.get("CKIT_TOKEN_FILE", "")) or endpoint_cfg.get("token", "")
    if tok:
        req.add_header("Authorization", "Bearer " + tok)

    cid = os.environ.get("CF_ACCESS_CLIENT_ID", "")
    if cid:
        req.add_header("CF-Access-Client-Id", cid)
        secret = _read_file(os.environ.get("CF_ACCESS_CLIENT_SECRET_FILE", ""))
        if secret:
            req.add_header("CF-Access-Client-Secret", secret)

    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            content_type = r.headers.get("Content-Type", "")
            raw = r.read() or b"{}"
            if "text/html" in content_type:
                return 401, {
                    "error": "cf_access_redirect",
                    "message": "Gateway returned HTML instead of JSON. The endpoint may be protected by Cloudflare Access. Please authenticate via device flow ('ckit auth') or provide a valid agent token."
                }
            return r.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        content_type = e.headers.get("Content-Type", "")
        raw = e.read() or b"{}"
        if "text/html" in content_type:
            return e.code, {
                "error": "cf_access_blocked" if e.code in (302, 401, 403) else "http_html_error",
                "message": f"Gateway returned HTTP {e.code} with HTML. Cloudflare Access or firewall may be intercepting requests."
            }
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"error": "http_error", "message": str(e)}
    except Exception as e:
        return 500, {"error": "network_error", "message": str(e)}


TOOLS = [
    {
        "name": "gate_status",
        "description": "Inspect the Comfy Colab session state, active profile, progress, remaining time, and Compute Unit balance.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "gate_wake",
        "description": "Wake or join the shared Google Colab L4 GPU instance with a specified model profile (e.g. 'moss-image', 'sd-latest', 'qwen-image').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "string",
                    "description": "The profile ID to wake. If omitted, the gate default profile is used."
                },
                "wait": {
                    "type": "boolean",
                    "description": "Whether to wait until ComfyUI reaches 'ready' state before returning (default: true)."
                }
            }
        }
    },
    {
        "name": "gate_txt2img",
        "description": "Generate an image using the pre-configured MOSS-Image DeBand pipeline with high quality and anti-banding LoRAs. Returns full image URLs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Text description of the image to generate (positive prompt)."
                },
                "negative_prompt": {
                    "type": "string",
                    "description": "Optional negative prompt elements to avoid (default: 'worst quality, bad anatomy, watermark, jpeg artifact')."
                },
                "preset": {
                    "type": "string",
                    "description": "Speed/quality preset: 'Fast alt' (default, 10 steps, recommended), 'Fast' (10 steps), 'Balanced' (14 steps), 'Quality' (25 steps)."
                },
                "seed": {
                    "type": "integer",
                    "description": "Random seed for reproducible generation (optional, default random integer)."
                },
                "width": {
                    "type": "integer",
                    "description": "Image width in pixels (default: 1024)."
                },
                "height": {
                    "type": "integer",
                    "description": "Image height in pixels (default: 1024)."
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Maximum seconds to wait for generation (default: 180)."
                }
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "gate_generate",
        "description": "Submit a raw ComfyUI prompt workflow graph to the running instance, wait for execution, and return image URLs and metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "object",
                    "description": "Standard ComfyUI prompt graph JSON object mapping node IDs to node configurations."
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Maximum seconds to wait for generation (default: 180)."
                }
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "gate_stop",
        "description": "Stop the Colab GPU session immediately and release hardware to avoid unnecessary Compute Unit charges.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Optional human-readable reason for stopping the session."
                }
            }
        }
    }
]


def _execute_prompt(prompt: dict, timeout_s: int = 180) -> dict:
    code, status = call_gate("GET", "/_gate/status")
    if status.get("state") != "ready":
        return {"isError": True, "content": [{"type": "text", "text": f"Cannot generate: ComfyUI is not ready (current state: '{status.get('state')}'). Call gate_wake first."}]}

    p_code, p_out = call_gate("POST", "/prompt", {"prompt": prompt})
    if p_code >= 400 or "prompt_id" not in p_out:
        return {"isError": True, "content": [{"type": "text", "text": f"Failed to submit prompt ({p_code}): {json.dumps(p_out, ensure_ascii=False)}"}]}

    prompt_id = p_out["prompt_id"]

    start_t = time.time()
    cfg = {}
    cfg_path = os.path.expanduser("~/.config/ckit/config.json")
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
    base = os.environ.get("CKIT_URL", cfg.get("default_endpoint") or "http://127.0.0.1:8080").rstrip("/")

    while time.time() - start_t < timeout_s:
        time.sleep(3)
        h_code, h_out = call_gate("GET", f"/history/{prompt_id}")
        if h_code < 400 and prompt_id in h_out:
            item = h_out[prompt_id]
            outputs = item.get("outputs", {})
            images = []
            for node_id, node_out in outputs.items():
                for img in node_out.get("images", []):
                    fn = img.get("filename", "")
                    sub = img.get("subfolder", "")
                    t = img.get("type", "output")
                    url = f"{base}/view?filename={urllib.parse.quote(fn)}&subfolder={urllib.parse.quote(sub)}&type={urllib.parse.quote(t)}"
                    images.append({"filename": fn, "url": url, "node_id": node_id})

            res_data = {"prompt_id": prompt_id, "status": item.get("status", {}), "images": images}
            res_text = json.dumps(res_data, indent=2, ensure_ascii=False)
            if images:
                img_md = "\n".join([f"![{img['filename']}]({img['url']})\nLink: {img['url']}" for img in images])
                res_text += f"\n\n### Generated Images\n{img_md}"
            return {"content": [{"type": "text", "text": res_text}]}

    return {"isError": True, "content": [{"type": "text", "text": f"Generation timed out after {timeout_s}s. Prompt ID: {prompt_id}"}]}


def handle_tool_call(name: str, args: dict) -> dict:
    if name == "gate_status":
        code, out = call_gate("GET", "/_gate/status")
        return {"content": [{"type": "text", "text": json.dumps(out, indent=2, ensure_ascii=False)}]}

    if name == "gate_wake":
        profile = args.get("profile")
        should_wait = args.get("wait", True)
        body = {}
        if profile:
            body["profile"] = profile

        code, out = call_gate("POST", "/_gate/wake", body)
        if code >= 400:
            return {"isError": True, "content": [{"type": "text", "text": f"Wake failed ({code}): {json.dumps(out, ensure_ascii=False)}"}]}

        if should_wait:
            max_wait = 900  # 15 minutes max
            start_t = time.time()
            while time.time() - start_t < max_wait:
                time.sleep(10)
                s_code, status = call_gate("GET", "/_gate/status")
                st = status.get("state")
                if st == "ready":
                    return {"content": [{"type": "text", "text": f"ComfyUI is ready!\n{json.dumps(status, indent=2, ensure_ascii=False)}"}]}
                if st in ("failed", "sleeping"):
                    return {"isError": True, "content": [{"type": "text", "text": f"Wake resulted in state '{st}': {json.dumps(status, ensure_ascii=False)}"}]}

            return {"isError": True, "content": [{"type": "text", "text": f"Timed out waiting for ComfyUI to become ready.\nLast status: {json.dumps(status, ensure_ascii=False)}"}]}

        return {"content": [{"type": "text", "text": json.dumps(out, indent=2, ensure_ascii=False)}]}

    if name == "gate_txt2img":
        prompt_text = (args.get("prompt") or "").strip()
        if not prompt_text:
            return {"isError": True, "content": [{"type": "text", "text": "Missing required argument 'prompt'"}]}
        negative_prompt = args.get("negative_prompt", "worst quality, bad anatomy, watermark, jpeg artifact")
        preset = args.get("preset", "Fast alt · fix + darask0 0.35 · 10 steps")
        seed = args.get("seed", -1)
        width = args.get("width", 1024)
        height = args.get("height", 1024)
        timeout_s = args.get("timeout_seconds", 180)

        try:
            from .moss_template import make_moss_txt2img_graph
        except ImportError:
            try:
                from cli.moss_template import make_moss_txt2img_graph
            except ImportError:
                from moss_template import make_moss_txt2img_graph

        graph = make_moss_txt2img_graph(
            prompt=prompt_text,
            negative_prompt=negative_prompt,
            preset=preset,
            seed=seed,
            width=width,
            height=height,
        )
        return _execute_prompt(graph, timeout_s)

    if name == "gate_generate":
        prompt = args.get("prompt")
        timeout_s = args.get("timeout_seconds", 180)
        return _execute_prompt(prompt, timeout_s)

    if name == "gate_stop":
        reason = args.get("reason", "agent requested stop")
        code, out = call_gate("POST", "/_gate/stop", {"reason": reason})
        return {"content": [{"type": "text", "text": f"Stop request returned ({code}):\n{json.dumps(out, indent=2, ensure_ascii=False)}"}]}

    return {"isError": True, "content": [{"type": "text", "text": f"Unknown tool: {name}"}]}


def run_stdio_server():
    """Stdio JSON-RPC loop adhering to the Model Context Protocol."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "comfy-colab-kit",
                        "version": "0.1.0"
                    }
                }
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()
        elif method == "tools/list":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": TOOLS
                }
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()
        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            call_res = handle_tool_call(tool_name, tool_args)
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": call_res
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()
        elif method == "ping":
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {}}) + "\n")
            sys.stdout.flush()
        else:
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                }
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run_stdio_server()
