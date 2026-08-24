"""Piper HAL voice adapter."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from huggingface_hub import snapshot_download

from hal9000.speech.tts.base import AudioBuffer, TtsEngine


class PiperHalEngine(TtsEngine):
    name = "Piper"
    repo_id = "campwill/HAL-9000-Piper-TTS"

    def __init__(self, cache_dir: Path, use_cuda: bool = False) -> None:
        self.cache_dir = cache_dir
        self.use_cuda = use_cuda
        self._voice = None
        self._backend = "CUDA" if use_cuda else "CPU"

    @property
    def initialized(self) -> bool:
        return self._voice is not None

    @property
    def backend(self) -> str:
        return self._backend

    def initialize(self, progress=None) -> None:
        if self._voice is not None:
            return
        if progress:
            progress("downloading", 0.0)
        model_dir = Path(
            snapshot_download(
                repo_id=self.repo_id,
                cache_dir=str(self.cache_dir),
                allow_patterns=["hal.onnx", "hal.onnx.json"],
            )
        )
        if progress:
            progress("loading", 0.8)
        from piper import PiperVoice

        self._voice = PiperVoice.load(str(model_dir / "hal.onnx"), use_cuda=self.use_cuda)
        if progress:
            progress("ready", 1.0)

    def synthesize(self, text: str, rate: float = 1.0) -> AudioBuffer:
        if self._voice is None:
            self.initialize()
        from piper import SynthesisConfig

        config = SynthesisConfig(
            length_scale=1.0 / max(0.5, min(2.0, rate)),
            noise_scale=0.667,
            noise_w_scale=0.8,
            normalize_audio=True,
        )
        chunks = list(self._voice.synthesize(text, syn_config=config))
        if not chunks:
            raise RuntimeError("Piper produced no audio chunks")
        samples = np.concatenate(
            [np.asarray(chunk.audio_float_array, dtype=np.float32).reshape(-1) for chunk in chunks]
        )
        buffer = AudioBuffer(samples, int(chunks[0].sample_rate), self.name)
        buffer.validate()
        return buffer

    def unload(self) -> None:
        self._voice = None
