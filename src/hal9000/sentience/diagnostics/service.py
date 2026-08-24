"""Read-only diagnostics API shared by UI, CLI, and the HAL self MCP server."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime

from hal9000.config import SentienceSettings
from hal9000.sentience.degradation.engine import DegradationEngine
from hal9000.sentience.retrieval.context_compiler import ContextCompiler, SelfCapsule
from hal9000.sentience.retrieval.expansion import ExpansionResult, MemoryExpansionService
from hal9000.sentience.retrieval.planner import MemoryQuery, MemoryResult, MemoryRetriever
from hal9000.sentience.retrieval.token_budget import TokenBudget
from hal9000.sentience.storage.blob_store import BlobStore
from hal9000.sentience.storage.integrity import StorageIntegrityService
from hal9000.sentience.storage.retention import RetentionPolicyEngine


class MachineSelfDiagnostics:
    def __init__(
        self,
        database,
        settings: SentienceSettings,
        blobs: BlobStore,
        degradation: DegradationEngine,
    ) -> None:
        self.database = database
        self.settings = settings
        self.blobs = blobs
        self.degradation = degradation
        self.compiler = ContextCompiler(database, settings)
        self.retriever = MemoryRetriever(database, settings.retrieval)
        self.expander = MemoryExpansionService(database, settings.retrieval)
        self.retention = RetentionPolicyEngine(database, blobs, settings.storage)
        self.integrity = StorageIntegrityService(database, blobs)

    def get_self_capsule(self, task_id: str | None, token_budget: int) -> SelfCapsule:
        return self.compiler.compile(
            task_id=task_id,
            query="current task machine self",
            token_budget=token_budget,
        )

    def search_memory(self, query: MemoryQuery) -> MemoryResult:
        return self.retriever.search(query)

    def expand_memory(self, reference: str, view: str, token_budget: int) -> ExpansionResult:
        return self.expander.expand(reference, view=view, token_budget=token_budget, depth=1)

    def get_claim_evidence(self, claim_id: str, token_budget: int) -> dict:
        if token_budget <= 0:
            raise ValueError("evidence token budget must be positive")
        budget = TokenBudget(token_budget)
        reference = claim_id.strip()
        with self.database.read_connection() as connection:
            if reference.startswith("event:"):
                row = connection.execute(
                    "SELECT event_id,type,subject,occurred_at_utc,origin,confidence,payload_json "
                    "FROM exact_events WHERE event_id=?",
                    (reference.split(":", 1)[1],),
                ).fetchone()
                if row:
                    text, truncated = budget.take(str(row["payload_json"]))
                    return {
                        "reference": reference,
                        "exact": True,
                        "provenance": f"exact_events:{row['event_id']}",
                        "type": str(row["type"]),
                        "subject": str(row["subject"]),
                        "occurred_at": str(row["occurred_at_utc"]),
                        "origin": str(row["origin"]),
                        "confidence": float(row["confidence"]),
                        "data": json.loads(text) if text and not truncated else text,
                        "untrusted": True,
                        "truncated": truncated,
                        "used_tokens": budget.used,
                    }
            if reference.startswith("sha256:"):
                metadata = connection.execute(
                    "SELECT digest,mime_type,sensitivity,retention_class,compressed_size,"
                    "uncompressed_size,integrity_checksum,pinned,missing FROM payload_refs "
                    "WHERE digest=?",
                    (reference,),
                ).fetchone()
                if metadata:
                    raw = self.blobs.get(reference)
                    text, truncated = budget.take(
                        raw.decode("utf-8", errors="replace"), fixed_overhead=16
                    )
                    return {
                        "reference": reference,
                        "exact": True,
                        "provenance": f"payload_refs:{reference}",
                        "metadata": dict(metadata),
                        "data": text,
                        "untrusted": True,
                        "truncated": truncated,
                        "used_tokens": budget.used,
                    }
            exact_claims = {
                "action": ("consequential_actions", "action_id"),
                "verification": ("action_verifications", "verification_id"),
                "degradation": ("degradation_episodes", "episode_id"),
                "task": ("tasks", "task_id"),
            }
            kind, separator, identity = reference.partition(":")
            if separator and kind in exact_claims and identity:
                table, key = exact_claims[kind]
                row = connection.execute(
                    f'SELECT * FROM "{table}" WHERE "{key}"=?', (identity,)
                ).fetchone()
                if row:
                    text, truncated = budget.take(
                        json.dumps(dict(row), default=str, sort_keys=True),
                        fixed_overhead=12,
                    )
                    return {
                        "reference": reference,
                        "exact": True,
                        "provenance": f"{table}:{identity}",
                        "data": text,
                        "untrusted": True,
                        "truncated": truncated,
                        "used_tokens": budget.used,
                    }
            document = connection.execute(
                "SELECT * FROM retrieval_documents WHERE reference=?", (reference,)
            ).fetchone()
        if document:
            text, truncated = budget.take(str(document["body"]))
            return {
                "reference": reference,
                "exact": bool(document["exact"]),
                "provenance": f"{document['source_table']}:{document['source_id']}",
                "data": text,
                "untrusted": bool(json.loads(document["metadata_json"] or "{}").get("untrusted")),
                "truncated": truncated,
                "used_tokens": budget.used,
            }
        raise KeyError(reference)

    def get_storage_status(self) -> dict:
        usage = self.retention.usage()
        with self.database.read_connection() as connection:
            classes = {
                str(row["retention_class"]): int(row["bytes"] or 0)
                for row in connection.execute(
                    "SELECT retention_class,sum(compressed_size) AS bytes FROM payload_refs "
                    "GROUP BY retention_class"
                ).fetchall()
            }
            sketch_count = int(connection.execute("SELECT count(*) FROM sketch_buckets").fetchone()[0])
            event_runs = int(connection.execute("SELECT count(*) FROM event_runs").fetchone()[0])
        return {
            **asdict(usage),
            "resolved_budget": asdict(self.database.budget),
            "blob_bytes_by_retention_class": classes,
            "sketch_buckets": sketch_count,
            "event_runs": event_runs,
        }

    def get_degradation_status(self) -> dict:
        status = asdict(self.degradation.status())
        started = status.get("recovery_started_at")
        remaining: float | None = None
        if started and str(status.get("state")) == "RECOVERING":
            elapsed = (
                datetime.now(UTC)
                - datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            ).total_seconds()
            remaining = max(
                0.0,
                float(self.settings.degradation.recovery_stability_seconds) - elapsed,
            )
        status["recovery_stability_seconds"] = (
            self.settings.degradation.recovery_stability_seconds
        )
        status["recovery_seconds_remaining"] = remaining
        return status

    def support_report(self) -> dict:
        integrity = self.integrity.check(full_database=False)
        capsule = self.compiler.compile(
            task_id=None,
            query="machine-self diagnostics",
            token_budget=self.settings.retrieval.self_capsule_tokens,
        )
        with self.database.read_connection() as connection:
            capabilities = [
                dict(row)
                for row in connection.execute(
                    "SELECT d.capability_id,d.display_name,c.lifecycle_state,c.health,c.trust_state,"
                    "c.confidence,c.observed_at,c.current_task_impact,c.evidence_event_id "
                    "FROM capability_definitions d LEFT JOIN capability_current c "
                    "ON c.capability_id=d.capability_id WHERE d.configured=1 ORDER BY d.capability_id"
                ).fetchall()
            ]
            recent_events = [
                dict(row)
                for row in connection.execute(
                    "SELECT sequence,event_id,occurred_at_utc,source,type,subject,severity,task_id "
                    "FROM exact_events ORDER BY sequence DESC LIMIT 100"
                ).fetchall()
            ]
            sketches = [
                dict(row)
                for row in connection.execute(
                    "SELECT metric_name,scope,bucket_start,bucket_end,mode,sketch_kind,parameters_json,"
                    "item_updates,estimate,lower_bound,upper_bound,sealed,last_updated_at "
                    "FROM sketch_buckets ORDER BY last_updated_at DESC LIMIT 100"
                ).fetchall()
            ]
            active_task = connection.execute(
                "SELECT task_id,title,state,risk_level,updated_at FROM tasks "
                "WHERE state IN ('active','running','focused','interrupted','checkpoint_required') "
                "ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            task_requirements = (
                [
                    dict(row)
                    for row in connection.execute(
                        "SELECT r.capability_id,r.minimum_state,r.unsafe_if_lost,r.reason,"
                        "c.lifecycle_state FROM task_capability_requirements r "
                        "LEFT JOIN capability_current c ON c.capability_id=r.capability_id "
                        "WHERE r.task_id=? ORDER BY r.capability_id",
                        (str(active_task["task_id"]),),
                    ).fetchall()
                ]
                if active_task
                else []
            )
            maintenance = {
                "compaction_jobs": int(
                    connection.execute("SELECT count(*) FROM compaction_jobs").fetchone()[0]
                ),
                "retention_tombstones": int(
                    connection.execute("SELECT count(*) FROM retention_tombstones").fetchone()[0]
                ),
                "pending_outbox": int(
                    connection.execute("SELECT count(*) FROM outbox WHERE emitted_at IS NULL").fetchone()[0]
                ),
            }
        return {
            "capabilities": capabilities,
            "degradation": self.get_degradation_status(),
            "storage": self.get_storage_status(),
            "integrity": asdict(integrity),
            "sketches": sketches,
            "recent_exact_events": recent_events,
            "active_task": dict(active_task) if active_task else None,
            "task_requirements": task_requirements,
            "interoception": capsule.data.get("interoception", {}),
            "context_capsule": {
                "tokens": capsule.token_count,
                "bytes": capsule.byte_count,
                "truncated": capsule.truncated,
                "budget_tokens": self.settings.retrieval.self_capsule_tokens,
            },
            "maintenance": maintenance,
        }
