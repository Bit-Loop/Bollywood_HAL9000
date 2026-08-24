"""Warm-engine selection, automatic fallback, and A/B benchmark orchestration."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import threading

from PySide6.QtCore import QObject, Property, Signal, Slot

from hal9000.speech.tts.base import AudioBuffer, TtsEngine
from hal9000.speech.tts.benchmark import benchmark_engine, select_auto_engine
from hal9000.speech.tts.piper import PiperHalEngine
from hal9000.speech.tts.xtts import XttsHalEngine


class TtsManager(QObject):
    statusChanged = Signal(str)
    activeEngineChanged = Signal(str)
    progressChanged = Signal(str, float)
    engineStatusChanged = Signal()
    synthesisReady = Signal(object, int)
    fallbackOccurred = Signal(str)
    benchmarkCompleted = Signal(dict, str, str)
    errorOccurred = Signal(str)

    def __init__(
        self,
        cache_dir: Path,
        mode: str = "auto",
        rate: float = 1.0,
        auto_selection: str = "",
        parent: QObject | None = None,
        engines: dict[str, TtsEngine] | None = None,
    ) -> None:
        super().__init__(parent)
        self.mode = mode.lower()
        self.rate = rate
        self.engines = engines or {
            "XTTS": XttsHalEngine(cache_dir / "xtts", prefer_cuda=True),
            "Piper": PiperHalEngine(cache_dir / "piper", use_cuda=False),
        }
        self._status = "not loaded"
        self._active_engine = ""
        self._auto_selection = auto_selection if auto_selection in {"XTTS", "Piper"} else "Piper"
        self._xtts_broken = False
        self._last_fallback = ""
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hal9000-tts")
        self._engine_status = {"XTTS": "not loaded", "Piper": "not loaded"}
        self._progress_engine = ""
        self._speech_generation = 0
        self._speech_futures: set[Future] = set()
        self._speech_lock = threading.Lock()
        self._closed = False

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(str, notify=activeEngineChanged)
    def activeEngine(self) -> str:
        return self._active_engine

    @Property(str, notify=fallbackOccurred)
    def lastFallbackReason(self) -> str:
        return self._last_fallback

    @Property(str, notify=engineStatusChanged)
    def xttsStatus(self) -> str:
        return self._engine_status["XTTS"]

    @Property(str, notify=engineStatusChanged)
    def piperStatus(self) -> str:
        return self._engine_status["Piper"]

    @property
    def speechGeneration(self) -> int:
        """Token carried with audio so the controller can reject stale Qt events."""

        with self._speech_lock:
            return self._speech_generation

    @Slot()
    def preload(self) -> None:
        if self._closed or self._status in {"loading", "benchmarking", "synthesizing"}:
            return
        preferred = self._preferred_name()
        if self.mode == "auto" and preferred == "XTTS":
            capacity_check = getattr(
                self.engines["XTTS"], "interactive_cuda_available", None
            )
            if callable(capacity_check):
                self._progress_engine = "XTTS"
                self._set_engine_status("XTTS", "checking CUDA")
                self._set_status("loading")
                future = self._executor.submit(capacity_check)
                future.add_done_callback(self._auto_preload_checked)
                return
        self._start_preload(preferred)

    def _start_preload(self, preferred: str) -> None:
        if self._closed:
            return
        self._progress_engine = preferred
        self._set_engine_status(preferred, "loading")
        self._set_status("loading")
        future = self._executor.submit(self.engines[preferred].initialize, self._progress)
        future.add_done_callback(lambda result, name=preferred: self._preload_done(name, result))

    @Slot(str)
    def speak(self, text: str) -> None:
        clean = text.strip()
        if self._closed or not clean:
            return
        self._progress_engine = self._preferred_name()
        self._set_status("synthesizing")
        with self._speech_lock:
            generation = self._speech_generation
        future = self._executor.submit(self._synthesize_with_fallback, clean)
        with self._speech_lock:
            self._speech_futures.add(future)
        future.add_done_callback(
            lambda result, token=generation: self._synthesis_done(result, token)
        )

    @Slot(str, str)
    def speakWith(self, engine: str, text: str) -> None:
        clean = text.strip()
        normalized = "XTTS" if engine.strip().lower() == "xtts" else "Piper"
        if self._closed or not clean:
            return
        self._progress_engine = normalized
        self._set_status("synthesizing")
        with self._speech_lock:
            generation = self._speech_generation
        future = self._executor.submit(self._synthesize_explicit, normalized, clean)
        with self._speech_lock:
            self._speech_futures.add(future)
        future.add_done_callback(
            lambda result, token=generation: self._synthesis_done(result, token)
        )

    @Slot()
    def runBenchmark(self) -> None:
        if self._closed or self._status in {"loading", "benchmarking", "synthesizing"}:
            return
        self._set_engine_status("XTTS", "benchmarking")
        self._set_engine_status("Piper", "queued")
        self._set_status("benchmarking")
        future = self._executor.submit(self._benchmark_sync)
        future.add_done_callback(self._benchmark_done)

    def set_mode(self, mode: str) -> None:
        normalized = mode.lower()
        if normalized in {"auto", "xtts", "piper"}:
            self.mode = normalized

    @Slot()
    def cancelPending(self) -> None:
        """Discard queued/running speech results from the previous turn."""

        with self._speech_lock:
            self._speech_generation += 1
            futures = tuple(self._speech_futures)
            self._speech_futures.clear()
        for future in futures:
            future.cancel()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.cancelPending()
        # Engines own native CPU/CUDA state. Do not unload it under an active
        # inference call; that race can repopulate or access a half-torn-down
        # model and keep the process consuming resources after the window exits.
        self._executor.shutdown(wait=True, cancel_futures=True)
        for engine in self.engines.values():
            engine.unload()

    def _preferred_name(self) -> str:
        if self.mode == "piper":
            return "Piper"
        if self.mode == "xtts":
            return "XTTS"
        if self._xtts_broken:
            return "Piper"
        return self._auto_selection or "XTTS"

    def _synthesize_with_fallback(self, text: str) -> AudioBuffer:
        preferred = self._preferred_name()
        try:
            audio = self.engines[preferred].synthesize(text, self.rate)
            self._set_engine_status(preferred, "ready")
            self._set_active_engine(preferred)
            return audio
        except Exception as primary_error:
            if preferred == "Piper":
                raise
            self._xtts_broken = True
            self.engines["XTTS"].unload()
            self._set_engine_status("XTTS", "error")
            self._last_fallback = f"XTTS failed: {primary_error}"
            self.fallbackOccurred.emit(self._last_fallback)
            audio = self.engines["Piper"].synthesize(text, self.rate)
            self._set_engine_status("Piper", "ready")
            self._set_active_engine("Piper")
            return audio

    def _synthesize_explicit(self, engine: str, text: str) -> AudioBuffer:
        try:
            audio = self.engines[engine].synthesize(text, self.rate)
            self._set_engine_status(engine, "ready")
            self._set_active_engine(engine)
            return audio
        except Exception as exc:
            if engine == "XTTS":
                self._xtts_broken = True
                self._last_fallback = f"XTTS A/B test failed: {exc}"
                self.fallbackOccurred.emit(self._last_fallback)
            self._set_engine_status(engine, "error")
            raise

    def _benchmark_sync(self) -> tuple[dict, str, str]:
        results: dict[str, list[dict]] = {}
        for name in ("XTTS", "Piper"):
            self._progress_engine = name
            self._set_engine_status(name, "benchmarking")
            rows = benchmark_engine(self.engines[name], self._benchmark_progress)
            results[name] = [row.as_dict() for row in rows]
            self._set_engine_status(
                name,
                "ready" if rows and all(row.synthesized for row in rows) else "error",
            )
        selected, reason = select_auto_engine(results)
        if selected:
            self._auto_selection = selected
            self._xtts_broken = selected != "XTTS" and any(
                row.get("error") for row in results.get("XTTS", [])
            )
        return results, selected, reason

    def _preload_done(self, name: str, future: Future) -> None:
        if self._closed:
            return
        try:
            future.result()
        except Exception as exc:
            if name == "XTTS":
                self._xtts_broken = True
                self._last_fallback = f"XTTS initialization failed: {exc}"
                self.fallbackOccurred.emit(self._last_fallback)
                self._set_engine_status("XTTS", "error")
                self._start_preload("Piper")
                return
            self._set_status("error")
            self._set_engine_status(name, "error")
            self.errorOccurred.emit(str(exc))
            return
        if (
            name == "XTTS"
            and self.mode == "auto"
            and self.engines["XTTS"].backend == "CPU"
        ):
            self._auto_selection = "Piper"
            self._last_fallback = (
                "Auto selected Piper because XTTS could not secure interactive CUDA capacity"
            )
            self.fallbackOccurred.emit(self._last_fallback)
            self._set_engine_status("XTTS", "CPU deferred")
            self.engines["XTTS"].unload()
            self._start_preload("Piper")
            return
        self._set_active_engine(name)
        self._set_engine_status(name, "ready")
        self._set_status("ready")

    def _synthesis_done(self, future: Future, generation: int) -> None:
        if self._closed:
            return
        with self._speech_lock:
            self._speech_futures.discard(future)
            current_generation = self._speech_generation
        if future.cancelled() or generation != current_generation:
            return
        try:
            audio = future.result()
        except Exception as exc:
            self._set_status("error")
            self.errorOccurred.emit(str(exc))
            return
        self._set_status("ready")
        self.synthesisReady.emit(audio, generation)

    def _benchmark_done(self, future: Future) -> None:
        if self._closed:
            return
        try:
            results, selected, reason = future.result()
        except Exception as exc:
            self._set_status("error")
            self.errorOccurred.emit(str(exc))
            return
        self._set_status("ready")
        if selected:
            self._set_active_engine(selected)
        self.benchmarkCompleted.emit(results, selected, reason)

    def _auto_preload_checked(self, future: Future) -> None:
        if self._closed:
            return
        try:
            available, detail = future.result()
        except Exception as exc:
            available, detail = False, f"XTTS CUDA capacity probe failed: {exc}"
        if self.mode != "auto":
            self._start_preload(self._preferred_name())
            return
        if available:
            self._start_preload("XTTS")
            return
        self._auto_selection = "Piper"
        self._last_fallback = f"Auto selected Piper for low latency: {detail}"
        self._set_engine_status("XTTS", "CUDA capacity unavailable")
        self.fallbackOccurred.emit(self._last_fallback)
        self._start_preload("Piper")

    def _progress(self, label: str, fraction: float) -> None:
        if self._progress_engine:
            self._set_engine_status(self._progress_engine, label)
        self.progressChanged.emit(label, fraction)

    def _benchmark_progress(self, engine: str, index: int, total: int) -> None:
        self._set_engine_status(engine, "benchmarking")
        self.progressChanged.emit(f"Benchmarking {engine}", index / max(1, total))

    def _set_status(self, status: str) -> None:
        if status == self._status:
            return
        self._status = status
        self.statusChanged.emit(status)

    def _set_active_engine(self, engine: str) -> None:
        if engine == self._active_engine:
            return
        self._active_engine = engine
        self.activeEngineChanged.emit(engine)

    def _set_engine_status(self, engine: str, status: str) -> None:
        if self._engine_status.get(engine) == status:
            return
        self._engine_status[engine] = status
        self.engineStatusChanged.emit()
