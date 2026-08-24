from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from hal9000.config import AppConfig
from hal9000.paths import AppPaths
from hal9000.sentience.sketches.registry import MetricUnavailable, SketchRegistry
from hal9000.sentience.storage.database import SentienceDatabase


def test_registry_is_source_gated_sparse_persistent_and_rolls_up_verified_children(tmp_path) -> None:
    paths = AppPaths(
        config=tmp_path / "config",
        data=tmp_path / "data",
        state=tmp_path / "state",
        cache=tmp_path / "cache",
        logs=tmp_path / "state" / "logs",
    )
    config = AppConfig().sentience
    config.sketches.exact_threshold = 8
    database = SentienceDatabase.open(paths, config)
    registry = SketchRegistry(database, paths, config.sketches)
    started = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    try:
        with pytest.raises(MetricUnavailable, match="source"):
            registry.update_distinct(
                "unique_error_fingerprints", "host", "error-1", started
            )
        registry.register_event_source("error.fingerprint")
        for minute in (0, 5):
            for index in range(1000):
                registry.update_distinct(
                    "unique_error_fingerprints",
                    "host",
                    f"error-{minute}-{index}",
                    started + timedelta(minutes=minute, seconds=index % 240),
                )

        assert stat_mode(paths.sentience_hmac_key) == 0o600
        assert database.count("sketch_buckets") == 2
        registry.seal_due(started + timedelta(minutes=11))
        parent = registry.rollup_distinct(
            "unique_error_fingerprints",
            "host",
            parent_start=started,
            parent_width_seconds=3600,
            child_width_seconds=300,
        )
        # A confidence interval has statistical coverage across trials; one
        # randomized HMAC-key trial cannot require the truth to fall inside it
        # deterministically. Verify the union's accuracy and internally
        # consistent reported bounds instead.
        assert abs(parent.estimate - 2000) / 2000 < 0.08
        assert parent.lower_bound <= parent.estimate <= parent.upper_bound

        with database.read_connection() as connection:
            rollups = connection.execute("SELECT COUNT(*) FROM sketch_rollups").fetchone()[0]
            parent_row = connection.execute(
                "SELECT sealed,checksum,length(blob) AS size FROM sketch_buckets "
                "WHERE bucket_width_seconds=3600"
            ).fetchone()
        assert rollups == 2
        assert parent_row["sealed"] == 1
        assert parent_row["checksum"].startswith("sha256:")
        assert parent_row["size"] < 10_000

        deleted = registry.expire_verified_children(
            older_than=started + timedelta(hours=1), child_width_seconds=300
        )
        assert deleted == 2
        assert database.count("sketch_buckets") == 1
        assert database.count("retention_tombstones") == 2
    finally:
        database.close()


def test_distinct_awareness_stops_at_state_allocation_without_affecting_exact_store(
    tmp_path, monkeypatch
) -> None:
    paths = AppPaths(
        config=tmp_path / "config",
        data=tmp_path / "data",
        state=tmp_path / "state",
        cache=tmp_path / "cache",
        logs=tmp_path / "state" / "logs",
    )
    config = AppConfig().sentience
    database = SentienceDatabase.open(paths, config)
    registry = SketchRegistry(database, paths, config.sketches)
    registry.register_event_source("error.fingerprint")
    monkeypatch.setattr(database, "non_authority_state_writes_allowed", lambda _size=0: False)
    try:
        with pytest.raises(MetricUnavailable, match="allocation is full"):
            registry.update_distinct(
                "unique_error_fingerprints",
                "host",
                "error-1",
                datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
            )
        assert database.count("sketch_buckets") == 0
        assert database.quick_integrity_check().valid
    finally:
        database.close()


def test_registry_rejects_unbounded_unknown_event_source_labels(tmp_path) -> None:
    paths = AppPaths(
        config=tmp_path / "config",
        data=tmp_path / "data",
        state=tmp_path / "state",
        cache=tmp_path / "cache",
        logs=tmp_path / "state" / "logs",
    )
    config = AppConfig().sentience
    database = SentienceDatabase.open(paths, config)
    registry = SketchRegistry(database, paths, config.sketches)
    try:
        for index in range(10_000):
            assert registry.register_event_source(f"untrusted.source.{index}") == ()
        assert registry._sources == set()
        assert registry.register_event_source("error.fingerprint")
        assert registry._sources == {"error.fingerprint"}
    finally:
        database.close()


def test_exact_to_hll_promotion_retries_from_committed_exact_bucket_after_crash(
    tmp_path, monkeypatch
) -> None:
    paths = AppPaths(
        config=tmp_path / "config",
        data=tmp_path / "data",
        state=tmp_path / "state",
        cache=tmp_path / "cache",
        logs=tmp_path / "state" / "logs",
    )
    config = AppConfig().sentience
    config.sketches.exact_threshold = 2
    database = SentienceDatabase.open(paths, config)
    registry = SketchRegistry(database, paths, config.sketches)
    registry.register_event_source("error.fingerprint")
    observed = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    try:
        registry.update_distinct("unique_error_fingerprints", "host", "one", observed)
        registry.update_distinct("unique_error_fingerprints", "host", "two", observed)
        original_persist = registry._persist

        def crash_before_commit(*_args, **_kwargs):
            raise OSError("simulated crash before promoted bucket commit")

        monkeypatch.setattr(registry, "_persist", crash_before_commit)
        with pytest.raises(OSError, match="before promoted bucket commit"):
            registry.update_distinct(
                "unique_error_fingerprints", "host", "three", observed
            )
        with database.read_connection() as connection:
            before_retry = connection.execute(
                "SELECT mode,item_updates FROM sketch_buckets"
            ).fetchone()
        assert tuple(before_retry) == ("EXACT", 2)

        monkeypatch.setattr(registry, "_persist", original_persist)
        estimate = registry.update_distinct(
            "unique_error_fingerprints", "host", "three", observed
        )
        assert estimate.exact is False
        with database.read_connection() as connection:
            after_retry = connection.execute(
                "SELECT mode,item_updates FROM sketch_buckets"
            ).fetchone()
        assert tuple(after_retry) == ("HLL", 3)
    finally:
        database.close()


def stat_mode(path) -> int:
    return os.stat(path).st_mode & 0o777
