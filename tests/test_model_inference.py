from __future__ import annotations

import os
from math import gcd

import numpy as np
import pytest
from scipy.signal import resample_poly
from scipy.io import wavfile

from hal9000.paths import AppPaths
from hal9000.speech.stt import FasterWhisperService
from hal9000.speech.tts.piper import PiperHalEngine
from hal9000.speech.tts.xtts import XttsHalEngine
from hal9000.speech.wake import SherpaWakeWord, ensure_sherpa_model


pytestmark = [
    pytest.mark.models,
    pytest.mark.skipif(
        os.environ.get("HAL9000_RUN_MODEL_TESTS") != "1",
        reason="set HAL9000_RUN_MODEL_TESTS=1 to run downloaded local models",
    ),
]


def test_piper_hal_adapter_real_inference() -> None:
    engine = PiperHalEngine(AppPaths.discover().model_cache / "piper")
    audio = engine.synthesize("Good morning.")
    audio.validate()
    assert audio.engine == "Piper"
    assert audio.duration > 0.25


def test_xtts_hal_adapter_real_inference() -> None:
    engine = XttsHalEngine(AppPaths.discover().model_cache / "xtts", prefer_cuda=True)
    try:
        audio = engine.synthesize("Good morning.")
        audio.validate()
        assert audio.engine == "XTTS"
        assert audio.duration > 0.25
        assert engine.backend in {"CUDA", "CPU"}
    finally:
        engine.unload()


@pytest.mark.hardware
def test_sherpa_real_speech_and_faster_whisper_piper_audio() -> None:
    paths = AppPaths.discover()
    wake_model = ensure_sherpa_model(paths.model_cache / "sherpa")
    sample_rate, real_speech = wavfile.read(wake_model / "test_wavs" / "0.wav")
    assert sample_rate == 16_000
    detector = SherpaWakeWord("light up", 0.6, wake_model)
    try:
        detected = any(
            detector.process(np.asarray(real_speech[offset : offset + 1280], dtype=np.int16))
            for offset in range(0, len(real_speech), 1280)
        )
    finally:
        detector.close()
    assert detected, "Sherpa did not recognize the real-speech custom keyword"

    piper = PiperHalEngine(paths.model_cache / "piper")
    spoken = piper.synthesize("The system is operating normally.")
    common = gcd(spoken.sample_rate, 16_000)
    samples = resample_poly(
        np.asarray(spoken.samples, dtype=np.float32),
        16_000 // common,
        spoken.sample_rate // common,
    ).astype(np.float32)
    stt = FasterWhisperService("small", "en", paths.model_cache / "faster-whisper")
    try:
        transcription = stt._transcribe_sync(samples)
    finally:
        stt.close()
    assert "system" in transcription.lower()
    assert "operating" in transcription.lower()
