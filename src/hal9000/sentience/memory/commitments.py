"""Exact commitments and triggers with evidence-backed resolution."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from hal9000.sentience.event_envelope import EventEnvelope
from hal9000.sentience.events.redact import redact_data, redact_text
from hal9000.sentience.models import EventOrigin, RetentionClass, Sensitivity, Severity
from hal9000.sentience.storage.database import SentienceDatabase


@dataclass(frozen=True, slots=True)
class Commitment:
    commitment_id: str
    statement: str
    trigger: dict
    state: str
    evidence_event_id: str


class CommitmentStore:
    def __init__(self, database: SentienceDatabase, boot_id: str) -> None:
        self.database = database
        self.boot_id = boot_id

    def create(
        self,
        statement: str,
        *,
        trigger: dict,
        evidence_event_id: str,
        task_id: str | None = None,
        origin: EventOrigin = EventOrigin.USER_ASSERTION,
    ) -> Commitment:
        identity = str(uuid.uuid4())
        clean_statement = redact_text(statement)[:10000]
        clean_trigger = redact_data(trigger)
        event = EventEnvelope.new(
            boot_id=self.boot_id,
            source="hal.memory",
            event_type="commitment.created",
            subject=identity,
            severity=Severity.NOTICE,
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.INTERNAL,
            origin=EventOrigin(origin),
            payload={
                "statement": clean_statement,
                "trigger": clean_trigger,
                "source_evidence": evidence_event_id,
            },
            task_id=task_id,
            idempotency_key=f"commitment-create:{identity}",
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "INSERT INTO commitments(commitment_id,task_id,statement,trigger_json,state,"
                "created_at,evidence_event_id) VALUES(?,?,?,?, 'open',?,?)",
                (
                    identity,
                    task_id,
                    clean_statement,
                    json.dumps(clean_trigger, sort_keys=True, separators=(",", ":")),
                    event.occurred_at_utc,
                    evidence_event_id,
                ),
            )
            connection.execute(
                "INSERT INTO retrieval_documents(reference,source_table,source_id,document_kind,"
                "title,body,subject,task_id,confidence,exact,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,1.0,1,?,?)",
                (
                    f"commitment:{identity}",
                    "commitments",
                    identity,
                    "commitment",
                    "Open commitment",
                    clean_statement,
                    "commitment",
                    task_id,
                    event.occurred_at_utc,
                    event.occurred_at_utc,
                ),
            )

        self.database.append_exact_event(event, projection=project)
        return Commitment(identity, clean_statement, clean_trigger, "open", evidence_event_id)

    def resolve(self, commitment_id: str, *, evidence_event_id: str) -> bool:
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT statement,task_id,state FROM commitments WHERE commitment_id=?",
                (commitment_id,),
            ).fetchone()
        if row is None or row["state"] != "open":
            return False
        event = EventEnvelope.new(
            boot_id=self.boot_id,
            source="hal.memory",
            event_type="commitment.resolved",
            subject=commitment_id,
            severity=Severity.INFO,
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.INTERNAL,
            origin=EventOrigin.OBSERVATION,
            payload={"resolution_evidence": evidence_event_id},
            task_id=row["task_id"],
            idempotency_key=f"commitment-resolve:{commitment_id}",
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "UPDATE commitments SET state='resolved',resolved_at=?,resolution_event_id=? "
                "WHERE commitment_id=? AND state='open'",
                (event.occurred_at_utc, evidence_event_id, commitment_id),
            )
            connection.execute(
                "DELETE FROM retrieval_documents WHERE source_table='commitments' AND source_id=?",
                (commitment_id,),
            )

        self.database.append_exact_event(event, projection=project)
        return True

    def open_count(self) -> int:
        with self.database.read_connection() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM commitments WHERE state='open'"
                ).fetchone()[0]
            )
