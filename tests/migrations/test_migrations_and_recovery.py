from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from hal9000.config import AppConfig
from hal9000.paths import AppPaths
from hal9000.sentience.event_envelope import EventEnvelope, utc_iso
from hal9000.sentience.identity.continuity import ContinuityService
from hal9000.sentience.identity.lease import CanonicalLease
from hal9000.sentience.identity.service import IdentityService
from hal9000.sentience.memory.facts import FactStore
from hal9000.sentience.models import (
    EventOrigin,
    RetentionClass,
    Sensitivity,
    Severity,
)
from hal9000.sentience.retrieval.planner import MemoryQuery, MemoryRetriever
from hal9000.sentience.storage.checkpoints import ProjectionCheckpointService
from hal9000.sentience.storage.database import SentienceDatabase


def _paths(root: Path) -> AppPaths:
    return AppPaths(
        config=root / "config",
        data=root / "data",
        state=root / "state",
        cache=root / "cache",
        logs=root / "state" / "logs",
    )


def _event(boot_id: str, subject: str) -> EventEnvelope:
    return EventEnvelope.new(
        boot_id=boot_id,
        source="test.migrations",
        event_type="test.control.recorded",
        subject=subject,
        severity=Severity.INFO,
        retention_class=RetentionClass.FOREVER,
        sensitivity=Sensitivity.INTERNAL,
        origin=EventOrigin.OBSERVATION,
        payload={"subject": subject},
        idempotency_key=f"test-control:{subject}",
    )


def test_empty_database_migration_and_fts5_unavailable_metadata_fallback(tmp_path) -> None:
    paths = _paths(tmp_path)
    settings = AppConfig().sentience
    database = SentienceDatabase(paths.sentience_database, settings)
    database._fts5_available = lambda: False  # type: ignore[method-assign]
    database._open()
    try:
        assert database.migration_version == 4
        assert "retrieval_fts" not in database.table_names()
        identity = IdentityService(database).load_or_create()
        boot = ContinuityService(database, identity.incarnation_id).start_boot()
        FactStore(database, boot.boot_id).create(
            subject="storage",
            statement="Metadata retrieval remains available without FTS5.",
            source_type=EventOrigin.OBSERVATION,
            exact=True,
            confidence=1.0,
            evidence_refs=("event:fts-probe",),
        )

        result = MemoryRetriever(database, settings.retrieval).search(
            MemoryQuery("metadata retrieval", token_budget=100)
        )
        assert result.retrieval_mode == "metadata_fallback"
        assert result.facts
    finally:
        database.close()


def test_upgrade_from_schema_v1_is_transactional_and_creates_backup(tmp_path) -> None:
    paths = _paths(tmp_path)
    paths.sentience_database.parent.mkdir(parents=True, exist_ok=True)
    migration_root = (
        Path(__file__).parents[2]
        / "src"
        / "hal9000"
        / "sentience"
        / "storage"
        / "migrations"
    )
    source = (migration_root / "0001_machine_self.sql").read_text(encoding="utf-8")
    checksum = "sha256:" + hashlib.sha256(source.encode()).hexdigest()
    raw = sqlite3.connect(paths.sentience_database)
    try:
        raw.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT NOT NULL,"
            "applied_at TEXT NOT NULL,checksum TEXT NOT NULL)"
        )
        raw.executescript(source)
        raw.execute(
            "INSERT INTO schema_migrations VALUES(1,'0001_machine_self',?,?)",
            (utc_iso(), checksum),
        )
        raw.commit()
    finally:
        raw.close()

    database = SentienceDatabase.open(paths, AppConfig().sentience)
    try:
        assert database.migration_version == 4
        assert "operational_metrics_current" in database.table_names()
        with database.read_connection() as connection:
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(degradation_episodes)")
            }
        assert "required_capabilities_json" in columns
        assert "started_monotonic_ns" in columns
        with database.read_connection() as connection:
            identity_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(identity_state)")
            }
        assert "role" in identity_columns
        backup = paths.sentience_root / "checkpoints" / "hal-state.pre-migration-v1.sqlite"
        assert backup.is_file()
        assert sqlite3.connect(backup).execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        database.close()


def test_migration_checksum_drift_is_rejected(tmp_path) -> None:
    paths = _paths(tmp_path)
    database = SentienceDatabase.open(paths, AppConfig().sentience)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE schema_migrations SET checksum='sha256:invalid' WHERE version=1"
        )
    database.close()

    reopened = SentienceDatabase(paths.sentience_database, AppConfig().sentience)
    try:
        with pytest.raises(RuntimeError, match="checksum drift"):
            reopened._open()
    finally:
        reopened.close()


def test_validated_backup_restore_has_automatic_rollback(tmp_path) -> None:
    paths = _paths(tmp_path)
    database = SentienceDatabase.open(paths, AppConfig().sentience)
    boot_id = "d5825af6-d0af-478e-9480-ee0f40ff9d51"
    try:
        first = database.append_exact_event(_event(boot_id, "before-backup"))
        backup = database.create_backup("known-good")
        second = database.append_exact_event(_event(boot_id, "after-backup"))
        assert second.sequence > first.sequence

        rollback = database.restore_backup(backup)
        assert rollback.is_file()
        assert database.read_exact_event(first.sequence) is not None
        assert database.read_exact_event(second.sequence) is None
        assert database.quick_integrity_check().valid
    finally:
        database.close()


def test_live_canonical_writer_lease_blocks_restore(tmp_path) -> None:
    paths = _paths(tmp_path)
    database = SentienceDatabase.open(paths, AppConfig().sentience)
    backup = database.create_backup("lease-test")
    lease = CanonicalLease(
        database,
        instance_id="hal-lease-test",
        boot_id="9d67ed13-fc21-4e86-a663-2daa1a423851",
        owner_id="test-writer",
        ttl_seconds=30,
    )
    try:
        lease.acquire()
        with pytest.raises(RuntimeError, match="writer lease is active"):
            database.restore_backup(backup)
    finally:
        lease.release()
        database.close()


def test_projection_checkpoint_falls_back_to_previous_valid_snapshot(tmp_path) -> None:
    paths = _paths(tmp_path)
    database = SentienceDatabase.open(paths, AppConfig().sentience)
    identity = IdentityService(database).load_or_create()
    boot = ContinuityService(database, identity.incarnation_id).start_boot()
    checkpoints = ProjectionCheckpointService(database, boot.boot_id)
    try:
        first_id = checkpoints.write()
        database.append_exact_event(_event(boot.boot_id, "between-checkpoints"))
        second_id = checkpoints.write()
        with database.transaction() as connection:
            connection.execute(
                "UPDATE projection_checkpoints SET checksum='sha256:corrupt' "
                "WHERE checkpoint_id=?",
                (second_id,),
            )

        restored = checkpoints.restore()
        assert restored.valid
        assert restored.checkpoint_id == first_id
        assert restored.events_after >= 2
        assert restored.payload is not None
        assert restored.payload["identity"]["canonical_name"] == "HAL"
    finally:
        database.close()
