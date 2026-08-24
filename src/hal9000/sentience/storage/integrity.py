"""Database, control-chain, FTS, and forensic-blob integrity diagnostics."""

from __future__ import annotations

from dataclasses import dataclass

from hal9000.sentience.storage.blob_store import BlobStore
from hal9000.sentience.storage.database import SentienceDatabase
from hal9000.sentience.retrieval.fts import FtsRepository


@dataclass(frozen=True, slots=True)
class StorageIntegrityReport:
    database_valid: bool
    database_detail: str
    control_chain_valid: bool
    control_chain_detail: str
    fts_valid: bool
    fts_detail: str
    checked_blobs: int
    missing_blobs: int
    orphan_blobs: int
    orphans_deleted: int


class StorageIntegrityService:
    def __init__(self, database: SentienceDatabase, blobs: BlobStore) -> None:
        self.database = database
        self.blobs = blobs

    def check(
        self, *, full_database: bool = False, full_blobs: bool = False, delete_orphans: bool = False
    ) -> StorageIntegrityReport:
        with self.database.read_connection() as connection:
            pragma = "integrity_check" if full_database else "quick_check"
            database_detail = str(connection.execute(f"PRAGMA {pragma}").fetchone()[0])
        chain = self.database.verify_control_chain()
        fts = FtsRepository(self.database)
        if not fts.available:
            fts_valid, fts_detail = True, "FTS5 unavailable; bounded metadata fallback active"
        else:
            fts_result = fts.validate()
            fts_valid, fts_detail = fts_result.valid, fts_result.detail
        reconciliation = self.blobs.reconcile(
            delete_orphans=delete_orphans,
            maximum_files=100_000 if full_blobs else 10_000,
        )
        return StorageIntegrityReport(
            database_detail == "ok",
            database_detail,
            chain.valid,
            chain.detail,
            fts_valid,
            fts_detail,
            reconciliation.checked_files,
            reconciliation.missing_blobs,
            reconciliation.orphan_blobs,
            reconciliation.orphans_deleted,
        )

    def rebuild_fts(self) -> None:
        FtsRepository(self.database).rebuild()
