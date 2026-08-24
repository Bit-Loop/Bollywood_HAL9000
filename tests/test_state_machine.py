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


def test_wake_word_can_interrupt_an_idle_open_manual_drawer() -> None:
    machine = HalStateMachine()
    machine.transition(HalState.STANDBY)
    machine.enterManual()
    machine.transition(HalState.WAKE_DETECTED, "wake while drawer is open")
    assert machine.current is HalState.WAKE_DETECTED


@pytest.mark.parametrize(
    "target",
    [HalState.THINKING, HalState.TOOL_RUNNING, HalState.SPEAKING],
)
def test_resumed_hermes_activity_can_wake_the_standby_display(target: HalState) -> None:
    machine = HalStateMachine()
    machine.transition(HalState.STANDBY)
    machine.transition(target, "activity from resumed Hermes session")
    assert machine.current is target
