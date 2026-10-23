"""The 10-state lifecycle of one Colab-backed ComfyUI session.

    sleeping ──wake──▶ allocating ─▶ installing ─▶ downloading ─▶ booting ─▶ smoke_testing ─▶ ready
        ▲                 │             │              │            │             │            │
        │                 └─────────────┴──── fail ────┴────────────┴─────────────┘            │
        │                                       ▼                                              │
        └────────── stopping ◀──── stop ──── failed                     ready ──switch──▶ switching
                       ▲                                                   (hot: ─▶ downloading / booting)
                       └──────────────────── stop (from any non-sleeping state) ───────────────┘
"""
from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Callable


class State(str, enum.Enum):
    SLEEPING = "sleeping"
    ALLOCATING = "allocating"
    INSTALLING = "installing"
    DOWNLOADING = "downloading"
    BOOTING = "booting"
    SMOKE_TESTING = "smoke_testing"
    READY = "ready"
    SWITCHING = "switching"
    STOPPING = "stopping"
    FAILED = "failed"


BOOT_STATES = (State.ALLOCATING, State.INSTALLING, State.DOWNLOADING, State.BOOTING, State.SMOKE_TESTING)
#: states in which a Colab VM may exist and is billing CU
BILLING_STATES = BOOT_STATES + (State.READY, State.SWITCHING, State.FAILED)

TRANSITIONS: dict[State, frozenset[State]] = {
    State.SLEEPING: frozenset({State.ALLOCATING}),
    State.ALLOCATING: frozenset({State.INSTALLING, State.FAILED, State.STOPPING}),
    # READY directly from installing/downloading: setup found ComfyUI already running on a reused VM
    State.INSTALLING: frozenset({State.DOWNLOADING, State.BOOTING, State.READY, State.FAILED, State.STOPPING}),
    State.DOWNLOADING: frozenset({State.BOOTING, State.READY, State.FAILED, State.STOPPING}),
    # FAST_START: ComfyUI may boot before optional downloads finish, so booting can be reached early
    State.BOOTING: frozenset({State.SMOKE_TESTING, State.READY, State.FAILED, State.STOPPING}),
    State.SMOKE_TESTING: frozenset({State.READY, State.FAILED, State.STOPPING}),
    State.READY: frozenset({State.SWITCHING, State.STOPPING, State.FAILED}),
    State.SWITCHING: frozenset({State.DOWNLOADING, State.BOOTING, State.READY, State.FAILED, State.STOPPING}),
    State.STOPPING: frozenset({State.SLEEPING, State.FAILED}),
    State.FAILED: frozenset({State.STOPPING, State.ALLOCATING}),
}

#: setup.py stage name -> gate state (setup prints `KIT_STAGE <name> start|done|fail`)
STAGE_TO_STATE = {
    "bootstrap": State.INSTALLING,
    "deps": State.INSTALLING,
    "download": State.DOWNLOADING,
    "install_workflows": State.DOWNLOADING,
    "comfy_boot": State.BOOTING,
    "smoke": State.SMOKE_TESTING,
}


class InvalidTransition(RuntimeError):
    pass


@dataclass
class Event:
    ts: float
    state: State
    note: str = ""


@dataclass
class Machine:
    state: State = State.SLEEPING
    profile: str | None = None
    reason: str = ""
    since: float = field(default_factory=time.time)
    session_started: float | None = None
    extends_used: int = 0
    history: list[Event] = field(default_factory=list)
    listeners: list[Callable[[Event], None]] = field(default_factory=list)
    clock: Callable[[], float] = time.time

    def can(self, target: State) -> bool:
        return target in TRANSITIONS[self.state]

    def to(self, target: State, note: str = "") -> Event:
        if target == self.state:
            ev = Event(self.clock(), target, note)
            return ev  # idempotent, no-op
        if not self.can(target):
            raise InvalidTransition(f"{self.state.value} -> {target.value} not allowed")
        now = self.clock()
        if self.state == State.SLEEPING and target == State.ALLOCATING:
            self.session_started = now
            self.extends_used = 0
        if target == State.SLEEPING:
            self.session_started = None
        self.state, self.since = target, now
        if note:
            self.reason = note
        ev = Event(now, target, note)
        self.history.append(ev)
        del self.history[:-200]
        for fn in list(self.listeners):
            fn(ev)
        return ev

    # ---- convenience -------------------------------------------------------
    @property
    def is_billing(self) -> bool:
        return self.state in BILLING_STATES

    @property
    def is_booting(self) -> bool:
        return self.state in BOOT_STATES

    def on_stage(self, stage: str, status: str) -> None:
        """Feed a parsed `KIT_STAGE <stage> <status>` line from setup.py."""
        if status == "fail":
            if self.state not in (State.FAILED, State.STOPPING, State.SLEEPING):
                self.to(State.FAILED, f"stage {stage} failed")
            return
        target = STAGE_TO_STATE.get(stage)
        if status == "start" and target and target != self.state and self.can(target):
            self.to(target, f"stage {stage}")

    def snapshot(self) -> dict:
        return {
            "state": self.state.value,
            "profile": self.profile,
            "reason": self.reason,
            "since": self.since,
            "session_started": self.session_started,
            "extends_used": self.extends_used,
        }
