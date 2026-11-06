#!/usr/bin/env python3
"""ckit — tiny client for comfy-gate (stdlib only; agent friendly).

  ckit auth [URL]                               (pair agent via RFC 8628 device flow)
  ckit doctor [URL]                             (diagnose connectivity, CF Access bypass, and auth)
  ckit validate [PATH]                          (validate custom or example profile directories)
  ckit status|profiles [--json]
  ckit wake [--profile ID] [--workflow ID ...] [--wait]
  ckit generate --prompt "..." [--image PATH_OR_URL] [--denoise 0.75] [--negative "..."] [--preset "..."]
  ckit switch PROFILE                           (owner)
  ckit stop | extend                            (owner)
  ckit mcp                                      (start stdio MCP server for Cursor / Claude Desktop)

Env: CKIT_URL (e.g. https://comfy.example.com), CKIT_TOKEN_FILE (agent bearer token file),
     optional CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET_FILE (Access service token for the public host).
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def _read(path):
    return open(os.path.expanduser(path)).read().strip() if path else ""


def load_config():
    cfg = {}
    p = os.path.expanduser("~/.config/ckit/config.json")
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

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
    return cfg


def save_config(cfg):
    p = os.path.expanduser("~/.config/ckit/config.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

    agents_dir = os.path.expanduser("~/.agents")
    if os.path.isdir(agents_dir):
        env_file = os.path.join(agents_dir, ".env")
        try:
            lines = []
            if os.path.isfile(env_file):
                with open(env_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            url = cfg.get("default_endpoint") or ""
            tok = cfg.get("token") or ""
            found_url, found_tok = False, False
            new_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("CKIT_URL="):
                    new_lines.append(f'CKIT_URL="{url}"\n')
                    found_url = True
                elif stripped.startswith("CKIT_TOKEN="):
                    new_lines.append(f'CKIT_TOKEN="{tok}"\n')
                    found_tok = True
                else:
                    new_lines.append(line)
            if not found_url and url:
                new_lines.append(f'CKIT_URL="{url}"\n')
            if not found_tok and tok:
                new_lines.append(f'CKIT_TOKEN="{tok}"\n')
            with open(env_file, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
        except Exception:
            pass


DEFAULT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 (ckit/0.2.0)"


def call(method, path, body=None, base_override=None):
    cfg = load_config()
    default_endpoint = cfg.get("default_endpoint") or "http://127.0.0.1:8080"
    base = (base_override or os.environ.get("CKIT_URL") or default_endpoint).rstrip("/")

    req = urllib.request.Request(
        base + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "User-Agent": DEFAULT_UA},
    )

    endpoint_cfg = cfg.get("endpoints", {}).get(base, {})
    tok = os.environ.get("CKIT_TOKEN") or _read(os.environ.get("CKIT_TOKEN_FILE", "")) or endpoint_cfg.get("token", "") or cfg.get("token", "")
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    cid = os.environ.get("CF_ACCESS_CLIENT_ID")
    if cid:
        req.add_header("CF-Access-Client-Id", cid)
        req.add_header("CF-Access-Client-Secret", _read(os.environ.get("CF_ACCESS_CLIENT_SECRET_FILE", "")))
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            content_type = r.headers.get("Content-Type", "")
            raw = r.read() or b"{}"
            if "text/html" in content_type:
                return 401, {
                    "error": "cf_access_redirect",
                    "message": "Gateway returned HTML (likely Cloudflare Access login redirect). Please authenticate via 'ckit auth' or provide a valid agent token.",
                }
            return r.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        content_type = e.headers.get("Content-Type", "")
        raw = e.read() or b"{}"
        if "text/html" in content_type:
            return e.code, {
                "error": "cf_access_blocked" if e.code in (302, 401, 403) else "http_html_error",
                "message": f"Gateway returned HTTP {e.code} with HTML. Cloudflare Access or firewall may be intercepting requests.",
            }
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"error": "http_error", "message": str(e)}
    except Exception as e:
        return 500, {"error": "network_error", "message": str(e)}


def do_auth(target_url=None):
    cfg = load_config()
    base = target_url or os.environ.get("CKIT_URL") or cfg.get("default_endpoint")
    if not base:
        try:
            val = input("请输入 Comfy Gate 网关地址 [http://127.0.0.1:8080]: ").strip()
            base = val if val else "http://127.0.0.1:8080"
        except (KeyboardInterrupt, EOFError):
            print("\n已取消", file=sys.stderr)
            sys.exit(1)
    base = base.rstrip("/")
    if not base.startswith("http://") and not base.startswith("https://"):
        base = "https://" + base

    client_name = os.environ.get("CKIT_CLIENT_NAME") or "AI Agent"
    print(f"[auth] Requesting device authorization from {base} ...")

    code_req = urllib.request.Request(
        f"{base}/api/auth/device/code",
        data=json.dumps({"client_name": client_name}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": DEFAULT_UA},
    )
    try:
        with urllib.request.urlopen(code_req, timeout=15) as r:
            content_type = r.headers.get("Content-Type", "")
            raw = r.read()
            if "text/html" in content_type:
                print(f"[auth] Error: Cloudflare Access login page intercepted request to {base}.", file=sys.stderr)
                print("[auth] Please verify that Cloudflare Zero Trust Bypass is enabled for /api/auth/device/*", file=sys.stderr)
                print("[auth] Or generate an Agent Token in the web browser UI and set CKIT_TOKEN.", file=sys.stderr)
                sys.exit(1)
            res = json.loads(raw)
    except Exception as e:
        print(f"[auth] Error connecting to gateway {base}: {e}", file=sys.stderr)
        sys.exit(1)

    device_code = res["device_code"]
    user_code = res["user_code"]
    verify_url = res.get("verification_uri_complete") or f"{base}/auth/device?code={user_code}"
    interval = max(2, res.get("interval", 3))

    print(f"\n[auth] Open the following URL in your browser to approve:\n       {verify_url}\n")
    print(f"[auth] Device code: {user_code}")
    print("[auth] Waiting for browser confirmation (Ctrl+C to abort)...", flush=True)

    try:
        import webbrowser
        webbrowser.open(verify_url)
    except Exception:
        pass

    poll_url = f"{base}/api/auth/device/token"
    start_time = time.time()
    while time.time() - start_time < res.get("expires_in", 600):
        time.sleep(interval)
        poll_req = urllib.request.Request(
            poll_url,
            data=json.dumps({"device_code": device_code}).encode(),
            headers={"Content-Type": "application/json", "User-Agent": DEFAULT_UA},
        )
        try:
            with urllib.request.urlopen(poll_req, timeout=10) as r:
                content_type = r.headers.get("Content-Type", "")
                raw = r.read()
                if "text/html" in content_type:
                    continue
                tok_res = json.loads(raw)
                access_token = tok_res.get("access_token")
                email = tok_res.get("email") or "user"

                cfg.setdefault("endpoints", {})[base] = {
                    "token": access_token,
                    "email": email,
                    "token_id": tok_res.get("token_id"),
                    "authorized_at": time.time(),
                }
                cfg["default_endpoint"] = base
                save_config(cfg)

                print(f"\n[auth] Authorization successful! Account: {email}")
                print("[auth] Credentials saved to: ~/.config/ckit/config.json")
                print("[auth] AI Agent / MCP is ready to use.\n")
                return
        except urllib.error.HTTPError as he:
            err_data = {}
            try:
                err_data = json.loads(he.read())
            except Exception:
                pass
            err_code = err_data.get("error")
            if err_code == "authorization_pending":
                continue
            elif err_code == "slow_down":
                interval += 2
                continue
            elif err_code == "access_denied":
                print("\n[auth] Authorization was denied by the user.", file=sys.stderr)
                sys.exit(1)
            elif err_code == "expired_token":
                print("\n[auth] Device code has expired. Please run 'ckit auth' again.", file=sys.stderr)
                sys.exit(1)
            else:
                msg = err_data.get("message") or f"HTTP {he.code}"
                print(f"\n[auth] Token error: {msg}", file=sys.stderr)
                sys.exit(1)
        except Exception:
            continue

    print("\n[auth] Timed out waiting for browser authorization.", file=sys.stderr)
    sys.exit(1)


def _load_templates_and_mcp():
    try:
        from . import moss_template as mt
        from . import mcp_server as ms
        return mt, ms
    except ImportError:
        try:
            from cli import moss_template as mt
            from cli import mcp_server as ms
            return mt, ms
        except ImportError:
            import moss_template as mt
            import mcp_server as ms
            return mt, ms


def do_generate(a):
    prompt_text = a.prompt or a.extra_arg
    if not prompt_text:
        print("[generate] Error: --prompt is required.", file=sys.stderr)
        sys.exit(1)

    code, st = call("GET", "/_gate/status")
    if code >= 400 or st.get("state") != "ready":
        print(f"[generate] Gateway is not ready (current state: '{st.get('state', 'unknown')}').", file=sys.stderr)
        print("[generate] Hint: run 'ckit wake --wait' first to allocate and start the GPU.", file=sys.stderr)
        sys.exit(1)

    mt, ms = _load_templates_and_mcp()
    active_profile = st.get("profile")

    if a.image:
        print(f"[generate] Preparing reference image '{a.image}' for Img2Img ...")
        ok, uploaded_or_err = ms.upload_image_to_gate(a.image)
        if not ok:
            print(f"[generate] {uploaded_or_err}", file=sys.stderr)
            sys.exit(1)
        graph = mt.make_img2img_graph(
            profile=active_profile,
            prompt=prompt_text,
            image=uploaded_or_err,
            negative_prompt=a.negative or "worst quality, bad anatomy, watermark, jpeg artifact",
            preset=a.preset or "Fast alt · fix + darask0 0.35 · 10 steps",
            denoise=a.denoise if a.denoise is not None else 0.75,
            seed=a.seed if a.seed is not None else -1,
        )
        mode_desc = f"Img2Img (image={uploaded_or_err}, denoise={a.denoise if a.denoise is not None else 0.75})"
    else:
        graph = mt.make_txt2img_graph(
            profile=active_profile,
            prompt=prompt_text,
            negative_prompt=a.negative or "worst quality, bad anatomy, watermark, jpeg artifact",
            preset=a.preset or "Fast alt · fix + darask0 0.35 · 10 steps",
            seed=a.seed if a.seed is not None else -1,
            width=a.width or 1024,
            height=a.height or 1024,
        )
        mode_desc = f"Txt2Img (profile={active_profile or 'moss-image'})"

    print(f"[generate] Submitting prompt to ComfyUI [{mode_desc}] ...")
    p_code, p_out = call("POST", "/prompt", {"prompt": graph})
    if p_code >= 400 or "prompt_id" not in p_out:
        print(f"[generate] Prompt submission failed ({p_code}): {json.dumps(p_out, ensure_ascii=False)}", file=sys.stderr)
        sys.exit(1)

    prompt_id = p_out["prompt_id"]
    print(f"[generate] Prompt queued. ID: {prompt_id}. Waiting for generation ...")

    cfg = load_config()
    default_endpoint = cfg.get("default_endpoint") or "http://127.0.0.1:8080"
    base = os.environ.get("CKIT_URL", default_endpoint).rstrip("/")

    deadline = time.time() + (a.timeout or 180)
    while time.time() < deadline:
        time.sleep(2)
        h_code, h_out = call("GET", f"/history/{prompt_id}")
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
                    images.append({"filename": fn, "url": url})

            if not images:
                print(f"[generate] Generation finished with no output images: {json.dumps(item, ensure_ascii=False)}")
                return

            print(f"\n[generate] Success! {len(images)} image(s) generated:\n")
            for img in images:
                print(f"  • {img['filename']}: {img['url']}")

            if a.json:
                print(json.dumps({"prompt_id": prompt_id, "images": images}, indent=2, ensure_ascii=False))
            return

    print(f"[generate] Timed out waiting for prompt {prompt_id}", file=sys.stderr)
    sys.exit(1)


def do_doctor(a):
    """Diagnose local configuration, gateway reachability, Cloudflare Access bypass, and authentication."""
    cfg = load_config()
    default_endpoint = cfg.get("default_endpoint") or "http://127.0.0.1:8080"
    base = (a.extra_arg or os.environ.get("CKIT_URL") or default_endpoint).rstrip("/")
    if not base.startswith("http://") and not base.startswith("https://"):
        base = "https://" + base

    checks = []
    endpoint_cfg = cfg.get("endpoints", {}).get(base, {})
    has_token = bool(
        os.environ.get("CKIT_TOKEN")
        or _read(os.environ.get("CKIT_TOKEN_FILE", ""))
        or endpoint_cfg.get("token")
        or cfg.get("token")
    )
    checks.append({"check": "credentials_configured", "ok": has_token, "endpoint": base})

    # 1. Healthz check
    h_code, h_out = call("GET", "/_gate/healthz", base_override=base)
    health_ok = h_code == 200 and isinstance(h_out, dict) and h_out.get("ok") is True
    checks.append({"check": "gate_healthz", "ok": health_ok, "status": h_code, "detail": h_out})

    # 2. MCP discovery (verifies /mcp is not blocked by Cloudflare Access)
    m_code, m_out = call("GET", "/mcp", base_override=base)
    mcp_ok = m_code == 200 and isinstance(m_out, dict) and m_out.get("name") == "comfy-colab-kit"
    checks.append({"check": "mcp_discovery_bypass", "ok": mcp_ok, "status": m_code, "detail": m_out})

    # 3. Authenticated status check
    s_code, s_out = call("GET", "/_gate/status", base_override=base)
    status_ok = s_code == 200 and isinstance(s_out, dict) and "state" in s_out
    checks.append({
        "check": "authenticated_status",
        "ok": status_ok,
        "status": s_code,
        "state": s_out.get("state") if status_ok else None,
        "profile": s_out.get("profile") if status_ok else None,
        "detail": None if status_ok else s_out,
    })

    all_ok = all(c["ok"] for c in checks)
    report = {"ok": all_ok, "endpoint": base, "checks": checks}
    if a.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"ckit doctor — target: {base}\n")
        for c in checks:
            mark = "OK  " if c["ok"] else "WARN"
            print(f"  [{mark}] {c['check']}")
            if not c["ok"] and c.get("detail"):
                print(f"         -> {json.dumps(c['detail'], ensure_ascii=False)}")
        if all_ok:
            print(f"\nAll checks passed! Gate state: {checks[-1].get('state')} (profile: {checks[-1].get('profile')})")
        else:
            print("\nSome checks failed. Run 'ckit auth <URL>' to pair an agent token or verify your Cloudflare Access bypass rules.")
    sys.exit(0 if all_ok else 1)


def do_validate(a):
    """Validate profile directories using scripts/validate-profile.py or gate.profiles."""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    validator = os.path.join(repo_root, "scripts", "validate-profile.py")
    cmd = [sys.executable, validator]
    if a.extra_arg:
        cmd.append(a.extra_arg)
    if os.path.isfile(validator):
        rc = subprocess.call(cmd)
        sys.exit(rc)
    print("[validate] Error: scripts/validate-profile.py not found.", file=sys.stderr)
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(prog="ckit", description="Comfy Colab Kit Operator CLI")
    ap.add_argument(
        "cmd",
        choices=["auth", "doctor", "validate", "status", "profiles", "wake", "switch", "stop", "extend", "generate", "mcp"],
    )
    ap.add_argument("extra_arg", nargs="?", default=None, help="URL for auth/doctor, path for validate, profile ID for switch, prompt for generate")
    ap.add_argument("--profile")
    ap.add_argument("--workflow", action="append", help="enable an optional workflow (repeatable)")
    ap.add_argument("--wait", action="store_true", help="block until ready/failed (wake)")
    ap.add_argument("--prompt", help="prompt text for image generation")
    ap.add_argument("--image", help="reference image path, URL, or input filename for Img2Img")
    ap.add_argument("--denoise", type=float, default=None, help="denoising strength for Img2Img (0.05-1.0, default 0.75)")
    ap.add_argument("--negative", help="negative prompt text for image generation")
    ap.add_argument("--preset", help="speed/quality preset for image generation")
    ap.add_argument("--seed", type=int, default=None, help="seed for image generation")
    ap.add_argument("--width", type=int, default=1024, help="image width (default 1024)")
    ap.add_argument("--height", type=int, default=1024, help="image height (default 1024)")
    ap.add_argument("--timeout", type=int, default=180, help="generation timeout seconds (default 180)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.cmd == "auth":
        do_auth(a.extra_arg)
        return
    if a.cmd == "doctor":
        do_doctor(a)
        return
    if a.cmd == "validate":
        do_validate(a)
        return
    if a.cmd == "generate":
        do_generate(a)
        return
    if a.cmd == "mcp":
        try:
            from .mcp_server import run_stdio_server
        except ImportError:
            try:
                from cli.mcp_server import run_stdio_server
            except ImportError:
                from mcp_server import run_stdio_server
        run_stdio_server()
        return
    if a.cmd == "status":
        code, out = call("GET", "/_gate/status")
    elif a.cmd == "profiles":
        code, out = call("GET", "/_gate/profiles")
    elif a.cmd == "wake":
        code, out = call("POST", "/_gate/wake", {k: v for k, v in
                         (("profile", a.profile), ("workflows", a.workflow)) if v})
        while a.wait and code < 300:
            time.sleep(10)
            code, out = call("GET", "/_gate/status")
            if out.get("state") in ("ready", "failed", "sleeping"):
                break
            if not a.json:
                print(f"… {out.get('state')} {json.dumps(out.get('progress', {}).get('download', {}))}", file=sys.stderr)
    elif a.cmd == "switch":
        code, out = call("POST", "/_gate/switch", {"profile": a.extra_arg or a.profile})
    else:
        code, out = call("POST", f"/_gate/{a.cmd}")
    print(json.dumps(out, indent=None if a.json else 2, ensure_ascii=False))
    sys.exit(0 if code < 400 and out.get("state") != "failed" else 1)


if __name__ == "__main__":
    main()
