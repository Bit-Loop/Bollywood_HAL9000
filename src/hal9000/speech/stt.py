"""Asynchronous Faster-Whisper transcription without persistent recordings."""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Property, Signal, Slot


class FasterWhisperService(QObject):
    statusChanged = Signal(str)
    backendChanged = Signal(str)
    transcriptionReady = Signal(str)
    errorOccurred = Signal(str)

    def __init__(
        self,
        model_name: str,
        language: str,
        cache_dir: Path,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.model_name = model_name
        self.language = language
        self.cache_dir = cache_dir
        self._model = None
        self._status = "not loaded"
        self._backend = "pending"
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hal9000-stt")
        self._reload_after_task = False

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(str, notify=backendChanged)
    def backend(self) -> str:
        return self._backend

    @Slot()
    def warmup(self) -> None:
        if self._model is not None or self._status == "loading":
            return
        self._set_status("loading")
        self._submit(self._load_model, self._warmup_done)

    @Slot(object)
    def transcribe(self, samples: object) -> None:
        audio = np.asarray(samples, dtype=np.float32)
        if audio.size == 0:
            self.transcriptionReady.emit("")
            return
        self._set_status("transcribing")
        self._submit(lambda: self._transcribe_sync(audio), self._transcription_done)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def configure(self, model_name: str, language: str) -> None:
        changed_model = model_name != self.model_name
        self.model_name = model_name
        self.language = language or "en"
        if not changed_model:
            return
        if self._status in {"loading", "transcribing"}:
            self._reload_after_task = True
            return
        self._model = None
        self._backend = "pending"
        self.backendChanged.emit(self._backend)
        self._set_status("not loaded")

    def _load_model(self):
        from faster_whisper import WhisperModel

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            model = WhisperModel(
                self.model_name,
                device="cuda",
                compute_type="float16",
                download_root=str(self.cache_dir),
            )
            backend = "CUDA / float16"
        except Exception as cuda_error:
            logging.getLogger("hal9000.stt").warning(
                "Faster-Whisper CUDA initialization failed; using CPU: %s", cuda_error
            )
            model = WhisperModel(
                self.model_name,
                device="cpu",
                compute_type="int8",
                download_root=str(self.cache_dir),
            )
            backend = "CPU / int8"
        return model, backend

    def _transcribe_sync(self, audio: np.ndarray) -> str:
        if self._model is None:
            self._model, backend = self._load_model()
            self._backend = backend
            self.backendChanged.emit(backend)
        try:
            return self._transcribe_once(audio)
        except Exception as cuda_error:
            if not self._backend.startswith("CUDA"):
                raise
            logging.getLogger("hal9000.stt").warning(
                "Faster-Whisper CUDA inference failed; rebuilding on CPU: %s", cuda_error
            )
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self.model_name,
                device="cpu",
                compute_type="int8",
                download_root=str(self.cache_dir),
            )
            self._backend = "CPU / int8"
            self.backendChanged.emit(self._backend)
            return self._transcribe_once(audio)

    def _transcribe_once(self, audio: np.ndarray) -> str:
        segments, _info = self._model.transcribe(
            audio,
            language=self.language or "en",
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()

    def _submit(self, work, callback) -> None:
        future = self._executor.submit(work)
        future.add_done_callback(callback)

    def _warmup_done(self, future: Future) -> None:
        try:
            self._model, backend = future.result()
        except Exception as exc:
            self._set_status("error")
            self.errorOccurred.emit(str(exc))
            return
        self._backend = backend
        self.backendChanged.emit(backend)
        self._set_status("ready")
        self._apply_pending_reload()

    def _transcription_done(self, future: Future) -> None:
        try:
            text = future.result()
        except Exception as exc:
            self._set_status("error")
            self.errorOccurred.emit(str(exc))
            return
        self._set_status("ready")
        self.transcriptionReady.emit(text)
        self._apply_pending_reload()

    def _apply_pending_reload(self) -> None:
        if not self._reload_after_task:
            return
        self._reload_after_task = False
        self._model = None
        self._backend = "pending"
        self.backendChanged.emit(self._backend)
        self._set_status("not loaded")

    def _set_status(self, status: str) -> None:
        if status == self._status:
            return
        self._status = status
        self.statusChanged.emit(status)
