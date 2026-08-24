"""KLL streaming quantiles for latency, utilization, and pressure metrics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from importlib.metadata import version

try:
    import datasketches
except ImportError:  # exact control remains available without approximate awareness
    datasketches = None

from hal9000.sentience.sketches.serialization import pack_envelope, unpack_envelope


@dataclass(frozen=True, slots=True)
class QuantileSummary:
    known: bool
    sample_count: int
    p50: float | None
    p90: float | None
    p95: float | None
    p99: float | None
    minimum_samples: int


class QuantileSketch:
    def __init__(
        self,
        metric_name: str,
        scope: str,
        *,
        k: int = 200,
        minimum_samples: int = 20,
    ) -> None:
        if datasketches is None:
            raise RuntimeError("Apache DataSketches KLL support is unavailable")
        if not 8 <= k <= 65_535:
            raise ValueError("KLL k must be between 8 and 65535")
        if minimum_samples < 1:
            raise ValueError("minimum quantile sample count must be positive")
        self.metric_name = metric_name
        self.scope = scope
        self.k = int(k)
        self.minimum_samples = int(minimum_samples)
        self._sketch = datasketches.kll_floats_sketch(self.k)

    @property
    def sample_count(self) -> int:
        return int(self._sketch.n)

    @property
    def num_retained(self) -> int:
        return int(self._sketch.num_retained)

    def update(self, value: float) -> None:
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("quantile samples must be finite")
        self._sketch.update(numeric)

    def rank(self, value: float) -> float | None:
        if not self.sample_count:
            return None
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("quantile rank value must be finite")
        return float(self._sketch.get_rank(numeric))

    def merge(self, other: "QuantileSketch") -> None:
        if (
            self.metric_name != other.metric_name
            or self.scope != other.scope
            or self.k != other.k
            or self.minimum_samples != other.minimum_samples
        ):
            raise ValueError("incompatible KLL sketches")
        self._sketch.merge(other._sketch)

    def summary(self) -> QuantileSummary:
        count = self.sample_count
        if count < self.minimum_samples:
            return QuantileSummary(False, count, None, None, None, None, self.minimum_samples)
        p50, p90, p95, p99 = self._sketch.get_quantiles([0.5, 0.9, 0.95, 0.99])
        return QuantileSummary(
            True,
            count,
            float(p50),
            float(p90),
            float(p95),
            float(p99),
            self.minimum_samples,
        )

    def serialize(self) -> bytes:
        summary = self.summary()
        return pack_envelope(
            {
                "metric_name": self.metric_name,
                "scope": self.scope,
                "mode": "KLL",
                "sketch_kind": "kll_floats",
                "parameters": {"k": self.k, "minimum_samples": self.minimum_samples},
                "key_version": 0,
                "library": "apache-datasketches",
                "library_version": version("datasketches"),
                "item_updates": self.sample_count,
                "estimate": summary.p50,
                "lower_bound": None,
                "upper_bound": None,
            },
            self._sketch.serialize(),
        )

    @classmethod
    def deserialize(cls, data: bytes) -> "QuantileSketch":
        metadata, blob = unpack_envelope(data)
        parameters = metadata.get("parameters") or {}
        instance = cls(
            str(metadata["metric_name"]),
            str(metadata["scope"]),
            k=int(parameters["k"]),
            minimum_samples=int(parameters["minimum_samples"]),
        )
        instance._sketch = datasketches.kll_floats_sketch.deserialize(blob)
        return instance
