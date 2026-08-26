"""Authoritative SQLite store with transactional migrations and exact events."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hal9000.config import SentienceSettings
from hal9000.paths import AppPaths
from hal9000.sentience.event_envelope import EventEnvelope, utc_iso
from hal9000.sentience.events.redact import bounded_redacted_record, redact_data
from hal9000.sentience.models import (
    EventOrigin,
    IntegrityResult,
    RetentionClass,
    Sensitivity,
    Severity,
    StoredEvent,
)

Projection = Callable[[sqlite3.Connection, int], None]

_KNOWN_EQUIVALENT_MIGRATION_CHECKSUMS: dict[int, frozenset[str]] = {
    2: frozenset(
        {
            "sha256:ae52ae2a0dd1d3f6ba0828b0019ece9caad3c841b61102cd616e503ffd572e45",
            "sha256:e090cfc8eda7db67ebe4432fa995eb8f7a3310a1eab5404c4071459105164ffe",
        }
    )
}


@dataclass(frozen=True, slots=True)
class StorageBudget:
    total_bytes: int
    soft_limit_bytes: int
    state_database_bytes: int
    blob_bytes: int
    checkpoint_bytes: int
    reserve_bytes: int
    derived_from_free_bytes: int


def resolve_storage_budget(root: Path, settings: SentienceSettings) -> StorageBudget:
    probe = root if root.exists() else root.parent
    probe.mkdir(parents=True, exist_ok=True, mode=0o700)
    free = shutil.disk_usage(probe).free
    configured = settings.storage.total_budget_mb
    if configured is None:
        if not settings.storage.auto_budget:
            raise ValueError("an explicit storage budget is required when auto budget is disabled")
        total = min(2 * 1024**3, max(512 * 1024**2, int(free * 0.005)))
    else:
        total = int(configured) * 1024**2
    storage = settings.storage
    return StorageBudget(
        total_bytes=total,
        soft_limit_bytes=int(total * storage.soft_limit_ratio),
        state_database_bytes=int(total * storage.state_db_ratio),
        blob_bytes=int(total * storage.blob_ratio),
        checkpoint_bytes=int(total * storage.checkpoint_ratio),
        reserve_bytes=total
        - int(total * storage.state_db_ratio)
        - int(total * storage.blob_ratio)
        - int(total * storage.checkpoint_ratio),
        derived_from_free_bytes=free,
    )


class SentienceDatabase:
    """One serialized writer plus short-lived read connections.

    Exact transition events and their materialized projections use the same
    SQLite transaction. The UI only talks to this object through service
    workers; no maintenance or large retrieval belongs on the Qt thread.
    """

    def __init__(self, path: Path, settings: SentienceSettings) -> None:
        self.path = path
        self.settings = settings
        self.root = path.parent
        self.budget = resolve_storage_budget(self.root, settings)
        self._lock = threading.RLock()
        self._writer: sqlite3.Connection | None = None

    @classmethod
    def open(cls, paths: AppPaths, settings: SentienceSettings) -> "SentienceDatabase":
        database = cls(paths.sentience_database, settings)
        database._open()
        return database

    def _open(self) -> None:
        new_database = not self.path.exists() or self.path.stat().st_size == 0
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self._writer = sqlite3.connect(
            self.path,
            timeout=self.settings.storage.busy_timeout_ms / 1000,
            isolation_level=None,
            check_same_thread=False,
        )
        self._writer.row_factory = sqlite3.Row
        self._configure(self._writer)
        if new_database:
            self._writer.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self._migrate()
        if self.path.exists():
            os.chmod(self.path, 0o600)

    def _configure(self, connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={int(self.settings.storage.busy_timeout_ms)}")
        if self.settings.storage.wal:
            connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(f"PRAGMA synchronous={self.settings.storage.synchronous}")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("PRAGMA trusted_schema=OFF")

    @property
    def writer(self) -> sqlite3.Connection:
        if self._writer is None:
            raise RuntimeError("sentience database is closed")
        return self._writer

    def _migrate(self) -> None:
        self.writer.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL, "
            "checksum TEXT NOT NULL)"
        )
        current = self.migration_version
        migration_root = Path(__file__).with_name("migrations")
        migrations: list[tuple[int, Path]] = []
        for path in sorted(migration_root.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            migrations.append((int(path.name.split("_", 1)[0]), path))
        known = {version: path for version, path in migrations}
        applied_rows = self.writer.execute(
            "SELECT version,checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
        for row in applied_rows:
            version = int(row["version"])
            path = known.get(version)
            if path is None:
                raise RuntimeError(f"applied migration {version} has no source file")
            source = path.read_bytes()
            expected = "sha256:" + hashlib.sha256(source).hexdigest()
            actual = str(row["checksum"])
            if actual != expected:
                equivalents = _KNOWN_EQUIVALENT_MIGRATION_CHECKSUMS.get(version, frozenset())
                if actual not in equivalents or expected not in equivalents:
                    raise RuntimeError(f"migration checksum drift detected at version {version}")
                self._verify_equivalent_migration_schema(version)
        pending = [(version, path) for version, path in migrations if version > current]
        if not pending:
            return
        if self.path.stat().st_size > 0 and current > 0:
            checkpoint = self.root / "checkpoints"
            checkpoint.mkdir(parents=True, exist_ok=True, mode=0o700)
            backup_path = checkpoint / f"hal-state.pre-migration-v{current}.sqlite"
            backup = sqlite3.connect(backup_path)
            try:
                self.writer.backup(backup)
                detail = str(backup.execute("PRAGMA quick_check").fetchone()[0])
                if detail != "ok":
                    raise RuntimeError(f"pre-migration backup integrity failed: {detail}")
            finally:
                backup.close()
            os.chmod(backup_path, 0o600)
        fts5_available = self._fts5_available()
        for version, path in pending:
            source_script = path.read_text(encoding="utf-8")
            script = (
                source_script
                if fts5_available
                else self._without_optional_fts5(source_script)
            )
            # Migration identity describes the source migration, not the
            # platform-specific optional statements selected at runtime.
            checksum = "sha256:" + hashlib.sha256(source_script.encode()).hexdigest()
            name = path.stem
            applied = utc_iso()
            escaped_name = name.replace("'", "''")
            escaped_applied = applied.replace("'", "''")
            escaped_checksum = checksum.replace("'", "''")
            try:
                self.writer.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + script
                    + "\nINSERT INTO schema_migrations(version, name, applied_at, checksum) "
                    + f"VALUES ({version}, '{escaped_name}', '{escaped_applied}', '{escaped_checksum}');\n"
                    + "PRAGMA optimize;\nCOMMIT;"
                )
            except BaseException:
                try:
                    self.writer.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

    def _verify_equivalent_migration_schema(self, version: int) -> None:
        """Fail closed when accepting a byte-different but known SQL migration.

        Version 2 differed only by a trailing newline in one released build.
        The alias is accepted only when the exact schema effect is present.
        """

        if version != 2:
            raise RuntimeError(
                f"migration {version} has no equivalent-schema verification rule"
            )
        columns = {
            str(row[1])
            for row in self.writer.execute("PRAGMA table_info(degradation_episodes)")
        }
        if "required_capabilities_json" not in columns:
            raise RuntimeError(
                "migration 2 checksum alias failed schema verification: "
                "degradation_episodes.required_capabilities_json is missing"
            )

    def _fts5_available(self) -> bool:
        try:
            self.writer.execute(
                "CREATE VIRTUAL TABLE temp.__hal_fts5_probe USING fts5(value)"
            )
            self.writer.execute("DROP TABLE temp.__hal_fts5_probe")
            return True
        except sqlite3.Error:
            return False

    @staticmethod
    def _without_optional_fts5(script: str) -> str:
        """Remove marked optional FTS5 blocks on minimal SQLite builds."""

        start_marker = "-- BEGIN OPTIONAL_FTS5"
        end_marker = "-- END OPTIONAL_FTS5"
        while start_marker in script:
            start = script.index(start_marker)
            end = script.find(end_marker, start)
            if end < 0:
                raise RuntimeError("unterminated optional FTS5 migration block")
            end += len(end_marker)
            script = script[:start] + script[end:]
        return script

    @property
    def migration_version(self) -> int:
        row = self.writer.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
        return int(row[0])

    def read_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.settings.storage.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        self._configure(connection)
        connection.execute("PRAGMA query_only=ON")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self.writer
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")

    def append_exact_event(
        self,
        event: EventEnvelope,
        *,
        projection: Projection | None = None,
        force_hash_chain: bool | None = None,
    ) -> StoredEvent:
        event.validate()
        redacted = event.with_payload(redact_data(event.payload))
        payload_json = json.dumps(
            redacted.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT sequence, event_id FROM exact_events "
                "WHERE event_id=? OR (? IS NOT NULL AND idempotency_key=?) ORDER BY sequence LIMIT 1",
                (redacted.event_id, redacted.idempotency_key, redacted.idempotency_key),
            ).fetchone()
            if existing is not None:
                return StoredEvent(int(existing["sequence"]), str(existing["event_id"]), False)
            should_chain = (
                redacted.retention_class is RetentionClass.FOREVER
                if force_hash_chain is None
                else force_hash_chain
            )
            previous_hash: str | None = None
            if should_chain:
                row = connection.execute(
                    "SELECT event_hash FROM exact_events WHERE event_hash IS NOT NULL "
                    "ORDER BY sequence DESC LIMIT 1"
                ).fetchone()
                previous_hash = str(row[0]) if row else None
            cursor = connection.execute(
                "INSERT INTO exact_events("
                "event_id,idempotency_key,schema_version,occurred_at_utc,received_at_utc,"
                "monotonic_ns,boot_id,source,type,subject,severity,correlation_id,causation_id,"
                "task_id,origin,observed,confidence,retention_class,sensitivity,payload_json,"
                "internal,previous_hash,event_hash"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (
                    redacted.event_id,
                    redacted.idempotency_key,
                    redacted.schema_version,
                    redacted.occurred_at_utc,
                    redacted.received_at_utc,
                    redacted.monotonic_ns,
                    redacted.boot_id,
                    redacted.source,
                    redacted.type,
                    redacted.subject,
                    redacted.severity.value,
                    redacted.correlation_id,
                    redacted.causation_id,
                    redacted.task_id,
                    redacted.origin.value,
                    int(redacted.observed),
                    redacted.confidence,
                    redacted.retention_class.value,
                    redacted.sensitivity.value,
                    payload_json,
                    int(redacted.internal),
                    previous_hash,
                ),
            )
            sequence = int(cursor.lastrowid)
            if should_chain:
                event_hash = self._event_hash(sequence, previous_hash, redacted, payload_json)
                connection.execute(
                    "UPDATE exact_events SET event_hash=? WHERE sequence=?",
                    (event_hash, sequence),
                )
            if projection is not None:
                projection(connection, sequence)
            return StoredEvent(sequence, redacted.event_id, True)

    @staticmethod
    def _event_hash(
        sequence: int,
        previous_hash: str | None,
        event: EventEnvelope,
        payload_json: str,
    ) -> str:
        canonical = json.dumps(
            {
                "sequence": sequence,
                "previous_hash": previous_hash,
                "event_id": event.event_id,
                "schema_version": event.schema_version,
                "occurred_at_utc": event.occurred_at_utc,
                "received_at_utc": event.received_at_utc,
                "monotonic_ns": event.monotonic_ns,
                "boot_id": event.boot_id,
                "source": event.source,
                "type": event.type,
                "subject": event.subject,
                "severity": event.severity.value,
                "correlation_id": event.correlation_id,
                "causation_id": event.causation_id,
                "task_id": event.task_id,
                "origin": event.origin.value,
                "confidence": event.confidence,
                "retention_class": event.retention_class.value,
                "sensitivity": event.sensitivity.value,
                "payload_json": payload_json,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        return "sha256:" + hashlib.sha256(canonical).hexdigest()

    def verify_control_chain(self) -> IntegrityResult:
        with self.read_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM exact_events WHERE event_hash IS NOT NULL ORDER BY sequence"
            )
            previous: str | None = None
            checked = 0
            for row in rows:
                if row["previous_hash"] != previous:
                    return IntegrityResult(
                        False,
                        f"chain predecessor mismatch at sequence {row['sequence']}",
                        checked,
                    )
                event = EventEnvelope(
                    event_id=row["event_id"],
                    schema_version=row["schema_version"],
                    occurred_at_utc=row["occurred_at_utc"],
                    received_at_utc=row["received_at_utc"],
                    monotonic_ns=row["monotonic_ns"],
                    boot_id=row["boot_id"],
                    source=row["source"],
                    type=row["type"],
                    subject=row["subject"],
                    severity=Severity(row["severity"]),
                    correlation_id=row["correlation_id"],
                    causation_id=row["causation_id"],
                    task_id=row["task_id"],
                    origin=EventOrigin(row["origin"]),
                    confidence=row["confidence"],
                    retention_class=RetentionClass(row["retention_class"]),
                    sensitivity=Sensitivity(row["sensitivity"]),
                    payload=json.loads(row["payload_json"]),
                    idempotency_key=row["idempotency_key"],
                    internal=bool(row["internal"]),
                )
                expected = self._event_hash(
                    int(row["sequence"]), previous, event, str(row["payload_json"])
                )
                if expected != row["event_hash"]:
                    return IntegrityResult(
                        False,
                        f"chain hash mismatch at sequence {row['sequence']}",
                        checked,
                    )
                previous = str(row["event_hash"])
                checked += 1
        return IntegrityResult(True, "control event hash chain verified", checked)

    def verify_sequence_continuity(self, *, after_sequence: int = 0) -> IntegrityResult:
        """Verify the exact tail without loading it into an unbounded list."""

        expected = int(after_sequence) + 1
        checked = 0
        with self.read_connection() as connection:
            cursor = connection.execute(
                "SELECT sequence FROM exact_events WHERE sequence>? ORDER BY sequence",
                (int(after_sequence),),
            )
            for row in cursor:
                sequence = int(row[0])
                if sequence != expected:
                    return IntegrityResult(
                        False,
                        f"exact sequence gap: expected {expected}, found {sequence}",
                        checked,
                    )
                expected += 1
                checked += 1
        return IntegrityResult(True, "exact event sequence is contiguous", checked)

    def record_dead_letter(
        self, source: str, reason: str, payload: Any, *, maximum_rows: int = 1000
    ) -> None:
        bounded, digest, _original_bytes, _truncated = bounded_redacted_record(payload)
        redacted = json.dumps(bounded, sort_keys=True, separators=(",", ":"))
        now = utc_iso()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO dead_letters(received_at,source,reason,redacted_payload_json,"
                "payload_digest,occurrences,last_seen_at) VALUES (?,?,?,?,?,1,?) "
                "ON CONFLICT(source,reason,payload_digest) DO UPDATE SET "
                "occurrences=occurrences+1,last_seen_at=excluded.last_seen_at",
                (now, source[:255], reason[:1000], redacted, digest, now),
            )
            connection.execute(
                "DELETE FROM dead_letters WHERE dead_letter_id IN ("
                "SELECT dead_letter_id FROM dead_letters ORDER BY last_seen_at DESC "
                "LIMIT -1 OFFSET ?)",
                (maximum_rows,),
            )

    def read_exact_event(self, sequence: int) -> sqlite3.Row | None:
        with self.read_connection() as connection:
            return connection.execute(
                "SELECT * FROM exact_events WHERE sequence=?", (sequence,)
            ).fetchone()

    def table_names(self) -> set[str]:
        with self.read_connection() as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            ).fetchall()
        return {str(row[0]) for row in rows}

    def pragmas(self) -> dict[str, Any]:
        with self.read_connection() as connection:
            return {
                "foreign_keys": int(connection.execute("PRAGMA foreign_keys").fetchone()[0]),
                "journal_mode": str(connection.execute("PRAGMA journal_mode").fetchone()[0]),
                "synchronous": int(connection.execute("PRAGMA synchronous").fetchone()[0]),
                "busy_timeout": int(connection.execute("PRAGMA busy_timeout").fetchone()[0]),
            }

    def count(self, table: str) -> int:
        if table not in self.table_names():
            raise ValueError(f"unknown table {table}")
        with self.read_connection() as connection:
            return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])

    def state_storage_bytes(self) -> int:
        """Return authoritative SQLite allocation, including live WAL pages."""

        database_bytes = self.path.stat().st_size if self.path.exists() else 0
        wal = Path(str(self.path) + "-wal")
        return database_bytes + (wal.stat().st_size if wal.exists() else 0)

    def non_authority_state_writes_allowed(self, incoming_bytes: int = 0) -> bool:
        """Keep low-value rows inside their share so exact writes retain reserve.

        This gate is never consulted by :meth:`append_exact_event`. Approximate
        awareness is permitted to become unavailable; authority is not.
        """

        return self.state_storage_bytes() + max(0, int(incoming_bytes)) < int(
            self.budget.state_database_bytes
        )

    def quick_integrity_check(self) -> IntegrityResult:
        with self.read_connection() as connection:
            result = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        return IntegrityResult(result == "ok", result)

    def checkpoint_wal(self, mode: str = "PASSIVE") -> tuple[int, int, int]:
        if mode not in {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}:
            raise ValueError("invalid WAL checkpoint mode")
        with self._lock:
            row = self.writer.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
            return int(row[0]), int(row[1]), int(row[2])

    @property
    def backup_root(self) -> Path:
        return self.root / "checkpoints"

    def create_backup(self, label: str = "manual") -> Path:
        clean_label = re.sub(r"[^a-zA-Z0-9_.-]+", "-", label).strip("-.") or "manual"
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        self.backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = self.backup_root / f"hal-state.{clean_label}.{timestamp}.sqlite"
        backup = sqlite3.connect(destination)
        try:
            with self._lock:
                self.writer.backup(backup)
            detail = str(backup.execute("PRAGMA quick_check").fetchone()[0])
            if detail != "ok":
                raise RuntimeError(f"backup integrity check failed: {detail}")
        except BaseException:
            backup.close()
            destination.unlink(missing_ok=True)
            raise
        else:
            backup.close()
        os.chmod(destination, 0o600)
        return destination

    def migration_backups(self) -> tuple[Path, ...]:
        if not self.backup_root.exists():
            return ()
        return tuple(sorted(self.backup_root.glob("hal-state.*.sqlite"), reverse=True))

    def restore_backup(self, backup_path: Path) -> Path:
        """Atomically restore a validated local backup with automatic rollback."""

        with self.read_connection() as connection:
            live_lease = connection.execute(
                "SELECT owner_id FROM instance_leases WHERE mode='writer' AND expires_at>? LIMIT 1",
                (utc_iso(),),
            ).fetchone()
        if live_lease is not None:
            raise RuntimeError(
                "cannot restore while the canonical HAL writer lease is active: "
                + str(live_lease["owner_id"])
            )
        selected = backup_path.resolve(strict=True)
        root = self.backup_root.resolve()
        if selected.parent != root or selected.suffix != ".sqlite":
            raise ValueError("backup must be a SQLite file in the machine-self checkpoint directory")
        probe = sqlite3.connect(f"file:{selected}?mode=ro", uri=True)
        try:
            detail = str(probe.execute("PRAGMA quick_check").fetchone()[0])
            if detail != "ok":
                raise RuntimeError(f"selected backup failed integrity check: {detail}")
            probe.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1")
        finally:
            probe.close()
        rollback = self.create_backup("pre-restore-rollback")
        self.close()
        temporary = self.path.with_name(self.path.name + ".restore.tmp")
        try:
            shutil.copy2(selected, temporary)
            os.chmod(temporary, 0o600)
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            for suffix in ("-wal", "-shm"):
                Path(str(self.path) + suffix).unlink(missing_ok=True)
            self._open()
            check = self.quick_integrity_check()
            if not check.valid:
                raise RuntimeError(f"restored database failed integrity check: {check.detail}")
        except BaseException:
            self.close()
            temporary.unlink(missing_ok=True)
            shutil.copy2(rollback, self.path)
            for suffix in ("-wal", "-shm"):
                Path(str(self.path) + suffix).unlink(missing_ok=True)
            self._open()
            raise
        return rollback

    def close(self) -> None:
        with self._lock:
            if self._writer is None:
                return
            try:
                self._writer.execute("PRAGMA optimize")
                self._writer.execute("PRAGMA wal_checkpoint(PASSIVE)")
            finally:
                self._writer.close()
                self._writer = None
