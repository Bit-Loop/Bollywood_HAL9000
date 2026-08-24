"""Shared local TTS contracts."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(slots=True)
class AudioBuffer:
    samples: np.ndarray
    sample_rate: int
    engine: str

    def validate(self) -> None:
        values = np.asarray(self.samples, dtype=np.float32).reshape(-1)
        if values.size < 128:
            raise RuntimeError(f"{self.engine} produced empty audio")
        if not np.isfinite(values).all():
            raise RuntimeError(f"{self.engine} produced NaN or infinite audio")
        if int(self.sample_rate) <= 0:
            raise RuntimeError(f"{self.engine} returned an invalid sample rate")
        peak = float(np.max(np.abs(values)))
        if not math.isfinite(peak) or peak < 1e-6:
            raise RuntimeError(f"{self.engine} produced silent audio")
        self.samples = np.clip(values, -1.0, 1.0)

    @property
    def duration(self) -> float:
        return float(len(self.samples)) / float(self.sample_rate)


@dataclass(slots=True)
class SynthesisMetrics:
    engine: str
    text: str
    initialized: bool
    synthesized: bool
    initialization_seconds: float
    first_playable_seconds: float
    synthesis_seconds: float
    output_seconds: float
    real_time_factor: float
    backend: str
    memory_megabytes: float | None
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "text": self.text,
            "initialized": self.initialized,
            "synthesized": self.synthesized,
            "initialization_seconds": round(self.initialization_seconds, 3),
            "first_playable_seconds": round(self.first_playable_seconds, 3),
            "synthesis_seconds": round(self.synthesis_seconds, 3),
            "output_seconds": round(self.output_seconds, 3),
            "real_time_factor": round(self.real_time_factor, 3),
            "backend": self.backend,
            "memory_megabytes": (
                round(self.memory_megabytes, 1) if self.memory_megabytes is not None else None
            ),
            "error": self.error,
        }


class TtsEngine(ABC):
    name: str

    @property
    @abstractmethod
    def initialized(self) -> bool: ...

    @property
    @abstractmethod
    def backend(self) -> str: ...

    @abstractmethod
    def initialize(self, progress=None) -> None: ...

    @abstractmethod
    def synthesize(self, text: str, rate: float = 1.0) -> AudioBuffer: ...

    def unload(self) -> None:
        return None
