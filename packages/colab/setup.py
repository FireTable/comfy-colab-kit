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
import signal
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


class KitAbort(Exception):
    """A stage failed and KIT_FAILED was already printed."""


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
            # not sys.exit(): inside the Jupyter kernel that dumps a huge (and broken) IPython traceback
            raise KitAbort(self.name) from None
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
# HF files download in a child process each, so a stalled file can be killed/restarted and background (non-core)
# files can be paused (SIGSTOP) while ComfyUI is executing a prompt.
HF_DL_SCRIPT = r"""
import os, sys
from huggingface_hub import hf_hub_download
print(hf_hub_download(sys.argv[1], sys.argv[2], revision=sys.argv[3], local_dir=sys.argv[4],
                      token=os.environ.get("HF_TOKEN") or None), flush=True)
"""
HF_TMP = "/content/hf"
STALL_S = int(os.environ.get("KIT_STALL_S", "90"))
RETRYABLE = re.compile(r"429|Too Many|\b5\d\d\b|timed out|Timeout|Connection|reset by peer")


def du(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def comfy_busy():
    """True while ComfyUI is executing a prompt (background downloads pause)."""
    try:
        q = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/queue", timeout=3).read())
        return bool(q.get("queue_running"))
    except Exception:
        return False


class Downloads:
    def __init__(self, models, concurrency, busy=comfy_busy, stall_s=STALL_S):
        self.models = models
        self.size = {m["id"]: int((m.get("size_gb") or 0) * 1e9) for m in models}
        self.total = sum(self.size.values())
        self.core_ids = [m["id"] for m in models if m.get("core")]
        self.core_total = sum(self.size[i] for i in self.core_ids)
        self.done_bytes, self.core_done_bytes = 0, 0
        self.files_done = 0
        self.errors = []
        self.restarts = 0
        self.lock = threading.Lock()
        self.live = {}   # id -> tmp dir of an in-flight HF download
        self.procs = {}  # id -> (Popen, pausable)
        self.paused = False
        self.busy, self.stall_s = busy, stall_s
        self.pool = ThreadPoolExecutor(max_workers=max(1, concurrency))
        self.bg_pool = ThreadPoolExecutor(max_workers=max(1, concurrency))
        self.core_done = threading.Event()
        self.futures = {}
        self.t0 = time.time()

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
        core = [self.futures[i] for i in self.core_ids if i in self.futures]
        threading.Thread(target=lambda: ([f.exception() for f in core], self.core_done.set()), daemon=True).start()
        threading.Thread(target=self._report, daemon=True).start()
        threading.Thread(target=self._pauser, daemon=True).start()

    def all_done(self):
        return self.files_done >= len(self.models) or bool(self.errors)

    def _after_core(self, m):
        self.core_done.wait()
        while self.paused:
            time.sleep(1)
        return self._one(m)

    def _link_after(self, first_id, m):
        self.futures[first_id].result()
        src = next(x for x in self.models if x["id"] == first_id)
        dst = f"{COMFY}/models/{m['dest']}"
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not os.path.exists(dst):
            os.link(f"{COMFY}/models/{src['dest']}", dst)
        self._finish(m)

    def _pauser(self):
        while not self.all_done():
            time.sleep(2)
            want = self.busy()
            if want == self.paused:
                continue
            self.paused = want
            for p, pausable in list(self.procs.values()):
                if pausable and p.poll() is None:
                    try:
                        p.send_signal(signal.SIGSTOP if want else signal.SIGCONT)
                    except OSError:
                        pass
            print(f"  background downloads {'paused: prompt running' if want else 'resumed'}", flush=True)

    def _hf(self, m, repo, rev, fname, dst):
        tmp = f"{HF_TMP}/{m['id']}"
        pausable = not m.get("core")
        attempt = 0
        while True:
            p = subprocess.Popen([sys.executable, "-c", HF_DL_SCRIPT, repo, fname, rev, tmp],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
            with self.lock:
                self.live[m["id"]] = tmp
                self.procs[m["id"]] = (p, pausable)
            if pausable and self.paused:
                p.send_signal(signal.SIGSTOP)
            out = []
            reader = threading.Thread(target=lambda: out.extend(p.stdout), daemon=True)
            reader.start()
            last, last_t, stalled = -1, time.time(), False
            while p.poll() is None:
                time.sleep(3)
                if pausable and self.paused:
                    last_t = time.time()  # paused time is not a stall
                    continue
                size = du(tmp)
                if size != last:
                    last, last_t = size, time.time()
                elif time.time() - last_t > self.stall_s:
                    print(f"  stall: no bytes for {self.stall_s}s on {m['id']}; restarting that file", flush=True)
                    p.kill()
                    stalled = True
                    break
            p.wait()
            reader.join(5)
            with self.lock:
                self.procs.pop(m["id"], None)
            text = "".join(out).strip()
            if p.returncode == 0 and not stalled and text:
                path = text.splitlines()[-1].strip()
                break
            attempt += 1
            if stalled:
                self.restarts += 1
            if attempt >= 8 or (not stalled and not RETRYABLE.search(text)):
                raise RuntimeError(text[-300:] or f"exit {p.returncode}")
            wait = 2 if stalled else min(120, 10 * 2 ** (attempt - 1))
            if not stalled:
                print(f"  retry {m['id']} in {wait}s ({text[-120:]})", flush=True)
            time.sleep(wait)
        shutil.move(path, dst)  # local_dir keeps repo subfolders; the file always lands flat at models/<dest>
        with self.lock:
            self.live[m["id"]] = dst
        shutil.rmtree(tmp, ignore_errors=True)

    def _one(self, m):
        dst = f"{COMFY}/models/{m['dest']}"
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            return self._finish(m)
        hm = HF_RE.match(m["url"])
        try:
            if hm:
                self._hf(m, hm.group(1), hm.group(2), hm.group(3), dst)
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
            self.live.pop(m["id"], None)
            self.files_done += 1
            self.done_bytes += self.size[m["id"]]
            if m.get("core"):
                self.core_done_bytes += self.size[m["id"]]
        print(f"  ok {m['dest']}", flush=True)

    def snapshot(self):
        with self.lock:
            live = dict(self.live)
            done, core_done = self.done_bytes, self.core_done_bytes
        inflight = {i: min(du(t), self.size[i] or 1 << 62) for i, t in live.items()}
        cur_b = done + sum(inflight.values())
        cur_cb = core_done + sum(v for i, v in inflight.items() if i in self.core_ids)
        with self.lock:
            if not hasattr(self, "_max_b"):
                self._max_b, self._max_cb = 0, 0
            self._max_b = max(self._max_b, cur_b)
            self._max_cb = max(self._max_cb, cur_cb)
            return (self._max_b, self._max_cb)

    def progress(self, rate, eta=None):
        b, cb = self.snapshot()
        left = max(0, self.total - b)
        if eta is None and rate > 1e5 and not self.paused:
            eta = int(left / rate)
        return {
            "bytes_done": b, "bytes_total": self.total,
            "core_bytes_done": cb, "core_bytes_total": self.core_total,
            "bytes_per_s": int(rate), "eta_s": eta,
            "files_done": self.files_done, "files_total": len(self.models),
            "core_done": self.core_done.is_set(), "paused": self.paused, "restarts": self.restarts,
            # legacy, file-granular
            "done_gb": round(b / 1e9, 2), "total_gb": round(self.total / 1e9, 2),
        }

    def _report(self):
        last_b, last_t, rate = self.snapshot()[0], time.time(), 0.0
        while not self.all_done():
            time.sleep(5)
            b, now = self.snapshot()[0], time.time()
            inst = max(0.0, (b - last_b) / max(1e-6, now - last_t))
            rate = inst if rate == 0 else 0.6 * rate + 0.4 * inst  # smoothed for a steady ETA
            last_b, last_t = b, now
            avg_rate = b / max(1.0, now - self.t0)
            effective_eta_rate = max(rate, 0.4 * avg_rate)
            left = max(0, self.total - b)
            eta = int(left / effective_eta_rate) if effective_eta_rate > 1e5 and not self.paused else None
            print("KIT_PROGRESS download " + json.dumps(self.progress(rate, eta=eta)), flush=True)
        print("KIT_PROGRESS download " + json.dumps(self.progress(0, eta=0)), flush=True)

    def wait(self, ids):
        for i in ids:
            self.futures[i].result()
        if set(self.core_ids).issubset(set(ids)):
            self.core_done.wait()


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


def remap_models(obj, aliases):
    """Profile `model_aliases`: rewrite loader filenames anywhere in a UI graph (nodes, subgraph definitions) or an
    API prompt (inputs), so every workflow uses the profile's shared weights. Returns the number of rewrites."""
    if not aliases:
        return 0
    n = 0
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and v in aliases:
                obj[k] = aliases[v]
                n += 1
            else:
                n += remap_models(v, aliases)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str) and v in aliases:
                obj[i] = aliases[v]
                n += 1
            else:
                n += remap_models(v, aliases)
    return n


def prepare_workflow(wf, data, default_view):
    """Apply declared filename overrides and ComfyUI App-mode metadata; graph otherwise untouched."""
    g = png_workflow(data) if wf["format"] == "png" else json.loads(data)
    nodes = {n["id"]: n for n in g.get("nodes", [])}
    for o in wf.get("overrides", []):
        n = nodes.get(o["node"])
        if not n or len(n.get("widgets_values") or []) <= o["widget_index"]:
            raise RuntimeError(f"{wf['id']}: override target {o} not found (upstream workflow changed?)")
        n["widgets_values"][o["widget_index"]] = o["value"]
    remap_models(g, PLAN.get("model_aliases") or {})
    extra = g.setdefault("extra", {})
    if wf.get("app_outputs"):
        extra["linearData"] = {"inputs": [list(x) for x in wf.get("app_inputs", [])],
                               "outputs": list(wf["app_outputs"])}
        extra["linearMode"] = default_view == "app"
    return g


def install_workflows(update_default=True):
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
    if update_default:
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
    remap_models(api, PLAN.get("model_aliases") or {})
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


def fetch_only():
    """On-demand download on a running VM (gate: POST /_gate/fetch). No KIT_STAGE lines: the session state
    stays `ready`; progress arrives as KIT_PROGRESS, the result as KIT_FETCHED / KIT_FETCH_FAILED."""
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    if PLAN.get("xet_high_performance", True):
        os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"
    ids = [w["id"] for w in PLAN["workflows"]]
    try:
        install_custom_nodes()
        install_workflows(update_default=False)
        dl = Downloads(PLAN["models"], PLAN.get("download_concurrency", 8))
        dl.start()
        dl.wait([m["id"] for m in PLAN["models"]])
    except Exception as e:  # noqa: BLE001
        print(f"KIT_FETCH_FAILED {json.dumps({'workflows': ids, 'error': repr(e)[:300]})}", flush=True)
        return
    print(f"KIT_FETCHED {json.dumps({'workflows': ids})}", flush=True)


def main():
    os.makedirs(LOG, exist_ok=True)
    if PLAN.get("fetch_only"):
        return fetch_only()
    cfg = PLAN["comfyui"]
    if up():
        print("ComfyUI already running on this VM; skipping setup.", flush=True)
        print("KIT_READY " + json.dumps({"reused": True}), flush=True)
        return
    with Stage("bootstrap"):
        sh("pip install -q -U uv 'huggingface_hub>=0.36' 'hf-xet>=1.5.2'", quiet=True)
        if PLAN.get("xet_high_performance", True):
            os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"
        os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"  # tqdm bars flood the wake-page log
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
           + (f"(uv pip install --system -q -r /tmp/req.txt {repr(frontend) if frontend else ''} || "
              if PLAN.get("pip_installer", "uv") == "uv" else "(")
           + f"pip install -q -r /tmp/req.txt {repr(frontend) if frontend else ''})")
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
    try:
        main()
    except KitAbort:
        pass
