"""Bounded projection checkpoints for fast, integrity-checked startup."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from hal9000.sentience.event_envelope import EventEnvelope
from hal9000.sentience.models import (
    EventOrigin,
    RetentionClass,
    Sensitivity,
    Severity,
)
from hal9000.sentience.storage.database import SentienceDatabase


@dataclass(frozen=True, slots=True)
class CheckpointRestore:
    state: str
    checkpoint_id: str | None
    sequence: int | None
    events_after: int
    detail: str
    payload: dict[str, Any] | None = None

    @property
    def valid(self) -> bool:
        return self.state in {"fresh", "verified"}


class ProjectionCheckpointService:
    """Checkpoint exact projections without serializing lifetime history.

    Exact events and projections are committed together, so restoration does
    not rerun reducers that have already landed. The checkpoint proves a
    bounded snapshot and verifies the contiguous exact-event tail after its
    watermark.
    """

    NAME = "machine_self"
    VERSION = 1
    MAX_STATE_BYTES = 256 * 1024

    def __init__(self, database: SentienceDatabase, boot_id: str = "") -> None:
        self.database = database
        self.boot_id = boot_id

    def set_boot_id(self, boot_id: str) -> None:
        self.boot_id = boot_id

    def write(self, *, clean_shutdown: bool = False) -> str:
        if not self.boot_id:
            raise RuntimeError("checkpoint writer requires an active boot")
        if not clean_shutdown:
            with self.database.read_connection() as connection:
                prior = connection.execute(
                    "SELECT checkpoint_id,sequence FROM projection_checkpoints "
                    "WHERE projection_name=? AND projection_version=? "
                    "ORDER BY sequence DESC LIMIT 1",
                    (self.NAME, self.VERSION),
                ).fetchone()
                maximum = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(sequence),0) FROM exact_events"
                    ).fetchone()[0]
                )
            if prior is not None and int(prior["sequence"]) >= maximum:
                return str(prior["checkpoint_id"])
        payload = self._snapshot()
        encoded = self._encode_state(payload)
        checkpoint_id = str(uuid.uuid4())
        event = EventEnvelope.new(
            boot_id=self.boot_id,
            source="hal.continuity",
            event_type="continuity.checkpoint.created",
            subject=self.NAME,
            severity=Severity.INFO,
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.INTERNAL,
            origin=EventOrigin.OBSERVATION,
            payload={
                "checkpoint_id": checkpoint_id,
                "projection_version": self.VERSION,
                "clean_shutdown": clean_shutdown,
            },
            idempotency_key=None,
            internal=True,
        )

        def project(connection, sequence: int) -> None:
            checksum = self._checksum(sequence, encoded)
            connection.execute(
                "INSERT INTO projection_checkpoints(checkpoint_id,projection_name,"
                "projection_version,sequence,created_at,state_json,checksum,clean_shutdown) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    checkpoint_id,
                    self.NAME,
                    self.VERSION,
                    sequence,
                    event.occurred_at_utc,
                    encoded,
                    checksum,
                    int(clean_shutdown),
                ),
            )
            connection.execute(
                "UPDATE boot_sessions SET checkpoint_sequence=? WHERE boot_id=?",
                (sequence, self.boot_id),
            )
            # Keep recent recovery points plus the latest clean checkpoint.
            connection.execute(
                "DELETE FROM projection_checkpoints WHERE projection_name=? "
                "AND checkpoint_id IN (SELECT checkpoint_id FROM projection_checkpoints "
                "WHERE projection_name=? ORDER BY sequence DESC LIMIT -1 OFFSET 32) "
                "AND clean_shutdown=0",
                (self.NAME, self.NAME),
            )

        self.database.append_exact_event(event, projection=project)
        return checkpoint_id

    def restore(self) -> CheckpointRestore:
        with self.database.read_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM projection_checkpoints WHERE projection_name=? "
                "AND projection_version=? ORDER BY sequence DESC LIMIT 32",
                (self.NAME, self.VERSION),
            ).fetchall()
            maximum = int(
                connection.execute("SELECT COALESCE(MAX(sequence),0) FROM exact_events").fetchone()[0]
            )
        if not rows:
            return CheckpointRestore("fresh", None, None, maximum, "no prior checkpoint")
        failures: list[str] = []
        for row in rows:
            sequence = int(row["sequence"])
            state_json = str(row["state_json"])
            if sequence > maximum:
                failures.append(f"{row['checkpoint_id']}: watermark exceeds ledger")
                continue
            if str(row["checksum"]) != self._checksum(sequence, state_json):
                failures.append(f"{row['checkpoint_id']}: checksum mismatch")
                continue
            try:
                state = json.loads(state_json)
            except json.JSONDecodeError:
                failures.append(f"{row['checkpoint_id']}: state JSON malformed")
                continue
            continuity = self.database.verify_sequence_continuity(after_sequence=sequence)
            if not continuity.valid:
                failures.append(f"{row['checkpoint_id']}: {continuity.detail}")
                continue
            return CheckpointRestore(
                "verified",
                str(row["checkpoint_id"]),
                sequence,
                maximum - sequence,
                "checkpoint and exact-event tail verified",
                state,
            )
        return CheckpointRestore(
            "invalid",
            None,
            None,
            maximum,
            "; ".join(failures[:8]) or "no valid checkpoint",
        )

    def _snapshot(self) -> dict[str, Any]:
        with self.database.read_connection() as connection:
            identity = connection.execute(
                "SELECT canonical_name,role,instance_id,lineage_id,lineage_verified,incarnation_id,"
                "integrity_state,evidence_event_id FROM identity_state WHERE singleton=1"
            ).fetchone()
            capabilities = [
                dict(row)
                for row in connection.execute(
                    "SELECT capability_id,lifecycle_state,health,permission_scope,trust_state,"
                    "confidence,observed_at,freshness_deadline,evidence_event_id,"
                    "replacement_capability,active_profile,current_task_impact "
                    "FROM capability_current ORDER BY capability_id"
                ).fetchall()
            ]
            active_tasks = [
                dict(row)
                for row in connection.execute(
                    "SELECT task_id,title,state,updated_at,risk_level,current_checkpoint_id,"
                    "unresolved_json FROM tasks WHERE state NOT IN ('completed','cancelled') "
                    "ORDER BY updated_at DESC LIMIT 32"
                ).fetchall()
            ]
            degradation = connection.execute(
                "SELECT episode_id,state,severity,lost_capabilities_json,affected_tasks_json,"
                "phrase_emitted,recovery_phrase_emitted,last_transition_event_id "
                "FROM degradation_episodes WHERE state!='NOMINAL' ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            counts = {
                name: int(connection.execute(query).fetchone()[0])
                for name, query in {
                    "active_tasks": "SELECT count(*) FROM tasks WHERE state NOT IN ('completed','cancelled')",
                    "open_commitments": "SELECT count(*) FROM commitments WHERE state='open'",
                    "open_contradictions": "SELECT count(*) FROM contradictions WHERE state='open'",
                    "pending_approvals": "SELECT count(*) FROM approvals WHERE decided_at IS NULL",
                    "uncertain_actions": "SELECT count(*) FROM consequential_actions WHERE state='uncertain'",
                    "pending_outbox": "SELECT count(*) FROM outbox WHERE emitted_at IS NULL",
                }.items()
            }
        return {
            "projection": self.NAME,
            "version": self.VERSION,
            "identity": dict(identity) if identity else None,
            "capabilities": capabilities,
            "active_tasks": active_tasks,
            "active_degradation": dict(degradation) if degradation else None,
            "counts": counts,
        }

    def _encode_state(self, payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if len(encoded.encode("utf-8")) > self.MAX_STATE_BYTES:
            raise ValueError("projection checkpoint exceeded its bounded state budget")
        return encoded

    @classmethod
    def _checksum(cls, sequence: int, state_json: str) -> str:
        canonical = f"{cls.NAME}\0{cls.VERSION}\0{sequence}\0".encode() + state_json.encode()
        return "sha256:" + hashlib.sha256(canonical).hexdigest()
