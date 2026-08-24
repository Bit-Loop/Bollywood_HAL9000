"""Explicit HAL application state machine."""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import QObject, Property, Signal, Slot


class HalState(StrEnum):
    BOOTING = "BOOTING"
    STANDBY = "STANDBY"
    WAKE_DETECTED = "WAKE DETECTED"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    THINKING = "THINKING"
    TOOL_RUNNING = "TOOL RUNNING"
    WAITING_APPROVAL = "WAITING APPROVAL"
    SPEAKING = "SPEAKING"
    MANUAL = "MANUAL"
    ERROR = "ERROR"
    DISABLED = "DISABLED"


ACTIVE_STATES = frozenset(
    {
        HalState.WAKE_DETECTED,
        HalState.LISTENING,
        HalState.TRANSCRIBING,
        HalState.THINKING,
        HalState.TOOL_RUNNING,
        HalState.WAITING_APPROVAL,
        HalState.SPEAKING,
        HalState.MANUAL,
        HalState.ERROR,
    }
)

ALLOWED_TRANSITIONS: dict[HalState, frozenset[HalState]] = {
    HalState.BOOTING: frozenset({HalState.STANDBY, HalState.MANUAL, HalState.ERROR, HalState.DISABLED}),
    HalState.STANDBY: frozenset({HalState.WAKE_DETECTED, HalState.LISTENING, HalState.MANUAL, HalState.ERROR, HalState.DISABLED}),
    HalState.WAKE_DETECTED: frozenset({HalState.LISTENING, HalState.MANUAL, HalState.ERROR, HalState.STANDBY}),
    HalState.LISTENING: frozenset({HalState.TRANSCRIBING, HalState.THINKING, HalState.MANUAL, HalState.ERROR, HalState.STANDBY}),
    HalState.TRANSCRIBING: frozenset({HalState.THINKING, HalState.LISTENING, HalState.MANUAL, HalState.ERROR, HalState.STANDBY}),
    HalState.THINKING: frozenset({HalState.TOOL_RUNNING, HalState.WAITING_APPROVAL, HalState.SPEAKING, HalState.MANUAL, HalState.ERROR, HalState.STANDBY}),
    HalState.TOOL_RUNNING: frozenset({HalState.THINKING, HalState.WAITING_APPROVAL, HalState.SPEAKING, HalState.MANUAL, HalState.ERROR, HalState.STANDBY}),
    HalState.WAITING_APPROVAL: frozenset({HalState.THINKING, HalState.TOOL_RUNNING, HalState.SPEAKING, HalState.MANUAL, HalState.ERROR}),
    HalState.SPEAKING: frozenset({HalState.LISTENING, HalState.MANUAL, HalState.STANDBY, HalState.ERROR}),
    HalState.MANUAL: frozenset({HalState.LISTENING, HalState.TRANSCRIBING, HalState.THINKING, HalState.TOOL_RUNNING, HalState.WAITING_APPROVAL, HalState.SPEAKING, HalState.STANDBY, HalState.ERROR, HalState.DISABLED}),
    HalState.ERROR: frozenset({HalState.BOOTING, HalState.STANDBY, HalState.MANUAL, HalState.DISABLED}),
    HalState.DISABLED: frozenset({HalState.BOOTING, HalState.STANDBY, HalState.MANUAL}),
}


class InvalidTransition(RuntimeError):
    pass


class HalStateMachine(QObject):
    stateChanged = Signal(str)
    transitionOccurred = Signal(str, str, str)
    manualOpenChanged = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._state = HalState.BOOTING
        self._manual_open = False

    @Property(str, notify=stateChanged)
    def state(self) -> str:
        return self._state.value

    @Property(bool, notify=stateChanged)
    def active(self) -> bool:
        return self._state in ACTIVE_STATES

    @Property(bool, notify=manualOpenChanged)
    def manualOpen(self) -> bool:
        return self._manual_open

    @property
    def current(self) -> HalState:
        return self._state

    def transition(self, target: HalState, reason: str = "") -> None:
        if target == self._state:
            return
        if target not in ALLOWED_TRANSITIONS[self._state]:
            raise InvalidTransition(f"{self._state.name} -> {target.name} is not allowed")
        previous = self._state
        self._state = target
        self.stateChanged.emit(target.value)
        self.transitionOccurred.emit(previous.value, target.value, reason)

    @Slot()
    def enterManual(self) -> None:
        if not self._manual_open:
            self._manual_open = True
            self.manualOpenChanged.emit(True)
        if self._state != HalState.MANUAL:
            self.transition(HalState.MANUAL, "manual drawer opened")

    @Slot()
    def leaveManual(self) -> None:
        if self._manual_open:
            self._manual_open = False
            self.manualOpenChanged.emit(False)
        if self._state == HalState.MANUAL:
            self.transition(HalState.STANDBY, "manual drawer closed")

    def return_to_rest(self, reason: str = "activity complete") -> None:
        target = HalState.MANUAL if self._manual_open else HalState.STANDBY
        if target != self._state:
            self.transition(target, reason)
