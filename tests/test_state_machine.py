from __future__ import annotations

import pytest

from hal9000.state import HalState, HalStateMachine, InvalidTransition


def test_voice_turn_lifecycle_returns_to_dark_standby(qtbot) -> None:
    machine = HalStateMachine()
    observed: list[str] = []
    machine.stateChanged.connect(observed.append)

    for state in (
        HalState.STANDBY,
        HalState.WAKE_DETECTED,
        HalState.LISTENING,
        HalState.THINKING,
        HalState.SPEAKING,
        HalState.STANDBY,
    ):
        machine.transition(state, "test")

    assert observed == [
        "STANDBY",
        "WAKE DETECTED",
        "LISTENING",
        "THINKING",
        "SPEAKING",
        "STANDBY",
    ]
    assert machine.active is False


def test_failure_can_recover_to_manual_and_then_standby() -> None:
    machine = HalStateMachine()
    machine.transition(HalState.ERROR)
    machine.enterManual()
    assert machine.current is HalState.MANUAL
    assert machine.manualOpen is True
    machine.leaveManual()
    assert machine.current is HalState.STANDBY
    assert machine.manualOpen is False


def test_invalid_transition_is_rejected_without_mutating_state() -> None:
    machine = HalStateMachine()
    with pytest.raises(InvalidTransition):
        machine.transition(HalState.SPEAKING)
    assert machine.current is HalState.BOOTING
