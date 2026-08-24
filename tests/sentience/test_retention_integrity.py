from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hal9000.config import AppConfig
from hal9000.paths import AppPaths
from hal9000.sentience.models import RetentionClass, Sensitivity
from hal9000.sentience.storage.blob_store import BlobStore
from hal9000.sentience.storage.database import SentienceDatabase
from hal9000.sentience.storage.integrity import StorageIntegrityService
from hal9000.sentience.storage.retention import RetentionPolicyEngine


def test_retention_dry_run_then_cleanup_preserves_pinned_and_forever_data(tmp_path) -> None:
    paths = AppPaths(
        config=tmp_path / "config",
        data=tmp_path / "data",
        state=tmp_path / "state",
        cache=tmp_path / "cache",
        logs=tmp_path / "state" / "logs",
    )
    config = AppConfig().sentience
    database = SentienceDatabase.open(paths, config)
    blobs = BlobStore(paths.sentience_blob_root, database)
    retention = RetentionPolicyEngine(database, blobs, config.storage)
    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    old = (now - timedelta(days=40)).isoformat().replace("+00:00", "Z")
    try:
        protected = blobs.put_text(
            "verified consequential output",
            mime_type="text/plain",
            sensitivity=Sensitivity.INTERNAL,
            retention_class=RetentionClass.FOREVER,
            pin=True,
        )
        disposable = blobs.put_text(
            "old routine telemetry",
            mime_type="text/plain",
            sensitivity=Sensitivity.INTERNAL,
            retention_class=RetentionClass.SHORT,
        )
        with database.transaction() as connection:
            connection.execute(
                "UPDATE payload_refs SET created_at=?,last_accessed_at=? WHERE digest=?",
                (old, old, disposable.digest),
            )
            connection.execute(
                "INSERT INTO event_runs(run_id,fingerprint,source,type,subject,first_seen,last_seen,"
                "count,severity_max,coalescing_epoch,normalized_template,retention_class,sensitivity) "
                "VALUES('old-run','sha256:old','journald','message','unit',?,?,10,'info',?,"
                "'old repeated event','short','internal')",
                (old, old, old),
            )

        preview = retention.run(now=now, dry_run=True)
        assert preview.candidate_count >= 2
        assert database.count("event_runs") == 1
        assert database.count("payload_refs") == 2

        applied = retention.run(now=now, dry_run=False)
        assert applied.deleted_count >= 2
        assert database.count("event_runs") == 0
        assert database.count("payload_refs") == 1
        assert blobs.get(protected.digest) == b"verified consequential output"
        assert not (paths.sentience_blob_root / disposable.relative_path).exists()
        assert database.count("retention_tombstones") >= 2
    finally:
        database.close()


def test_integrity_reconciles_orphans_without_trusting_missing_blobs(tmp_path) -> None:
    paths = AppPaths(
        config=tmp_path / "config",
        data=tmp_path / "data",
        state=tmp_path / "state",
        cache=tmp_path / "cache",
        logs=tmp_path / "state" / "logs",
    )
    config = AppConfig().sentience
    database = SentienceDatabase.open(paths, config)
    blobs = BlobStore(paths.sentience_blob_root, database)
    integrity = StorageIntegrityService(database, blobs)
    try:
        reference = blobs.put_text(
            "evidence",
            mime_type="text/plain",
            sensitivity=Sensitivity.INTERNAL,
            retention_class=RetentionClass.LONG,
        )
        orphan = paths.sentience_blob_root / "aa" / "bb" / ("a" * 64 + ".zst")
        orphan.parent.mkdir(parents=True)
        orphan.write_bytes(b"not trusted")
        (paths.sentience_blob_root / reference.relative_path).unlink()

        report = integrity.check(full_blobs=True, delete_orphans=True)
        assert report.database_valid is True
        assert report.control_chain_valid is True
        assert report.fts_valid is True
        assert report.missing_blobs == 1
        assert report.orphan_blobs == 1
        assert report.orphans_deleted == 1
        assert not orphan.exists()
        with database.read_connection() as connection:
            assert connection.execute(
                "SELECT missing FROM payload_refs WHERE digest=?", (reference.digest,)
            ).fetchone()[0] == 1
    finally:
        database.close()
