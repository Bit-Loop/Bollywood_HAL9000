"""Operational CLI for migrations, integrity, retention, and recovery."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from hal9000.config import ConfigStore
from hal9000.paths import AppPaths
from hal9000.sentience.retrieval.fts import FtsRepository
from hal9000.sentience.storage.blob_store import BlobStore
from hal9000.sentience.storage.database import SentienceDatabase
from hal9000.sentience.storage.integrity import StorageIntegrityService
from hal9000.sentience.storage.retention import RetentionPolicyEngine


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hal-self", description="HAL machine-self maintenance")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply pending transactional schema migrations")
    commands.add_parser("status", help="show schema, storage budget, and bounded row counts")
    integrity = commands.add_parser("integrity", help="check SQLite, event chain, FTS, and blobs")
    integrity.add_argument("--full", action="store_true", help="run full SQLite/blob checks")
    integrity.add_argument("--delete-orphans", action="store_true")
    commands.add_parser("fts-rebuild", help="rebuild the external-content FTS5 index")
    retention = commands.add_parser("retention", help="preview or apply retention")
    retention.add_argument("--apply", action="store_true", help="apply rather than dry-run")
    backup = commands.add_parser("backup", help="create a validated SQLite backup")
    backup.add_argument("--label", default="manual")
    commands.add_parser("backups", help="list local validated migration/manual backups")
    restore = commands.add_parser("restore", help="atomically restore one local backup")
    restore.add_argument("backup", help="backup filename or exact path in the checkpoint directory")
    wal = commands.add_parser("wal-checkpoint", help="run a bounded WAL checkpoint")
    wal.add_argument("--mode", choices=("PASSIVE", "FULL", "RESTART", "TRUNCATE"), default="PASSIVE")
    return parser


def _print(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    paths = AppPaths.discover()
    paths.ensure()
    config = ConfigStore(paths).load()
    database = SentienceDatabase.open(paths, config.sentience)
    blobs = BlobStore(paths.sentience_blob_root, database)
    try:
        if arguments.command == "migrate":
            _print(
                {
                    "database": str(database.path),
                    "migration_version": database.migration_version,
                    "integrity": asdict(database.quick_integrity_check()),
                }
            )
        elif arguments.command == "status":
            counts = {
                table: database.count(table)
                for table in (
                    "exact_events",
                    "event_runs",
                    "sketch_buckets",
                    "episodes",
                    "semantic_facts",
                    "commitments",
                    "contradictions",
                    "payload_refs",
                    "outbox",
                )
            }
            _print(
                {
                    "database": str(database.path),
                    "migration_version": database.migration_version,
                    "pragmas": database.pragmas(),
                    "budget": asdict(database.budget),
                    "state_storage_bytes": database.state_storage_bytes(),
                    "counts": counts,
                    "fts5": FtsRepository(database).available,
                }
            )
        elif arguments.command == "integrity":
            report = StorageIntegrityService(database, blobs).check(
                full_database=arguments.full,
                full_blobs=arguments.full,
                delete_orphans=arguments.delete_orphans,
            )
            _print(asdict(report))
            return 0 if report.database_valid and report.control_chain_valid and report.fts_valid else 2
        elif arguments.command == "fts-rebuild":
            repository = FtsRepository(database)
            repository.rebuild()
            _print(asdict(repository.validate()))
        elif arguments.command == "retention":
            result = RetentionPolicyEngine(database, blobs, config.sentience.storage).run(
                dry_run=not arguments.apply
            )
            _print(asdict(result))
        elif arguments.command == "backup":
            _print({"backup": str(database.create_backup(arguments.label))})
        elif arguments.command == "backups":
            _print({"backups": [str(path) for path in database.migration_backups()]})
        elif arguments.command == "restore":
            selected = Path(arguments.backup)
            if not selected.is_absolute():
                selected = database.backup_root / selected
            rollback = database.restore_backup(selected)
            _print(
                {
                    "restored": str(selected.resolve()),
                    "automatic_rollback_backup": str(rollback),
                    "migration_version": database.migration_version,
                }
            )
        elif arguments.command == "wal-checkpoint":
            busy, log_frames, checkpointed = database.checkpoint_wal(arguments.mode)
            _print(
                {
                    "mode": arguments.mode,
                    "busy": busy,
                    "log_frames": log_frames,
                    "checkpointed_frames": checkpointed,
                }
            )
    finally:
        database.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
