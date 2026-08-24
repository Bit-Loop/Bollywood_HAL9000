"""Compact semantic facts with downward evidence references."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from hal9000.sentience.event_envelope import EventEnvelope, canonical_subject
from hal9000.sentience.events.redact import redact_text
from hal9000.sentience.models import EventOrigin, RetentionClass, Sensitivity, Severity
from hal9000.sentience.storage.database import SentienceDatabase


@dataclass(frozen=True, slots=True)
class Fact:
    fact_id: str
    subject: str
    statement: str
    source_type: EventOrigin
    exact: bool
    confidence: float
    evidence_refs: tuple[str, ...]
    stale: bool = False


class FactStore:
    def __init__(self, database: SentienceDatabase, boot_id: str) -> None:
        self.database = database
        self.boot_id = boot_id

    def create(
        self,
        *,
        subject: str,
        statement: str,
        source_type: EventOrigin,
        exact: bool,
        confidence: float,
        evidence_refs: tuple[str, ...],
        stale_after: str | None = None,
        pinned: bool = False,
    ) -> Fact:
        if not evidence_refs:
            raise ValueError("a fact requires at least one evidence reference")
        fact_id = str(uuid.uuid4())
        clean_subject = redact_text(subject)[:1000]
        clean_statement = redact_text(statement)[:10000]
        event = EventEnvelope.new(
            boot_id=self.boot_id,
            source="hal.memory",
            event_type="memory.fact.created",
            subject=canonical_subject(clean_subject, fallback="fact"),
            severity=Severity.INFO,
            retention_class=RetentionClass.LONG,
            sensitivity=Sensitivity.INTERNAL,
            origin=EventOrigin(source_type),
            confidence=confidence,
            payload={
                "fact_id": fact_id,
                "statement": clean_statement,
                "exact": exact,
                "evidence_refs": evidence_refs,
            },
            idempotency_key=f"fact-create:{fact_id}",
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "INSERT INTO semantic_facts(fact_id,subject,statement,source_type,exact,confidence,"
                "stale_after,created_at,updated_at,pinned) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    fact_id,
                    clean_subject,
                    clean_statement,
                    EventOrigin(source_type).value,
                    int(exact),
                    confidence,
                    stale_after,
                    event.occurred_at_utc,
                    event.occurred_at_utc,
                    int(pinned),
                ),
            )
            connection.executemany(
                "INSERT INTO fact_evidence(fact_id,evidence_ref) VALUES(?,?)",
                ((fact_id, reference) for reference in evidence_refs),
            )
            connection.execute(
                "INSERT INTO retrieval_documents(reference,source_table,source_id,document_kind,"
                "title,body,subject,confidence,exact,pinned,created_at,updated_at,metadata_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"fact:{fact_id}",
                    "semantic_facts",
                    fact_id,
                    "fact",
                    clean_subject,
                    clean_statement,
                    clean_subject,
                    confidence,
                    int(exact),
                    int(pinned),
                    event.occurred_at_utc,
                    event.occurred_at_utc,
                    '{"untrusted":false}',
                ),
            )

        self.database.append_exact_event(event, projection=project)
        return Fact(
            fact_id,
            clean_subject,
            clean_statement,
            EventOrigin(source_type),
            exact,
            confidence,
            evidence_refs,
        )
