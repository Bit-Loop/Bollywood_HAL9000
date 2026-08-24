from __future__ import annotations

import math

import pytest

from hal9000.sentience.sketches.frequency import FrequencySketch
from hal9000.sentience.sketches.hybrid_distinct import (
    HybridDistinctBucket,
    HybridMode,
    IncompatibleSketchError,
)
from hal9000.sentience.sketches.quantiles import QuantileSketch
from hal9000.sentience.sketches.sampling import BoundedRepresentativeSampler
from hal9000.sentience.sketches.theta import ThetaSetSketch
from hal9000.sentience.models import Severity

KEY = bytes.fromhex("11" * 32)


def distinct(*, key_version: int = 1, threshold: int = 32) -> HybridDistinctBucket:
    return HybridDistinctBucket(
        metric_name="unique_error_fingerprints",
        scope="host",
        bucket_start="2026-08-24T12:00:00Z",
        bucket_end="2026-08-24T12:05:00Z",
        hmac_key=KEY,
        key_version=key_version,
        exact_threshold=threshold,
        exact_bytes_limit=4096,
        hll_lg_k=12,
        hll_target_type="HLL_4",
    )


def test_hybrid_distinct_exact_promotion_bounds_and_serialization_round_trip() -> None:
    bucket = distinct()
    for index in range(32):
        bucket.update(f"failure-{index}")
    assert bucket.mode is HybridMode.EXACT
    assert bucket.estimate().estimate == 32

    bucket.update("failure-32")
    assert bucket.mode is HybridMode.HLL
    for index in range(33, 10_000):
        bucket.update(f"failure-{index}")
    estimate = bucket.estimate()
    assert estimate.exact is False
    assert estimate.lower_bound <= 10_000 <= estimate.upper_bound
    assert abs(estimate.estimate - 10_000) / 10_000 < 0.05
    assert len(bucket.serialize()) < 10_000

    restored = HybridDistinctBucket.deserialize(bucket.serialize(), hmac_key=KEY)
    assert restored.mode is HybridMode.HLL
    assert restored.is_compatible(bucket)
    assert restored.estimate().estimate == pytest.approx(estimate.estimate)
    restored.update("one-more")


def test_hll_union_is_real_union_and_incompatible_keys_are_rejected() -> None:
    first = distinct()
    second = distinct()
    for index in range(5_000):
        first.update(index)
    for index in range(2_500, 7_500):
        second.update(index)
    merged = first.merge(second)
    assert merged.lower_bound <= 7_500 <= merged.upper_bound

    incompatible = distinct(key_version=2)
    incompatible.update("x")
    with pytest.raises(IncompatibleSketchError, match="key version"):
        first.merge(incompatible)


def test_theta_performs_union_intersection_difference_and_jaccard() -> None:
    current = ThetaSetSketch("errors", "host", hmac_key=KEY, key_version=1, lg_k=12)
    baseline = ThetaSetSketch("errors", "host", hmac_key=KEY, key_version=1, lg_k=12)
    for index in range(10_000):
        baseline.update(f"error-{index}")
    for index in range(5_000, 15_000):
        current.update(f"error-{index}")

    union = baseline.union(current)
    intersection = baseline.intersection(current)
    novel = current.difference(baseline)
    assert abs(union.estimate - 15_000) / 15_000 < 0.08
    assert abs(intersection.estimate - 5_000) / 5_000 < 0.1
    assert abs(novel.estimate - 5_000) / 5_000 < 0.1
    assert baseline.jaccard(current).estimate == pytest.approx(1 / 3, abs=0.06)

    restored = ThetaSetSketch.deserialize(current.serialize(), hmac_key=KEY)
    assert restored.estimate().estimate == pytest.approx(current.estimate().estimate)


def test_frequent_items_returns_dominant_values_with_bounds() -> None:
    sketch = FrequencySketch("failing_services", "host", lg_max_k=8)
    for _ in range(6000):
        sketch.update("docker.service")
    for _ in range(2500):
        sketch.update("network.service")
    for index in range(1500):
        sketch.update(f"tail-{index}")

    items = sketch.frequent_items(no_false_negatives=True)
    assert items[0].item == "docker.service"
    assert items[0].lower_bound <= 6000 <= items[0].upper_bound
    assert any(item.item == "network.service" for item in items[:4])
    restored = FrequencySketch.deserialize(sketch.serialize())
    assert restored.estimate("docker.service") >= 6000


def test_kll_quantiles_are_bounded_and_unknown_until_enough_samples() -> None:
    sketch = QuantileSketch("model_latency_ms", "host", k=200, minimum_samples=20)
    for value in range(10):
        sketch.update(float(value))
    assert sketch.summary().known is False
    for value in range(10, 100_000):
        sketch.update(float(value))

    summary = sketch.summary()
    assert summary.known is True
    assert summary.p50 == pytest.approx(50_000, rel=0.04)
    assert summary.p95 == pytest.approx(95_000, rel=0.04)
    assert summary.p99 == pytest.approx(99_000, rel=0.04)
    assert len(sketch.serialize()) < 20_000
    assert sketch.num_retained < 2_000


def test_non_finite_quantile_inputs_are_rejected() -> None:
    sketch = QuantileSketch("resource_cpu", "host")
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="finite"):
            sketch.update(value)


def test_missing_datasketches_never_falls_back_to_an_unbounded_exact_set(monkeypatch) -> None:
    import hal9000.sentience.sketches.hybrid_distinct as module

    monkeypatch.setattr(module, "DATASKETCHES", None)
    bucket = distinct(threshold=8)
    for index in range(100_000):
        bucket.update(f"failure-{index}")

    estimate = bucket.estimate()
    assert bucket.mode is HybridMode.UNAVAILABLE
    assert bucket.exact_memory_bytes <= 9 * bucket._EXACT_ENTRY_BYTES
    assert estimate.exact is False
    assert estimate.upper_bound is None
    assert "unavailable" in bucket.degraded_reason


def test_representative_sampler_retains_first_latest_highest_and_bounded_uniform_set() -> None:
    first = BoundedRepresentativeSampler(8, "test-stream")
    second = BoundedRepresentativeSampler(8, "test-stream")
    for index in range(10_000):
        severity = Severity.CRITICAL if index == 4_321 else Severity.INFO
        first.update(index, f"2026-08-24T12:00:{index % 60:02d}Z", severity)
        second.update(index, f"2026-08-24T12:00:{index % 60:02d}Z", severity)

    samples = first.samples()
    assert first.count == 10_000
    assert len(samples) <= 11
    assert first.first.value == 0
    assert first.latest.value == 9_999
    assert first.highest.value == 4_321
    assert samples == second.samples()
