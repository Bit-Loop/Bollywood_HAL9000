"""Consequence-aware TTL and hard-budget retention policy."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hal9000.config import SentienceStorageSettings
from hal9000.sentience.event_envelope import utc_iso
from hal9000.sentience.models import RetentionClass
from hal9000.sentience.storage.blob_store import BlobStore
from hal9000.sentience.storage.database import SentienceDatabase

DEFAULT_TTLS = {
    RetentionClass.TRANSIENT: timedelta(hours=1),
    RetentionClass.SHORT: timedelta(days=7),
    RetentionClass.EPISODIC: timedelta(days=90),
    RetentionClass.LONG: timedelta(days=365),
    RetentionClass.FOREVER: None,
    RetentionClass.NEVER: timedelta(0),
}
_EVICTION_PRIORITY = {
    RetentionClass.NEVER: 0,
    RetentionClass.TRANSIENT: 1,
    RetentionClass.SHORT: 2,
    RetentionClass.EPISODIC: 3,
    RetentionClass.LONG: 4,
    RetentionClass.FOREVER: 99,
}


@dataclass(frozen=True, slots=True)
class StorageUsage:
    database_bytes: int
    wal_bytes: int
    blob_bytes: int
    checkpoint_bytes: int
    total_bytes: int
    budget_bytes: int
    soft_limit_bytes: int
    pressure: str


@dataclass(frozen=True, slots=True)
class RetentionCandidate:
    object_type: str
    object_id: str
    retention_class: RetentionClass
    bytes_reclaimable: int
    reason: str
    path: str | None = None


@dataclass(frozen=True, slots=True)
class RetentionReport:
    dry_run: bool
    candidate_count: int
    deleted_count: int
    bytes_reclaimed: int
    protected_bytes: int
    usage_before: StorageUsage
    usage_after: StorageUsage
    reasons: tuple[str, ...]


class RetentionPolicyEngine:
    def __init__(
        self,
        database: SentienceDatabase,
        blobs: BlobStore,
        settings: SentienceStorageSettings,
    ) -> None:
        self.database = database
        self.blobs = blobs
        self.settings = settings

    def usage(self) -> StorageUsage:
        database_bytes = self.database.path.stat().st_size if self.database.path.exists() else 0
        wal_path = Path(str(self.database.path) + "-wal")
        wal_bytes = wal_path.stat().st_size if wal_path.exists() else 0
        blob_bytes = self._tree_bytes(self.blobs.root)
        checkpoint_bytes = self._tree_bytes(self.database.root / "checkpoints")
        total = database_bytes + wal_bytes + blob_bytes + checkpoint_bytes
        budget = self.database.budget
        pressure = "hard" if total >= budget.total_bytes else "soft" if total >= budget.soft_limit_bytes else "nominal"
        return StorageUsage(
            database_bytes,
            wal_bytes,
            blob_bytes,
            checkpoint_bytes,
            total,
            budget.total_bytes,
            budget.soft_limit_bytes,
            pressure,
        )

    @staticmethod
    def _tree_bytes(root: Path, maximum_files: int = 200_000) -> int:
        total = count = 0
        if not root.exists():
            return 0
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            count += 1
            if count > maximum_files:
                break
            try:
                total += path.stat().st_size
            except FileNotFoundError:
                continue
        return total

    def plan(self, *, now: datetime | None = None) -> tuple[RetentionCandidate, ...]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        candidates: list[RetentionCandidate] = []
        with self.database.read_connection() as connection:
            run_rows = connection.execute(
                "SELECT run_id,last_seen,retention_class,length(COALESCE(normalized_template,'')) "
                "FROM event_runs WHERE retention_class NOT IN ('forever','long') "
                "ORDER BY last_seen LIMIT 10000"
            ).fetchall()
            blob_rows = connection.execute(
                "SELECT digest,created_at,retention_class,compressed_size,relative_path "
                "FROM payload_refs WHERE pinned=0 AND refcount=0 AND retention_class!='forever' "
                "AND NOT EXISTS(SELECT 1 FROM payload_links WHERE payload_links.digest=payload_refs.digest) "
                "ORDER BY created_at LIMIT 10000"
            ).fetchall()
        for row in run_rows:
            retention = RetentionClass(str(row["retention_class"]))
            if self._expired(str(row["last_seen"]), retention, current):
                candidates.append(
                    RetentionCandidate(
                        "event_run",
                        str(row["run_id"]),
                        retention,
                        int(row[3] or 0) + 512,
                        "retention TTL expired",
                    )
                )
        for row in blob_rows:
            retention = RetentionClass(str(row["retention_class"]))
            if self._expired(str(row["created_at"]), retention, current):
                candidates.append(
                    RetentionCandidate(
                        "payload_ref",
                        str(row["digest"]),
                        retention,
                        int(row["compressed_size"]),
                        "unreferenced forensic payload TTL expired",
                        str(row["relative_path"]),
                    )
                )
        usage = self.usage()
        if usage.pressure in {"soft", "hard"}:
            known = {(item.object_type, item.object_id) for item in candidates}
            for row in blob_rows:
                identity = ("payload_ref", str(row["digest"]))
                if identity in known:
                    continue
                retention = RetentionClass(str(row["retention_class"]))
                candidates.append(
                    RetentionCandidate(
                        identity[0],
                        identity[1],
                        retention,
                        int(row["compressed_size"]),
                        "storage budget pressure",
                        str(row["relative_path"]),
                    )
                )
            # Keep the three newest recovery points. Older optional backups
            # are the last checkpoint allocation evicted under total pressure.
            backups = sorted(
                self.database.backup_root.glob("hal-state.*.sqlite"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            for path in backups[3:]:
                candidates.append(
                    RetentionCandidate(
                        "checkpoint_file",
                        path.name,
                        RetentionClass.LONG,
                        path.stat().st_size,
                        "checkpoint allocation under storage budget pressure",
                        str(path),
                    )
                )
        candidates.sort(key=lambda item: (_EVICTION_PRIORITY[item.retention_class], -item.bytes_reclaimable))
        return tuple(candidates[:20_000])

    @staticmethod
    def _expired(stamp: str, retention: RetentionClass, now: datetime) -> bool:
        ttl = DEFAULT_TTLS[retention]
        if ttl is None:
            return False
        created = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        return created + ttl <= now

    def run(self, *, now: datetime | None = None, dry_run: bool = False) -> RetentionReport:
        before = self.usage()
        candidates = self.plan(now=now)
        if dry_run:
            return RetentionReport(
                True,
                len(candidates),
                0,
                0,
                self._protected_bytes(),
                before,
                before,
                tuple(sorted({item.reason for item in candidates})),
            )
        deleted = reclaimed = 0
        for candidate in candidates:
            if candidate.retention_class is RetentionClass.FOREVER:
                continue
            removed = self._delete(candidate)
            if removed:
                deleted += 1
                reclaimed += candidate.bytes_reclaimable
        self.database.writer.execute("PRAGMA incremental_vacuum(1000)")
        self.database.checkpoint_wal("PASSIVE")
        after = self.usage()
        return RetentionReport(
            False,
            len(candidates),
            deleted,
            reclaimed,
            self._protected_bytes(),
            before,
            after,
            tuple(sorted({item.reason for item in candidates})),
        )

    def _delete(self, candidate: RetentionCandidate) -> bool:
        if candidate.object_type == "checkpoint_file":
            if not candidate.path:
                return False
            path = Path(candidate.path).resolve(strict=False)
            root = self.database.backup_root.resolve(strict=False)
            if path.parent != root or not path.name.startswith("hal-state."):
                return False
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO retention_tombstones(tombstone_id,object_type,object_id,"
                    "retention_class,deleted_at,reason,bytes_reclaimed) VALUES(?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        candidate.object_type,
                        candidate.object_id,
                        candidate.retention_class.value,
                        utc_iso(),
                        candidate.reason,
                        candidate.bytes_reclaimable,
                    ),
                )
            try:
                path.unlink(missing_ok=False)
            except FileNotFoundError:
                return False
            return True
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO retention_tombstones(tombstone_id,object_type,object_id,"
                "retention_class,deleted_at,reason,bytes_reclaimed) VALUES(?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    candidate.object_type,
                    candidate.object_id,
                    candidate.retention_class.value,
                    utc_iso(),
                    candidate.reason,
                    candidate.bytes_reclaimable,
                ),
            )
            if candidate.object_type == "event_run":
                cursor = connection.execute(
                    "DELETE FROM event_runs WHERE run_id=? AND retention_class NOT IN ('forever','long')",
                    (candidate.object_id,),
                )
            elif candidate.object_type == "payload_ref":
                cursor = connection.execute(
                    "DELETE FROM payload_refs WHERE digest=? AND pinned=0 AND refcount=0 "
                    "AND retention_class!='forever' AND NOT EXISTS("
                    "SELECT 1 FROM payload_links WHERE payload_links.digest=payload_refs.digest)",
                    (candidate.object_id,),
                )
            else:
                return False
        if cursor.rowcount and candidate.path:
            try:
                self.blobs._safe_path(Path(candidate.path)).unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
        return cursor.rowcount == 1

    def _protected_bytes(self) -> int:
        with self.database.read_connection() as connection:
            return int(
                connection.execute(
                    "SELECT COALESCE(SUM(compressed_size),0) FROM payload_refs "
                    "WHERE pinned=1 OR retention_class='forever'"
                ).fetchone()[0]
            )
