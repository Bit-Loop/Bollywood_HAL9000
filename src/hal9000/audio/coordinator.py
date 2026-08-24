"""One microphone stream shared by wake detection, VAD, and push-to-talk."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, Property, Signal, Slot

from hal9000.config import SpeechRecognitionSettings
from hal9000.speech.wake import SherpaWakeWord


class AudioCoordinator(QObject):
    levelChanged = Signal(float)
    modeChanged = Signal(str)
    wakeDetected = Signal()
    utteranceReady = Signal(object)
    errorOccurred = Signal(str)

    SAMPLE_RATE = 16_000
    BLOCK_SIZE = 1_280

    def __init__(
        self,
        settings: SpeechRecognitionSettings,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self._mode = "stopped"
        self._level = 0.0
        self._muted = False
        self._stream: Any = None
        self._wake: SherpaWakeWord | None = None
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=96)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._recorded: list[np.ndarray] = []
        self._heard_speech = False
        self._silence_started: float | None = None
        self._record_started = 0.0
        self._pre_roll: deque[np.ndarray] = deque(maxlen=5)

    @Property(str, notify=modeChanged)
    def mode(self) -> str:
        return self._mode

    @Property(float, notify=levelChanged)
    def level(self) -> float:
        return self._level

    @Property(bool, notify=modeChanged)
    def muted(self) -> bool:
        return self._muted

    def set_wake_engine(self, engine: SherpaWakeWord | None) -> None:
        old = self._wake
        self._wake = engine
        if old is not None and old is not engine:
            old.close()

    @Slot(str)
    def start(self, device: str = "") -> None:
        if self._stream is not None:
            self._set_mode("wake")
            return
        try:
            import sounddevice as sd

            selected: int | str | None = None
            if device.strip():
                selected = int(device) if device.strip().isdigit() else device.strip()
            self._stream = sd.RawInputStream(
                samplerate=self.SAMPLE_RATE,
                blocksize=self.BLOCK_SIZE,
                device=selected,
                channels=1,
                dtype="int16",
                callback=self._audio_callback,
                finished_callback=self._stream_finished,
            )
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._consume, daemon=True, name="hal9000-audio"
            )
            self._thread.start()
            self._stream.start()
            self._set_mode("wake")
        except Exception as exc:
            self._stream = None
            self._set_mode("error")
            self.errorOccurred.emit(f"Microphone unavailable: {exc}")

    @Slot()
    def stop(self) -> None:
        self._stop.set()
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:
                logging.getLogger("hal9000.audio").debug("Audio close failed: %s", exc)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        self._set_mode("stopped")

    @Slot()
    def startRecording(self) -> None:
        if self._muted:
            return
        if self._stream is None:
            self.errorOccurred.emit("Microphone is not available")
            return
        self._recorded = list(self._pre_roll)
        self._heard_speech = False
        self._silence_started = None
        self._record_started = time.monotonic()
        self._set_mode("record")

    @Slot()
    def stopRecording(self) -> None:
        if self._mode != "record":
            return
        self._finish_recording()

    @Slot(bool)
    def setSpeaking(self, speaking: bool) -> None:
        if self._muted:
            return
        self._set_mode("speaking" if speaking else ("wake" if self._stream is not None else "stopped"))
        if not speaking and self._wake is not None:
            self._wake.reset()

    @Slot()
    def toggleMute(self) -> None:
        self._muted = not self._muted
        self._set_mode(
            "muted" if self._muted else ("wake" if self._stream is not None else "stopped")
        )

    def restart(self, device: str = "") -> None:
        self.stop()
        self.start(device)

    def _audio_callback(self, data, frames: int, _time_info, status) -> None:
        if status:
            logging.getLogger("hal9000.audio").debug("PortAudio status: %s", status)
        try:
            self._queue.put_nowait(bytes(data[: frames * 2]))
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(bytes(data[: frames * 2]))
            except queue.Empty:
                return

    def _stream_finished(self) -> None:
        if not self._stop.is_set():
            self._stream = None
            self._set_mode("error")
            self.errorOccurred.emit("Microphone stream stopped; reconnect the device and run Mic Test")

    def _consume(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            pcm = np.frombuffer(raw, dtype="<i2").copy()
            normalized = pcm.astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(normalized * normalized))) if pcm.size else 0.0
            level = min(1.0, rms / 0.18)
            if abs(level - self._level) >= 0.012:
                self._level = level
                self.levelChanged.emit(level)
            mode = self._mode
            if mode == "wake" and not self._muted:
                self._pre_roll.append(pcm)
                try:
                    if self._wake is not None and self._wake.process(pcm):
                        self.wakeDetected.emit()
                except Exception as exc:
                    self.errorOccurred.emit(f"Wake detector failed: {exc}")
                    self._wake = None
            elif mode == "record" and not self._muted:
                self._recorded.append(pcm)
                self._consume_record_level(rms)

    def _consume_record_level(self, rms: float) -> None:
        now = time.monotonic()
        threshold = max(0.002, self.settings.silence_threshold)
        if rms >= threshold:
            self._heard_speech = True
            self._silence_started = None
        elif self._heard_speech:
            self._silence_started = self._silence_started or now
            if now - self._silence_started >= self.settings.silence_seconds:
                self._finish_recording()
                return
        elif now - self._record_started >= 7.0:
            self._finish_recording()
            return
        if now - self._record_started >= self.settings.max_utterance_seconds:
            self._finish_recording()

    def _finish_recording(self) -> None:
        chunks, self._recorded = self._recorded, []
        self._set_mode("wake")
        if not chunks or not self._heard_speech:
            self.utteranceReady.emit(np.empty(0, dtype=np.float32))
            return
        pcm = np.concatenate(chunks).astype(np.float32) / 32768.0
        self.utteranceReady.emit(pcm)

    def _set_mode(self, mode: str) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        self.modeChanged.emit(mode)
