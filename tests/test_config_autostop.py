from datetime import datetime, time as dtime

import pytest

from gate import stopper
from gate.config import AutoStop, ConfigError, Settings, in_quiet_hours, parse_quiet_hours
from gate.state import State

BASE = {"CF_ACCESS_TEAM_DOMAIN": "t.cloudflareaccess.com", "CF_ACCESS_AUD": "x"}


def test_defaults():
    s = Settings.from_env(BASE)
    a = s.auto_stop
    assert (a.idle_timeout_min, a.max_session_hours, a.extend_max_times, a.boot_timeout_min) == (30, 6.0, 1, 40)
    assert (a.daily_cu_budget, a.monthly_cu_budget, a.min_cu_balance) == (12.0, 150.0, 5.0)
    assert a.wake_on_visit is False and a.quiet_hours is None
    assert s.owner_only_actions == ("switch", "stop", "extend")
    assert s.default_view == "app"


def test_env_overrides_and_hosts():
    s = Settings.from_env({**BASE, "IDLE_TIMEOUT_MIN": "15", "MAX_SESSION_HOURS": "2.5", "QUIET_HOURS": "23:30-07:00",
                           "WAKE_ON_VISIT": "true", "DAILY_CU_BUDGET": "4", "PUBLIC_HOST": "comfy.example.com",
                           "COLAB_TUNNEL_HOST": "comfy-colab.example.com", "LISTEN_ADDR": "127.0.0.1:9000"})
    assert s.auto_stop.idle_timeout_min == 15 and s.auto_stop.max_session_hours == 2.5
    assert s.auto_stop.quiet_hours == (dtime(23, 30), dtime(7, 0))
    assert s.auto_stop.wake_on_visit and s.auto_stop.daily_cu_budget == 4
    assert s.backend_url == "https://comfy-colab.example.com"
    assert (s.listen_host, s.listen_port) == ("127.0.0.1", 9000)


@pytest.mark.parametrize("env", [
    {"IDLE_TIMEOUT_MIN": "abc"}, {"IDLE_TIMEOUT_MIN": "-1"}, {"QUIET_HOURS": "1-2"},
    {"OWNER_ONLY_ACTIONS": "stop,delete"}, {"DEFAULT_VIEW": "canvas"}, {"WAKE_ON_VISIT": "maybe"},
    {"LISTEN_ADDR": "8080"}, {"CF_ACCESS_AUD": ""},
])
def test_invalid_env(env):
    with pytest.raises(ConfigError):
        Settings.from_env({**BASE, **env})


def test_auth_disabled_allows_missing_access():
    assert Settings.from_env({"AUTH_DISABLED": "true"}).auth_disabled


def test_quiet_hours_wraps_midnight():
    w = parse_quiet_hours("23:00-07:00")
    assert in_quiet_hours(w, dtime(23, 30)) and in_quiet_hours(w, dtime(6, 59))
    assert not in_quiet_hours(w, dtime(7, 0)) and not in_quiet_hours(w, dtime(12, 0))
    assert not in_quiet_hours(None, dtime(1, 0))


NOON = datetime(2026, 10, 23, 12, 0).timestamp()


def snap(**kw):
    d = dict(now=NOON, state=State.READY, state_since=NOON - 600, session_started=NOON - 600,
             last_activity=NOON - 60, queue_busy=False, open_clients=1, clients_zero_since=None,
             extends_used=0, cu_today=1.0, cu_month=10.0, cu_balance=None)
    d.update(kw)
    return stopper.Snapshot(**d)


CFG = AutoStop(idle_timeout_min=30, max_session_hours=6, extend_max_times=1, extend_hours=1,
               boot_timeout_min=40, stop_on_page_close_min=10, daily_cu_budget=12, monthly_cu_budget=150,
               min_cu_balance=5)


def test_running_ok():
    d = stopper.evaluate(CFG, snap())
    assert not d.stop and d.remaining_s == pytest.approx(6 * 3600 - 600)


def test_idle_stop_unless_queue_busy():
    assert stopper.evaluate(CFG, snap(last_activity=NOON - 31 * 60)).reason == "idle"
    assert not stopper.evaluate(CFG, snap(last_activity=NOON - 31 * 60, queue_busy=True)).stop


def test_max_session_and_extend():
    started = NOON - 6 * 3600 - 1
    assert stopper.evaluate(CFG, snap(session_started=started)).reason == "max_session"
    assert not stopper.evaluate(CFG, snap(session_started=started, extends_used=1)).stop
    assert stopper.can_extend(CFG, 0) and not stopper.can_extend(CFG, 1)


def test_warn_before_end():
    d = stopper.evaluate(CFG, snap(session_started=NOON - 6 * 3600 + 300))
    assert d.warn == "session_ending" and not d.stop


def test_boot_timeout():
    s = snap(state=State.DOWNLOADING, session_started=NOON - 41 * 60, last_activity=NOON - 41 * 60)
    assert stopper.evaluate(CFG, s).reason == "boot_timeout"
    s = snap(state=State.DOWNLOADING, session_started=NOON - 39 * 60, last_activity=NOON - 39 * 60)
    assert not stopper.evaluate(CFG, s).stop  # idle rules do not apply while booting


def test_page_close():
    s = snap(open_clients=0, clients_zero_since=NOON - 11 * 60)
    assert stopper.evaluate(CFG, s).reason == "page_closed"
    # Grace period: just entered READY (< 3 min) -> should not stop
    assert not stopper.evaluate(CFG, snap(open_clients=0, clients_zero_since=NOON - 11 * 60, state_since=NOON - 60)).stop
    # HTTP activity within 60s -> should not stop
    assert not stopper.evaluate(CFG, snap(open_clients=0, clients_zero_since=NOON - 11 * 60, last_activity=NOON - 10)).stop
    off = AutoStop(stop_on_page_close_min=0)
    assert not stopper.evaluate(off, s).stop


def test_budgets_and_balance():
    assert stopper.evaluate(CFG, snap(cu_today=12)).reason == "daily_budget"
    assert stopper.evaluate(CFG, snap(cu_month=150)).reason == "monthly_budget"
    assert stopper.evaluate(CFG, snap(cu_balance=4.9)).reason == "low_balance"


def test_quiet_hours_stop_and_admission():
    cfg = AutoStop(quiet_hours=parse_quiet_hours("11:00-13:00"))
    assert stopper.evaluate(cfg, snap()).reason == "quiet_hours"
    assert stopper.admit_wake(cfg, NOON, 0, 0, None) == (False, "quiet_hours")
    assert stopper.admit_wake(CFG, NOON, 0, 0, None) == (True, "")
    assert stopper.admit_wake(CFG, NOON, 12, 0, None)[1] == "daily_budget"
    assert stopper.admit_wake(CFG, NOON, 0, 0, 1.0)[1] == "low_balance"


def test_failed_and_sleeping():
    assert stopper.evaluate(CFG, snap(state=State.FAILED)).reason == "failed"
    assert not stopper.evaluate(CFG, snap(state=State.SLEEPING)).stop
