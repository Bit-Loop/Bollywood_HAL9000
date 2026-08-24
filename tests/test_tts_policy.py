from __future__ import annotations

import numpy as np

from hal9000.speech.tts.base import AudioBuffer, TtsEngine
from hal9000.speech.tts.benchmark import select_auto_engine
from hal9000.speech.tts.manager import TtsManager


class FakeEngine(TtsEngine):
    def __init__(self, name: str, failure: str = "") -> None:
        self.name = name
        self.failure = failure
        self.calls = 0
        self._initialized = False

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def backend(self) -> str:
        return "test"

    def initialize(self, progress=None) -> None:
        if self.failure:
            raise RuntimeError(self.failure)
        self._initialized = True

    def synthesize(self, text: str, rate: float = 1.0) -> AudioBuffer:
        self.calls += 1
        self.initialize()
        return AudioBuffer(np.ones(800, dtype=np.float32) * 0.1, 16_000, self.name)


def healthy_rows(rtf: float) -> list[dict]:
    return [{"synthesized": True, "real_time_factor": rtf} for _ in range(4)]


def test_auto_strongly_prefers_healthy_xtts_even_when_piper_is_faster() -> None:
    selected, reason = select_auto_engine(
        {"XTTS": healthy_rows(1.8), "Piper": healthy_rows(0.03)}
    )
    assert selected == "XTTS"
    assert "reliably" in reason


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
