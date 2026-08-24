#!/usr/bin/env python3
"""Reproducible bounded-storage and streaming-sketch host benchmark."""

from __future__ import annotations

import argparse
import gc
import json
import platform
import resource
import tempfile
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

from hal9000.config import AppConfig
from hal9000.paths import AppPaths
from hal9000.sentience.events.coalescer import EventRunCoalescer, EventRunInput
from hal9000.sentience.models import RetentionClass, Sensitivity, Severity
from hal9000.sentience.sketches.frequency import FrequencySketch
from hal9000.sentience.sketches.hybrid_distinct import HybridDistinctBucket
from hal9000.sentience.sketches.quantiles import QuantileSketch
from hal9000.sentience.sketches.registry import SketchRegistry
from hal9000.sentience.storage.database import SentienceDatabase


def _paths(root: Path) -> AppPaths:
    return AppPaths(
        config=root / "config",
        data=root / "data",
        state=root / "state",
        cache=root / "cache",
        logs=root / "state" / "logs",
    )


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * quantile))]


def _process_peak_rss_bytes() -> int:
    """Prefer Linux VmHWM; this host's getrusage value can be inconsistent."""

    status = Path("/proc/self/status")
    if status.is_file():
        for line in status.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024)


def _process_rss_bytes() -> int:
    status = Path("/proc/self/status")
    if status.is_file():
        for line in status.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    return 0


def _timed_updates(count: int, update, *, batch: int = 1000) -> tuple[float, float]:
    started = time.perf_counter()
    batch_latencies: list[float] = []
    for begin in range(0, count, batch):
        batch_started = time.perf_counter_ns()
        end = min(count, begin + batch)
        for index in range(begin, end):
            update(index)
        elapsed_ns = time.perf_counter_ns() - batch_started
        batch_latencies.append((elapsed_ns / (end - begin)) / 1000.0)
    return time.perf_counter() - started, _percentile(batch_latencies, 0.95)


def benchmark_identical_events(count: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="hal-self-events-") as temporary:
        root = Path(temporary)
        config = AppConfig().sentience
        database = SentienceDatabase.open(_paths(root), config)
        database.checkpoint_wal("TRUNCATE")
        before = database.state_storage_bytes()
        coalescer = EventRunCoalescer(database, config.ingestion, epoch_seconds=300)
        item = EventRunInput(
            source="journald",
            type="systemd.unit_restart_failed",
            subject="docker.service",
            observed_at=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
            severity=Severity.ERROR,
            task_id=None,
            normalized_template="docker.service restart failed exit=23",
            redacted_payload={"exit_code": 23},
            retention_class=RetentionClass.SHORT,
            sensitivity=Sensitivity.INTERNAL,
        )
        gc.collect()
        rss_before = _process_rss_bytes()
        runtime, p95_us = _timed_updates(count, lambda _index: coalescer.add(item))
        rss_after = _process_rss_bytes()
        coalescer.flush()
        database.checkpoint_wal("TRUNCATE")
        after = database.state_storage_bytes()
        with database.read_connection() as connection:
            row = connection.execute(
                "SELECT count(*) AS rows,sum(count) AS observations FROM event_runs"
            ).fetchone()
            samples = int(
                connection.execute("SELECT count(*) FROM event_run_samples").fetchone()[0]
            )
        coalescer.close()
        database.close()
        return {
            "input_events": count,
            "runtime_seconds": runtime,
            "throughput_events_per_second": count / runtime,
            "p95_batch_normalized_update_us": p95_us,
            "resident_bytes_before": rss_before,
            "resident_bytes_after": rss_after,
            "resident_growth_bytes": max(0, rss_after - rss_before),
            "event_run_rows": int(row["rows"]),
            "represented_observations": int(row["observations"]),
            "sample_rows": samples,
            "database_bytes_before": before,
            "database_bytes_after": after,
            "database_growth_bytes": after - before,
            "blob_growth_bytes": 0,
        }


def _hybrid(
    *,
    metric: str = "unique_error_fingerprints",
    scope: str = "host",
    start: str = "2026-08-24T12:00:00Z",
    end: str = "2026-08-24T12:05:00Z",
    key: bytes = b"H" * 32,
    key_version: int = 1,
) -> HybridDistinctBucket:
    settings = AppConfig().sentience.sketches
    return HybridDistinctBucket(
        metric_name=metric,
        scope=scope,
        bucket_start=start,
        bucket_end=end,
        hmac_key=key,
        key_version=key_version,
        exact_threshold=settings.exact_threshold,
        exact_bytes_limit=settings.exact_bytes_limit,
        hll_lg_k=settings.hll_lg_k,
        hll_target_type=settings.hll_target_type,
    )


def benchmark_distinct(count: int) -> dict:
    bucket = _hybrid()
    gc.collect()
    rss_before = _process_rss_bytes()
    runtime, p95_us = _timed_updates(count, bucket.update)
    rss_after = _process_rss_bytes()
    estimate = bucket.estimate()
    serialized = bucket.serialize()
    return {
        "input_distinct_identifiers": count,
        "runtime_seconds": runtime,
        "throughput_updates_per_second": count / runtime,
        "p95_batch_normalized_update_us": p95_us,
        "resident_bytes_before": rss_before,
        "resident_bytes_after": rss_after,
        "resident_growth_bytes": max(0, rss_after - rss_before),
        "mode": bucket.mode.value,
        "parameters": bucket.parameters,
        "estimate": estimate.estimate,
        "lower_bound": estimate.lower_bound,
        "upper_bound": estimate.upper_bound,
        "relative_error": abs(estimate.estimate - count) / count,
        "serialized_bytes": len(serialized),
        "exact_member_rows_retained": 0,
    }


def benchmark_bucket_rollup() -> dict:
    with tempfile.TemporaryDirectory(prefix="hal-self-rollup-") as temporary:
        root = Path(temporary)
        config = AppConfig().sentience
        database = SentienceDatabase.open(_paths(root), config)
        registry = SketchRegistry(database, _paths(root), config.sketches)
        registry.register_event_source("error.fingerprint")
        started = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
        child_count = 12
        child_items = 10_000
        stride = 5_000
        direct = _hybrid(
            start="2026-08-24T12:00:00Z",
            end="2026-08-24T13:00:00Z",
            key=registry.key.key,
            key_version=registry.key.version,
        )
        measured_started = time.perf_counter()
        for child_index in range(child_count):
            child_start = started + timedelta(minutes=child_index * 5)
            child_end = child_start + timedelta(minutes=5)
            child = _hybrid(
                start=child_start.isoformat().replace("+00:00", "Z"),
                end=child_end.isoformat().replace("+00:00", "Z"),
                key=registry.key.key,
                key_version=registry.key.version,
            )
            for offset in range(child_items):
                item = f"error-{child_index * stride + offset}"
                child.update(item)
                direct.update(item)
            registry._persist(child, 300, sealed=True)
        child_rows_before = database.count("sketch_buckets")
        parent = registry.rollup_distinct(
            "unique_error_fingerprints",
            "host",
            parent_start=started,
            parent_width_seconds=3600,
            child_width_seconds=300,
        )
        runtime = time.perf_counter() - measured_started
        direct_estimate = direct.estimate()
        true_cardinality = (child_count - 1) * stride + child_items
        with database.read_connection() as connection:
            parent_row = connection.execute(
                "SELECT length(blob) AS bytes,checksum FROM sketch_buckets "
                "WHERE bucket_width_seconds=3600"
            ).fetchone()
            verified_links = int(
                connection.execute("SELECT count(*) FROM sketch_rollups").fetchone()[0]
            )
        expired = registry.expire_verified_children(
            older_than=started + timedelta(hours=1), child_width_seconds=300
        )
        rows_after = database.count("sketch_buckets")
        database.close()
        return {
            "child_buckets": child_count,
            "child_updates": child_count * child_items,
            "true_union_cardinality": true_cardinality,
            "runtime_seconds": runtime,
            "parent_estimate": parent.estimate,
            "parent_lower_bound": parent.lower_bound,
            "parent_upper_bound": parent.upper_bound,
            "direct_reference_estimate": direct_estimate.estimate,
            "parent_vs_direct_relative_difference": abs(
                parent.estimate - direct_estimate.estimate
            )
            / direct_estimate.estimate,
            "parent_relative_error": abs(parent.estimate - true_cardinality)
            / true_cardinality,
            "parent_serialized_bytes": int(parent_row["bytes"]),
            "parent_checksum": str(parent_row["checksum"]),
            "verified_child_links": verified_links,
            "bucket_rows_before_expiry": child_rows_before + 1,
            "children_expired_after_verified_commit": expired,
            "bucket_rows_after_expiry": rows_after,
        }


def benchmark_quantiles(count: int) -> dict:
    sketch = QuantileSketch("model_latency", "host", k=200, minimum_samples=20)
    gc.collect()
    rss_before = _process_rss_bytes()
    runtime, p95_us = _timed_updates(
        count, lambda index: sketch.update(float(index % 100_000))
    )
    rss_after = _process_rss_bytes()
    summary = sketch.summary()
    return {
        "input_samples": count,
        "runtime_seconds": runtime,
        "throughput_updates_per_second": count / runtime,
        "p95_batch_normalized_update_us": p95_us,
        "resident_bytes_before": rss_before,
        "resident_bytes_after": rss_after,
        "resident_growth_bytes": max(0, rss_after - rss_before),
        "k": sketch.k,
        "retained_items": sketch.num_retained,
        "p50": summary.p50,
        "p95": summary.p95,
        "p99": summary.p99,
        "serialized_bytes": len(sketch.serialize()),
    }


def benchmark_frequency(count: int) -> dict:
    sketch = FrequencySketch("repeating_error_fingerprints", "host", lg_max_k=10)

    def update(index: int) -> None:
        if index < count * 0.60:
            item = "dominant"
        elif index < count * 0.85:
            item = "secondary"
        else:
            item = f"tail-{index % 1000}"
        sketch.update(item)

    runtime, p95_us = _timed_updates(count, update)
    hitters = sketch.frequent_items(no_false_negatives=True)
    return {
        "input_updates": count,
        "runtime_seconds": runtime,
        "throughput_updates_per_second": count / runtime,
        "p95_batch_normalized_update_us": p95_us,
        "serialized_bytes": len(sketch.serialize()),
        "top_heavy_hitters": [asdict(item) for item in hitters[:5]],
        "dominant_detected": bool(hitters and hitters[0].item == "dominant"),
    }


def run(count: int) -> dict:
    started = datetime.now(UTC)
    result = {
        "started_at_utc": started.isoformat().replace("+00:00", "Z"),
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "processor": platform.processor(),
            "datasketches": version("datasketches"),
        },
        "identical_event_coalescing": benchmark_identical_events(count),
        "distinct_cardinality": benchmark_distinct(count),
        "bucket_rollup": benchmark_bucket_rollup(),
        "latency_quantiles": benchmark_quantiles(count),
        "repeated_error_frequency": benchmark_frequency(count),
    }
    result["elapsed_seconds"] = (datetime.now(UTC) - started).total_seconds()
    # The host field is the process-wide high-water mark, while operation
    # fields above report Python allocator peaks where useful.
    result["process_peak_rss_bytes"] = _process_peak_rss_bytes()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1_000_000)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be positive")
    print(json.dumps(run(args.count), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
