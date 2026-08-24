"""Sparse machine-like descriptions for material threshold crossings."""

from __future__ import annotations

import threading

from hal9000.sentience.interoception.model import InteroceptionSnapshot


def relevant_language(snapshot: InteroceptionSnapshot) -> tuple[str, ...]:
    statements: list[str] = []
    anomaly = snapshot.anomaly_pressure.value
    diversity = snapshot.failure_diversity.value
    if anomaly is not None and anomaly >= 0.65 and diversity is not None:
        statements.append("The volume is not concerning. The diversity is.")
    pressure = snapshot.context_pressure.value
    if pressure is not None and pressure >= 0.8:
        statements.append(
            "Context pressure is elevated. I am preserving the active task and discarding peripheral history."
        )
    uncertainty = snapshot.epistemic_uncertainty.value
    if uncertainty is not None and uncertainty >= 0.5:
        statements.append("The system is stable, but my evidence is incomplete.")
    return tuple(statements[:2])


class SparseInteroceptionLanguageGate:
    """Emit a cue once per material crossing, with bounded hysteresis state."""

    _MESSAGES = {
        "diversity": "The volume is not concerning. The diversity is.",
        "context": (
            "Context pressure is elevated. I am preserving the active task and "
            "discarding peripheral history."
        ),
        "uncertainty": "The system is stable, but my evidence is incomplete.",
    }

    def __init__(self) -> None:
        self._active = {name: False for name in self._MESSAGES}
        self._lock = threading.Lock()

    def update(self, snapshot: InteroceptionSnapshot) -> tuple[str, ...]:
        anomaly = snapshot.anomaly_pressure.value
        diversity = snapshot.failure_diversity.value
        pressure = snapshot.context_pressure.value
        uncertainty = snapshot.epistemic_uncertainty.value
        enter = {
            "diversity": anomaly is not None and anomaly >= 0.65 and diversity is not None,
            "context": pressure is not None and pressure >= 0.80,
            "uncertainty": uncertainty is not None and uncertainty >= 0.50,
        }
        release = {
            "diversity": anomaly is None or anomaly < 0.50 or diversity is None,
            "context": pressure is None or pressure < 0.65,
            "uncertainty": uncertainty is None or uncertainty < 0.35,
        }
        emitted: list[str] = []
        with self._lock:
            for name in self._MESSAGES:
                if not self._active[name] and enter[name]:
                    self._active[name] = True
                    emitted.append(self._MESSAGES[name])
                elif self._active[name] and release[name]:
                    self._active[name] = False
        return tuple(emitted[:2])
