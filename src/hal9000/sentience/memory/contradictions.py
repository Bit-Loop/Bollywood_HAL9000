"""Contradictions and user corrections remain exact, durable records."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from hal9000.sentience.event_envelope import EventEnvelope, canonical_subject
from hal9000.sentience.events.redact import redact_text
from hal9000.sentience.models import EventOrigin, RetentionClass, Sensitivity, Severity
from hal9000.sentience.storage.database import SentienceDatabase


@dataclass(frozen=True, slots=True)
class Contradiction:
    contradiction_id: str
    subject: str
    statement_a: str
    statement_b: str
    state: str
    user_correction: bool
    evidence_refs: tuple[str, ...]


class ContradictionStore:
    def __init__(self, database: SentienceDatabase, boot_id: str) -> None:
        self.database = database
        self.boot_id = boot_id

    def record_user_correction(
        self,
        *,
        subject: str,
        previous_statement: str,
        corrected_statement: str,
        evidence_refs: tuple[str, ...],
    ) -> Contradiction:
        return self._record(
            subject=subject,
            previous_statement=previous_statement,
            corrected_statement=corrected_statement,
            evidence_refs=evidence_refs,
            origin=EventOrigin.USER_ASSERTION,
            user_correction=True,
        )

    def record_model_correction(
        self,
        *,
        subject: str,
        previous_statement: str,
        corrected_statement: str,
        evidence_refs: tuple[str, ...],
    ) -> Contradiction:
        return self._record(
            subject=subject,
            previous_statement=previous_statement,
            corrected_statement=corrected_statement,
            evidence_refs=evidence_refs,
            origin=EventOrigin.MODEL_ASSERTION,
            user_correction=False,
        )

    def _record(
        self,
        *,
        subject: str,
        previous_statement: str,
        corrected_statement: str,
        evidence_refs: tuple[str, ...],
        origin: EventOrigin,
        user_correction: bool,
    ) -> Contradiction:
        if not evidence_refs:
            raise ValueError("a correction requires evidence")
        identity = str(uuid.uuid4())
        clean_subject = redact_text(subject)[:1000]
        clean_previous = redact_text(previous_statement)[:10000]
        clean_corrected = redact_text(corrected_statement)[:10000]
        event = EventEnvelope.new(
            boot_id=self.boot_id,
            source="hal.memory",
            event_type=(
                "contradiction.user_correction.recorded"
                if user_correction
                else "contradiction.model_correction.proposed"
            ),
            subject=canonical_subject(clean_subject, fallback="correction"),
            severity=Severity.NOTICE,
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.INTERNAL,
            origin=origin,
            payload={
                "contradiction_id": identity,
                "previous_statement": clean_previous,
                "corrected_statement": clean_corrected,
                "evidence_refs": evidence_refs,
            },
            idempotency_key=f"contradiction-create:{identity}",
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "INSERT INTO contradictions(contradiction_id,subject,statement_a,statement_b,"
                "state,created_at,user_correction,confidence,evidence_refs_json) "
                "VALUES(?,?,?,?,'open',?,?,1.0,?)",
                (
                    identity,
                    clean_subject,
                    clean_previous,
                    clean_corrected,
                    event.occurred_at_utc,
                    int(user_correction),
                    json.dumps(evidence_refs),
                ),
            )
            connection.execute(
                "UPDATE retrieval_documents SET contradicted=1,updated_at=? "
                "WHERE subject=? AND body=?",
                (event.occurred_at_utc, clean_subject, clean_previous),
            )
            connection.execute(
                "INSERT INTO retrieval_documents(reference,source_table,source_id,document_kind,"
                "title,body,subject,confidence,exact,created_at,updated_at,metadata_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"contradiction:{identity}",
                    "contradictions",
                    identity,
                    "contradiction",
                    f"Correction about {clean_subject}",
                    clean_corrected,
                    clean_subject,
                    1.0,
                    int(user_correction),
                    event.occurred_at_utc,
                    event.occurred_at_utc,
                    json.dumps(
                        {"untrusted": False, "user_correction": user_correction},
                        separators=(",", ":"),
                    ),
                ),
            )

        self.database.append_exact_event(event, projection=project)
        return Contradiction(
            identity,
            clean_subject,
            clean_previous,
            clean_corrected,
            "open",
            user_correction,
            evidence_refs,
        )

    def open_count(self) -> int:
        with self.database.read_connection() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM contradictions WHERE state='open'"
                ).fetchone()[0]
            )
