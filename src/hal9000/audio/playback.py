"""Interruptible PipeWire-friendly audio playback."""

from __future__ import annotations

import threading
import logging
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, Property, Signal, Slot

from hal9000.speech.tts.base import AudioBuffer


class AudioPlayback(QObject):
    playingChanged = Signal(bool)
    levelChanged = Signal(float)
    finished = Signal()
    errorOccurred = Signal(str)

    def __init__(
        self,
        device: str = "",
        volume: float = 0.82,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.device = device
        self.volume = volume
        self._playing = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @Property(bool, notify=playingChanged)
    def playing(self) -> bool:
        return self._playing

    @Slot(object)
    def play(self, audio: object) -> None:
        if not isinstance(audio, AudioBuffer):
            self.errorOccurred.emit("Invalid synthesized audio")
            return
        self.stop()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._play_sync,
            args=(audio,),
            daemon=True,
            name="hal9000-playback",
        )
        self._set_playing(True)
        self._thread.start()

    @Slot()
    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        try:
            import sounddevice as sd

            sd.stop()
        except Exception as exc:
            logging.getLogger("hal9000.audio").debug("Playback stop failed: %s", exc)
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        if self._thread is thread:
            self._thread = None

    def _play_sync(self, audio: AudioBuffer) -> None:
        try:
            import sounddevice as sd

            selected: int | str | None = None
            if self.device.strip():
                selected = int(self.device) if self.device.strip().isdigit() else self.device.strip()
            samples = np.asarray(audio.samples, dtype=np.float32) * min(1.0, max(0.0, self.volume))
            block = max(256, int(audio.sample_rate * 0.04))
            with sd.OutputStream(
                samplerate=audio.sample_rate,
                channels=1,
                dtype="float32",
                device=selected,
                blocksize=block,
            ) as stream:
                for offset in range(0, len(samples), block):
                    if self._stop.is_set():
                        break
                    chunk = samples[offset : offset + block]
                    level = min(1.0, float(np.sqrt(np.mean(chunk * chunk))) / 0.22)
                    self.levelChanged.emit(level)
                    stream.write(chunk.reshape(-1, 1))
        except Exception as exc:
            self.errorOccurred.emit(str(exc))
        finally:
            self.levelChanged.emit(0.0)
            self._set_playing(False)
            if self._thread is threading.current_thread():
                self._thread = None
            self.finished.emit()

    def _set_playing(self, value: bool) -> None:
        if value == self._playing:
            return
        self._playing = value
        self.playingChanged.emit(value)
