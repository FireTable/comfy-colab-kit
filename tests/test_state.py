import pytest

from gate.state import TRANSITIONS, InvalidTransition, Machine, State


def test_ten_states_and_every_state_has_transitions():
    assert len(State) == 10
    assert set(TRANSITIONS) == set(State)


def test_happy_path_sets_session_start():
    t = [100.0]
    m = Machine(clock=lambda: t[0])
    m.to(State.ALLOCATING)
    assert m.session_started == 100.0
    for s in (State.INSTALLING, State.DOWNLOADING, State.BOOTING, State.SMOKE_TESTING, State.READY):
        m.to(s)
    assert m.state == State.READY and m.is_billing
    m.to(State.STOPPING, "idle")
    m.to(State.SLEEPING)
    assert m.session_started is None and not m.is_billing
    assert [e.state for e in m.history][-1] == State.SLEEPING


@pytest.mark.parametrize("src,dst", [
    (State.SLEEPING, State.READY),
    (State.SLEEPING, State.STOPPING),
    (State.READY, State.ALLOCATING),
    (State.STOPPING, State.READY),
])
def test_invalid_transitions(src, dst):
    m = Machine()
    m.state = src
    with pytest.raises(InvalidTransition):
        m.to(dst)


def test_same_state_is_noop():
    m = Machine()
    m.to(State.SLEEPING)
    assert m.history == []


def test_on_stage_maps_setup_lines():
    m = Machine()
    m.to(State.ALLOCATING)
    m.on_stage("bootstrap", "start")
    assert m.state == State.INSTALLING
    m.on_stage("download", "start")
    assert m.state == State.DOWNLOADING
    m.on_stage("comfy_boot", "start")
    assert m.state == State.BOOTING
    m.on_stage("smoke", "fail")
    assert m.state == State.FAILED and "smoke" in m.reason


def test_failed_can_retry_or_stop():
    m = Machine()
    m.to(State.ALLOCATING)
    m.to(State.FAILED, "boom")
    assert m.can(State.ALLOCATING) and m.can(State.STOPPING)


def test_listeners_receive_events():
    seen = []
    m = Machine()
    m.listeners.append(seen.append)
    m.to(State.ALLOCATING, "wake")
    assert seen[0].state == State.ALLOCATING and seen[0].note == "wake"
