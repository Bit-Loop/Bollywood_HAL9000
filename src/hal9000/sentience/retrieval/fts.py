"""External-content SQLite FTS5 search, validation, and rebuild."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from hal9000.sentience.storage.database import SentienceDatabase

_TERM = re.compile(r"[\w./:-]{2,}", re.UNICODE)


@dataclass(frozen=True, slots=True)
class FtsIntegrity:
    valid: bool
    source_rows: int
    index_rows: int
    detail: str


class FtsRepository:
    def __init__(self, database: SentienceDatabase) -> None:
        self.database = database
        self.available = "retrieval_fts" in database.table_names()

    @staticmethod
    def query_expression(query: str) -> str:
        terms = _TERM.findall(query)[:16]
        return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)

    def search(
        self,
        query: str,
        *,
        task_id: str | None,
        subject: str | None,
        limit: int,
    ) -> list[sqlite3.Row]:
        if not self.available:
            raise sqlite3.OperationalError("FTS5 is unavailable")
        expression = self.query_expression(query)
        if not expression:
            raise sqlite3.OperationalError("query contains no searchable terms")
        filters = ["retrieval_fts MATCH ?"]
        values: list[object] = [expression]
        if task_id:
            filters.append("(d.task_id=? OR d.task_id IS NULL)")
            values.append(task_id)
        if subject:
            filters.append("d.subject=?")
            values.append(subject)
        values.append(max(1, min(200, limit)))
        sql = (
            "SELECT d.*,bm25(retrieval_fts,2.0,1.0,1.5) AS fts_score "
            "FROM retrieval_fts JOIN retrieval_documents d "
            "ON d.document_rowid=retrieval_fts.rowid WHERE "
            + " AND ".join(filters)
            + " ORDER BY fts_score LIMIT ?"
        )
        with self.database.read_connection() as connection:
            return connection.execute(sql, values).fetchall()

    def metadata_fallback(
        self,
        query: str,
        *,
        task_id: str | None,
        subject: str | None,
        limit: int,
    ) -> list[sqlite3.Row]:
        terms = _TERM.findall(query)[:8]
        filters: list[str] = []
        values: list[object] = []
        if terms:
            filters.append(
                "("
                + " OR ".join(
                    "(title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\' "
                    "OR subject LIKE ? ESCAPE '\\')"
                    for _ in terms
                )
                + ")"
            )
            for term in terms:
                wildcard = "%" + term.replace("%", "\\%").replace("_", "\\_") + "%"
                values.extend((wildcard, wildcard, wildcard))
        if task_id:
            filters.append("(task_id=? OR task_id IS NULL)")
            values.append(task_id)
        if subject:
            filters.append("subject=?")
            values.append(subject)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        values.append(max(1, min(200, limit)))
        with self.database.read_connection() as connection:
            return connection.execute(
                "SELECT *,1000.0 AS fts_score FROM retrieval_documents"
                + where
                + " ORDER BY pinned DESC,exact DESC,updated_at DESC LIMIT ?",
                values,
            ).fetchall()

    def validate(self) -> FtsIntegrity:
        if not self.available:
            return FtsIntegrity(False, 0, 0, "FTS5 unavailable")
        try:
            with self.database.read_connection() as connection:
                source = int(connection.execute("SELECT COUNT(*) FROM retrieval_documents").fetchone()[0])
                index = int(connection.execute("SELECT COUNT(*) FROM retrieval_fts_docsize").fetchone()[0])
            valid = source == index
            return FtsIntegrity(
                valid,
                source,
                index,
                "external-content FTS synchronized" if valid else "FTS row count differs from source",
            )
        except sqlite3.Error as exc:
            return FtsIntegrity(False, 0, 0, str(exc))

    def rebuild(self) -> None:
        if not self.available:
            raise RuntimeError("FTS5 is unavailable")
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO retrieval_fts(retrieval_fts) VALUES('rebuild')")
