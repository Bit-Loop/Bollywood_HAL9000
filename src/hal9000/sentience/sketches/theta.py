"""Apache DataSketches Theta set relations; never HLL subtraction."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib.metadata import version

try:
    import datasketches
except ImportError:  # exact control remains available without approximate awareness
    datasketches = None

from hal9000.sentience.models import CardinalityEstimate
from hal9000.sentience.sketches.hybrid_distinct import (
    IncompatibleSketchError,
    keyed_item_digest,
)
from hal9000.sentience.sketches.serialization import pack_envelope, unpack_envelope


@dataclass(frozen=True, slots=True)
class RelationshipEstimate:
    estimate: float
    lower_bound: float
    upper_bound: float
    operation: str
    exact: bool


class ThetaSetSketch:
    def __init__(
        self,
        metric_name: str,
        scope: str,
        *,
        hmac_key: bytes,
        key_version: int,
        lg_k: int = 12,
        seed: int = 9001,
    ) -> None:
        if datasketches is None:
            raise RuntimeError("Apache DataSketches Theta support is unavailable")
        if not 5 <= lg_k <= 26:
            raise ValueError("Theta lg_k must be between 5 and 26")
        self.metric_name = metric_name
        self.scope = scope
        self.key_version = int(key_version)
        self.lg_k = int(lg_k)
        self.seed = int(seed)
        self._key = bytes(hmac_key)
        self._key_id = hashlib.sha256(self._key).hexdigest()[:16]
        self._update = datasketches.update_theta_sketch(lg_k=self.lg_k, seed=self.seed)
        self._base = None
        self._new_updates = 0
        self.item_updates = 0

    def update(self, item: bytes | str | int) -> None:
        digest = keyed_item_digest(item, metric_name=self.metric_name, key=self._key)
        self._update.update(digest.hex())
        self._new_updates += 1
        self.item_updates += 1

    def _sketch(self):
        if self._base is None:
            return self._update.compact()
        if self._new_updates == 0:
            return self._base
        current = self._update.compact()
        union = datasketches.theta_union(lg_k=self.lg_k, seed=self.seed)
        union.update(self._base)
        union.update(current)
        return union.get_result()

    def _assert_compatible(self, other: "ThetaSetSketch") -> None:
        if self.key_version != other.key_version or self._key_id != other._key_id:
            raise IncompatibleSketchError("Theta key version or identity differs")
        if (
            self.metric_name != other.metric_name
            or self.scope != other.scope
            or self.seed != other.seed
        ):
            raise IncompatibleSketchError("Theta metric, scope, or seed differs")

    @staticmethod
    def _relationship(sketch, operation: str) -> RelationshipEstimate:
        return RelationshipEstimate(
            float(sketch.get_estimate()),
            float(sketch.get_lower_bound(2)),
            float(sketch.get_upper_bound(2)),
            operation,
            not bool(sketch.is_estimation_mode()),
        )

    def estimate(self) -> CardinalityEstimate:
        sketch = self._sketch()
        exact = not bool(sketch.is_estimation_mode())
        return CardinalityEstimate(
            float(sketch.get_estimate()),
            exact,
            float(sketch.get_lower_bound(2)),
            float(sketch.get_upper_bound(2)),
            self.item_updates,
            "THETA",
            self.metric_name,
            "",
            {"lg_k": self.lg_k, "seed": self.seed},
        )

    def union(self, other: "ThetaSetSketch") -> RelationshipEstimate:
        self._assert_compatible(other)
        operation = datasketches.theta_union(lg_k=min(self.lg_k, other.lg_k), seed=self.seed)
        operation.update(self._sketch())
        operation.update(other._sketch())
        return self._relationship(operation.get_result(), "union")

    def merge(self, other: "ThetaSetSketch") -> None:
        """Mutating union used only for verified time-bucket roll-ups."""

        self._assert_compatible(other)
        operation = datasketches.theta_union(
            lg_k=min(self.lg_k, other.lg_k), seed=self.seed
        )
        operation.update(self._sketch())
        operation.update(other._sketch())
        self._base = operation.get_result()
        self._update = datasketches.update_theta_sketch(
            lg_k=self.lg_k, seed=self.seed
        )
        self._new_updates = 0
        self.item_updates += other.item_updates

    def intersection(self, other: "ThetaSetSketch") -> RelationshipEstimate:
        self._assert_compatible(other)
        operation = datasketches.theta_intersection(seed=self.seed)
        operation.update(self._sketch())
        operation.update(other._sketch())
        return self._relationship(operation.get_result(), "intersection")

    def difference(self, other: "ThetaSetSketch") -> RelationshipEstimate:
        self._assert_compatible(other)
        result = datasketches.theta_a_not_b(seed=self.seed).compute(
            self._sketch(), other._sketch()
        )
        return self._relationship(result, "a_not_b")

    def jaccard(self, other: "ThetaSetSketch") -> RelationshipEstimate:
        self._assert_compatible(other)
        lower, estimate, upper = datasketches.theta_jaccard_similarity.jaccard(
            self._sketch(), other._sketch(), self.seed
        )
        return RelationshipEstimate(float(estimate), float(lower), float(upper), "jaccard", False)

    def serialize(self) -> bytes:
        sketch = self._sketch()
        return pack_envelope(
            {
                "metric_name": self.metric_name,
                "scope": self.scope,
                "mode": "THETA",
                "sketch_kind": "theta",
                "parameters": {"lg_k": self.lg_k, "seed": self.seed},
                "key_version": self.key_version,
                "key_id": self._key_id,
                "library": "apache-datasketches",
                "library_version": version("datasketches"),
                "item_updates": self.item_updates,
                "estimate": float(sketch.get_estimate()),
                "lower_bound": float(sketch.get_lower_bound(2)),
                "upper_bound": float(sketch.get_upper_bound(2)),
            },
            sketch.serialize(compress=True),
        )

    @classmethod
    def deserialize(cls, data: bytes, *, hmac_key: bytes) -> "ThetaSetSketch":
        metadata, blob = unpack_envelope(data)
        parameters = metadata.get("parameters") or {}
        instance = cls(
            str(metadata["metric_name"]),
            str(metadata["scope"]),
            hmac_key=hmac_key,
            key_version=int(metadata["key_version"]),
            lg_k=int(parameters["lg_k"]),
            seed=int(parameters["seed"]),
        )
        if metadata.get("key_id") != instance._key_id:
            raise IncompatibleSketchError("serialized Theta key identity differs")
        instance._base = datasketches.compact_theta_sketch.deserialize(blob, instance.seed)
        instance.item_updates = int(metadata.get("item_updates") or 0)
        return instance
