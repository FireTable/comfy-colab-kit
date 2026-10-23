import asyncio
import json
from pathlib import Path

import pytest

from gate.config import Settings
from gate.profiles import load_profile
from gate.session import Session
from gate.state import State
from tests.fakes import FakeColab

ROOT = Path(__file__).resolve().parent.parent


def make(tmp_path, colab=None, **env):
    s = Settings.from_env({"AUTH_DISABLED": "true", "STATE_DIR": str(tmp_path), "DEFAULT_PROFILE": "tiny", **env})
    prof = {p.id: p for p in (load_profile(ROOT / "tests/fixtures/profiles/tiny"),
                              load_profile(ROOT / "examples/profiles/moss-image"))}
    return Session(s, prof, colab or FakeColab())


async def wait_for(pred, timeout=2.0):
    end = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > end:
            raise TimeoutError
        await asyncio.sleep(0.01)


async def test_concurrent_wake_launches_exactly_one_colab():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        colab = FakeColab(new_delay=0.1)
        s = make(Path(d), colab)
        results = await asyncio.gather(*[s.wake(None, by=f"user{i}@x.com") for i in range(25)])
        assert sum(1 for r in results if not r.joined) == 1
        assert sum(1 for r in results if r.joined) == 24
        await wait_for(lambda: s.machine.state == State.READY)
        assert [c for c in colab.calls if c.startswith("new:")] == ["new:comfy:L4"]
        # waking again while ready just joins
        r = await s.wake("moss-image", by="late@x.com")
        assert r.joined and r.profile == "tiny"
        assert len([c for c in colab.calls if c.startswith("new:")]) == 1


async def test_all_viewers_get_the_same_broadcast(tmp_path):
    gate = asyncio.Event()
    s = make(tmp_path, FakeColab(exec_gate=gate))
    q1, q2 = s.bus.subscribe(), s.bus.subscribe()
    await s.wake(None, by="a@x.com")
    gate.set()
    await wait_for(lambda: s.machine.state == State.READY)
    def drain(q):
        out = []
        while not q.empty():
            out.append(q.get_nowait())
        return out
    e1, e2 = drain(q1), drain(q2)
    assert e1 == e2 and len(e1) > 5
    states = [d["state"] for k, d in e1 if k == "state"]
    assert states[0] == "allocating" and states[-1] == "ready"
    assert any(k == "log" and "KIT_READY" in d for k, d in e1)
    assert s.status()["progress"]["download"]["total_gb"] == 2


async def test_state_persisted_and_restored_no_duplicate_vm(tmp_path):
    gate = asyncio.Event()
    colab = FakeColab(exec_gate=gate)
    s = make(tmp_path, colab)
    await s.wake(None, by="a@x.com")
    await wait_for(lambda: "exec" in colab.calls)
    saved = json.loads((tmp_path / "session.json").read_text())
    assert saved["state"] == "installing" and saved["profile"] == "tiny"
    s._task.cancel()
    # simulate gate restart: new Session object, same state dir, VM still alive
    s2 = make(tmp_path, colab)
    s2.restore()
    await s2.reconcile()
    assert s2.machine.state == State.INSTALLING
    r = await s2.wake(None, by="b@x.com")
    assert r.joined
    assert len([c for c in colab.calls if c.startswith("new:")]) == 1


async def test_restore_when_vm_gone_goes_to_sleep(tmp_path):
    (tmp_path / "session.json").write_text(json.dumps({"state": "ready", "profile": "tiny",
                                                       "session_started": 1, "since": 1}))
    s = make(tmp_path, FakeColab())
    s.restore()
    await s.reconcile()
    assert s.machine.state == State.SLEEPING


async def test_stop_releases_vm_and_accrues_cu(tmp_path):
    t = [1_000_000.0]
    colab = FakeColab()
    s = make(tmp_path, colab)
    s.clock = s.machine.clock = lambda: t[0]
    s._last_accrual = t[0]
    await s.wake(None, by="a@x.com")
    await wait_for(lambda: s.machine.state == State.READY)
    t[0] += 3600
    await s.stop("test")
    assert s.machine.state == State.SLEEPING and "stop" in colab.calls
    assert sum(s.ledger.per_day.values()) == pytest.approx(1.71, rel=1e-3)


async def test_setup_failure_marks_failed_then_retry_restarts(tmp_path):
    colab = FakeColab(lines=["KIT_STAGE download start", "KIT_STAGE download fail 3.0", "KIT_FAILED stage=download"])
    s = make(tmp_path, colab)
    await s.wake(None)
    await wait_for(lambda: s.machine.state == State.FAILED)
    colab.lines = ['KIT_READY {}']
    r = await s.wake(None)
    assert not r.joined
    await wait_for(lambda: s.machine.state == State.READY)
    assert "stop" in colab.calls


async def test_wake_refused_by_budget(tmp_path):
    s = make(tmp_path, FakeColab(), DAILY_CU_BUDGET="1")
    s.ledger.add(2.0)
    r = await s.wake(None)
    assert r.refused == "daily_budget" and s.machine.state == State.SLEEPING


async def test_secrets_go_through_stdin_not_argv(tmp_path):
    tok = tmp_path / "tunnel"
    tok.write_text("tunnel-token-value")
    colab = FakeColab()
    s = make(tmp_path, colab, CF_TUNNEL_TOKEN_FILE=str(tok))
    await s.wake(None)
    await wait_for(lambda: s.machine.state == State.READY)
    assert "tunnel-token-value" in colab.exec_code
    assert not any("tunnel-token-value" in c for c in colab.calls)


async def test_extend_limit(tmp_path):
    s = make(tmp_path, FakeColab())
    assert await s.extend() is True
    assert await s.extend() is False


async def test_logs_and_progress_persisted_and_restored(tmp_path):
    colab = FakeColab(lines=[
        "KIT_STAGE download start",
        'KIT_PROGRESS download {"bytes_done": 500, "bytes_total": 1000}',
        "custom log line from setup",
        "KIT_READY {}",
    ])
    s = make(tmp_path, colab)
    await s.wake(None)
    await wait_for(lambda: s.machine.state == State.READY)

    log_file = tmp_path / "session.log"
    assert log_file.exists()
    content = log_file.read_text()
    assert "custom log line from setup" in content
    assert "KIT_READY" in content

    state_file = tmp_path / "session.json"
    saved = json.loads(state_file.read_text())
    assert saved["progress"]["download"]["bytes_done"] == 500

    s2 = make(tmp_path, colab)
    s2.restore()
    assert s2.progress["download"]["bytes_done"] == 500
    assert any("custom log line from setup" in line for line in s2.bus.logs)
    assert any("KIT_READY" in line for line in s2.bus.logs)
