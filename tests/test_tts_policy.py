from __future__ import annotations

import threading
from types import SimpleNamespace

import numpy as np

from hal9000.speech.tts.base import AudioBuffer, TtsEngine
from hal9000.speech.tts.benchmark import select_auto_engine
from hal9000.speech.tts.manager import TtsManager
from hal9000.speech.tts.xtts import XttsHalEngine


class FakeEngine(TtsEngine):
    def __init__(self, name: str, failure: str = "") -> None:
        self.name = name
        self.failure = failure
        self.calls = 0
        self.initializations = 0
        self._initialized = False

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def backend(self) -> str:
        return "test"

    def initialize(self, progress=None) -> None:
        self.initializations += 1
        if self.failure:
            raise RuntimeError(self.failure)
        self._initialized = True

    def synthesize(self, text: str, rate: float = 1.0) -> AudioBuffer:
        self.calls += 1
        self.initialize()
        return AudioBuffer(np.ones(800, dtype=np.float32) * 0.1, 16_000, self.name)


def healthy_rows(rtf: float) -> list[dict]:
    return [{"synthesized": True, "real_time_factor": rtf} for _ in range(4)]


def test_auto_prefers_low_latency_piper_when_both_voices_are_healthy() -> None:
    selected, reason = select_auto_engine(
        {"XTTS": healthy_rows(1.8), "Piper": healthy_rows(0.03)}
    )
    assert selected == "Piper"
    assert "latency" in reason.lower()


def test_auto_uses_piper_when_xtts_is_absurdly_slow() -> None:
    selected, reason = select_auto_engine(
        {"XTTS": healthy_rows(5.1), "Piper": healthy_rows(0.04)}
    )
    assert selected == "Piper"
    assert "absurdly slow" in reason


def test_runtime_xtts_failure_falls_back_once_and_stays_on_piper(tmp_path) -> None:
    xtts = FakeEngine("XTTS", "GPU OOM")
    piper = FakeEngine("Piper")
    manager = TtsManager(
        tmp_path,
        mode="auto",
        auto_selection="XTTS",
        engines={"XTTS": xtts, "Piper": piper},
    )
    fallbacks: list[str] = []
    manager.fallbackOccurred.connect(fallbacks.append)

    first = manager._synthesize_with_fallback("first")
    second = manager._synthesize_with_fallback("second")

    assert first.engine == second.engine == "Piper"
    assert xtts.calls == 1
    assert piper.calls == 2
    assert "GPU OOM" in fallbacks[0]
    manager.close()


def test_xtts_does_not_resplit_hal_streaming_chunks(tmp_path) -> None:
    calls: list[dict] = []

    class FakeXtts:
        def inference(self, **kwargs):
            calls.append(kwargs)
            return {"wav": np.ones(800, dtype=np.float32) * 0.1}

    engine = XttsHalEngine(tmp_path)
    engine._model = FakeXtts()
    engine._config = SimpleNamespace(audio=SimpleNamespace(output_sample_rate=24_000))
    engine._conditioning = (object(), object())

    audio = engine.synthesize("Good morning.")

    assert audio.duration > 0
    assert calls[0]["enable_text_splitting"] is False


def test_auto_preload_uses_piper_when_xtts_lacks_cuda_capacity(qtbot, tmp_path) -> None:
    class ConstrainedXtts(FakeEngine):
        def interactive_cuda_available(self):
            return False, "only 2.9 GiB free"

    xtts = ConstrainedXtts("XTTS")
    piper = FakeEngine("Piper")
    manager = TtsManager(
        tmp_path,
        mode="auto",
        auto_selection="XTTS",
        engines={"XTTS": xtts, "Piper": piper},
    )
    fallbacks: list[str] = []
    manager.fallbackOccurred.connect(fallbacks.append)

    manager.preload()

    qtbot.waitUntil(lambda: manager.activeEngine == "Piper", timeout=1500)
    assert xtts.initializations == 0
    assert piper.initializations == 1
    assert "low latency" in fallbacks[-1]
    manager.close()


def test_close_waits_for_worker_before_unloading_voice_models(tmp_path) -> None:
    class BlockingEngine(FakeEngine):
        def __init__(self, name: str) -> None:
            super().__init__(name)
            self.started = threading.Event()
            self.release = threading.Event()
            self.unloaded = False

        def synthesize(self, text: str, rate: float = 1.0) -> AudioBuffer:
            self.started.set()
            assert self.release.wait(2)
            return super().synthesize(text, rate)

        def unload(self) -> None:
            self.unloaded = True
            self._initialized = False

    piper = BlockingEngine("Piper")
    manager = TtsManager(
        tmp_path,
        mode="piper",
        engines={"XTTS": FakeEngine("XTTS"), "Piper": piper},
    )
    manager.speak("Please stop safely.")
    assert piper.started.wait(1)

    closer = threading.Thread(target=manager.close)
    closer.start()
    closer.join(timeout=0.1)

    assert closer.is_alive()
    assert piper.unloaded is False

    piper.release.set()
    closer.join(timeout=2)
    assert not closer.is_alive()
    assert piper.unloaded is True
