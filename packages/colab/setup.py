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


def ts(label):
    """Absolute timestamp marker (KIT_TS <epoch> <label>). The gate forwards it as a plain log line."""
    print(f"KIT_TS {time.time():.1f} {label}", flush=True)


ts("import")
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
#
# The downloader is a hand-rolled multi-connection Range fetcher (stdlib only). Rationale, measured on the
# Colab L4 VM: huggingface_hub's classic path tops out around 130-145 MB/s per file, a plain GET reaches
# ~226 MB/s, and 8 parallel Range connections on the same object reach ~700 MB/s (the VM egress ceiling).
# `HF_HUB_ENABLE_HF_TRANSFER` is ignored by huggingface_hub >= 0.32 and Xet stays disabled on purpose
# (Xet-backed repos report 403/429 stalls on shared Colab IPs), so the Range fan-out is the fast path.
# Chunks land in <tmp>/<name>.parts/ so a killed file resumes where it stopped instead of restarting.
DL_SCRIPT = r"""
import os, sys, time, math, shutil, hashlib, threading, queue
import urllib.request, urllib.error, urllib.parse

REPO, FNAME, REV, TMP = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
EXPECT_SHA = (sys.argv[5] if len(sys.argv) > 5 else "")
CONNS = max(1, int(os.environ.get("KIT_DL_CONNS", "8")))
CHUNK = max(16, int(os.environ.get("KIT_DL_CHUNK_MB", "256"))) * 1024 * 1024
TOKEN = os.environ.get("HF_TOKEN") or ""
URL = "https://huggingface.co/{}/resolve/{}/{}".format(REPO, REV or "main", FNAME)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    # Follow the HF -> CDN redirect, but never carry the HF token to the CDN host.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlparse(newurl).netloc != urllib.parse.urlparse(req.full_url).netloc:
            new.headers = {k: v for k, v in new.headers.items() if k.lower() != "authorization"}
        return new


def total_size(url):
    # One probe request to learn the object size. The signed CDN URL it redirects to is deliberately
    # NOT reused: sharing one signed URL across parallel ranges is what throttles the CDN to ~65 MB/s.
    opener = urllib.request.build_opener(_SafeRedirect)
    hdrs = {"User-Agent": "comfy-colab-kit", "Range": "bytes=0-0", "Accept-Encoding": "identity"}
    if TOKEN:
        hdrs["Authorization"] = "Bearer " + TOKEN
    with opener.open(urllib.request.Request(url, headers=hdrs), timeout=60) as r:
        cr = r.headers.get("Content-Range")
        etag = (r.headers.get("ETag") or r.headers.get("x-linked-etag") or "").strip().strip('"')
        if r.status != 206 or not cr:
            raise RuntimeError("server ignored Range (status {})".format(r.status))
        return int(cr.rsplit("/", 1)[1]), etag


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(8 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def fetch_range(url, total, out_path):
    # One disk write pass: chunks are written straight into the final file at their offsets.
    # A sidecar bitmap keeps resume state, so a killed download continues instead of restarting.
    nchunks = max(1, int(math.ceil(total / float(CHUNK))))
    bounds = [(i * CHUNK, min(total, (i + 1) * CHUNK) - 1) for i in range(nchunks)]
    bitmap = out_path + ".chunks"
    done = set()
    if os.path.exists(bitmap):
        with open(bitmap) as f:
            for line in f:
                line = line.strip()
                if line.isdigit():
                    done.add(int(line))
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    fd = os.open(out_path, os.O_RDWR | os.O_CREAT, 0o644)
    os.ftruncate(fd, total)
    state = {"done": sum(bounds[i][1] - bounds[i][0] + 1 for i in done), "inflight": 0}
    lock = threading.Lock()
    q = queue.Queue()
    for i, b in enumerate(bounds):
        if i not in done:
            q.put((i, b))
    errors = []

    def worker():
        while True:
            try:
                i, (start_b, end_b) = q.get_nowait()
            except queue.Empty:
                return
            need = end_b - start_b + 1
            have = 0
            attempt = 0
            while have < need and attempt < 6:
                attempt += 1
                try:
                    s = start_b + have
                    hdrs = {"User-Agent": "comfy-colab-kit", "Accept-Encoding": "identity",
                            "Range": "bytes={}-{}".format(s, end_b)}
                    if TOKEN:
                        hdrs["Authorization"] = "Bearer " + TOKEN
                    opener = urllib.request.build_opener(_SafeRedirect)
                    with opener.open(urllib.request.Request(url, headers=hdrs), timeout=120) as r:
                        if r.status != 206:
                            raise RuntimeError("no range support (status {})".format(r.status))
                        buf = b""
                        while True:
                            b = r.read(1 << 20)
                            if not b:
                                break
                            buf += b
                            if len(buf) >= (8 << 20):
                                os.pwrite(fd, buf, s + have)
                                have += len(buf)
                                with lock:
                                    state["inflight"] += len(buf)
                                buf = b""
                        if buf:
                            os.pwrite(fd, buf, s + have)
                            have += len(buf)
                            with lock:
                                state["inflight"] += len(buf)
                except Exception as e:
                    if attempt >= 6:
                        errors.append("chunk {}: {!r}".format(i, e))
                        break
                    time.sleep(min(20, 2 * attempt))
            if have >= need:
                with lock:
                    state["inflight"] -= need
                    state["done"] += need
                with open(bitmap, "a") as f:
                    f.write("{}\n".format(i))

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(min(CONNS, max(1, nchunks)))]
    for t in threads:
        t.start()

    def report():
        while any(t.is_alive() for t in threads):
            time.sleep(1)
            with lock:
                n = state["done"] + state["inflight"]
            print("DL_PROGRESS {} {}".format(n, total), flush=True)
    mon = threading.Thread(target=report, daemon=True)
    mon.start()
    for t in threads:
        t.join()
    mon.join(2)
    os.close(fd)
    if errors:
        raise RuntimeError("; ".join(errors[:3]))
    if os.path.getsize(out_path) != total:
        raise RuntimeError("size mismatch")
    print("DL_PROGRESS {} {}".format(total, total), flush=True)


class Fatal(Exception):
    pass


out = None
try:
    total, etag = total_size(URL)
    out = os.path.join(TMP, os.path.basename(FNAME))
    fetch_range(URL, total, out)
    if EXPECT_SHA and os.environ.get("KIT_VERIFY_SHA") == "1":
        if sha256_of(out).lower() != EXPECT_SHA.lower():
            os.remove(out)
            raise Fatal("sha256 mismatch")  # corrupt transfer: retrying the same way will not help
except Fatal:
    raise
except Exception as e:
    print("  range download failed ({!r}); falling back to huggingface_hub".format(e), flush=True)
    os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "huggingface_hub"], check=False)
        from huggingface_hub import hf_hub_download
    out = hf_hub_download(REPO, FNAME, revision=REV or "main", local_dir=TMP, token=TOKEN or None)
print(out, flush=True)
"""
HF_DL_SCRIPT = DL_SCRIPT
HF_TMP = "/content/hf"
STORE_ROOT = None   # set to a RAM tmpfs root when the RAM model store is active
STALL_S = int(os.environ.get("KIT_STALL_S", "30"))
RETRYABLE = re.compile(r"429|Too Many|\b5\d\d\b|timed out|Timeout|Connection|reset by peer")


def mem_available():
    """Bytes of RAM the kernel can hand out right now."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def setup_model_store(plan):
    """Optionally keep the model files in a RAM-backed tmpfs instead of on the VM disk.

    Measured on the Colab L4: sequential disk writes top out at ~292 MB/s while /dev/shm does
    ~2.4 GB/s, so a RAM store turns the download stage from disk-bound into network-bound
    (the 24.9 GB default profile lands ~45 s instead of ~68 s) and lets ComfyUI boot faster.
    It costs ~25 GB of RAM, so it is opt-in and falls back to disk whenever RAM is too tight.
    """
    mode = os.environ.get("KIT_MODEL_STORE", PLAN.get("model_store", "disk"))
    if mode != "ram":
        return None
    total = sum(int((m.get("size_gb") or 0) * 1e9) for m in plan["models"])
    avail = mem_available()
    need = int(total * 1.15) + (4 * 1000 ** 3)  # models + slack for torch/offload
    if avail < need:
        print(f"  model store: RAM {avail / 1e9:.0f} GB free < {need / 1e9:.0f} GB needed -> disk", flush=True)
        return None
    root = "/mnt/ram"
    os.makedirs(root, exist_ok=True)
    size_mb = int((total * 1.1 + 2 * 1000 ** 3) // (1024 * 1024))
    if root not in open("/proc/mounts").read():
        rc = sh(f"mount -t tmpfs -o size={size_mb}M,mode=0755 tmpfs {root}", check=False, quiet=True)
        if rc:
            print("  model store: tmpfs mount failed -> disk", flush=True)
            return None
    os.makedirs(f"{root}/models", exist_ok=True)
    print(f"  model store: RAM tmpfs at {root} ({total / 1e9:.1f} GB models, {size_mb / 1024:.0f} GB tmpfs)", flush=True)
    return root


def point_models_to(root):
    """Stage downloads on the RAM filesystem. Model files are placed in the RAM store and exposed to
    ComfyUI as symlinks, so an on-demand workflow that no longer fits in RAM simply lands on disk
    instead of failing with ENOSPC."""
    global HF_TMP, STORE_ROOT
    os.makedirs(f"{root}/models", exist_ok=True)
    HF_TMP = f"{root}/hf"  # same filesystem -> the final move is a rename, not a copy
    STORE_ROOT = root
    os.makedirs(HF_TMP, exist_ok=True)


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
        self.child_bytes = {}  # id -> bytes the downloader child reports (accurate with sparse writes)
        self.procs = {}  # id -> (Popen, pausable)
        self.paused = False
        self.pending_links = []  # (target, dst) pairs deferred until COMFY directory exists
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
        src_target = self._ram_target(src) or f"{COMFY}/models/{src['dest']}"
        dst = f"{COMFY}/models/{m['dest']}"
        if not os.path.isdir(COMFY):
            with self.lock:
                self.pending_links.append((src_target, dst))
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if not (os.path.islink(dst) or os.path.exists(dst)):
                if os.path.exists(src_target):
                    os.symlink(src_target, dst)
                elif os.path.exists(f"{COMFY}/models/{src['dest']}"):
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
            p = subprocess.Popen([sys.executable, "-c", HF_DL_SCRIPT, repo, fname, rev, tmp, m.get("sha256") or ""],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
            with self.lock:
                self.live[m["id"]] = tmp
                self.procs[m["id"]] = (p, pausable)
            if pausable and self.paused:
                p.send_signal(signal.SIGSTOP)
            out = []

            def _pump():
                for line in p.stdout:
                    if line.startswith("DL_PROGRESS "):
                        try:
                            n = int(line.split()[1])
                            with self.lock:
                                self.child_bytes[m["id"]] = n
                        except (IndexError, ValueError):
                            pass
                    else:
                        out.append(line)
            reader = threading.Thread(target=_pump, daemon=True)
            reader.start()
            last, last_t, stalled = -1, time.time(), False
            while p.poll() is None:
                time.sleep(3)
                if pausable and self.paused:
                    last_t = time.time()  # paused time is not a stall
                    continue
                with self.lock:
                    reported = self.child_bytes.get(m["id"])
                size = reported if reported is not None else du(tmp)
                target = int(m.get("size_gb", 0) * 1e9)
                is_verifying = target > 0 and size >= int(target * 0.98)
                max_stall = (self.stall_s * 3) if is_verifying else self.stall_s
                if size != last:
                    last, last_t = size, time.time()
                elif time.time() - last_t > max_stall:
                    print(f"  stall: no bytes for {max_stall}s on {m['id']}; restarting that file", flush=True)
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

    def _ram_target(self, m):
        """Where this file should really live: the RAM store when it fits, the disk otherwise."""
        if not STORE_ROOT:
            return None
        try:
            free = shutil.disk_usage(STORE_ROOT).free
        except OSError:
            return None
        need = int((m.get("size_gb") or 0) * 1e9 * 1.15) + 1 * 1000 ** 3
        if free > need:
            return f"{STORE_ROOT}/models/{m['dest']}"
        return None

    def _link(self, target, dst):
        if not os.path.isdir(COMFY):
            with self.lock:
                self.pending_links.append((target, dst))
            return
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.islink(dst) or os.path.exists(dst):
            os.remove(dst) if os.path.islink(dst) else os.remove(dst)
        os.symlink(target, dst)

    def sync_links(self):
        """Link any downloads that finished before ComfyUI was cloned."""
        with self.lock:
            links = list(self.pending_links)
            self.pending_links.clear()
        for target, dst in links:
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if os.path.islink(dst) or os.path.exists(dst):
                    os.remove(dst) if os.path.islink(dst) else os.remove(dst)
                os.symlink(target, dst)
            except Exception:
                pass

    def _one(self, m):
        dst = f"{COMFY}/models/{m['dest']}"
        target = self._ram_target(m) or dst
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.exists(target):
            if target != dst:
                self._link(target, dst)
            return self._finish(m)
        hm = HF_RE.match(m["url"])
        try:
            if hm:
                self._hf(m, hm.group(1), hm.group(2), hm.group(3), target)
            else:
                rc = sh(f"aria2c -q -x16 -s16 -k1M -d '{os.path.dirname(target)}' -o '{os.path.basename(target)}' '{m['url']}'",
                        check=False, quiet=True) if shutil.which("aria2c") else 1
                if rc:
                    with urllib.request.urlopen(m["url"], timeout=60) as r, open(target + ".part", "wb") as f:
                        shutil.copyfileobj(r, f, 8 << 20)
                    os.replace(target + ".part", target)
                if m.get("sha256"):
                    out = subprocess.run(["sha256sum", target], capture_output=True, text=True).stdout.split()[0]
                    if out.lower() != m["sha256"].lower():
                        os.remove(target)
                        raise RuntimeError(f"sha256 mismatch for {m['dest']}")
        except Exception as e:
            with self.lock:
                self.errors.append(f"{m['id']}: {e}")
            print(f"download failed {m['id']}: {e}", flush=True)
            raise
        if target != dst:
            self._link(target, dst)
        self._finish(m)

    def _finish(self, m):
        with self.lock:
            self.live.pop(m["id"], None)
            self.child_bytes.pop(m["id"], None)
            self.files_done += 1
            self.done_bytes += self.size[m["id"]]
            if m.get("core"):
                self.core_done_bytes += self.size[m["id"]]
        print(f"  ok {m['dest']}", flush=True)

    def snapshot(self):
        with self.lock:
            live = dict(self.live)
            done, core_done = self.done_bytes, self.core_done_bytes
        inflight = {}
        for i, t in live.items():
            reported = self.child_bytes.get(i)
            n = reported if reported is not None else du(t)
            inflight[i] = min(n, self.size[i] or 1 << 62)
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


def _slot_type_for(wname):
    if wname in ("text", "value", "prompt"):
        return "STRING"
    if wname in ("seed", "noise_seed", "width", "height", "batch_size"):
        return "INT"
    if wname == "image":
        return "IMAGE"
    if wname == "switch":
        return "BOOLEAN"
    if wname == "denoise":
        return "FLOAT"
    if wname == "choice":
        return "COMBO"
    return "*"


def decorate_app_nodes(g, wf):
    """Ensure nodes and widgets exposed in App Mode have clean, descriptive titles and labels."""
    nodes = {n["id"]: n for n in g.get("nodes", [])}
    for item in wf.get("app_inputs", []):
        nid, wname = item[0], item[1]
        custom_label = item[2] if len(item) > 2 and item[2] else None
        node = nodes.get(nid)
        if not node:
            continue
        ntype = str(node.get("type", ""))
        curr_title = str(node.get("title") or "")
        curr_lower = curr_title.lower()

        label = custom_label
        new_title = None

        if not label:
            if wname == "image":
                if "1" in curr_lower or "first" in curr_lower:
                    label = "首张参考底图 (First Image)"
                    new_title = "参考底图 (Input Image)"
                elif "2" in curr_lower or "second" in curr_lower or nid in (19, 475):
                    label = "第二张参考底图 (Second Image)"
                    new_title = "参考底图 (Input Image)"
                else:
                    label = "参考底图 (Input Image)"
                    new_title = "参考底图 (Input Image)"
            elif wname == "denoise":
                label = "重绘幅度 (Denoise)"
                new_title = "重绘幅度 (Denoise)"
            elif wname == "choice":
                label = "画质与速度预设 (Preset)"
                new_title = "画质与速度预设 (Preset)"
            elif wname in ("seed", "noise_seed"):
                label = "随机种子 (Seed)"
                new_title = "随机种子 (Seed)"
            elif wname == "width":
                label = "图像宽度 (Width)"
                new_title = "图像尺寸 (Image Size)"
            elif wname == "height":
                label = "图像高度 (Height)"
                new_title = "图像尺寸 (Image Size)"
            elif wname == "batch_size":
                label = "生成张数 (Batch Size)"
                new_title = "图像尺寸 (Image Size)"
            elif wname == "switch":
                label = "提示词增强 (Prompt Enhancer)"
                new_title = "提示词增强 (Prompt Enhancer)"
            elif wname in ("value", "text", "prompt"):
                if "width" in curr_lower:
                    label = "图像宽度 (Width)"
                    new_title = "图像宽度 (Width)"
                elif "height" in curr_lower:
                    label = "图像高度 (Height)"
                    new_title = "图像高度 (Height)"
                elif "batch" in curr_lower:
                    label = "生成张数 (Batch Size)"
                    new_title = "生成张数 (Batch Size)"
                elif "seed" in curr_lower or ntype == "PrimitiveInt":
                    label = "随机种子 (Seed)"
                    new_title = "随机种子 (Seed)"
                elif "neg" in curr_lower or nid in (4, 8, 40) or (nid == 18 and wname == "text") or (nid == 10 and "chinoll" in wf.get("id", "")):
                    label = "负面提示词 (Negative Prompt)"
                    new_title = "负面提示词 (Negative Prompt)"
                else:
                    label = "正向提示词 (Positive Prompt)"
                    new_title = "正向提示词 (Positive Prompt)"

        generic_titles = {"", "none", "cliptextencode", "primitivestringmultiline", "primitiveint",
                          "emptyflux2latentimage", "randomnoise", "basicscheduler", "loadimage", "customcombo"}
        if new_title and (not curr_title or curr_lower in generic_titles):
            node["title"] = new_title

        # Set widget label via LiteGraph's canonical backing slot channel:
        # LiteGraph configure() maps:
        #   const input = this.inputs.find((i) => i.widget?.name === w.name);
        #   if (input?.label) w.label = input.label;
        # CRITICAL:
        # 1. NEVER inject a 'widgets' key on node dictionary (destroys live widget class instances).
        # 2. Backing slot in node['inputs'] must carry {"widget": {"name": wname}, "label": label}.
        if label:
            inputs = node.setdefault("inputs", [])
            matched = False
            for slot in inputs:
                if slot.get("widget", {}).get("name") == wname or (slot.get("name") == wname and not slot.get("link")):
                    slot["label"] = label
                    slot.setdefault("widget", {})["name"] = wname
                    matched = True
                    break
            if not matched:
                inputs.append({
                    "name": wname,
                    "type": _slot_type_for(wname),
                    "widget": {"name": wname},
                    "label": label,
                    "link": None,
                })


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
    decorate_app_nodes(g, wf)
    extra = g.setdefault("extra", {})
    if wf.get("app_outputs"):
        extra["linearData"] = {"inputs": [list(x[:2]) for x in wf.get("app_inputs", [])],
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
        workflows_summary = [
            {"id": w["id"], "title": w.get("title", w["id"]), "file": f"{w['id']}.json"}
            for w in PLAN.get("workflows", [])
        ]
        json.dump({"default_workflow": default_file, "default_view": PLAN.get("default_view", "app"),
                   "workflows": workflows_summary},
                  open(f"{COMFY}/user/default/kit.json", "w"))
        if default_file and os.path.exists(f"{d}/{default_file}"):
            shutil.copy(f"{d}/{default_file}", f"{COMFY}/default_workflow.json")


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
COMFY = "/content/ComfyUI"

@PromptServer.instance.routes.post("/kit/heartbeat")
@PromptServer.instance.routes.get("/kit/heartbeat")
async def _hb(request):
    try:
        open(HB, "w").write(str(time.time()))
    except Exception:
        pass
    return web.json_response({"ok": True})

@web.middleware
async def _touch_hb_mw(request, handler):
    try:
        open(HB, "w").write(str(time.time()))
    except Exception:
        pass
    return await handler(request)

try:
    if hasattr(PromptServer.instance, "app") and _touch_hb_mw not in PromptServer.instance.app.middlewares:
        PromptServer.instance.app.middlewares.append(_touch_hb_mw)
except Exception:
    pass

@PromptServer.instance.routes.get("/kit/info")
async def _info(request):
    return web.json_response(json.load(open(KIT)) if os.path.exists(KIT) else {})

@PromptServer.instance.routes.get("/kit/workflow")
async def _default_wf(request):
    p = f"{COMFY}/default_workflow.json"
    if os.path.exists(p):
        return web.FileResponse(p, headers={"Content-Type": "application/json", "Cache-Control": "no-cache"})
    return web.Response(status=404, text="default workflow not found")

@PromptServer.instance.routes.get("/kit/workflows/{name:.*}")
async def _named_wf(request):
    name = request.match_info["name"]
    p = f"{COMFY}/user/default/workflows/{name}"
    if os.path.exists(p):
        return web.FileResponse(p, headers={"Content-Type": "application/json", "Cache-Control": "no-cache"})
    return web.Response(status=404, text="workflow not found")

@PromptServer.instance.routes.get("/api/userdata/workflows/{name:.*}")
@PromptServer.instance.routes.get("/userdata/workflows/{name:.*}")
async def _subpath_wf(request):
    name = request.match_info["name"]
    p = f"{COMFY}/user/default/workflows/{name}"
    if os.path.exists(p):
        return web.FileResponse(p, headers={"Content-Type": "application/json", "Cache-Control": "no-cache"})
    return web.Response(status=404, text="workflow not found")

NODE_CLASS_MAPPINGS = {}
WEB_DIRECTORY = "./web"
'''

KIT_JS = r'''
import { app } from "../../scripts/app.js";

app.registerExtension({
  name: "kit.init",
  async setup() {
    // Defer execution so ComfyUI can finish constructing LGraphCanvas and mounting the DOM
    setTimeout(async () => {
      try {
        const info = await (await fetch("/kit/info")).json();
        if (!info.default_workflow) return;
        if (sessionStorage.getItem("kit.loaded_wf") === info.default_workflow) return;

        // Poll until canvas and graph are fully initialized by ComfyUI
        for (let i = 0; i < 60; i++) {
          if (app.canvas && app.graph && (app.canvas.canvas || document.querySelector("canvas"))) break;
          await new Promise(r => setTimeout(r, 100));
        }
        if (!app.canvas || !app.graph) return;

        let res = await fetch("/kit/workflow");
        if (!res.ok) res = await fetch("/kit/workflows/" + encodeURIComponent(info.default_workflow));
        if (!res.ok) res = await fetch("/default_workflow");
        if (!res.ok) res = await fetch("/api/userdata/workflows/" + encodeURIComponent(info.default_workflow));
        if (res.ok) {
          const wf = await res.json();
          await app.loadGraphData(wf, true, true, info.default_workflow);
          sessionStorage.setItem("kit.loaded_wf", info.default_workflow);
          if (info.default_view === "app") {
            setTimeout(async () => {
              try {
                const isLinear = !!document.querySelector('[data-testid="linear-mode"], .linear-mode, [class*="linear"]');
                if (!isLinear) {
                  await app.extensionManager.command.execute("Comfy.ToggleLinear");
                }
              } catch (e) {}
            }, 300);
          }
        }
      } catch (e) {
        console.warn("[kit.init]", e);
      }
    }, 600);
  }
});
'''


def install_custom_nodes():
    base = f"{COMFY}/custom_nodes"
    os.makedirs(f"{base}/kit_heartbeat/web", exist_ok=True)
    open(f"{base}/kit_heartbeat/__init__.py", "w").write(HEARTBEAT_NODE)
    open(f"{base}/kit_heartbeat/web/kit.js", "w").write(KIT_JS)
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
    subprocess.Popen(["bash", "-lc", f"nohup cloudflared tunnel --protocol http2 --no-autoupdate run --token-file /content/.cf_token "
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


def _is_comfy_busy() -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/prompt", timeout=3) as r:
            data = json.loads(r.read())
            exec_info = data.get("exec_info", {})
            if exec_info.get("queue_remaining", 0) > 0:
                return True
    except Exception:
        pass
    return False


def spawn_watchdog():
    """Run the gate-loss watchdog in its own process.

    The watchdog has to outlive setup.py: setup.py returns as soon as ComfyUI is ready so the
    Jupyter kernel stays free for follow-up execs (the gate's heartbeat and the on-demand fetch
    both need the kernel). A separate process keeps the cost guard alive.
    """
    env = {**os.environ, "KIT_WATCHDOG_ONLY": "1", "KIT_T0": str(T0)}
    try:
        subprocess.Popen([sys.executable, os.path.abspath(__file__)], env=env,
                         stdout=open(f"{LOG}/watchdog.log", "w"), stderr=subprocess.STDOUT,
                         start_new_session=True)
    except Exception as e:  # noqa: BLE001
        print(f"[watchdog] spawn failed: {e!r}", flush=True)


def watchdog():
    lost = PLAN.get("watchdog_gate_lost_min", 20) * 60
    max_s = PLAN.get("max_session_hours", 7) * 3600
    t0 = float(os.environ.get("KIT_T0", T0))
    open(HB_FILE, "w").write(str(time.time()))
    consecutive_overdue = 0
    while True:
        time.sleep(60)
        # Revive cloudflared if process died
        if os.path.exists("/content/.cf_token") and os.system("pgrep -f 'cloudflared tunnel' > /dev/null") != 0:
            print("[watchdog] cloudflared died; restarting tunnel", flush=True)
            start_tunnel()

        if time.time() - t0 > max_s:
            print("[watchdog] session over limit; releasing VM", flush=True)
            break

        try:
            last = float(open(HB_FILE).read())
        except Exception:
            last = time.time()

        # Gate heartbeat check with two-phase verification (2 次检测)
        if time.time() - last > lost:
            # Check 1: If ComfyUI is generating images, do not release
            if _is_comfy_busy():
                print("[watchdog] gate heartbeat overdue but ComfyUI is generating; extending lease", flush=True)
                open(HB_FILE, "w").write(str(time.time()))
                consecutive_overdue = 0
                continue

            consecutive_overdue += 1
            print(f"[watchdog] gate heartbeat overdue check ({consecutive_overdue}/2)", flush=True)
            if consecutive_overdue < 2:
                continue

            print("[watchdog] gate lost confirmed after 2 consecutive checks; releasing VM", flush=True)
            break
        else:
            consecutive_overdue = 0

    subprocess.run(["bash", "-lc", "pkill -f cloudflared; pkill -f 'main.py --listen'"])
    try:
        from google.colab import runtime
        runtime.unassign()
    except Exception as e:
        open(f"{LOG}/watchdog.err", "w").write(repr(e))


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
    os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ["KIT_DL_CONNS"] = str(PLAN.get("download_connections", 8))
    if PLAN.get("xet_high_performance", True):
        os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"  # only bites on the huggingface_hub fallback path
    ids = [w["id"] for w in PLAN["workflows"]]
    try:
        store = setup_model_store(PLAN)
        if store and not os.path.islink(f"{COMFY}/models"):
            point_models_to(store)
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
    ts("main_start")

    # Start model downloads immediately at T0 into RAM tmpfs to saturate the VM's ~7 Gbps network bandwidth.
    # Model downloads run concurrently with cloudflared installation, ComfyUI git clone, and pip dependencies.
    os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ["KIT_DL_CONNS"] = str(PLAN.get("download_connections", 8))
    if PLAN.get("xet_high_performance", True):
        os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"  # only bites on the huggingface_hub fallback path
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"  # tqdm bars flood the wake-page log

    store = setup_model_store(PLAN)
    if store:
        point_models_to(store)
    dl = Downloads(PLAN["models"], PLAN.get("download_concurrency", 8))
    dl.start()  # downloads run in parallel from T0 with everything below
    ts("downloads_started")

    with Stage("bootstrap"):
        # uv is only needed by the deps stage -> install it in the background while cloudflared
        # and the ComfyUI clone run in parallel.
        pip_uv = subprocess.Popen([sys.executable, "-m", "pip", "install", "-q", "-U", "uv"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        procs = []
        if not shutil.which("cloudflared"):
            procs.append(subprocess.Popen(
                "wget -q -O /tmp/cf.deb https://github.com/cloudflare/cloudflared/releases/latest/download/"
                "cloudflared-linux-amd64.deb && dpkg -i /tmp/cf.deb >/dev/null", shell=True))
        if not os.path.isdir(COMFY):
            procs.append(subprocess.Popen(
                f"git clone -q {cfg['repo']} {COMFY} && cd {COMFY} && git checkout -q {cfg['commit']}", shell=True))
        for p in procs:
            if p.wait() != 0:
                raise RuntimeError("bootstrap step failed")
        dl.sync_links()
        start_tunnel()  # cloudflared retries until :8188 answers, so start it early
        sh("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader", check=False)
        pip_uv.wait()  # uv must be on PATH before the deps stage uses it

    with Stage("deps"):
        frontend = cfg.get("frontend_package")
        sh(f"cd {COMFY} && grep -viE '^(torch|torchvision|torchaudio)([<>=~ ]|$)' requirements.txt > /tmp/req.txt && "
           + (f"(uv pip install --system -q -r /tmp/req.txt {repr(frontend) if frontend else ''} || "
              if PLAN.get("pip_installer", "uv") == "uv" else "(")
           + f"pip install -q -r /tmp/req.txt {repr(frontend) if frontend else ''})")
        install_custom_nodes()
        dl.sync_links()

    with Stage("install_workflows"):
        install_workflows()
        apply_settings()
        dl.sync_links()
    # ComfyUI is started BEFORE the last models land, so its ~30 s of startup (torch import,
    # custom nodes) overlaps the tail of the download instead of sitting on the critical path.
    # Safe because ComfyUI's filename cache is invalidated by folder mtime (folder_paths.py
    # cached_filename_list_), so the model list refreshes as soon as the files appear.
    for d in ("checkpoints", "clip", "clip_vision", "configs", "controlnet", "diffusers",
              "diffusion_models", "embeddings", "gligen", "hypernetworks", "loras", "model_patches",
              "scene_models", "style_models", "unet", "upscale_models", "vae", "vocoders"):
        os.makedirs(f"{COMFY}/models/{d}", exist_ok=True)
    ts("comfy_starting")
    # the boot runs in parallel with the remaining downloads -> tell the gate so the wake page
    # can show it as a concurrent step instead of a serial one
    print("KIT_PARALLEL booting start", flush=True)
    t_comfy = time.time()
    cenv = {**os.environ, **{k: str(v) for k, v in (cfg.get("env") or {}).items()}}
    cenv.pop("CF_TUNNEL_TOKEN", None)
    cenv.pop("HF_TOKEN", None)
    subprocess.Popen(comfy_cmd(), cwd=COMFY, env=cenv, stdout=open(f"{LOG}/comfyui.log", "w"),
                     stderr=subprocess.STDOUT, start_new_session=True)
    ts("download_wait")
    with Stage("download"):
        core = [m["id"] for m in PLAN["models"] if m.get("core")]
        dl.wait(core if PLAN.get("fast_start", True) else [m["id"] for m in PLAN["models"]])
        dl.sync_links()
    ts("comfy_check")
    with Stage("comfy_boot"):
        for _ in range(300):
            if up():
                break
            time.sleep(1)
        else:
            sh(f"tail -40 {LOG}/comfyui.log", check=False)
            raise RuntimeError("ComfyUI did not start")
        sh(f"grep -iE 'IMPORT FAILED|Traceback' {LOG}/comfyui.log | tail -10", check=False, quiet=True)
    # report the real boot time (it ran in parallel with the download, not after it)
    TIMES["comfy_boot"] = round(time.time() - t_comfy, 1)
    print(f"KIT_PARALLEL booting done {TIMES['comfy_boot']}", flush=True)
    if not any(t.name == "kit_watchdog" for t in threading.enumerate()):
        threading.Thread(target=watchdog, name="kit_watchdog", daemon=True).start()
    if PLAN.get("smoke"):
        with Stage("smoke"):
            smoke()
    ts("ready")
    spawn_watchdog()
    print("KIT_READY " + json.dumps({"total_s": round(time.time() - T0), "stages": TIMES,
                                     "background_downloads": len(PLAN["models"]) - dl.files_done,
                                     "keepalive": "gate-heartbeat"}), flush=True)
    # Return instead of looping forever: a cell that never finishes blocks the Jupyter kernel, and
    # every later `colab exec` (gate heartbeat, on-demand fetch) would queue behind it. The runtime
    # stays alive because the gate touches the kernel every ~90 s (Colab reclaims idle runtimes
    # after ~12 min), and the cost guard keeps running in its own process.


if __name__ == "__main__":
    if os.environ.get("KIT_WATCHDOG_ONLY") == "1":
        watchdog()
    else:
        try:
            main()
        except KitAbort:
            pass
