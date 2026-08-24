"""Asynchronous wake-model preparation for the UI lifecycle."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Property, Signal, Slot

from hal9000.speech.wake import SherpaWakeWord, ensure_sherpa_model


class WakeWordService(QObject):
    statusChanged = Signal(str)
    progressChanged = Signal(float)
    ready = Signal(object)
    errorOccurred = Signal(str)

    def __init__(
        self,
        phrase: str,
        sensitivity: float,
        cache_dir: Path,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.phrase = phrase
        self.sensitivity = sensitivity
        self.cache_dir = cache_dir
        self._status = "not loaded"
        self._progress = 0.0
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hal9000-wake")
        self._restart_requested = False

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(float, notify=progressChanged)
    def progress(self) -> float:
        return self._progress

    @Slot()
    def start(self) -> None:
        if self._status in {"downloading", "loading", "ready"}:
            return
        self._set_status("downloading")
        future = self._executor.submit(self._build)
        future.add_done_callback(self._done)

    def configure(self, phrase: str, sensitivity: float) -> None:
        self.phrase = (phrase or "hey hal").strip().lower()
        self.sensitivity = min(1.0, max(0.0, float(sensitivity)))
        if self._status in {"downloading", "loading"}:
            self._restart_requested = True
            return
        self._set_status("not loaded")
        self.start()

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _build(self) -> SherpaWakeWord:
        model = ensure_sherpa_model(self.cache_dir, self._download_progress)
        self._set_status("loading")
        return SherpaWakeWord(self.phrase, self.sensitivity, model)

    def _download_progress(self, received: int, total: int) -> None:
        self._progress = received / total if total else 0.0
        self.progressChanged.emit(self._progress)

    def _done(self, future: Future) -> None:
        try:
            engine = future.result()
        except Exception as exc:
            self._set_status("error")
            self.errorOccurred.emit(str(exc))
            return
        if self._restart_requested:
            self._restart_requested = False
            engine.close()
            self._set_status("not loaded")
            self.start()
            return
        self._progress = 1.0
        self.progressChanged.emit(1.0)
        self._set_status("ready")
        self.ready.emit(engine)

    def _set_status(self, status: str) -> None:
        if status == self._status:
            return
        self._status = status
        self.statusChanged.emit(status)
