"""Bounded frequent-items summaries with explicit error bounds."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import version

try:
    import datasketches
except ImportError:  # exact control remains available without approximate awareness
    datasketches = None

from hal9000.sentience.sketches.serialization import pack_envelope, unpack_envelope


@dataclass(frozen=True, slots=True)
class HeavyHitter:
    item: str
    estimate: int
    lower_bound: int
    upper_bound: int


class FrequencySketch:
    def __init__(self, metric_name: str, scope: str, *, lg_max_k: int = 10) -> None:
        if datasketches is None:
            raise RuntimeError("Apache DataSketches frequent-items support is unavailable")
        if not 3 <= lg_max_k <= 20:
            raise ValueError("frequency lg_max_k must be between 3 and 20")
        self.metric_name = metric_name
        self.scope = scope
        self.lg_max_k = int(lg_max_k)
        self._sketch = datasketches.frequent_strings_sketch(self.lg_max_k)
        self.item_updates = 0

    def update(self, item: str, weight: int = 1) -> None:
        if weight <= 0:
            raise ValueError("frequency weight must be positive")
        safe = str(item)[:2048]
        self._sketch.update(safe, int(weight))
        self.item_updates += int(weight)

    def estimate(self, item: str) -> int:
        return int(self._sketch.get_estimate(str(item)[:2048]))

    def merge(self, other: "FrequencySketch") -> None:
        if (
            self.metric_name != other.metric_name
            or self.scope != other.scope
            or self.lg_max_k != other.lg_max_k
        ):
            raise ValueError("incompatible frequency sketches")
        self._sketch.merge(other._sketch)
        self.item_updates += other.item_updates

    def frequent_items(
        self, *, no_false_negatives: bool = True, threshold: int = 0
    ) -> tuple[HeavyHitter, ...]:
        mode = (
            datasketches.frequent_items_error_type.NO_FALSE_NEGATIVES
            if no_false_negatives
            else datasketches.frequent_items_error_type.NO_FALSE_POSITIVES
        )
        rows = self._sketch.get_frequent_items(mode, int(threshold))
        return tuple(
            HeavyHitter(str(item), int(estimate), int(lower), int(upper))
            for item, estimate, lower, upper in rows
        )

    def serialize(self) -> bytes:
        return pack_envelope(
            {
                "metric_name": self.metric_name,
                "scope": self.scope,
                "mode": "FREQUENCY",
                "sketch_kind": "frequent_strings",
                "parameters": {"lg_max_k": self.lg_max_k},
                "key_version": 0,
                "library": "apache-datasketches",
                "library_version": version("datasketches"),
                "item_updates": self.item_updates,
            },
            self._sketch.serialize(),
        )

    @classmethod
    def deserialize(cls, data: bytes) -> "FrequencySketch":
        metadata, blob = unpack_envelope(data)
        parameters = metadata.get("parameters") or {}
        instance = cls(
            str(metadata["metric_name"]),
            str(metadata["scope"]),
            lg_max_k=int(parameters["lg_max_k"]),
        )
        instance._sketch = datasketches.frequent_strings_sketch.deserialize(blob)
        instance.item_updates = int(metadata.get("item_updates") or 0)
        return instance
