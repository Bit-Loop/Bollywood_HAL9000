"""Content-addressed compressed forensic evidence with transactional references."""

from __future__ import annotations

import gzip
import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from hal9000.sentience.event_envelope import utc_iso
from hal9000.sentience.events.redact import redact_text
from hal9000.sentience.models import RetentionClass, Sensitivity
from hal9000.sentience.storage.database import SentienceDatabase

_DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")


class BlobStoreError(RuntimeError):
    pass


class MissingBlobError(BlobStoreError):
    pass


class CorruptBlobError(BlobStoreError):
    pass


class BlobBudgetExceeded(BlobStoreError):
    pass


@dataclass(frozen=True, slots=True)
class BlobReference:
    digest: str
    relative_path: Path
    compressed_size: int
    uncompressed_size: int
    compression: str
    deduplicated: bool


@dataclass(frozen=True, slots=True)
class BlobReconciliation:
    checked_files: int
    checked_references: int
    orphan_blobs: int
    orphans_deleted: int
    missing_blobs: int


class BlobStore:
    def __init__(self, root: Path, database: SentienceDatabase, *, maximum_blob_bytes: int = 64 * 1024**2) -> None:
        self.root = root
        self.database = database
        self.maximum_blob_bytes = maximum_blob_bytes
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)

    def put_text(self, text: str, **kwargs) -> BlobReference:
        return self.put(redact_text(text).encode("utf-8"), **kwargs)

    def put(
        self,
        data: bytes,
        *,
        mime_type: str,
        sensitivity: Sensitivity,
        retention_class: RetentionClass,
        owner_type: str | None = None,
        owner_id: str | None = None,
        relation: str = "evidence",
        pin: bool = False,
    ) -> BlobReference:
        retention = RetentionClass(retention_class)
        if retention is RetentionClass.NEVER:
            raise ValueError("NEVER-retention payloads cannot enter the forensic store")
        if mime_type.lower().startswith("audio/"):
            raise ValueError("raw microphone/audio payloads are not retained by default")
        if len(data) > self.maximum_blob_bytes:
            raise ValueError("forensic payload exceeds the configured per-object limit")
        if mime_type.startswith("text/") or mime_type in {"application/json", "application/xml"}:
            data = redact_text(data.decode("utf-8", errors="replace")).encode("utf-8")
        digest_hex = hashlib.sha256(data).hexdigest()
        digest = "sha256:" + digest_hex
        compressed, compression, suffix = self._compress(data)
        checksum = "sha256:" + hashlib.sha256(compressed).hexdigest()
        relative = Path(digest_hex[:2]) / digest_hex[2:4] / f"{digest_hex}.{suffix}"
        destination = self._safe_path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        deduplicated = destination.exists()
        if not deduplicated and not pin and retention is not RetentionClass.FOREVER:
            with self.database.read_connection() as connection:
                allocated = int(
                    connection.execute(
                        "SELECT COALESCE(SUM(compressed_size),0) FROM payload_refs"
                    ).fetchone()[0]
                )
            if allocated + len(compressed) > self.database.budget.blob_bytes:
                raise BlobBudgetExceeded(
                    "forensic blob allocation is full; low-value evidence was not retained"
                )
        if not deduplicated:
            temporary = destination.with_name(destination.name + f".tmp-{uuid.uuid4().hex}")
            try:
                with temporary.open("xb") as handle:
                    handle.write(compressed)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, destination)
                directory_fd = os.open(destination.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                temporary.unlink(missing_ok=True)
        now = utc_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO payload_refs(digest,compressed_size,uncompressed_size,compression,"
                "mime_type,sensitivity,retention_class,created_at,last_accessed_at,"
                "integrity_checksum,pinned,relative_path) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(digest) DO UPDATE SET last_accessed_at=excluded.last_accessed_at,"
                "pinned=MAX(payload_refs.pinned,excluded.pinned),missing=0",
                (
                    digest,
                    len(compressed),
                    len(data),
                    compression,
                    mime_type[:255],
                    Sensitivity(sensitivity).value,
                    retention.value,
                    now,
                    now,
                    checksum,
                    int(pin),
                    str(relative),
                ),
            )
            if owner_type and owner_id:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO payload_links(owner_type,owner_id,relation,digest,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (owner_type[:128], owner_id[:512], relation[:128], digest, now),
                )
                if cursor.rowcount:
                    connection.execute(
                        "UPDATE payload_refs SET refcount=refcount+1 WHERE digest=?", (digest,)
                    )
        return BlobReference(digest, relative, len(compressed), len(data), compression, deduplicated)

    def get(self, digest: str) -> bytes:
        self._digest_hex(digest)
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM payload_refs WHERE digest=?", (digest,)
            ).fetchone()
        if row is None:
            raise MissingBlobError(f"unknown evidence object {digest}")
        path = self._safe_path(Path(str(row["relative_path"])))
        if not path.is_file():
            self._mark_missing(digest)
            raise MissingBlobError(f"evidence object is missing: {digest}")
        compressed = path.read_bytes()
        checksum = "sha256:" + hashlib.sha256(compressed).hexdigest()
        if checksum != row["integrity_checksum"]:
            self._mark_missing(digest)
            raise CorruptBlobError(f"compressed evidence checksum failed: {digest}")
        try:
            if row["compression"] == "zstd":
                import zstandard

                data = zstandard.ZstdDecompressor().decompress(
                    compressed, max_output_size=int(row["uncompressed_size"])
                )
            elif row["compression"] == "gzip":
                data = gzip.decompress(compressed)
            else:
                raise CorruptBlobError(f"unsupported compression {row['compression']}")
        except CorruptBlobError:
            raise
        except Exception as exc:
            self._mark_missing(digest)
            raise CorruptBlobError(f"evidence decompression failed: {digest}") from exc
        if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
            self._mark_missing(digest)
            raise CorruptBlobError(f"evidence content digest failed: {digest}")
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE payload_refs SET last_accessed_at=?,missing=0 WHERE digest=?",
                (utc_iso(), digest),
            )
        return data

    def reconcile(
        self, *, delete_orphans: bool = False, maximum_files: int = 100_000
    ) -> BlobReconciliation:
        """Stream over disk and references without building an unbounded path set."""

        checked_files = orphan_blobs = orphans_deleted = 0
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            checked_files += 1
            if checked_files > maximum_files:
                break
            relative = str(path.relative_to(self.root))
            with self.database.read_connection() as connection:
                known = connection.execute(
                    "SELECT 1 FROM payload_refs WHERE relative_path=?", (relative,)
                ).fetchone()
            if known is not None:
                continue
            orphan_blobs += 1
            if delete_orphans:
                path.unlink(missing_ok=True)
                orphans_deleted += 1

        with self.database.read_connection() as connection:
            rows = connection.execute(
                "SELECT digest,relative_path FROM payload_refs ORDER BY created_at LIMIT ?",
                (maximum_files,),
            ).fetchall()
        missing: list[str] = []
        for row in rows:
            try:
                path = self._safe_path(Path(str(row["relative_path"])))
            except ValueError:
                missing.append(str(row["digest"]))
                continue
            if not path.is_file():
                missing.append(str(row["digest"]))
        with self.database.transaction() as connection:
            connection.execute("UPDATE payload_refs SET missing=0")
            connection.executemany(
                "UPDATE payload_refs SET missing=1 WHERE digest=?",
                ((digest,) for digest in missing),
            )
        return BlobReconciliation(
            checked_files,
            len(rows),
            orphan_blobs,
            orphans_deleted,
            len(missing),
        )

    @staticmethod
    def _compress(data: bytes) -> tuple[bytes, str, str]:
        try:
            import zstandard

            return zstandard.ZstdCompressor(level=6).compress(data), "zstd", "zst"
        except ImportError:
            return gzip.compress(data, compresslevel=6, mtime=0), "gzip", "gz"

    def _mark_missing(self, digest: str) -> None:
        with self.database.transaction() as connection:
            connection.execute("UPDATE payload_refs SET missing=1 WHERE digest=?", (digest,))

    def _safe_path(self, relative: Path) -> Path:
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid forensic blob path")
        target = (self.root / relative).resolve(strict=False)
        root = self.root.resolve(strict=False)
        if target != root and root not in target.parents:
            raise ValueError("forensic blob path escaped its root")
        return target

    @staticmethod
    def _digest_hex(digest: str) -> str:
        match = _DIGEST.fullmatch(digest)
        if not match:
            raise ValueError("invalid SHA-256 evidence reference")
        return match.group(1)
