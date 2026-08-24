"""PipeWire/PulseAudio-compatible device enumeration through PortAudio."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Property, Signal, Slot


class AudioDeviceCatalog(QObject):
    devicesChanged = Signal()
    errorChanged = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._inputs: list[dict[str, Any]] = []
        self._outputs: list[dict[str, Any]] = []
        self._error = ""

    @Property("QVariantList", notify=devicesChanged)
    def inputDevices(self) -> list[dict[str, Any]]:
        return list(self._inputs)

    @Property("QVariantList", notify=devicesChanged)
    def outputDevices(self) -> list[dict[str, Any]]:
        return list(self._outputs)

    @Property(str, notify=errorChanged)
    def error(self) -> str:
        return self._error

    @Slot()
    def refresh(self) -> None:
        try:
            import sounddevice as sd

            devices = sd.query_devices()
            default_input, default_output = sd.default.device
            inputs: list[dict[str, Any]] = []
            outputs: list[dict[str, Any]] = []
            for index, raw in enumerate(devices):
                item = {
                    "id": str(index),
                    "name": str(raw.get("name") or f"Device {index}"),
                    "sampleRate": int(raw.get("default_samplerate") or 0),
                    "default": False,
                }
                if int(raw.get("max_input_channels") or 0) > 0:
                    inputs.append({**item, "default": index == default_input})
                if int(raw.get("max_output_channels") or 0) > 0:
                    outputs.append({**item, "default": index == default_output})
            self._inputs = inputs
            self._outputs = outputs
            self._set_error("")
        except Exception as exc:
            self._inputs = []
            self._outputs = []
            self._set_error(str(exc))
        self.devicesChanged.emit()

    def _set_error(self, value: str) -> None:
        if value == self._error:
            return
        self._error = value
        self.errorChanged.emit(value)
