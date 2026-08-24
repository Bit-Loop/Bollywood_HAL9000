"""Bounded exact-small-set to Apache DataSketches HLL promotion."""

from __future__ import annotations

import hashlib
import hmac
import unicodedata
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from hal9000.sentience.models import CardinalityEstimate
from hal9000.sentience.sketches.serialization import pack_envelope, unpack_envelope

try:
    import datasketches as DATASKETCHES
except ImportError:  # guarded bounded degraded mode
    DATASKETCHES = None


class HybridMode(StrEnum):
    EXACT = "EXACT"
    HLL = "HLL"
    UNAVAILABLE = "UNAVAILABLE"


class IncompatibleSketchError(ValueError):
    pass


class SketchUnavailableError(RuntimeError):
    pass


def canonical_item(item: bytes | str | int) -> bytes:
    if isinstance(item, bytes):
        return b"bytes:\0" + item
    if isinstance(item, str):
        return b"str:\0" + unicodedata.normalize("NFC", item).encode("utf-8")
    if isinstance(item, bool):
        return b"bool:\0" + (b"1" if item else b"0")
    if isinstance(item, int):
        return b"int:\0" + str(item).encode("ascii")
    raise TypeError("distinct sketch items must be bytes, str, or int")


def keyed_item_digest(
    item: bytes | str | int, *, metric_name: str, key: bytes, schema_version: int = 1
) -> bytes:
    if len(key) < 16:
        raise ValueError("sketch HMAC key must contain at least 128 bits")
    namespace = f"{metric_name}\0v{schema_version}\0".encode()
    return hmac.new(key, namespace + canonical_item(item), hashlib.sha256).digest()[:16]


def _library_version() -> str:
    try:
        return version("datasketches")
    except PackageNotFoundError:
        return "unavailable"


class HybridDistinctBucket:
    _EXACT_ENTRY_BYTES = 48

    def __init__(
        self,
        *,
        metric_name: str,
        scope: str,
        bucket_start: str,
        bucket_end: str,
        hmac_key: bytes,
        key_version: int,
        exact_threshold: int = 512,
        exact_bytes_limit: int = 32_768,
        hll_lg_k: int = 12,
        hll_target_type: str = "HLL_4",
    ) -> None:
        if exact_threshold < 1 or exact_bytes_limit < self._EXACT_ENTRY_BYTES:
            raise ValueError("exact distinct limits must be positive and bounded")
        if not 4 <= hll_lg_k <= 21:
            raise ValueError("HLL lg_k must be between 4 and 21")
        if hll_target_type not in {"HLL_4", "HLL_6", "HLL_8"}:
            raise ValueError("unsupported HLL target type")
        self.metric_name = metric_name
        self.scope = scope
        self.bucket_start = bucket_start
        self.bucket_end = bucket_end
        self.key_version = int(key_version)
        self.exact_threshold = int(exact_threshold)
        self.exact_bytes_limit = int(exact_bytes_limit)
        self.hll_lg_k = int(hll_lg_k)
        self.hll_target_type = hll_target_type
        self._key = bytes(hmac_key)
        self._key_id = hashlib.sha256(self._key).hexdigest()[:16]
        self._mode = HybridMode.EXACT
        self._exact: set[bytes] = set()
        self._hll = None
        self.item_updates = 0
        self.degraded_reason: str | None = None

    @property
    def mode(self) -> HybridMode:
        return self._mode

    @property
    def exact_memory_bytes(self) -> int:
        return len(self._exact) * self._EXACT_ENTRY_BYTES

    def update(self, item: bytes | str | int) -> None:
        digest = keyed_item_digest(item, metric_name=self.metric_name, key=self._key)
        self.item_updates += 1
        if self._mode is HybridMode.HLL:
            self._hll.update(digest.hex())
            return
        if self._mode is HybridMode.UNAVAILABLE:
            return
        self._exact.add(digest)
        if len(self._exact) > self.exact_threshold or self.exact_memory_bytes > self.exact_bytes_limit:
            self._promote()

    def _promote(self) -> None:
        if self._mode is not HybridMode.EXACT:
            return
        if DATASKETCHES is None:
            self._mode = HybridMode.UNAVAILABLE
            self.degraded_reason = "apache-datasketches is unavailable; exact set capped"
            return
        target = getattr(DATASKETCHES.tgt_hll_type, self.hll_target_type)
        sketch = DATASKETCHES.hll_sketch(self.hll_lg_k, target)
        for digest in self._exact:
            sketch.update(digest.hex())
        estimate = float(sketch.get_estimate())
        if self._exact and not (len(self._exact) * 0.5 <= estimate <= len(self._exact) * 2):
            raise RuntimeError("HLL promotion verification produced an implausible estimate")
        self._hll = sketch
        self._mode = HybridMode.HLL
        self._exact.clear()

    def estimate(self) -> CardinalityEstimate:
        if self._mode is HybridMode.EXACT:
            count = float(len(self._exact))
            return CardinalityEstimate(
                count,
                True,
                count,
                count,
                self.item_updates,
                self._mode.value,
                self.metric_name,
                f"{self.bucket_start}/{self.bucket_end}",
                self.parameters,
            )
        if self._mode is HybridMode.UNAVAILABLE:
            floor = float(len(self._exact))
            return CardinalityEstimate(
                floor,
                False,
                floor,
                None,
                self.item_updates,
                self._mode.value,
                self.metric_name,
                f"{self.bucket_start}/{self.bucket_end}",
                self.parameters,
            )
        return CardinalityEstimate(
            float(self._hll.get_estimate()),
            False,
            float(self._hll.get_lower_bound(2)),
            float(self._hll.get_upper_bound(2)),
            self.item_updates,
            self._mode.value,
            self.metric_name,
            f"{self.bucket_start}/{self.bucket_end}",
            self.parameters,
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "exact_threshold": self.exact_threshold,
            "exact_bytes_limit": self.exact_bytes_limit,
            "hll_lg_k": self.hll_lg_k,
            "hll_target_type": self.hll_target_type,
        }

    def is_compatible(self, other: "HybridDistinctBucket") -> bool:
        return (
            isinstance(other, HybridDistinctBucket)
            and self.metric_name == other.metric_name
            and self.scope == other.scope
            and self.key_version == other.key_version
            and self._key_id == other._key_id
            and self.hll_lg_k == other.hll_lg_k
            and self.hll_target_type == other.hll_target_type
        )

    def _assert_compatible(self, other: "HybridDistinctBucket") -> None:
        if self.key_version != other.key_version or self._key_id != other._key_id:
            raise IncompatibleSketchError("sketch key version or key identity differs")
        if not self.is_compatible(other):
            raise IncompatibleSketchError("sketch metric, scope, or HLL parameters differ")

    def merge(self, other: "HybridDistinctBucket") -> CardinalityEstimate:
        self._assert_compatible(other)
        if HybridMode.UNAVAILABLE in {self._mode, other._mode}:
            self._mode = HybridMode.UNAVAILABLE
            self.degraded_reason = "cannot merge an unavailable distinct bucket"
            return self.estimate()
        self.item_updates += other.item_updates
        if self._mode is HybridMode.EXACT and other._mode is HybridMode.EXACT:
            self._exact.update(other._exact)
            if len(self._exact) > self.exact_threshold or self.exact_memory_bytes > self.exact_bytes_limit:
                self._promote()
            return self.estimate()
        if DATASKETCHES is None:
            self._mode = HybridMode.UNAVAILABLE
            self.degraded_reason = "apache-datasketches unavailable during HLL union"
            return self.estimate()
        if self._mode is HybridMode.EXACT:
            self._promote()
        union = DATASKETCHES.hll_union(self.hll_lg_k)
        union.update(self._hll)
        if other._mode is HybridMode.HLL:
            union.update(other._hll)
        else:
            for digest in other._exact:
                union.update(digest.hex())
        self._hll = union.get_result(
            getattr(DATASKETCHES.tgt_hll_type, self.hll_target_type)
        )
        self._mode = HybridMode.HLL
        return self.estimate()

    def serialize(self) -> bytes:
        if self._mode is HybridMode.HLL:
            blob = self._hll.serialize_compact()
        else:
            blob = b"".join(sorted(self._exact))
        estimate = self.estimate()
        return pack_envelope(
            {
                "metric_name": self.metric_name,
                "scope": self.scope,
                "bucket_start": self.bucket_start,
                "bucket_end": self.bucket_end,
                "mode": self._mode.value,
                "sketch_kind": "hll" if self._mode is HybridMode.HLL else "exact_set",
                "parameters": self.parameters,
                "key_version": self.key_version,
                "key_id": self._key_id,
                "library": "apache-datasketches",
                "library_version": _library_version(),
                "item_updates": self.item_updates,
                "estimate": estimate.estimate,
                "lower_bound": estimate.lower_bound,
                "upper_bound": estimate.upper_bound,
                "degraded_reason": self.degraded_reason,
            },
            blob,
        )

    @classmethod
    def deserialize(cls, data: bytes, *, hmac_key: bytes) -> "HybridDistinctBucket":
        metadata, blob = unpack_envelope(data)
        parameters = metadata.get("parameters") or {}
        instance = cls(
            metric_name=str(metadata["metric_name"]),
            scope=str(metadata["scope"]),
            bucket_start=str(metadata["bucket_start"]),
            bucket_end=str(metadata["bucket_end"]),
            hmac_key=hmac_key,
            key_version=int(metadata["key_version"]),
            exact_threshold=int(parameters["exact_threshold"]),
            exact_bytes_limit=int(parameters["exact_bytes_limit"]),
            hll_lg_k=int(parameters["hll_lg_k"]),
            hll_target_type=str(parameters["hll_target_type"]),
        )
        if metadata.get("key_id") != instance._key_id:
            raise IncompatibleSketchError("serialized sketch key identity differs")
        mode = HybridMode(str(metadata["mode"]))
        if mode is HybridMode.HLL:
            if DATASKETCHES is None:
                raise SketchUnavailableError("apache-datasketches is required to load HLL")
            instance._hll = DATASKETCHES.hll_sketch.deserialize(blob)
            instance._mode = mode
        else:
            if len(blob) % 16:
                raise ValueError("corrupt exact distinct member encoding")
            members = {blob[offset : offset + 16] for offset in range(0, len(blob), 16)}
            if len(members) > instance.exact_threshold + 1 or len(members) * instance._EXACT_ENTRY_BYTES > max(
                instance.exact_bytes_limit + instance._EXACT_ENTRY_BYTES,
                instance.exact_bytes_limit * 2,
            ):
                raise ValueError("serialized exact distinct set exceeds its bound")
            instance._exact = members
            instance._mode = mode
        instance.item_updates = int(metadata.get("item_updates") or 0)
        instance.degraded_reason = metadata.get("degraded_reason")
        return instance
