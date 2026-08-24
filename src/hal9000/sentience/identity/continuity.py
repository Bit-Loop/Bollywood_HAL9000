"""Boot lifecycle, interrupted-work recovery, and continuity truth state."""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass

from hal9000.sentience.event_envelope import EventEnvelope, utc_iso
from hal9000.sentience.models import EventOrigin, RetentionClass, Sensitivity, Severity
from hal9000.sentience.storage.database import SentienceDatabase


@dataclass(frozen=True, slots=True)
class Boot:
    boot_id: str
    started_at: str
    started_monotonic_ns: int
    recovered_from_unclean_shutdown: bool
    interrupted_boot_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContinuityStatus:
    state: str
    boot_id: str | None
    last_shutdown_clean: bool | None
    interrupted_tasks: tuple[str, ...]
    uncertain_actions: tuple[str, ...]
    sequence_integrity: bool
    database_integrity: bool


class ContinuityService:
    def __init__(self, database: SentienceDatabase, incarnation_id: str) -> None:
        self.database = database
        self.incarnation_id = incarnation_id
        self.current_boot: Boot | None = None

    def start_boot(
        self, *, boot_id: str | None = None, checkpoint_sequence: int | None = None
    ) -> Boot:
        boot_id = boot_id or str(uuid.uuid4())
        started_at = utc_iso()
        monotonic_ns = time.monotonic_ns()
        with self.database.read_connection() as connection:
            interrupted_count = int(
                connection.execute(
                    "SELECT count(*) FROM boot_sessions WHERE ended_at IS NULL"
                ).fetchone()[0]
            )
            interrupted = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT boot_id FROM boot_sessions WHERE ended_at IS NULL "
                    "ORDER BY started_at DESC LIMIT 100"
                ).fetchall()
            )
        event = EventEnvelope.new(
            event_id=str(uuid.uuid4()),
            boot_id=boot_id,
            source="hal.continuity",
            event_type="continuity.boot.started",
            subject=boot_id,
            severity=Severity.WARNING if interrupted else Severity.INFO,
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.INTERNAL,
            origin=EventOrigin.OBSERVATION,
            payload={
                "incarnation_id": self.incarnation_id,
                "interrupted_boot_ids": interrupted,
                "interrupted_boot_count": interrupted_count,
                "recovered_from_unclean_shutdown": interrupted_count > 0,
                "checkpoint_sequence": checkpoint_sequence,
            },
            idempotency_key=f"boot-start:{boot_id}",
            monotonic_ns=monotonic_ns,
        )

        def project(connection, _sequence: int) -> None:
            if interrupted:
                connection.execute(
                    "UPDATE boot_sessions SET recovery_state='superseded_unclean',"
                    "ended_at=?,shutdown_clean=0 "
                    "WHERE ended_at IS NULL",
                    (started_at,),
                )
                connection.execute(
                    "UPDATE tasks SET state='interrupted',interrupted_at=?,updated_at=? "
                    "WHERE state IN ('active','running','focused','checkpointing')",
                    (started_at, started_at),
                )
                connection.execute(
                    "UPDATE consequential_actions SET state='uncertain',"
                    "uncertainty_reason='unclean shutdown before a verified outcome' "
                    "WHERE state IN ('pending','running','committing','verifying')"
                )
            connection.execute(
                "INSERT INTO boot_sessions(boot_id,incarnation_id,started_at,started_monotonic_ns,"
                "recovery_state,checkpoint_sequence,process_id) VALUES(?,?,?,?,?,?,?)",
                (
                    boot_id,
                    self.incarnation_id,
                    started_at,
                    monotonic_ns,
                    "recovered_with_uncertainty" if interrupted else "verified",
                    checkpoint_sequence,
                    os.getpid(),
                ),
            )

        self.database.append_exact_event(event, projection=project)
        self.current_boot = Boot(
            boot_id,
            started_at,
            monotonic_ns,
            interrupted_count > 0,
            interrupted,
        )
        return self.current_boot

    def finish_boot(self, *, clean: bool) -> bool:
        if self.current_boot is None:
            return False
        boot = self.current_boot
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT ended_at FROM boot_sessions WHERE boot_id=?", (boot.boot_id,)
            ).fetchone()
        if row is None or row["ended_at"] is not None:
            return False
        ended_at = utc_iso()
        ended_monotonic_ns = time.monotonic_ns()
        event = EventEnvelope.new(
            boot_id=boot.boot_id,
            source="hal.continuity",
            event_type="continuity.boot.finished",
            subject=boot.boot_id,
            severity=Severity.INFO if clean else Severity.WARNING,
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.INTERNAL,
            origin=EventOrigin.OBSERVATION,
            payload={"clean": clean},
            idempotency_key=f"boot-finish:{boot.boot_id}",
            monotonic_ns=ended_monotonic_ns,
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "UPDATE boot_sessions SET ended_at=?,ended_monotonic_ns=?,shutdown_clean=?,"
                "recovery_state=? WHERE boot_id=? AND ended_at IS NULL",
                (
                    ended_at,
                    ended_monotonic_ns,
                    int(clean),
                    "clean" if clean else "unclean",
                    boot.boot_id,
                ),
            )

        self.database.append_exact_event(event, projection=project)
        return True

    def status(self) -> ContinuityStatus:
        with self.database.read_connection() as connection:
            boot = connection.execute(
                "SELECT * FROM boot_sessions ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            tasks = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT task_id FROM tasks WHERE state='interrupted' ORDER BY updated_at DESC LIMIT 100"
                ).fetchall()
            )
            actions = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT action_id FROM consequential_actions WHERE state='uncertain' "
                    "ORDER BY started_at DESC LIMIT 100"
                ).fetchall()
            )
        chain = self.database.verify_control_chain().valid
        integrity = self.database.quick_integrity_check().valid
        recovery = str(boot["recovery_state"]) if boot else "uninitialized"
        state = (
            "integrity_degraded"
            if not chain or not integrity
            else "recovered_with_uncertainty"
            if tasks or actions or recovery == "recovered_with_uncertainty"
            else "verified"
        )
        return ContinuityStatus(
            state=state,
            boot_id=str(boot["boot_id"]) if boot else None,
            last_shutdown_clean=(bool(boot["shutdown_clean"]) if boot and boot["shutdown_clean"] is not None else None),
            interrupted_tasks=tasks,
            uncertain_actions=actions,
            sequence_integrity=chain,
            database_integrity=integrity,
        )
