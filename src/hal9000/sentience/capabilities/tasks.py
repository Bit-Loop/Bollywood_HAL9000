"""Exact task lifecycle, checkpoints, interruption, and completion ledger."""

from __future__ import annotations

import json
import uuid
from typing import Any

from hal9000.sentience.event_envelope import EventEnvelope
from hal9000.sentience.events.redact import redact_data, redact_text
from hal9000.sentience.models import EventOrigin, RetentionClass, Sensitivity, Severity
from hal9000.sentience.storage.database import SentienceDatabase


class TaskLedger:
    def __init__(self, database: SentienceDatabase, boot_id: str) -> None:
        self.database = database
        self.boot_id = boot_id

    def checkpoint(
        self,
        task_id: str,
        *,
        state: dict[str, Any],
        unresolved: list[Any] | tuple[Any, ...] = (),
        pending_actions: list[Any] | tuple[Any, ...] = (),
        checkpoint_id: str | None = None,
    ) -> str:
        checkpoint_id = checkpoint_id or str(uuid.uuid4())
        clean_state = redact_data(state)
        clean_unresolved = redact_data(list(unresolved))
        clean_pending = redact_data(list(pending_actions))
        event = self._event(
            "task.checkpointed",
            task_id,
            {"checkpoint_id": checkpoint_id, "unresolved": clean_unresolved, "pending_actions": clean_pending},
            idempotency_key=f"task-checkpoint:{checkpoint_id}",
        )

        def project(connection, _sequence: int) -> None:
            exists = connection.execute("SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if exists is None:
                raise KeyError(task_id)
            ordinal = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 FROM task_checkpoints WHERE task_id=?",
                    (task_id,),
                ).fetchone()[0]
            )
            connection.execute(
                "INSERT INTO task_checkpoints(checkpoint_id,task_id,sequence,created_at,state_json,"
                "unresolved_json,pending_actions_json) VALUES(?,?,?,?,?,?,?)",
                (
                    checkpoint_id,
                    task_id,
                    ordinal,
                    event.occurred_at_utc,
                    json.dumps(clean_state, separators=(",", ":"), sort_keys=True),
                    json.dumps(clean_unresolved, separators=(",", ":"), sort_keys=True),
                    json.dumps(clean_pending, separators=(",", ":"), sort_keys=True),
                ),
            )
            connection.execute(
                "UPDATE tasks SET current_checkpoint_id=?,updated_at=?,unresolved_json=? "
                "WHERE task_id=?",
                (
                    checkpoint_id,
                    event.occurred_at_utc,
                    json.dumps(clean_unresolved, separators=(",", ":")),
                    task_id,
                ),
            )

        self.database.append_exact_event(event, projection=project)
        return checkpoint_id

    def finish(self, task_id: str, *, outcome: str, verified: bool) -> bool:
        state = "completed" if verified else "completed_unverified"
        event = self._event(
            "task.completed" if verified else "task.completion.uncertain",
            task_id,
            {"outcome": redact_text(outcome)[:4000], "verified": verified},
            severity=Severity.INFO if verified else Severity.WARNING,
            idempotency_key=f"task-finish:{task_id}:{state}",
        )

        def project(connection, _sequence: int) -> None:
            changed = connection.execute(
                "UPDATE tasks SET state=?,completed_at=?,updated_at=?,exact_completion_event_id=? "
                "WHERE task_id=? AND state NOT IN ('completed','cancelled')",
                (state, event.occurred_at_utc, event.occurred_at_utc, event.event_id, task_id),
            ).rowcount
            if changed != 1:
                raise KeyError(task_id)

        self.database.append_exact_event(event, projection=project)
        return True

    def interrupt(self, task_id: str, *, reason: str, cancelled: bool = False) -> bool:
        state = "cancelled" if cancelled else "interrupted"
        event = self._event(
            "task.cancelled" if cancelled else "task.interrupted",
            task_id,
            {"reason": redact_text(reason)[:2000]},
            severity=Severity.NOTICE,
            idempotency_key=f"task-interrupt:{task_id}:{state}",
        )

        def project(connection, _sequence: int) -> None:
            changed = connection.execute(
                "UPDATE tasks SET state=?,interrupted_at=?,updated_at=? WHERE task_id=? "
                "AND state NOT IN ('completed','cancelled')",
                (state, event.occurred_at_utc, event.occurred_at_utc, task_id),
            ).rowcount
            if changed != 1:
                raise KeyError(task_id)

        self.database.append_exact_event(event, projection=project)
        return True

    def _event(
        self,
        event_type: str,
        task_id: str,
        payload: dict[str, Any],
        *,
        severity: Severity = Severity.INFO,
        idempotency_key: str,
    ) -> EventEnvelope:
        return EventEnvelope.new(
            boot_id=self.boot_id,
            source="hal.tasks",
            event_type=event_type,
            subject=task_id,
            severity=severity,
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.INTERNAL,
            origin=EventOrigin.OBSERVATION,
            payload=payload,
            task_id=task_id,
            idempotency_key=idempotency_key,
        )
