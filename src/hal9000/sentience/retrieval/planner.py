"""Progressive metadata/FTS retrieval under strict result and token bounds."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, replace

from hal9000.config import SentienceRetrievalSettings
from hal9000.sentience.event_envelope import utc_iso
from hal9000.sentience.retrieval.fts import FtsRepository
from hal9000.sentience.retrieval.ranking import rank_document
from hal9000.sentience.retrieval.token_budget import TokenBudget
from hal9000.sentience.storage.database import SentienceDatabase


@dataclass(frozen=True, slots=True)
class MemoryQuery:
    query: str
    task_id: str | None = None
    subject: str | None = None
    token_budget: int = 3000
    max_results: int = 10
    max_depth: int = 2


@dataclass(frozen=True, slots=True)
class MemoryItem:
    reference: str
    kind: str
    text: str
    subject: str
    relevance: float
    confidence: float
    source_type: str
    exact: bool
    stale: bool
    contradicted: bool
    provenance: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    untrusted: bool


@dataclass(frozen=True, slots=True)
class MemoryResult:
    query: str
    requested_tokens: int
    used_tokens: int
    facts: tuple[MemoryItem, ...]
    episodes: tuple[MemoryItem, ...]
    commitments: tuple[MemoryItem, ...]
    contradictions: tuple[MemoryItem, ...]
    approximate_awareness: tuple[dict, ...]
    expansion_available: tuple[str, ...]
    truncated: bool
    retrieval_mode: str

    @property
    def all_items(self) -> tuple[MemoryItem, ...]:
        return self.commitments + self.contradictions + self.facts + self.episodes


class MemoryRetriever:
    def __init__(
        self, database: SentienceDatabase, settings: SentienceRetrievalSettings
    ) -> None:
        self.database = database
        self.settings = settings
        self.fts = FtsRepository(database)
        self.force_fts_failure = False

    def search(self, query: MemoryQuery) -> MemoryResult:
        if not query.query.strip():
            raise ValueError("memory query must not be empty")
        if query.max_results < 1 or query.max_results > 50:
            raise ValueError("memory result limit must be between 1 and 50")
        if query.max_depth < 0 or query.max_depth > self.settings.max_depth:
            raise ValueError("memory retrieval depth exceeds the configured maximum")
        started = time.perf_counter()
        mode = "fts5"
        try:
            if self.force_fts_failure:
                raise sqlite3.OperationalError("forced FTS diagnostic failure")
            rows = self.fts.search(
                query.query,
                task_id=query.task_id,
                subject=query.subject,
                limit=query.max_results * 5,
            )
        except sqlite3.Error:
            mode = "metadata_fallback"
            rows = self.fts.metadata_fallback(
                query.query,
                task_id=query.task_id,
                subject=query.subject,
                limit=query.max_results * 5,
            )
        ranked = sorted(
            rows,
            key=lambda row: rank_document(row, task_id=query.task_id, subject=query.subject),
            reverse=True,
        )
        budget = TokenBudget(query.token_budget)
        grouped: dict[str, list[MemoryItem]] = {
            "fact": [],
            "episode": [],
            "commitment": [],
            "contradiction": [],
        }
        expansions: list[str] = []
        seen_subject_kind: dict[tuple[str, str], int] = {}
        truncated = False
        for row in ranked:
            if sum(map(len, grouped.values())) >= query.max_results:
                truncated = True
                break
            kind = str(row["document_kind"])
            if kind not in grouped:
                continue
            diversity_key = (kind, str(row["subject"]))
            if seen_subject_kind.get(diversity_key, 0) >= 3:
                continue
            clipped, was_truncated = budget.take(str(row["body"]))
            if not clipped:
                truncated = True
                break
            evidence = self._evidence(str(row["source_table"]), str(row["source_id"]))
            metadata = json.loads(row["metadata_json"] or "{}")
            item = MemoryItem(
                reference=str(row["reference"]),
                kind=kind,
                text=clipped,
                subject=str(row["subject"]),
                relevance=rank_document(row, task_id=query.task_id, subject=query.subject),
                confidence=float(row["confidence"]),
                source_type=str(row["source_table"]),
                exact=bool(row["exact"]),
                stale=bool(row["stale"]),
                contradicted=bool(row["contradicted"]),
                provenance=(f"{row['source_table']}:{row['source_id']}",),
                evidence_refs=evidence,
                untrusted=bool(metadata.get("untrusted", False)),
            )
            grouped[kind].append(item)
            seen_subject_kind[diversity_key] = seen_subject_kind.get(diversity_key, 0) + 1
            if kind == "episode":
                expansions.extend((item.reference + ".actions", item.reference + ".logs"))
            elif evidence:
                expansions.append(item.reference + ".evidence")
            truncated = truncated or was_truncated
            if was_truncated:
                break
        awareness: list[dict] = []
        for item in self._approximate_awareness(limit=4):
            encoded = json.dumps(item, sort_keys=True, separators=(",", ":"))
            _accepted, clipped = budget.take(encoded, fixed_overhead=4)
            if clipped:
                truncated = True
                break
            awareness.append(item)
        result_ids = [item.reference for values in grouped.values() for item in values]
        self._audit(
            "task_memory" if query.task_id else "memory_search",
            result_ids,
            (time.perf_counter() - started) * 1000,
            budget.used,
        )
        return MemoryResult(
            query.query,
            query.token_budget,
            budget.used,
            tuple(grouped["fact"]),
            tuple(grouped["episode"]),
            tuple(grouped["commitment"]),
            tuple(grouped["contradiction"]),
            tuple(awareness),
            tuple(expansions[: query.max_results * 2]),
            truncated,
            mode,
        )

    def _approximate_awareness(self, *, limit: int) -> tuple[dict, ...]:
        with self.database.read_connection() as connection:
            rows = connection.execute(
                "SELECT metric_name,scope,bucket_start,bucket_end,mode,sketch_kind,"
                "item_updates,estimate,lower_bound,upper_bound,last_updated_at FROM "
                "sketch_buckets ORDER BY bucket_end DESC LIMIT 64"
            ).fetchall()
        seen: set[tuple[str, str]] = set()
        result: list[dict] = []
        for row in rows:
            key = (str(row["metric_name"]), str(row["scope"]))
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "metric": key[0],
                    "scope": key[1],
                    "exact": str(row["mode"]).upper() == "EXACT",
                    "approximate": str(row["mode"]).upper() != "EXACT",
                    "sketch_kind": str(row["sketch_kind"]),
                    "estimate": row["estimate"],
                    "lower_bound": row["lower_bound"],
                    "upper_bound": row["upper_bound"],
                    "sample_count": int(row["item_updates"]),
                    "window": {
                        "start": str(row["bucket_start"]),
                        "end": str(row["bucket_end"]),
                    },
                    "provenance": "sketch_buckets:" + key[0] + ":" + key[1],
                    "updated_at": str(row["last_updated_at"]),
                }
            )
            if len(result) >= max(0, min(16, limit)):
                break
        return tuple(result)

    def _evidence(self, source_table: str, source_id: str) -> tuple[str, ...]:
        with self.database.read_connection() as connection:
            if source_table == "semantic_facts":
                rows = connection.execute(
                    "SELECT evidence_ref FROM fact_evidence WHERE fact_id=? ORDER BY evidence_ref LIMIT 64",
                    (source_id,),
                ).fetchall()
                return tuple(str(row[0]) for row in rows)
            if source_table == "episodes":
                rows = connection.execute(
                    "SELECT evidence_ref FROM episode_evidence WHERE episode_id=? ORDER BY evidence_ref LIMIT 128",
                    (source_id,),
                ).fetchall()
                return tuple(str(row[0]) for row in rows)
            if source_table == "contradictions":
                row = connection.execute(
                    "SELECT evidence_refs_json FROM contradictions WHERE contradiction_id=?",
                    (source_id,),
                ).fetchone()
                return tuple(json.loads(row[0])) if row else ()
            if source_table == "commitments":
                row = connection.execute(
                    "SELECT evidence_event_id FROM commitments WHERE commitment_id=?", (source_id,)
                ).fetchone()
                return (str(row[0]),) if row else ()
        return ()

    def _audit(self, category: str, result_ids: list[str], latency_ms: float, tokens: int) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO retrieval_audits(audit_id,query_category,result_ids_json,latency_ms,"
                "token_count,created_at) VALUES(?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    category,
                    json.dumps(result_ids[:50]),
                    latency_ms,
                    tokens,
                    utc_iso(),
                ),
            )
            connection.execute(
                "DELETE FROM retrieval_audits WHERE audit_id IN (SELECT audit_id FROM retrieval_audits "
                "ORDER BY created_at DESC LIMIT -1 OFFSET 5000)"
            )
