"""Warm-engine selection, automatic fallback, and A/B benchmark orchestration."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

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
    synthesisReady = Signal(object)
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
        self._auto_selection = auto_selection if auto_selection in {"XTTS", "Piper"} else "XTTS"
        self._xtts_broken = False
        self._last_fallback = ""
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hal9000-tts")
        self._engine_status = {"XTTS": "not loaded", "Piper": "not loaded"}
        self._progress_engine = ""

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

    @Slot()
    def preload(self) -> None:
        if self._status in {"loading", "benchmarking", "synthesizing"}:
            return
        preferred = self._preferred_name()
        self._progress_engine = preferred
        self._set_engine_status(preferred, "loading")
        self._set_status("loading")
        future = self._executor.submit(self.engines[preferred].initialize, self._progress)
        future.add_done_callback(lambda result, name=preferred: self._preload_done(name, result))

    @Slot(str)
    def speak(self, text: str) -> None:
        clean = text.strip()
        if not clean:
            return
        self._progress_engine = self._preferred_name()
        self._set_status("synthesizing")
        future = self._executor.submit(self._synthesize_with_fallback, clean)
        future.add_done_callback(self._synthesis_done)

    @Slot(str, str)
    def speakWith(self, engine: str, text: str) -> None:
        clean = text.strip()
        normalized = "XTTS" if engine.strip().lower() == "xtts" else "Piper"
        if not clean:
            return
        self._progress_engine = normalized
        self._set_status("synthesizing")
        future = self._executor.submit(self._synthesize_explicit, normalized, clean)
        future.add_done_callback(self._synthesis_done)

    @Slot()
    def runBenchmark(self) -> None:
        if self._status in {"loading", "benchmarking", "synthesizing"}:
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

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
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
        try:
            future.result()
        except Exception as exc:
            if name == "XTTS":
                self._xtts_broken = True
                self._last_fallback = f"XTTS initialization failed: {exc}"
                self.fallbackOccurred.emit(self._last_fallback)
                self._set_engine_status("XTTS", "error")
                self._progress_engine = "Piper"
                self._set_engine_status("Piper", "loading")
                fallback = self._executor.submit(self.engines["Piper"].initialize, self._progress)
                fallback.add_done_callback(lambda result: self._preload_done("Piper", result))
                return
            self._set_status("error")
            self._set_engine_status(name, "error")
            self.errorOccurred.emit(str(exc))
            return
        self._set_active_engine(name)
        self._set_engine_status(name, "ready")
        self._set_status("ready")

    def _synthesis_done(self, future: Future) -> None:
        try:
            audio = future.result()
        except Exception as exc:
            self._set_status("error")
            self.errorOccurred.emit(str(exc))
            return
        self._set_status("ready")
        self.synthesisReady.emit(audio)

    def _benchmark_done(self, future: Future) -> None:
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
