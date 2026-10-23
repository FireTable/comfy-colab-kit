# comfy-colab-kit — runs INSIDE the Colab kernel (started by comfy-gate via `colab exec`).
# Input:  KIT_PLAN=/content/kit/plan.json (built by gate/plan.py), optional CF_TUNNEL_TOKEN / HF_TOKEN in env.
# Output protocol on stdout (parsed by gate/session.py):
#   KIT_STAGE <name> start|done <secs>|fail      KIT_PROGRESS <key> <json>
#   KIT_READY <json timings>                     KIT_FAILED stage=<name> error=<repr>
# Ported and generalised from the original moss-colab-kit setup script.
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

_PLAN_PATH = os.environ.get("KIT_PLAN", "/content/kit/plan.json")
PLAN = json.load(open(_PLAN_PATH)) if os.path.exists(_PLAN_PATH) else {}
COMFY = "/content/ComfyUI"
LOG = "/content/kit_logs"
PORT = 8188
T0 = time.time()
TIMES = {}
HB_FILE = "/content/kit_heartbeat"


class Stage:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.t = time.time()
        print(f"KIT_STAGE {self.name} start", flush=True)
        return self

    def __exit__(self, et, ev, tb):
        d = time.time() - self.t
        TIMES[self.name] = round(d, 1)
        if et:
            print(f"KIT_STAGE {self.name} fail {d:.1f}", flush=True)
            print(f"KIT_FAILED stage={self.name} error={ev!r}"[:800], flush=True)
            sys.exit(1)
        print(f"KIT_STAGE {self.name} done {d:.1f}", flush=True)


def sh(cmd, check=True, quiet=False, env=None):
    if not quiet:
        print(f"$ {cmd}", flush=True)
    # subprocess output written straight to fd 1 bypasses the Jupyter kernel and never reaches `colab exec`;
    # pipe it through Python's print so the gate (and the wake page log) sees it
    p = subprocess.Popen(["bash", "-lc", cmd], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, errors="replace")
    for line in p.stdout:
        print(line.rstrip("\n"), flush=True)
    rc = p.wait()
    if check and rc:
        raise RuntimeError(f"failed ({rc}): {cmd}")
    return rc


def up(path="/system_stats"):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=3)
        return True
    except Exception:
        return False


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "comfy-colab-kit"})
    if os.environ.get("HF_TOKEN") and url.startswith("https://huggingface.co/"):
        req.add_header("Authorization", "Bearer " + os.environ["HF_TOKEN"])
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


HF_RE = re.compile(r"^https://huggingface\.co/([^/]+/[^/]+)/resolve/([^/]+)/(.+)$")


# ─────────────────────────── downloads ───────────────────────────
class Downloads:
    def __init__(self, models, concurrency):
        self.models = models
        self.total = sum((m.get("size_gb") or 0) for m in models)
        self.done_gb = 0.0
        self.files_done = 0
        self.errors = []
        self.lock = threading.Lock()
        self.by_sha = {}
        self.pool = ThreadPoolExecutor(max_workers=max(1, concurrency))
        self.bg_pool = ThreadPoolExecutor(max_workers=max(1, concurrency))
        self.core_done = threading.Event()
        self.futures = {}

    def start(self):
        # core first, biggest first: the critical path is the largest core file
        order = sorted(self.models, key=lambda m: (not m.get("core"), -(m.get("size_gb") or 0)))
        seen_sha = {}
        for m in order:
            sha = m.get("sha256")
            if sha and sha in seen_sha:
                # identical file under a second name: link after the first one lands
                self.futures[m["id"]] = self.bg_pool.submit(self._link_after, seen_sha[sha], m)
                continue
            if sha:
                seen_sha[sha] = m["id"]
            if m.get("core"):
                self.futures[m["id"]] = self.pool.submit(self._one, m)
            else:
                # non-core files must not steal bandwidth from the critical path: they start once core is done
                self.futures[m["id"]] = self.bg_pool.submit(self._after_core, m)
        core = [f for i, f in self.futures.items() if next(x for x in self.models if x["id"] == i).get("core")]
        threading.Thread(target=lambda: ([f.exception() for f in core], self.core_done.set()), daemon=True).start()
        threading.Thread(target=self._report, daemon=True).start()

    def _after_core(self, m):
        self.core_done.wait()
        return self._one(m)

    def _link_after(self, first_id, m):
        self.futures[first_id].result()
        src = next(x for x in self.models if x["id"] == first_id)
        dst = f"{COMFY}/models/{m['dest']}"
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not os.path.exists(dst):
            os.link(f"{COMFY}/models/{src['dest']}", dst)
        self._finish(m)

    def _one(self, m):
        dst = f"{COMFY}/models/{m['dest']}"
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            return self._finish(m)
        hm = HF_RE.match(m["url"])
        try:
            if hm:
                from huggingface_hub import hf_hub_download
                tmp = f"/content/hf/{m['id']}"
                p = hf_hub_download(hm.group(1), hm.group(3), revision=hm.group(2), local_dir=tmp,
                                    token=os.environ.get("HF_TOKEN") or None)
                shutil.move(p, dst)
                shutil.rmtree(tmp, ignore_errors=True)
            else:
                rc = sh(f"aria2c -q -x16 -s16 -k1M -d '{os.path.dirname(dst)}' -o '{os.path.basename(dst)}' '{m['url']}'",
                        check=False, quiet=True) if shutil.which("aria2c") else 1
                if rc:
                    with urllib.request.urlopen(m["url"], timeout=60) as r, open(dst + ".part", "wb") as f:
                        shutil.copyfileobj(r, f, 8 << 20)
                    os.replace(dst + ".part", dst)
                if m.get("sha256"):
                    out = subprocess.run(["sha256sum", dst], capture_output=True, text=True).stdout.split()[0]
                    if out.lower() != m["sha256"].lower():
                        os.remove(dst)
                        raise RuntimeError(f"sha256 mismatch for {m['dest']}")
        except Exception as e:
            with self.lock:
                self.errors.append(f"{m['id']}: {e}")
            print(f"download failed {m['id']}: {e}", flush=True)
            raise
        self._finish(m)

    def _finish(self, m):
        with self.lock:
            self.files_done += 1
            self.done_gb += m.get("size_gb") or 0
        print(f"  ok {m['dest']}", flush=True)

    def _report(self):
        # bytes on disk under /content is the honest throughput signal (files_done only moves per file)
        disk0 = shutil.disk_usage("/content").used
        last = (disk0, time.time())
        while self.files_done < len(self.models) and not self.errors:
            time.sleep(5)
            now, used = time.time(), shutil.disk_usage("/content").used
            speed = (used - last[0]) / 1e9 / max(1e-6, now - last[1])
            last = (used, now)
            print("KIT_PROGRESS download " + json.dumps({
                "done_gb": round(self.done_gb, 2), "total_gb": round(self.total, 2),
                "written_gb": round((used - disk0) / 1e9, 2),
                "files_done": self.files_done, "files_total": len(self.models),
                "core_done": self.core_done.is_set(), "gbps": round(speed, 3)}), flush=True)

    def wait(self, ids):
        for i in ids:
            self.futures[i].result()


# ─────────────────────────── workflows ───────────────────────────
def png_workflow(data):
    b, i = data[8:], 0
    while i < len(b):
        n, = struct.unpack(">I", b[i:i + 4])
        t, d = b[i + 4:i + 8], b[i + 8:i + 8 + n]
        i += 12 + n
        if t == b"tEXt" and d.startswith(b"workflow\0"):
            return json.loads(d.split(b"\0", 1)[1])
    raise RuntimeError("PNG has no embedded workflow")


def prepare_workflow(wf, data, default_view):
    """Apply declared filename overrides and ComfyUI App-mode metadata; graph otherwise untouched."""
    g = png_workflow(data) if wf["format"] == "png" else json.loads(data)
    nodes = {n["id"]: n for n in g.get("nodes", [])}
    for o in wf.get("overrides", []):
        n = nodes.get(o["node"])
        if not n or len(n.get("widgets_values") or []) <= o["widget_index"]:
            raise RuntimeError(f"{wf['id']}: override target {o} not found (upstream workflow changed?)")
        n["widgets_values"][o["widget_index"]] = o["value"]
    extra = g.setdefault("extra", {})
    if wf.get("app_outputs"):
        extra["linearData"] = {"inputs": [list(x) for x in wf.get("app_inputs", [])],
                               "outputs": list(wf["app_outputs"])}
        extra["linearMode"] = default_view == "app"
    return g


def install_workflows():
    d = f"{COMFY}/user/default/workflows"
    os.makedirs(d, exist_ok=True)
    default_file = None
    for wf in PLAN["workflows"]:
        data = wf["inline"].encode() if wf.get("inline") else fetch(wf["url"])
        g = prepare_workflow(wf, data, PLAN.get("default_view", "app"))
        for u in wf.get("loras") or []:
            dst = f"{COMFY}/models/loras/{u.rsplit('/', 1)[1]}"
            if not os.path.exists(dst):
                with open(dst + ".part", "wb") as f:
                    f.write(fetch(u, timeout=600))
                os.replace(dst + ".part", dst)
        name = f"{wf['id']}.json"
        json.dump(g, open(f"{d}/{name}", "w"), ensure_ascii=False)
        if wf.get("default"):
            default_file = name
        print(f"  workflow {name} ({wf['title']})", flush=True)
    json.dump({"default_workflow": default_file, "default_view": PLAN.get("default_view", "app")},
              open(f"{COMFY}/user/default/kit.json", "w"))


def apply_settings():
    p = f"{COMFY}/user/default/comfy.settings.json"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    cur = json.load(open(p)) if os.path.exists(p) else {}
    cur.update(PLAN["comfyui"].get("settings") or {})
    json.dump(cur, open(p, "w"), indent=1)


HEARTBEAT_NODE = '''
import json, os, time
from aiohttp import web
from server import PromptServer
HB = "/content/kit_heartbeat"
KIT = "/content/ComfyUI/user/default/kit.json"
@PromptServer.instance.routes.post("/kit/heartbeat")
async def _hb(request):
    open(HB, "w").write(str(time.time()))
    return web.json_response({"ok": True})
@PromptServer.instance.routes.get("/kit/info")
async def _info(request):
    return web.json_response(json.load(open(KIT)) if os.path.exists(KIT) else {})
NODE_CLASS_MAPPINGS = {}
'''


def install_custom_nodes():
    base = f"{COMFY}/custom_nodes"
    os.makedirs(f"{base}/kit_heartbeat", exist_ok=True)
    open(f"{base}/kit_heartbeat/__init__.py", "w").write(HEARTBEAT_NODE)
    for n in PLAN.get("custom_nodes", []):
        dst = f"{base}/{n['id']}"
        if os.path.isdir(dst):
            continue
        if n["url"].startswith("https://huggingface.co/"):
            repo = n["url"].split("huggingface.co/", 1)[1].strip("/")
            from huggingface_hub import snapshot_download
            tmp = snapshot_download(repo, allow_patterns=[f"{n['subdir']}/*"] if n.get("subdir") else None,
                                    revision=n.get("ref") or None, local_dir=f"/content/hf/node_{n['id']}")
            shutil.copytree(os.path.join(tmp, n.get("subdir") or ""), dst)
        else:
            sh(f"git clone -q {n['url']} {dst}" + (f" && cd {dst} && git checkout -q {n['ref']}" if n.get("ref") else ""))
            if n.get("subdir"):
                inner = f"{dst}.tmp"
                os.rename(dst, inner)
                shutil.copytree(f"{inner}/{n['subdir']}", dst)
                shutil.rmtree(inner)
        if os.path.exists(f"{dst}/requirements.txt"):
            sh(f"uv pip install --system -q -r {dst}/requirements.txt || pip install -q -r {dst}/requirements.txt", check=False)


def start_tunnel():
    tok = os.environ.get("CF_TUNNEL_TOKEN", "").strip()
    sh("pkill -f 'cloudflared tunnel' || true", check=False, quiet=True)
    if not tok:
        print("WARNING: no CF_TUNNEL_TOKEN; ComfyUI is only reachable inside the VM", flush=True)
        return
    with open("/content/.cf_token", "w") as f:
        f.write(tok)
    os.chmod("/content/.cf_token", 0o600)
    subprocess.Popen(["bash", "-lc", f"nohup cloudflared tunnel --no-autoupdate run --token-file /content/.cf_token "
                                     f"> {LOG}/cloudflared.log 2>&1 &"])


def comfy_cmd():
    args = ["python", "main.py", "--listen", "127.0.0.1", "--port", str(PORT), "--disable-auto-launch"]
    emp = PLAN["comfyui"].get("extra_model_paths")
    if emp:
        path = f"{COMFY}/extra_model_paths.kit.yaml"
        json.dump(emp, open(path, "w"))  # JSON is valid YAML
        args += ["--extra-model-paths-config", path]
    args += list(PLAN["comfyui"].get("args") or [])
    return args


def watchdog():
    lost = PLAN.get("watchdog_gate_lost_min", 20) * 60
    max_s = PLAN.get("max_session_hours", 7) * 3600
    open(HB_FILE, "w").write(str(time.time()))
    while True:
        time.sleep(60)
        try:
            last = float(open(HB_FILE).read())
        except Exception:
            last = time.time()
        if time.time() - last > lost or time.time() - T0 > max_s:
            print("[watchdog] gate lost or session over limit; releasing VM", flush=True)
            subprocess.run(["bash", "-lc", "pkill -f cloudflared; pkill -f 'main.py --listen'"])
            try:
                from google.colab import runtime
                runtime.unassign()
            except Exception as e:
                open(f"{LOG}/watchdog.err", "w").write(repr(e))
            return


def smoke_prompt(d):
    """Accept a bare API graph or a /prompt request body ({"prompt": {...}, ...}, as some repos ship it)."""
    if isinstance(d, dict) and isinstance(d.get("prompt"), dict):
        return d["prompt"]
    return d


def smoke():
    api = smoke_prompt(json.loads(fetch(PLAN["smoke"]["api_url"])))
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/prompt", data=json.dumps({"prompt": api}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=30))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"prompt rejected ({e.code}): {e.read().decode(errors='replace')[:600]}") from None
    if "prompt_id" not in r:
        raise RuntimeError(f"prompt rejected: {json.dumps(r)[:500]}")
    pid = r["prompt_id"]
    deadline = time.time() + PLAN["smoke"].get("timeout_s", 600)
    while time.time() < deadline:
        time.sleep(2)
        h = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/history/{pid}", timeout=10))
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") == "error":
                raise RuntimeError(json.dumps(st.get("messages"))[:800])
            if any(o.get("images") for o in h[pid].get("outputs", {}).values()):
                return
    raise RuntimeError("timeout waiting for smoke image")


def main():
    os.makedirs(LOG, exist_ok=True)
    cfg = PLAN["comfyui"]
    if up():
        print("ComfyUI already running on this VM; skipping setup.", flush=True)
        print("KIT_READY " + json.dumps({"reused": True}), flush=True)
        return
    with Stage("bootstrap"):
        sh("pip install -q -U uv 'huggingface_hub>=0.36' 'hf-xet>=1.5.2'", quiet=True)
        os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"
        if not shutil.which("cloudflared"):
            sh("wget -q -O /tmp/cf.deb https://github.com/cloudflare/cloudflared/releases/latest/download/"
               "cloudflared-linux-amd64.deb && dpkg -i /tmp/cf.deb >/dev/null")
        if not os.path.isdir(COMFY):
            sh(f"git clone -q {cfg['repo']} {COMFY} && cd {COMFY} && git checkout -q {cfg['commit']}")
        start_tunnel()  # cloudflared retries until :8188 answers, so start it early
        sh("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader", check=False)
    dl = Downloads(PLAN["models"], PLAN.get("download_concurrency", 8))
    dl.start()  # downloads run in parallel with everything below
    with Stage("deps"):
        frontend = cfg.get("frontend_package")
        sh(f"cd {COMFY} && grep -viE '^(torch|torchvision|torchaudio)([<>=~ ]|$)' requirements.txt > /tmp/req.txt && "
           f"(uv pip install --system -q -r /tmp/req.txt {repr(frontend) if frontend else ''} || "
           f"pip install -q -r /tmp/req.txt {repr(frontend) if frontend else ''})")
        install_custom_nodes()
    with Stage("install_workflows"):
        install_workflows()
        apply_settings()
    with Stage("download"):
        core = [m["id"] for m in PLAN["models"] if m.get("core")]
        dl.wait(core if PLAN.get("fast_start", True) else [m["id"] for m in PLAN["models"]])
    with Stage("comfy_boot"):
        env = {**os.environ, **{k: str(v) for k, v in (cfg.get("env") or {}).items()}}
        env.pop("CF_TUNNEL_TOKEN", None)
        env.pop("HF_TOKEN", None)
        subprocess.Popen(comfy_cmd(), cwd=COMFY, env=env, stdout=open(f"{LOG}/comfyui.log", "w"),
                         stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(300):
            if up():
                break
            time.sleep(1)
        else:
            sh(f"tail -40 {LOG}/comfyui.log", check=False)
            raise RuntimeError("ComfyUI did not start")
        sh(f"grep -iE 'IMPORT FAILED|Traceback' {LOG}/comfyui.log | tail -10", check=False, quiet=True)
    if not any(t.name == "kit_watchdog" for t in threading.enumerate()):
        threading.Thread(target=watchdog, name="kit_watchdog", daemon=True).start()
    if PLAN.get("smoke"):
        with Stage("smoke"):
            smoke()
    print("KIT_READY " + json.dumps({"total_s": round(time.time() - T0), "stages": TIMES,
                                     "background_downloads": len(PLAN["models"]) - dl.files_done}), flush=True)


if __name__ == "__main__":
    main()
