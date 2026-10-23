"""Pure auto-stop / wake-admission rules. No I/O: the app feeds in a Snapshot
and acts on the returned Decision, which keeps every rule unit-testable."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import AutoStop, in_quiet_hours
from .state import State


@dataclass(frozen=True)
class Snapshot:
    now: float
    state: State
    state_since: float
    session_started: float | None
    last_activity: float  # last HTTP request or ws client message
    queue_busy: bool  # ComfyUI queue non-empty
    open_clients: int  # connected ws clients (tabs)
    clients_zero_since: float | None
    extends_used: int
    cu_today: float
    cu_month: float
    cu_balance: float | None


@dataclass(frozen=True)
class Decision:
    stop: bool
    reason: str = ""
    warn: str = ""
    remaining_s: float | None = None


def session_limit_s(cfg: AutoStop, extends_used: int) -> float:
    return (cfg.max_session_hours + extends_used * cfg.extend_hours) * 3600


def can_extend(cfg: AutoStop, extends_used: int) -> bool:
    return extends_used < cfg.extend_max_times


def admit_wake(cfg: AutoStop, now: float, cu_today: float, cu_month: float, cu_balance: float | None) -> tuple[bool, str]:
    t = datetime.fromtimestamp(now).time()
    if in_quiet_hours(cfg.quiet_hours, t):
        return False, "quiet_hours"
    if cfg.daily_cu_budget and cu_today >= cfg.daily_cu_budget:
        return False, "daily_budget"
    if cfg.monthly_cu_budget and cu_month >= cfg.monthly_cu_budget:
        return False, "monthly_budget"
    if cu_balance is not None and cu_balance < cfg.min_cu_balance:
        return False, "low_balance"
    return True, ""


def evaluate(cfg: AutoStop, s: Snapshot) -> Decision:
    if s.state in (State.SLEEPING, State.STOPPING):
        return Decision(False)
    if s.state == State.FAILED:
        return Decision(True, "failed")
    boot_like = s.state in (State.ALLOCATING, State.INSTALLING, State.DOWNLOADING, State.BOOTING, State.SMOKE_TESTING)
    if boot_like and s.now - (s.session_started or s.state_since) > cfg.boot_timeout_min * 60:
        return Decision(True, "boot_timeout")
    t = datetime.fromtimestamp(s.now).time()
    if in_quiet_hours(cfg.quiet_hours, t):
        return Decision(True, "quiet_hours")
    if cfg.daily_cu_budget and s.cu_today >= cfg.daily_cu_budget:
        return Decision(True, "daily_budget")
    if cfg.monthly_cu_budget and s.cu_month >= cfg.monthly_cu_budget:
        return Decision(True, "monthly_budget")
    if s.cu_balance is not None and s.cu_balance < cfg.min_cu_balance:
        return Decision(True, "low_balance")
    remaining = None
    if s.session_started is not None:
        remaining = session_limit_s(cfg, s.extends_used) - (s.now - s.session_started)
        if remaining <= 0:
            return Decision(True, "max_session", remaining_s=0)
    if s.state != State.READY:
        return Decision(False, remaining_s=remaining)
    if not s.queue_busy:
        if cfg.idle_timeout_min and s.now - s.last_activity >= cfg.idle_timeout_min * 60:
            return Decision(True, "idle", remaining_s=remaining)
        if (cfg.stop_on_page_close_min and s.open_clients == 0 and s.clients_zero_since is not None
                and s.now - s.clients_zero_since >= cfg.stop_on_page_close_min * 60):
            return Decision(True, "page_closed", remaining_s=remaining)
    warn = ""
    if remaining is not None and remaining <= cfg.warn_before_min * 60:
        warn = "session_ending"
    return Decision(False, warn=warn, remaining_s=remaining)
