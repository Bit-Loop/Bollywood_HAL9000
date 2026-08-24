"""Exact approval, consequential-action, and verification ledger."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from hal9000.sentience.event_envelope import EventEnvelope
from hal9000.sentience.events.redact import redact_text
from hal9000.sentience.models import EventOrigin, RetentionClass, Sensitivity, Severity
from hal9000.sentience.storage.database import SentienceDatabase


@dataclass(frozen=True, slots=True)
class ActionRecord:
    action_id: str
    tool_call_id: str
    state: str
    event_id: str


class ExactActionLedger:
    def __init__(self, database: SentienceDatabase, boot_id: str) -> None:
        self.database = database
        self.boot_id = boot_id

    def approval_requested(
        self,
        request_id: str,
        *,
        description: str,
        task_id: str | None,
        scope: str = "once",
    ) -> str:
        approval_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"hal-approval:{request_id}"))
        clean = redact_text(description)[:4000]
        event = self._event(
            "approval.requested",
            approval_id,
            {"hermes_request_id": request_id, "description": clean, "scope": scope},
            task_id=task_id,
            idempotency_key=f"approval-request:{request_id}",
            severity=Severity.NOTICE,
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "INSERT OR IGNORE INTO approvals(approval_id,hermes_request_id,task_id,requested_at,"
                "scope,description,request_event_id) VALUES(?,?,?,?,?,?,?)",
                (approval_id, request_id, task_id, event.occurred_at_utc, scope, clean, event.event_id),
            )

        self.database.append_exact_event(event, projection=project)
        return approval_id

    def approval_resolved(self, request_id: str, *, choice: str) -> bool:
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE hermes_request_id=?", (request_id,)
            ).fetchone()
        if row is None:
            return False
        normalized = choice if choice in {"once", "session", "always", "deny"} else "deny"
        event = self._event(
            "approval.resolved",
            str(row["approval_id"]),
            {"choice": normalized},
            task_id=str(row["task_id"]) if row["task_id"] else None,
            idempotency_key=f"approval-resolution:{request_id}:{normalized}",
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "UPDATE approvals SET decided_at=?,choice=?,decision_event_id=? "
                "WHERE hermes_request_id=? AND decided_at IS NULL",
                (event.occurred_at_utc, normalized, event.event_id, request_id),
            )

        self.database.append_exact_event(event, projection=project)
        return True

    def start_action(
        self,
        tool_call_id: str,
        *,
        action_type: str,
        target: str,
        task_id: str | None,
        approval_id: str | None = None,
    ) -> ActionRecord:
        action_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"hal-action:{tool_call_id}"))
        clean_target = redact_text(target)[:4000]
        event = self._event(
            "action.started",
            action_id,
            {"tool_call_id": tool_call_id, "action_type": action_type, "target": clean_target},
            task_id=task_id,
            idempotency_key=f"action-start:{tool_call_id}",
            severity=Severity.NOTICE,
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "INSERT OR IGNORE INTO consequential_actions(action_id,task_id,tool_call_id,"
                "action_type,target,state,started_at,event_id,approval_id) "
                "VALUES(?,?,?,?,?,'running',?,?,?)",
                (
                    action_id,
                    task_id,
                    tool_call_id,
                    action_type,
                    clean_target,
                    event.occurred_at_utc,
                    event.event_id,
                    approval_id,
                ),
            )

        self.database.append_exact_event(event, projection=project)
        return ActionRecord(action_id, tool_call_id, "running", event.event_id)

    def finish_action(
        self,
        tool_call_id: str,
        *,
        succeeded: bool,
        summary: str,
        payload_ref: str | None = None,
    ) -> bool:
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM consequential_actions WHERE tool_call_id=?", (tool_call_id,)
            ).fetchone()
        if row is None:
            return False
        state = "completed_unverified" if succeeded else "failed"
        clean_summary = redact_text(summary)[:4000]
        event = self._event(
            "action.completed" if succeeded else "action.failed",
            str(row["action_id"]),
            {"succeeded": succeeded, "summary": clean_summary, "payload_ref": payload_ref},
            task_id=str(row["task_id"]) if row["task_id"] else None,
            idempotency_key=f"action-finish:{tool_call_id}:{state}",
            severity=Severity.INFO if succeeded else Severity.ERROR,
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "UPDATE consequential_actions SET state=?,completed_at=?,result_summary=?,payload_ref=? "
                "WHERE tool_call_id=? AND state IN ('running','pending','uncertain')",
                (state, event.occurred_at_utc, clean_summary, payload_ref, tool_call_id),
            )

        self.database.append_exact_event(event, projection=project)
        return True

    def verify_action(
        self,
        action_id: str,
        *,
        outcome: str,
        statement: str,
        payload_ref: str | None = None,
    ) -> str:
        verification_id = str(uuid.uuid4())
        clean_statement = redact_text(statement)[:4000]
        event = self._event(
            "action.verified",
            action_id,
            {"verification_id": verification_id, "outcome": outcome, "statement": clean_statement},
            idempotency_key=f"action-verification:{action_id}:{verification_id}",
            severity=Severity.INFO if outcome == "success" else Severity.WARNING,
        )

        def project(connection, _sequence: int) -> None:
            exists = connection.execute(
                "SELECT 1 FROM consequential_actions WHERE action_id=?", (action_id,)
            ).fetchone()
            if exists is None:
                raise KeyError(action_id)
            connection.execute(
                "INSERT INTO action_verifications(verification_id,action_id,verified_at,outcome,"
                "statement,event_id,payload_ref) VALUES(?,?,?,?,?,?,?)",
                (
                    verification_id,
                    action_id,
                    event.occurred_at_utc,
                    outcome,
                    clean_statement,
                    event.event_id,
                    payload_ref,
                ),
            )
            connection.execute(
                "UPDATE consequential_actions SET state=? WHERE action_id=?",
                ("verified" if outcome == "success" else "verification_failed", action_id),
            )

        self.database.append_exact_event(event, projection=project)
        return verification_id

    def _event(
        self,
        event_type: str,
        subject: str,
        payload: dict,
        *,
        task_id: str | None = None,
        idempotency_key: str,
        severity: Severity = Severity.INFO,
    ) -> EventEnvelope:
        return EventEnvelope.new(
            boot_id=self.boot_id,
            source="hal.control",
            event_type=event_type,
            subject=subject,
            severity=severity,
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.INTERNAL,
            origin=EventOrigin.OBSERVATION,
            payload=payload,
            task_id=task_id,
            idempotency_key=idempotency_key,
        )
