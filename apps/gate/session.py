"""One global, shared session.

* Exactly one Colab VM per gate. `wake()` is idempotent: whoever calls it while a
  session is booting or ready simply joins it (no second `colab new`).
* All state changes and setup log lines are broadcast to every connected page (SSE).
* State is persisted to STATE_DIR/session.json so a gate restart re-attaches
  instead of launching a duplicate VM.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import tarfile
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import stopper
from .budget import Ledger
from .colab import ColabError, ColabRunner
from .config import Settings, read_secret_file
from .plan import build_fetch_plan, build_plan
from .profiles import Profile
from .state import BOOT_STATES, Event, Machine, State

log = logging.getLogger("gate.session")

_SECRET_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{20,}", re.IGNORECASE),
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"ey[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{6,}"),
    re.compile(r"(?:CF_TUNNEL_TOKEN|HF_TOKEN)['\":= ]+([A-Za-z0-9_\-\.]{20,})"),
]


def redact_secrets(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        text = pat.sub("[REDACTED_SECRET]", text)
    return text


def _find_repo_root() -> Path:
    # 1. Check current working directory
    cwd = Path.cwd().resolve()
    for p in [cwd] + list(cwd.parents):
        if (p / "pyproject.toml").is_file():
            return p
    # 2. Check /app (standard Docker container location)
    if Path("/app/pyproject.toml").is_file():
        return Path("/app")
    # 3. Check source file ancestry (editable installs or local checkout)
    cur = Path(__file__).resolve()
    for p in [cur] + list(cur.parents):
        if (p / "pyproject.toml").is_file():
            return p
    return cur.parent.parent


REPO_ROOT = _find_repo_root()


@dataclass
class WakeResult:
    joined: bool
    state: str
    profile: str | None
    refused: str = ""


class Broadcaster:
    def __init__(self, backlog: int = 1000, on_log: Callable[[str], None] | None = None):
        self.subscribers: set[asyncio.Queue] = set()
        self.logs: deque[str] = deque(maxlen=backlog)
        self.total_logs: int = 0
        self.on_log = on_log

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def clear_logs(self) -> None:
        self.logs.clear()
        self.total_logs = 0

    def logs_since(self, since: int) -> list[str]:
        if since <= 0:
            return list(self.logs)
        oldest_index = max(0, self.total_logs - len(self.logs))
        if since <= oldest_index:
            return list(self.logs)
        offset = since - oldest_index
        logs_list = list(self.logs)
        return logs_list[offset:] if offset < len(logs_list) else []

    def publish(self, kind: str, data: Any) -> None:
        if kind == "log":
            s = str(data)
            self.logs.append(s)
            self.total_logs += 1
            if self.on_log:
                try:
                    self.on_log(s)
                except Exception:
                    pass
        for q in list(self.subscribers):
            try:
                q.put_nowait((kind, data))
            except asyncio.QueueFull:
                pass  # slow client; it will resync from /_gate/status


class Session:
    def __init__(self, settings: Settings, profiles: dict[str, Profile], colab: ColabRunner,
                 clock: Callable[[], float] = time.time):
        self.settings, self.profiles, self.colab, self.clock = settings, profiles, colab, clock
        self.machine = Machine(clock=clock)
        self.lock = asyncio.Lock()
        self.state_file = Path(settings.state_dir) / "session.json"
        self.log_file = Path(settings.state_dir) / "session.log"
        self.bus = Broadcaster(backlog=1000, on_log=self._append_log)
        self.ledger = Ledger.load(Path(settings.state_dir) / "cu_ledger.json")
        self.last_activity = clock()
        self.client_conns: dict[str, int] = {}
        self.open_clients = 0
        self.clients_zero_since: float | None = None
        self.queue_busy = False
        self.progress: dict[str, Any] = {}
        self.stage_durations: dict[str, dict[str, Any]] = {}
        self.workflows: list[str] | None = None
        self._task: asyncio.Task | None = None
        self._fetch_task: asyncio.Task | None = None
        self.on_demand: list[str] = []      # installed workflows whose models are not on the VM yet
        self.fetching: list[str] = []
        self.fetch_error = ""
        self._last_accrual = clock()
        self.machine.listeners.append(self._on_state_event)

    def _on_state_event(self, ev: Event) -> None:
        now = self.clock()
        st = ev.state.value
        for k, v in self.stage_durations.items():
            if v.get("duration") is None:
                v["duration"] = round(max(0.1, now - v.get("start", now)), 1)
        if st in ("allocating", "installing", "downloading", "booting", "smoke_testing"):
            self.stage_durations[st] = {"start": now, "duration": None}
        elif ev.state == State.READY:
            self.clients_zero_since = now if self.open_clients == 0 else None
            self.touch()
        self.persist()
        self.bus.publish("state", self.status())

    def _append_log(self, line: str) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(redact_secrets(line) + "\n")
        except Exception:
            pass

    def _archive_logs(self) -> None:
        history_dir = Path(self.settings.state_dir) / "history"
        try:
            history_dir.mkdir(parents=True, exist_ok=True)
            if self.log_file.is_file() and self.log_file.stat().st_size > 0:
                ts = int(self.clock())
                dest = history_dir / f"session-{ts}.log"
                shutil.copy2(self.log_file, dest)
            cutoff = self.clock() - 3 * 86400
            for p in history_dir.glob("session-*.log"):
                try:
                    if p.stat().st_mtime < cutoff:
                        p.unlink(missing_ok=True)
                except Exception:
                    pass
        except Exception as e:
            log.warning("log archive/cleanup failed: %s", e)

    # ── persistence ───────────────────────────────────────────────────────
    def persist(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({**self.machine.snapshot(), "workflows": self.workflows,
                                   "last_activity": self.last_activity,
                                   "progress": self.progress,
                                   "stage_durations": self.stage_durations}))
        tmp.replace(self.state_file)

    def restore(self) -> None:
        if self.state_file.is_file():
            try:
                d = json.loads(self.state_file.read_text())
            except Exception as e:
                log.warning("failed to parse state file %s: %s", self.state_file, e)
                d = {}
            try:
                st = State(d.get("state", "sleeping"))
            except ValueError:
                st = State.SLEEPING
            m = self.machine
            m.state, m.profile, m.reason = st, d.get("profile"), d.get("reason", "")
            m.since, m.session_started = d.get("since", self.clock()), d.get("session_started")
            m.extends_used = int(d.get("extends_used", 0))
            self.workflows = d.get("workflows")
            self.last_activity = d.get("last_activity", self.clock())
            self.progress = d.get("progress") or {}
            self.stage_durations = d.get("stage_durations") or {}
            if st == State.STOPPING:
                m.state = State.FAILED  # stop was interrupted; tick() will retry it
                m.reason = "stop interrupted by gate restart"
        if self.log_file.is_file():
            try:
                lines = self.log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                self.bus.clear_logs()
                self.bus.total_logs = len(lines)
                maxlen = self.bus.logs.maxlen or 1000
                for line in lines[-maxlen:]:
                    self.bus.logs.append(line)
            except Exception as e:
                log.warning("failed to restore logs from %s: %s", self.log_file, e)

    async def reconcile(self) -> None:
        """After restart: if we think a VM exists, check Colab still has it."""
        if not self.machine.is_billing:
            return
        try:
            names = await self.colab.sessions()
        except ColabError as e:
            log.warning("reconcile failed: %s", e)
            return
        if self.settings.colab_session_name not in names:
            async with self.lock:
                self._force(State.SLEEPING, "VM gone (released outside the gate)")
        elif self.machine.is_booting:
            # setup output stream is lost on restart; the VM keeps booting, readiness is probed via the tunnel
            self.bus.publish("log", "gate restarted during boot; re-attaching and probing backend")

    def _force(self, st: State, reason: str) -> None:
        m = self.machine
        m.state, m.reason, m.since = st, reason, self.clock()
        if st == State.SLEEPING:
            m.session_started = None
        self.persist()
        self.bus.publish("state", self.status())

    # ── public API ────────────────────────────────────────────────────────
    def status(self) -> dict:
        m = self.machine
        snap = self._snapshot()
        d = stopper.evaluate(self.settings.auto_stop, snap)
        now = self.clock()
        durs = {}
        for k, v in self.stage_durations.items():
            dur = v.get("duration")
            start = v.get("start", now)
            durs[k] = {
                "start": start,
                "duration": dur,
                "elapsed": dur if dur is not None else round(max(0.1, now - start), 1),
            }
        return {
            **m.snapshot(),
            "server_time": now,
            "busy": m.state not in (State.SLEEPING, State.READY, State.FAILED),
            "progress": self.progress,
            "stage_durations": durs,
            "enable_smoke_test": self.settings.enable_smoke_test,
            "remaining_s": d.remaining_s,
            "warn": d.warn,
            "can_extend": stopper.can_extend(self.settings.auto_stop, m.extends_used),
            "cu": {"today": self.ledger.today(), "month": self.ledger.month(), "balance": self.ledger.balance,
                   "per_hour": self.settings.auto_stop.cu_per_hour},
            "backend_url": self.settings.backend_url,
            "clients": self.open_clients,
            "default_view": self.settings.default_view,
            "models": {"on_demand": list(self.on_demand), "fetching": list(self.fetching),
                       "fetch_error": self.fetch_error},
        }

    def reload_profiles(self) -> dict[str, str]:
        """Re-read profiles from disk (bind-mounted, read-only) so edits apply on the next wake without a
        restart. Updates the shared dict in place; a profile that fails to load keeps its last good version."""
        from .profiles import load_all
        fresh, errors = load_all(self.settings.profiles, REPO_ROOT)
        for ref, err in errors.items():
            log.warning("profile reload failed for %s: %s (keeping last good)", ref, err)
        # new/changed profiles apply now; removing one from PROFILES needs a restart
        self.profiles.update(fresh)
        return errors

    async def wake(self, profile_id: str | None, by: str = "", workflows: list[str] | None = None) -> WakeResult:
        async with self.lock:
            if self.machine.state in (State.SLEEPING, State.FAILED):
                self.reload_profiles()
            m = self.machine
            if m.state not in (State.SLEEPING, State.FAILED):
                # someone already started it: join the in-flight / running session
                return WakeResult(True, m.state.value, m.profile)
            pid = profile_id or self.settings.default_profile
            if pid not in self.profiles:
                raise KeyError(pid)
            ok, why = stopper.admit_wake(self.settings.auto_stop, self.clock(), self.ledger.today(),
                                         self.ledger.month(), self.ledger.balance)
            if not ok:
                return WakeResult(False, m.state.value, m.profile, refused=why)
            if m.state == State.FAILED:
                await self._stop_locked("restart after failure")
            self._archive_logs()
            self.bus.clear_logs()
            self.log_file.unlink(missing_ok=True)
            self.stage_durations = {"allocating": {"start": self.clock(), "duration": None}}
            m.profile, self.workflows, self.progress = pid, workflows, {}
            m.to(State.ALLOCATING, f"wake by {by or 'unknown'}")
            self.last_activity = self.clock()
            self.persist()
            self._task = asyncio.create_task(self._boot(pid))
            return WakeResult(False, m.state.value, pid)

    async def stop(self, reason: str) -> None:
        async with self.lock:
            await self._stop_locked(reason)

    async def extend(self) -> bool:
        async with self.lock:
            if not stopper.can_extend(self.settings.auto_stop, self.machine.extends_used):
                return False
            self.machine.extends_used += 1
            self.persist()
            self.bus.publish("state", self.status())
            return True

    async def switch(self, profile_id: str, by: str = "") -> WakeResult:
        """v1: cold switch (stop, then wake with the new profile)."""
        self.reload_profiles()
        if profile_id not in self.profiles:
            raise KeyError(profile_id)
        async with self.lock:
            if self.machine.state == State.READY:
                self.machine.to(State.SWITCHING, f"switch to {profile_id} by {by}")
                await self._stop_locked(f"switch to {profile_id}")
        return await self.wake(profile_id, by=by)

    async def fetch(self, workflow_ids: list[str]) -> str:
        """Download the models of extra workflows onto the running VM (on demand). Returns '' or a refusal."""
        m = self.machine
        if m.state != State.READY or not m.profile:
            return "not_ready"
        if self._fetch_task and not self._fetch_task.done():
            return "fetch_in_progress"
        try:
            plan = build_fetch_plan(self.profiles[m.profile], self.settings, list(workflow_ids))
        except ValueError:
            return "unknown_workflow"
        self.fetching, self.fetch_error = list(workflow_ids), ""
        self.bus.publish("state", self.status())
        self._fetch_task = asyncio.create_task(self._fetch(plan))
        return ""

    async def _fetch(self, plan: dict) -> None:
        name = self.settings.colab_session_name
        fd = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        try:
            json.dump(plan, fd)
            fd.close()
            await self.colab.upload(name, fd.name, "/content/kit/fetch-plan.json")
            code = ("import os, runpy\nos.environ['KIT_PLAN']='/content/kit/fetch-plan.json'\n"
                    "_kit = runpy.run_path('/content/kit/colab/setup.py', run_name='__main__'); del _kit\n")
            async for line in self.colab.exec_stream(name, code, self.settings.auto_stop.boot_timeout_min * 60):
                self.handle_line(line)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self.fetch_error = str(e)[:300]
        finally:
            Path(fd.name).unlink(missing_ok=True)
            self.fetching = []
            self.bus.publish("state", self.status())

    def touch(self) -> None:
        self.last_activity = self.clock()

    def client_opened(self, cid: str = "") -> None:
        key = (cid or "").strip() or "default"
        self.client_conns[key] = self.client_conns.get(key, 0) + 1
        self.open_clients = len(self.client_conns)
        self.clients_zero_since = None
        self.bus.publish("state", self.status())

    def client_closed(self, cid: str = "") -> None:
        key = (cid or "").strip() or "default"
        if key in self.client_conns:
            self.client_conns[key] -= 1
            if self.client_conns[key] <= 0:
                self.client_conns.pop(key, None)
        self.open_clients = len(self.client_conns)
        if self.open_clients == 0:
            self.clients_zero_since = self.clock()
        self.bus.publish("state", self.status())

    # ── internals ─────────────────────────────────────────────────────────
    async def _stop_locked(self, reason: str) -> None:
        m = self.machine
        if m.state == State.SLEEPING:
            return
        if self._task and not self._task.done() and self._task is not asyncio.current_task():
            self._task.cancel()
        self._accrue()  # bill up to now while still in a billing state
        if m.state != State.STOPPING:
            if not m.can(State.STOPPING):
                m.to(State.FAILED, reason)
            m.to(State.STOPPING, reason)
        self.persist()
        try:
            await self.colab.stop(self.settings.colab_session_name)
        except ColabError as e:
            msg = str(e).lower()
            if any(k in msg for k in ("not found", "no such", "404", "unknown", "already")):
                log.info("colab stop %s: instance already gone (%s)", self.settings.colab_session_name, e)
            else:
                self.bus.publish("log", f"stop error: {e}")
                m.to(State.FAILED, f"stop failed: {e}")
                self.persist()
                return
        m.to(State.SLEEPING, reason)
        self.progress = {}
        self.on_demand, self.fetching = [], []
        if self._fetch_task and not self._fetch_task.done():
            self._fetch_task.cancel()
        self.persist()
        self.ledger.save()

    def _accrue(self) -> None:
        now = self.clock()
        if self.machine.is_billing:
            self.ledger.add((now - self._last_accrual) / 3600 * self.settings.auto_stop.cu_per_hour, now)
        self._last_accrual = now

    def _snapshot(self) -> stopper.Snapshot:
        m = self.machine
        return stopper.Snapshot(
            now=self.clock(), state=m.state, state_since=m.since, session_started=m.session_started,
            last_activity=self.last_activity, queue_busy=self.queue_busy, open_clients=self.open_clients,
            clients_zero_since=self.clients_zero_since, extends_used=m.extends_used,
            cu_today=self.ledger.today(), cu_month=self.ledger.month(), cu_balance=self.ledger.balance)

    async def tick(self) -> stopper.Decision:
        self._accrue()
        self.ledger.save()
        d = stopper.evaluate(self.settings.auto_stop, self._snapshot())
        if d.stop:
            self.bus.publish("log", f"auto-stop: {d.reason}")
            await self.stop(f"auto:{d.reason}")
        elif d.warn:
            self.bus.publish("warn", {"warn": d.warn, "remaining_s": d.remaining_s})
        return d

    def _kit_tarball(self, plan: dict) -> str:
        fd = tempfile.NamedTemporaryFile(suffix=".tgz", delete=False)
        colab_dir = REPO_ROOT / "packages/colab" if (REPO_ROOT / "packages/colab").is_dir() else REPO_ROOT / "colab"
        with tarfile.open(fileobj=fd, mode="w:gz") as tar:
            tar.add(colab_dir, arcname="colab",
                    filter=lambda ti: None if "__pycache__" in ti.name else ti)
            data = json.dumps(plan).encode()
            info = tarfile.TarInfo("plan.json")
            info.size = len(data)
            import io
            tar.addfile(info, io.BytesIO(data))
        fd.close()
        return fd.name

    def _bootstrap_code(self) -> str:
        secrets = {}
        tok = read_secret_file(self.settings.cf_tunnel_token_file)
        if tok:
            secrets["CF_TUNNEL_TOKEN"] = tok
        hf = read_secret_file(self.settings.hf_token_file)
        if hf:
            secrets["HF_TOKEN"] = hf
        # secrets travel on stdin only and live in the kernel's memory
        return (
            "import os, json, runpy, subprocess\n"
            "subprocess.run(['bash','-lc','rm -rf /content/kit && mkdir -p /content/kit && "
            "tar -xzf /content/kit.tgz -C /content/kit'], check=True)\n"
            f"os.environ.update(json.loads({json.dumps(json.dumps(secrets))}))\n"
            "os.environ['KIT_PLAN']='/content/kit/plan.json'\n"
            # assign the result: a bare expression would make IPython pretty-print setup.py's globals
            "_kit = runpy.run_path('/content/kit/colab/setup.py', run_name='__main__'); del _kit\n"
        )

    def handle_line(self, line: str) -> None:
        """Parse setup.py protocol lines; everything is also forwarded as a log line."""
        clean = redact_secrets(line)
        self.bus.publish("log", clean)
        parts = clean.split(" ", 3)
        if clean.startswith("KIT_STAGE ") and len(parts) >= 3:
            self.machine.on_stage(parts[1], parts[2])
            self.progress["stage"] = parts[1]
            if parts[2] == "done" and len(parts) >= 4:
                try:
                    dur = float(parts[3])
                    smap = {"bootstrap": "installing", "deps": "installing", "download": "downloading",
                            "comfy_boot": "booting", "smoke": "smoke_testing"}
                    gk = smap.get(parts[1])
                    if gk and gk in self.stage_durations:
                        self.stage_durations[gk]["duration"] = round(dur, 1)
                except ValueError:
                    pass
        elif clean.startswith("KIT_PROGRESS ") and len(parts) >= 3:
            try:
                self.progress[parts[1]] = json.loads(clean.split(" ", 2)[2])
            except ValueError:
                pass
            self.bus.publish("progress", self.progress)
        elif clean.startswith(("KIT_FETCHED", "KIT_FETCH_FAILED")):
            try:
                d = json.loads(clean.split(" ", 1)[1])
            except (IndexError, ValueError):
                d = {}
            if clean.startswith("KIT_FETCHED"):
                self.on_demand = [w for w in self.on_demand if w not in (d.get("workflows") or [])]
            else:
                self.fetch_error = str(d.get("error", "fetch failed"))
            self.bus.publish("state", self.status())
        elif clean.startswith("KIT_READY"):
            if self.machine.state != State.READY and self.machine.can(State.READY):
                self.clients_zero_since = self.clock() if self.open_clients == 0 else None
                self.touch()
                self.machine.to(State.READY, "ComfyUI ready")
        elif clean.startswith("KIT_FAILED"):
            if self.machine.state not in (State.FAILED, State.STOPPING, State.SLEEPING):
                self.machine.to(State.FAILED, clean[len("KIT_FAILED"):].strip() or "setup failed")
        self.persist()

    async def _boot(self, pid: str) -> None:
        name = self.settings.colab_session_name
        try:
            plan = build_plan(self.profiles[pid], self.settings, self.workflows)
            self.on_demand, self.fetching, self.fetch_error = sorted(plan.get("on_demand") or {}), [], ""
            if name not in await self.colab.sessions():
                self.bus.publish("log", f"allocating Colab {self.settings.colab_accelerator} VM …")
                await self.colab.new(name, self.settings.colab_accelerator)
            tgz = self._kit_tarball(plan)
            try:
                await self.colab.upload(name, tgz, "/content/kit.tgz")
            finally:
                Path(tgz).unlink(missing_ok=True)
            self.machine.to(State.INSTALLING, "setup started")
            self.persist()
            timeout = self.settings.auto_stop.boot_timeout_min * 60
            for attempt in range(1, 4):
                try:
                    async for line in self.colab.exec_stream(name, self._bootstrap_code(), timeout):
                        self.handle_line(line)
                    break
                except ColabError as ce:
                    if self.machine.state not in (State.READY, State.FAILED) and attempt < 3 and not self.progress.get("stage"):
                        self.bus.publish("log", f"Colab kernel connection reset; retrying ({attempt}/3) in 3s …")
                        await asyncio.sleep(3)
                        continue
                    raise
            if self.machine.state not in (State.READY, State.FAILED):
                self.machine.to(State.FAILED, "setup ended without KIT_READY")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("boot failed")
            self.bus.publish("log", f"boot error: {e}")
            if self.machine.state not in (State.FAILED, State.STOPPING, State.SLEEPING):
                self.machine.to(State.FAILED, str(e)[:300])
        finally:
            self.persist()

    def boot_task_running(self) -> bool:
        return bool(self._task and not self._task.done())

    def is_boot_state(self) -> bool:
        return self.machine.state in BOOT_STATES
