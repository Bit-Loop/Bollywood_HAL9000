"""Interruptible PipeWire-friendly audio playback."""

from __future__ import annotations

import logging
import threading
from collections import deque

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
        self._queue: deque[AudioBuffer] = deque()
        self._lock = threading.RLock()
        self._ready = threading.Condition(self._lock)

    @Property(bool, notify=playingChanged)
    def playing(self) -> bool:
        return self._playing

    @Slot(object)
    def play(self, audio: object) -> None:
        if not isinstance(audio, AudioBuffer):
            self.errorOccurred.emit("Invalid synthesized audio")
            return
        with self._ready:
            self._queue.append(audio)
            thread = self._thread
            start_thread = thread is None
            if thread is None:
                self._stop.clear()
                thread = threading.Thread(
                    target=self._drain_queue,
                    daemon=True,
                    name="hal9000-playback",
                )
                self._thread = thread
            self._set_playing(True)
            self._ready.notify()
            if start_thread:
                thread.start()

    @Slot()
    def stop(self) -> None:
        self._stop.set()
        with self._ready:
            self._queue.clear()
            thread = self._thread
            self._ready.notify_all()
        try:
            import sounddevice as sd

            sd.stop()
        except Exception as exc:
            logging.getLogger("hal9000.audio").debug("Playback stop failed: %s", exc)
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        with self._ready:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def _drain_queue(self) -> None:
        try:
            while True:
                with self._ready:
                    while not self._queue and not self._stop.is_set():
                        self._ready.wait()
                    if self._stop.is_set():
                        break
                    audio = self._queue.popleft()
                try:
                    self._play_buffer_sync(audio)
                except Exception as exc:
                    self.errorOccurred.emit(str(exc))
                batch_finished = False
                with self._ready:
                    if not self._queue and not self._stop.is_set():
                        self._set_playing(False)
                        batch_finished = True
                if batch_finished:
                    self.levelChanged.emit(0.0)
                    self.finished.emit()
        finally:
            with self._ready:
                if self._thread is threading.current_thread():
                    self._thread = None
                was_playing = self._playing
                self._set_playing(False)
            self.levelChanged.emit(0.0)
            if was_playing:
                self.finished.emit()

    def _play_buffer_sync(self, audio: AudioBuffer) -> None:
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

    def _set_playing(self, value: bool) -> None:
        if value == self._playing:
            return
        self._playing = value
        self.playingChanged.emit(value)
