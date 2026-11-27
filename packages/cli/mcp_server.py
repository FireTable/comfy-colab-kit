#!/usr/bin/env python3
"""Comfy Colab Kit — Model Context Protocol (MCP) Server.

Exposes comfy-gate lifecycle and ComfyUI generation tools to AI agents
(Claude Desktop, Cursor, OpenClaw, Cline, etc.) over JSON-RPC 2.0 stdio.

Tools:
  - gate_status: Query session state, profile, remaining seconds, CU ledger, and queue counts.
  - gate_wake: Wake or join the shared Colab GPU instance and wait until ready.
  - gate_txt2img: High-level text-to-image / text-to-video (profile-aware).
  - gate_img2img: High-level image-to-image / image-to-video (auto-uploads local file or URL).
  - gate_generate: Submit a raw ComfyUI prompt graph, poll for completion, and return output URLs.
  - gate_stop: Release the Colab GPU immediately to prevent Compute Unit waste.
  - gate_queue_job: Enqueue a durable prompt job (works even when GPU is sleeping).
  - gate_list_jobs: List queued, running, interrupted, failed, and completed jobs.
  - gate_get_job: Fetch job metadata, prompt, outputs, and archive URLs (works offline).
  - gate_manage_job: Retry, cancel, pin/unpin, or delete a durable job (works offline).
  - gate_storage_status: Inspect archive storage backend and optionally trigger a hot DB backup.

Environment Variables:
  CKIT_URL: Gate endpoint, e.g. https://comfy.example.com (default: http://127.0.0.1:8080)
  CKIT_TOKEN: Bearer token string (or CKIT_TOKEN_FILE)
  CKIT_TOKEN_FILE: Path to agent bearer token file (optional if AUTH_DISABLED)
  CF_ACCESS_CLIENT_ID: Cloudflare Access Service Token Client ID (optional)
  CF_ACCESS_CLIENT_SECRET_FILE: Cloudflare Access Service Token Secret file (optional)
"""
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

PROTOCOL_VERSION = "2024-11-05"
SERVER_VERSION = "0.5.0"

DEFAULT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 (ckit/0.5.0)"


def _read_file(path: str) -> str:
    if not path:
        return ""
    try:
        with open(os.path.expanduser(path), "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _resolve_endpoint_and_headers() -> tuple[str, dict[str, str]]:
    cfg = {}
    cfg_path = os.path.expanduser("~/.config/ckit/config.json")
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass

    agents_env = os.path.expanduser("~/.agents/.env")
    if os.path.isfile(agents_env):
        try:
            with open(agents_env, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("'\"")
                    if k == "CKIT_URL" and not cfg.get("default_endpoint"):
                        cfg["default_endpoint"] = v
                    elif k in ("CKIT_TOKEN", "COMFY_AGENT_TOKEN") and not cfg.get("token"):
                        cfg["token"] = v
        except Exception:
            pass

    default_base = cfg.get("default_endpoint") or "http://127.0.0.1:8080"
    base = os.environ.get("CKIT_URL", default_base).rstrip("/")
    headers = {"User-Agent": DEFAULT_UA}

    endpoint_cfg = cfg.get("endpoints", {}).get(base, {})
    tok = os.environ.get("CKIT_TOKEN") or _read_file(os.environ.get("CKIT_TOKEN_FILE", "")) or endpoint_cfg.get("token", "") or cfg.get("token", "")
    if tok:
        headers["Authorization"] = "Bearer " + tok

    cid = os.environ.get("CF_ACCESS_CLIENT_ID", "")
    if cid:
        headers["CF-Access-Client-Id"] = cid
        secret = _read_file(os.environ.get("CF_ACCESS_CLIENT_SECRET_FILE", ""))
        if secret:
            headers["CF-Access-Client-Secret"] = secret

    return base, headers


def call_gate(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    base, headers = _resolve_endpoint_and_headers()
    url = base + path
    data = json.dumps(body).encode() if body is not None else None
    req_headers = {**headers, "Content-Type": "application/json"}
    req = urllib.request.Request(url, method=method, data=data, headers=req_headers)

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


def _upload_image_impl(image_ref: str, allow_offline: bool = False) -> tuple[bool, str, str | None]:
    """Ensure a reference image is uploaded to ComfyUI or offline job archive storage.

    Returns (ok, uploaded_name_or_error, asset_id_or_none).
    """
    ref = (image_ref or "").strip()
    if not ref:
        return False, "Missing image reference", None

    expanded = os.path.expanduser(ref)
    raw_bytes = None
    filename = "reference.png"

    if os.path.isfile(expanded):
        filename = os.path.basename(expanded) or "reference.png"
        try:
            with open(expanded, "rb") as f:
                raw_bytes = f.read()
        except Exception as e:
            return False, f"Failed to read local image {expanded}: {e}", None
    elif ref.startswith("http://") or ref.startswith("https://"):
        parsed = urllib.parse.urlparse(ref)
        bn = os.path.basename(parsed.path)
        if bn and "." in bn:
            filename = bn
        try:
            dl_req = urllib.request.Request(ref, headers={"User-Agent": DEFAULT_UA})
            with urllib.request.urlopen(dl_req, timeout=30) as r:
                raw_bytes = r.read()
        except Exception as e:
            return False, f"Failed to download image from URL {ref}: {e}", None
    else:
        return True, ref, None

    base, headers = _resolve_endpoint_and_headers()
    boundary = f"----ckitBoundary{uuid.uuid4().hex}"
    mime_type = mimetypes.guess_type(filename)[0] or "image/png"

    body_parts = [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {mime_type}\r\n\r\n".encode(),
        raw_bytes,
        f"\r\n--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n',
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="type"\r\n\r\ninput\r\n',
        f"--{boundary}--\r\n".encode(),
    ]
    payload = b"".join(body_parts)
    req_headers = {**headers, "Content-Type": f"multipart/form-data; boundary={boundary}"}

    def _post_multipart(endpoint_path: str) -> tuple[int, dict, str]:
        req = urllib.request.Request(base + endpoint_path, method="POST", data=payload, headers=req_headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.loads(r.read() or b"{}")
                return r.status, out, ""
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            return e.code, {}, raw

    def _format_upload_res(out: dict) -> tuple[bool, str, str | None]:
        uploaded_name = out.get("name") or out.get("filename") or filename
        subfolder = out.get("subfolder") or ""
        resolved = f"{subfolder}/{uploaded_name}".lstrip("/") if subfolder else uploaded_name
        asset_id = out.get("id") or out.get("asset_id") or None
        return True, resolved, asset_id

    try:
        if allow_offline:
            st, out, raw = _post_multipart("/_gate/jobs/upload")
            if st < 400:
                return _format_upload_res(out)
            if st != 404:
                return False, f"Offline image upload failed ({st}): {raw}", None

        st, out, raw = _post_multipart("/upload/image")
        if st < 400:
            return _format_upload_res(out)
        if st == 503:
            off_st, off_out, _ = _post_multipart("/_gate/jobs/upload")
            if off_st < 400:
                return _format_upload_res(off_out)
        return False, f"Image upload failed ({st}): {raw}", None
    except Exception as e:
        return False, f"Image upload failed: {e}", None


def upload_image_to_gate(image_ref: str, allow_offline: bool = False) -> tuple[bool, str]:
    """Ensure a reference image is uploaded to ComfyUI's input directory or offline job archive.

    Accepts:
      - A local file path (expanded with ~),
      - An http:// or https:// image URL, or
      - An existing ComfyUI input filename (returned as-is if not a local file or URL).
    """
    ok, name_or_err, _asset_id = _upload_image_impl(image_ref, allow_offline=allow_offline)
    return ok, name_or_err


TOOLS = [
    {
        "name": "gate_status",
        "description": "Inspect the Comfy Colab session state, active profile, progress, remaining time, and Compute Unit balance.",
        "annotations": {
            "title": "Inspect Session & CU Status",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        },
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "gate_wake",
        "description": "Wake or join the shared Google Colab GPU instance with a specified model profile (e.g. 'moss-image', 'flux-klein', 'wan-ltx-video', 'sd-latest', 'qwen-image', 'minimax-h3-image', 'minimax-h3-video').",
        "annotations": {
            "title": "Wake or Join Colab GPU Session",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "string",
                    "description": "The profile ID to wake (e.g. 'moss-image', 'flux-klein', 'wan-ltx-video', 'sd-latest', 'qwen-image', 'minimax-h3-image', 'minimax-h3-video'). If omitted, the gate default profile is used."
                },
                "accelerator": {
                    "type": "string",
                    "description": "Optional preferred GPU tier: 'G4', 'A100', 'L4', 'T4', or 'H100' (overrides profile default)."
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
        "description": "Generate an image or video from a text prompt using the active profile's pipeline (MOSS-Image DeBand, FLUX.2 Klein 4B, Wan 2.2 5B TI2V, or SD 3.5 Large fp8). Returns full output URLs.",
        "annotations": {
            "title": "Text-to-Image / Text-to-Video Generation",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Text description of the image or video to generate (positive prompt)."
                },
                "negative_prompt": {
                    "type": "string",
                    "description": "Optional negative prompt elements to avoid (default: 'worst quality, bad anatomy, watermark, jpeg artifact')."
                },
                "preset": {
                    "type": "string",
                    "description": "Speed/quality preset (for moss-image): 'Fast alt' (default, 10 steps, recommended), 'Fast' (10 steps), 'Balanced' (14 steps), 'Quality' (25 steps)."
                },
                "seed": {
                    "type": "integer",
                    "description": "Random seed for reproducible generation (optional, default random integer)."
                },
                "width": {
                    "type": "integer",
                    "description": "Output width in pixels (default: 1024; for wan-ltx-video defaults to 832 if left at 1024)."
                },
                "height": {
                    "type": "integer",
                    "description": "Output height in pixels (default: 1024; for wan-ltx-video defaults to 480 if left at 1024)."
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
        "name": "gate_img2img",
        "description": "Generate a new image edit/variation or image-to-video clip from a reference image (local file path, HTTP/HTTPS URL, or uploaded ComfyUI input filename) and a text prompt.",
        "annotations": {
            "title": "Image-to-Image / Image Edit / Image-to-Video Generation",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "image": {
                    "type": "string",
                    "description": "Reference image: local file path (e.g. '~/pic.png'), HTTP/HTTPS image URL, or filename already in ComfyUI input/."
                },
                "prompt": {
                    "type": "string",
                    "description": "Text description guiding the image transformation, instruction edit, or video motion."
                },
                "denoise": {
                    "type": "number",
                    "description": "Denoising strength from 0.05 (subtle) to 1.0 (complete redraw). Default: 0.75."
                },
                "negative_prompt": {
                    "type": "string",
                    "description": "Optional negative prompt elements to avoid."
                },
                "preset": {
                    "type": "string",
                    "description": "Speed/quality preset: 'Fast alt' (default, 10 steps), 'Fast', 'Balanced', 'Quality' (25 steps)."
                },
                "seed": {
                    "type": "integer",
                    "description": "Random seed for reproducible generation (optional)."
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Maximum seconds to wait for generation (default: 180)."
                }
            },
            "required": ["image", "prompt"]
        }
    },
    {
        "name": "gate_generate",
        "description": "Submit a raw ComfyUI prompt workflow graph to the running instance, wait for execution, and return output URLs and metadata.",
        "annotations": {
            "title": "Submit Raw ComfyUI Workflow Graph",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True
        },
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
        "annotations": {
            "title": "Stop & Release Colab GPU Session",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Optional human-readable reason for stopping the session."
                }
            }
        }
    },
    {
        "name": "gate_queue_job",
        "description": "Enqueue a ComfyUI prompt graph into the durable SQLite/Google Drive queue (works even while GPU is sleeping). Auto-dispatches on next wake and archives outputs.",
        "annotations": {
            "title": "Queue Durable Job (Works Offline)",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "object",
                    "description": "ComfyUI API prompt graph dictionary mapping node IDs to node configs."
                },
                "profile": {
                    "type": "string",
                    "description": "Target profile ID (e.g. 'moss-image', 'flux-klein', 'wan-ltx-video')."
                },
                "workflow_id": {
                    "type": "string",
                    "description": "Optional workflow identifier."
                },
                "image": {
                    "type": "string",
                    "description": "Optional reference image (local path, HTTP/HTTPS URL, or uploaded filename; local/URL inputs are pre-uploaded offline via /_gate/jobs/upload)."
                }
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "gate_list_jobs",
        "description": "List durable queued, running, interrupted, failed, and completed jobs with archive output URLs (works even while GPU is sleeping).",
        "annotations": {
            "title": "List Queued & Archived Jobs",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile": {"type": "string", "description": "Filter by profile ID."},
                "status": {"type": "string", "description": "Filter by job status (queued, running, archiving, interrupted, completed, failed, canceled)."},
                "query": {"type": "string", "description": "Search substring across prompt text, job ID, profile, or error message."},
                "pinned": {"type": "boolean", "description": "Filter by pinned status (true/false)."},
                "limit": {"type": "integer", "description": "Maximum number of jobs to return (default: 20).", "default": 20}
            }
        }
    },
    {
        "name": "gate_get_job",
        "description": "Fetch detailed status, metadata, inputs, outputs, and archive URLs for a job by ID (works even while GPU is sleeping).",
        "annotations": {
            "title": "Get Job Details & Outputs",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job ID (or client_prompt_id)."},
                "include_prompt": {"type": "boolean", "description": "Include full prompt graph JSON in the response (default: false).", "default": False}
            },
            "required": ["job_id"]
        }
    },
    {
        "name": "gate_manage_job",
        "description": "Manage a durable job: retry, cancel, pin, unpin, or delete (optionally deleting unshared archived files). Works while GPU is sleeping.",
        "annotations": {
            "title": "Manage Job (Retry / Cancel / Pin / Delete)",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Target job ID."},
                "action": {
                    "type": "string",
                    "enum": ["retry", "cancel", "pin", "unpin", "delete"],
                    "description": "Management action to perform on the job."
                },
                "delete_files": {
                    "type": "boolean",
                    "description": "When action='delete', also delete unshared output blobs from archive storage (default: false).",
                    "default": False
                }
            },
            "required": ["job_id", "action"]
        }
    },
    {
        "name": "gate_storage_status",
        "description": "Inspect archive storage backend (Google Drive or local disk), job status counts, per-profile archive stats, and optionally trigger an immediate DB hot backup.",
        "annotations": {
            "title": "Inspect Storage & Backup DB",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "backup_now": {
                    "type": "boolean",
                    "description": "Trigger an immediate SQLite conn.backup() snapshot before returning status (default: false).",
                    "default": False
                }
            }
        }
    }
]


def _absolutize_asset_urls(job: dict, base: str) -> dict:
    if not isinstance(job, dict):
        return job
    clean_base = base.rstrip("/")
    for key in ("outputs", "inputs"):
        items = job.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            for url_key in ("url", "thumb_url"):
                u = item.get(url_key)
                if isinstance(u, str) and u.startswith("/"):
                    item[url_key] = f"{clean_base}{u}"
    return job


def _execute_prompt(prompt: dict, timeout_s: int = 180) -> dict:
    code, status = call_gate("GET", "/_gate/status")
    if status.get("state") != "ready":
        return {"isError": True, "content": [{"type": "text", "text": f"Cannot generate: ComfyUI is not ready (current state: '{status.get('state')}'). Call gate_wake first."}]}

    p_code, p_out = call_gate("POST", "/prompt", {"prompt": prompt})
    if p_code >= 400 or "prompt_id" not in p_out:
        return {"isError": True, "content": [{"type": "text", "text": f"Failed to submit prompt ({p_code}): {json.dumps(p_out, ensure_ascii=False)}"}]}

    prompt_id = p_out["prompt_id"]
    job_id = p_out.get("job_id")
    start_t = time.time()
    base, _ = _resolve_endpoint_and_headers()

    while time.time() - start_t < timeout_s:
        time.sleep(3)
        h_code, h_out = call_gate("GET", f"/history/{prompt_id}")
        if h_code < 400 and prompt_id in h_out:
            item = h_out[prompt_id]
            outputs = item.get("outputs", {})
            images = []
            seen = set()
            for node_id, node_out in outputs.items():
                for key in ("images", "videos", "gifs", "audio"):
                    for img in node_out.get(key, []):
                        fn = img.get("filename", "")
                        sub = img.get("subfolder", "")
                        t = img.get("type", "output")
                        raw_url = img.get("url")
                        if not fn and not raw_url:
                            continue
                        if (fn, sub, t, raw_url) in seen:
                            continue
                        seen.add((fn, sub, t, raw_url))
                        if isinstance(raw_url, str) and raw_url.startswith("/"):
                            url = f"{base}{raw_url}"
                        elif isinstance(raw_url, str) and raw_url:
                            url = raw_url
                        else:
                            url = f"{base}/view?filename={urllib.parse.quote(fn)}&subfolder={urllib.parse.quote(sub)}&type={urllib.parse.quote(t)}"
                        entry = {"filename": fn, "url": url, "node_id": node_id}
                        if img.get("asset_id"):
                            entry["asset_id"] = img["asset_id"]
                        images.append(entry)

            res_data = {"prompt_id": prompt_id, "status": item.get("status", {}), "images": images}
            if job_id:
                res_data["job_id"] = job_id
                j_code, j_out = call_gate("GET", f"/_gate/jobs/{urllib.parse.quote(str(job_id), safe='')}")
                if j_code < 400 and isinstance(j_out, dict):
                    _absolutize_asset_urls(j_out, base)
                    if j_out.get("outputs"):
                        res_data["archive_outputs"] = [
                            {k: o.get(k) for k in ("id", "filename", "mime", "url", "thumb_url") if o.get(k)}
                            for o in j_out["outputs"]
                        ]

            res_text = json.dumps(res_data, indent=2, ensure_ascii=False)
            if images:
                img_md = "\n".join([f"![{img['filename']}]({img['url']})\nLink: {img['url']}" for img in images])
                res_text += f"\n\n### Generated Outputs\n{img_md}"
            return {"content": [{"type": "text", "text": res_text}]}

    return {"isError": True, "content": [{"type": "text", "text": f"Generation timed out after {timeout_s}s. Prompt ID: {prompt_id}"}]}


def _load_templates():
    try:
        from . import moss_template as mt
        return mt
    except ImportError:
        try:
            from cli import moss_template as mt
            return mt
        except ImportError:
            import moss_template as mt
            return mt


def handle_tool_call(name: str, args: dict) -> dict:
    if name == "gate_status":
        code, out = call_gate("GET", "/_gate/status")
        return {"content": [{"type": "text", "text": json.dumps(out, indent=2, ensure_ascii=False)}]}

    if name == "gate_wake":
        profile = args.get("profile")
        accelerator = args.get("accelerator")
        should_wait = args.get("wait", True)
        body = {}
        if profile:
            body["profile"] = profile
        if accelerator:
            body["accelerator"] = accelerator

        code, out = call_gate("POST", "/_gate/wake", body)
        if code >= 400:
            return {"isError": True, "content": [{"type": "text", "text": f"Wake failed ({code}): {json.dumps(out, ensure_ascii=False)}"}]}

        if should_wait:
            max_wait = 900  # 15 minutes max
            start_t = time.time()
            status = out
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

        _, st = call_gate("GET", "/_gate/status")
        active_profile = st.get("profile") if isinstance(st, dict) else None
        mt = _load_templates()
        graph = mt.make_txt2img_graph(
            profile=active_profile,
            prompt=prompt_text,
            negative_prompt=negative_prompt,
            preset=preset,
            seed=seed,
            width=width,
            height=height,
        )
        return _execute_prompt(graph, timeout_s)

    if name == "gate_img2img":
        prompt_text = (args.get("prompt") or "").strip()
        image_ref = (args.get("image") or "").strip()
        if not prompt_text or not image_ref:
            return {"isError": True, "content": [{"type": "text", "text": "Missing required arguments 'image' and 'prompt'"}]}

        _, st = call_gate("GET", "/_gate/status")
        if st.get("state") != "ready":
            return {"isError": True, "content": [{"type": "text", "text": f"Cannot generate: ComfyUI is not ready (current state: '{st.get('state')}'). Call gate_wake first."}]}

        ok, uploaded_Or_err = upload_image_to_gate(image_ref)
        if not ok:
            return {"isError": True, "content": [{"type": "text", "text": uploaded_Or_err}]}

        mt = _load_templates()
        graph = mt.make_img2img_graph(
            profile=st.get("profile"),
            prompt=prompt_text,
            image=uploaded_Or_err,
            negative_prompt=args.get("negative_prompt", "worst quality, bad anatomy, watermark, jpeg artifact"),
            preset=args.get("preset", "Fast alt · fix + darask0 0.35 · 10 steps"),
            denoise=args.get("denoise", 0.75),
            seed=args.get("seed", -1),
        )
        return _execute_prompt(graph, args.get("timeout_seconds", 180))

    if name == "gate_generate":
        prompt = args.get("prompt")
        timeout_s = args.get("timeout_seconds", 180)
        return _execute_prompt(prompt, timeout_s)

    if name == "gate_stop":
        reason = args.get("reason", "agent requested stop")
        code, out = call_gate("POST", "/_gate/stop", {"reason": reason})
        return {"content": [{"type": "text", "text": f"Stop request returned ({code}):\n{json.dumps(out, indent=2, ensure_ascii=False)}"}]}

    if name == "gate_queue_job":
        prompt = args.get("prompt")
        if not isinstance(prompt, dict) or not prompt:
            return {"isError": True, "content": [{"type": "text", "text": "Missing or invalid required argument 'prompt' (must be non-empty object)."}]}
        payload: dict = {"prompt": prompt}
        if args.get("profile"):
            payload["profile"] = args["profile"]
        if args.get("workflow_id"):
            payload["workflow_id"] = args["workflow_id"]
        image_ref = (args.get("image") or "").strip()
        if image_ref:
            ok, uploaded_or_err, asset_id = _upload_image_impl(image_ref, allow_offline=True)
            if not ok:
                return {"isError": True, "content": [{"type": "text", "text": uploaded_or_err}]}
            payload["inputs"] = [uploaded_or_err]
            if asset_id:
                payload["input_asset_ids"] = [asset_id]
        code, out = call_gate("POST", "/_gate/jobs", payload)
        if code >= 400:
            return {"isError": True, "content": [{"type": "text", "text": f"Failed to queue job ({code}): {json.dumps(out, ensure_ascii=False)}"}]}
        res = {
            "job_id": out.get("id") or out.get("job_id"),
            "prompt_id": out.get("client_prompt_id") or out.get("prompt_id"),
            "status": out.get("status"),
            "profile_id": out.get("profile_id"),
        }
        return {"content": [{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}]}

    if name == "gate_list_jobs":
        params = {}
        if args.get("profile"):
            params["profile"] = str(args["profile"])
        if args.get("status"):
            params["status"] = str(args["status"])
        if args.get("query"):
            params["q"] = str(args["query"])
        if "pinned" in args and args["pinned"] is not None:
            params["pinned"] = "1" if args["pinned"] else "0"
        limit = args.get("limit")
        params["limit"] = str(limit if limit is not None else 20)
        qs = urllib.parse.urlencode(params)
        code, out = call_gate("GET", f"/_gate/jobs?{qs}" if qs else "/_gate/jobs")
        if code >= 400:
            return {"isError": True, "content": [{"type": "text", "text": f"Failed to list jobs ({code}): {json.dumps(out, ensure_ascii=False)}"}]}
        base, _ = _resolve_endpoint_and_headers()
        if isinstance(out, dict) and isinstance(out.get("jobs"), list):
            for job in out["jobs"]:
                _absolutize_asset_urls(job, base)
        return {"content": [{"type": "text", "text": json.dumps(out, indent=2, ensure_ascii=False)}]}

    if name == "gate_get_job":
        job_id = (args.get("job_id") or "").strip()
        if not job_id:
            return {"isError": True, "content": [{"type": "text", "text": "Missing required argument 'job_id'"}]}
        code, out = call_gate("GET", f"/_gate/jobs/{urllib.parse.quote(job_id, safe='')}")
        if code >= 400:
            return {"isError": True, "content": [{"type": "text", "text": f"Failed to get job ({code}): {json.dumps(out, ensure_ascii=False)}"}]}
        base, _ = _resolve_endpoint_and_headers()
        if isinstance(out, dict):
            if not args.get("include_prompt", False):
                out.pop("prompt", None)
            _absolutize_asset_urls(out, base)
        return {"content": [{"type": "text", "text": json.dumps(out, indent=2, ensure_ascii=False)}]}

    if name == "gate_manage_job":
        job_id = (args.get("job_id") or "").strip()
        action = (args.get("action") or "").strip().lower()
        if not job_id or action not in ("retry", "cancel", "pin", "unpin", "delete"):
            return {"isError": True, "content": [{"type": "text", "text": "Required: 'job_id' and 'action' in ('retry', 'cancel', 'pin', 'unpin', 'delete')."}]}
        qid = urllib.parse.quote(job_id, safe="")
        if action == "retry":
            code, out = call_gate("POST", f"/_gate/jobs/{qid}/retry", {})
        elif action == "cancel":
            code, out = call_gate("POST", f"/_gate/jobs/{qid}/cancel", {})
        elif action in ("pin", "unpin"):
            code, out = call_gate("POST", f"/_gate/jobs/{qid}/pin", {"pinned": action == "pin"})
        else:
            del_files = "1" if args.get("delete_files", False) else "0"
            code, out = call_gate("DELETE", f"/_gate/jobs/{qid}?delete_files={del_files}")
        if code >= 400:
            return {"isError": True, "content": [{"type": "text", "text": f"Failed to {action} job ({code}): {json.dumps(out, ensure_ascii=False)}"}]}
        return {"content": [{"type": "text", "text": json.dumps(out, indent=2, ensure_ascii=False)}]}

    if name == "gate_storage_status":
        backup_res = None
        if args.get("backup_now"):
            b_code, b_out = call_gate("POST", "/_gate/storage/backup", {})
            backup_res = b_out if b_code < 400 else {"ok": False, "status": b_code, "error": b_out}
        s_code, s_out = call_gate("GET", "/_gate/storage/status")
        if s_code >= 400:
            return {"isError": True, "content": [{"type": "text", "text": f"Failed to fetch storage status ({s_code}): {json.dumps(s_out, ensure_ascii=False)}"}]}
        p_code, p_out = call_gate("GET", "/_gate/archive/profiles")
        res = {
            "storage": s_out,
            "counts": s_out.get("counts", {}) if isinstance(s_out, dict) else {},
            "profiles": p_out.get("profiles", []) if p_code < 400 and isinstance(p_out, dict) else [],
        }
        if backup_res is not None:
            res["backup"] = backup_res
        return {"content": [{"type": "text", "text": json.dumps(res, indent=2, ensure_ascii=False)}]}

    return {"isError": True, "content": [{"type": "text", "text": f"Unknown tool: {name}"}]}


def run_stdio_server(base_url: str = ""):
    """Stdio JSON-RPC loop adhering to the Model Context Protocol."""
    if base_url:
        os.environ["CKIT_URL"] = base_url
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

        if req_id is None or str(method).startswith("notifications/"):
            continue

        if method == "initialize":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {},
                        "prompts": {},
                        "resources": {}
                    },
                    "serverInfo": {
                        "name": "comfy-colab-kit",
                        "version": SERVER_VERSION
                    },
                    "instructions": "Use gate_status to check session state, gate_wake to boot/join a profile ('moss-image', 'flux-klein', 'wan-ltx-video', 'sd-latest', 'qwen-image', 'minimax-h3-image', 'minimax-h3-video'), gate_txt2img / gate_img2img / gate_generate to create images or videos when ready, gate_queue_job / gate_list_jobs / gate_get_job / gate_manage_job / gate_storage_status for durable offline queuing and archive browsing even while sleeping, and gate_stop when finished to release GPU compute units."
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
        elif method == "prompts/list":
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {"prompts": []}}) + "\n")
            sys.stdout.flush()
        elif method == "resources/list":
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {"resources": []}}) + "\n")
            sys.stdout.flush()
        elif method == "resources/templates/list":
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {"resourceTemplates": []}}) + "\n")
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
    import argparse
    ap = argparse.ArgumentParser(description="Comfy Colab Kit MCP Server")
    ap.add_argument("--gate-url", "--url", dest="gate_url", help="gate endpoint URL (default: http://127.0.0.1:8080)")
    args, _ = ap.parse_known_args()
    run_stdio_server(args.gate_url or "")
