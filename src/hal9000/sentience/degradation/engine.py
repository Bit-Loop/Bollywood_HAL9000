"""Normative degradation state machine driven only by exact projections."""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime

from hal9000.config import SentienceDegradationSettings
from hal9000.sentience.capabilities.registry import CapabilityTransition
from hal9000.sentience.capabilities.task_impact import ImpactLevel
from hal9000.sentience.degradation.episodes import DegradationStatus
from hal9000.sentience.degradation.revalidation import RevalidationService
from hal9000.sentience.degradation.severity import from_impact, maximum
from hal9000.sentience.event_envelope import EventEnvelope
from hal9000.sentience.models import (
    CapabilityLifecycle,
    DegradationSeverity,
    DegradationState,
    EventOrigin,
    RetentionClass,
    Sensitivity,
    Severity,
)
from hal9000.sentience.storage.database import SentienceDatabase

_RESTORED = {CapabilityLifecycle.READY.value}


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class DegradationEngine:
    """There is deliberately no sketch/interoception entry point on this class."""

    def __init__(
        self,
        database: SentienceDatabase,
        boot_id: str,
        settings: SentienceDegradationSettings,
        *,
        nominal_profile: str = "hal-full",
    ) -> None:
        self.database = database
        self.boot_id = boot_id
        self.settings = settings
        self.nominal_profile = nominal_profile
        self.revalidation = RevalidationService(database, boot_id)

    def status(self) -> DegradationStatus:
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM degradation_episodes ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return DegradationStatus.from_row(row) if row else DegradationStatus(DegradationState.NOMINAL)

    def on_transition(
        self,
        transition: CapabilityTransition,
        *,
        active_profile: str,
        fallback_model: str | None = None,
        at: datetime | None = None,
        monotonic_ns: int | None = None,
    ) -> DegradationStatus:
        stamp, monotonic = self._timing(at, monotonic_ns)
        active = self._active_row()
        if transition.current is CapabilityLifecycle.READY:
            if active is not None:
                return self._reconcile_recovery(active, active_profile, stamp, monotonic)
            return self.status()
        if not transition.material or transition.expected:
            if active is not None:
                return self._reconcile_recovery(
                    active, active_profile, stamp, monotonic
                )
            return self.status()
        if transition.task_impact not in {ImpactLevel.COGNITIVE, ImpactLevel.CRITICAL}:
            return self.status()

        if active is None:
            active = self._recent_recovered_for_flap(stamp, monotonic)
        if active is None:
            self._start_episode(
                transition, active_profile, fallback_model, stamp, monotonic
            )
        else:
            self._aggregate_loss(
                active, transition, active_profile, fallback_model, stamp, monotonic
            )
        return self.status()

    def tick(
        self, *, at: datetime | None = None, monotonic_ns: int | None = None
    ) -> DegradationStatus:
        stamp, monotonic = self._timing(at, monotonic_ns)
        row = self._active_row()
        if row is None:
            return self.status()
        state = DegradationState(str(row["state"]))
        if state is DegradationState.DEGRADING:
            if self._elapsed_seconds(
                row,
                wall_field="started_at",
                monotonic_field="started_monotonic_ns",
                stamp=stamp,
                monotonic_ns=monotonic,
            ) >= self.settings.aggregation_window_seconds:
                if self._all_required_restored(row):
                    self._cancel_transient(row, stamp, monotonic)
                else:
                    self._enter_degraded(row, stamp, monotonic)
        elif state is DegradationState.RECOVERING:
            if not self._all_required_restored(row):
                self._return_to_degraded(
                    row, str(row["active_profile"]), stamp, monotonic
                )
            else:
                if self._elapsed_seconds(
                    row,
                    wall_field="recovery_started_at",
                    monotonic_field="recovery_started_monotonic_ns",
                    stamp=stamp,
                    monotonic_ns=monotonic,
                ) >= self.settings.recovery_stability_seconds:
                    self._finish_recovery(row, stamp, monotonic)
        return self.status()

    def record_conclusion(
        self,
        claim_reference: str,
        reason: str,
        *,
        at: datetime | None = None,
    ) -> str:
        row = self._active_row()
        if row is None or str(row["state"]) not in {
            DegradationState.DEGRADED.value,
            DegradationState.RECOVERING.value,
        }:
            raise RuntimeError("conclusions require revalidation only during degradation")
        return self.revalidation.add(str(row["episode_id"]), claim_reference, reason, at=at)

    def _active_row(self):
        with self.database.read_connection() as connection:
            return connection.execute(
                "SELECT * FROM degradation_episodes WHERE state!='NOMINAL' "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()

    def _recent_recovered_for_flap(self, stamp: datetime, monotonic_ns: int):
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM degradation_episodes WHERE state='NOMINAL' AND recovered_at IS NOT NULL "
                "ORDER BY recovered_at DESC LIMIT 1"
            ).fetchone()
        if row and self._elapsed_seconds(
            row,
            wall_field="recovered_at",
            monotonic_field="recovered_monotonic_ns",
            stamp=stamp,
            monotonic_ns=monotonic_ns,
        ) < self.settings.flap_suppression_seconds:
            return row
        return None

    def _event_task(self, transition: CapabilityTransition) -> str | None:
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT task_id FROM exact_events WHERE event_id=?", (transition.event_id,)
            ).fetchone()
        return str(row["task_id"]) if row and row["task_id"] else None

    def _required_snapshot(self, lost: str, task_id: str | None) -> list[str]:
        with self.database.read_connection() as connection:
            rows = connection.execute(
                "SELECT d.capability_id,c.lifecycle_state FROM capability_definitions d "
                "LEFT JOIN capability_current c ON c.capability_id=d.capability_id "
                "WHERE d.configured=1 AND d.nominal_requirement='required'"
            ).fetchall()
            required = {
                str(row["capability_id"])
                for row in rows
                if str(row["lifecycle_state"] or "") in _RESTORED
            }
            if task_id:
                required.update(
                    str(row[0])
                    for row in connection.execute(
                        "SELECT capability_id FROM task_capability_requirements "
                        "WHERE task_id=? AND required=1",
                        (task_id,),
                    ).fetchall()
                )
        required.add(lost)
        return sorted(required)

    def _start_episode(
        self,
        transition: CapabilityTransition,
        active_profile: str,
        fallback_model: str | None,
        stamp: datetime,
        monotonic_ns: int,
    ) -> None:
        episode_id = str(uuid.uuid4())
        task_id = self._event_task(transition)
        severity = from_impact(transition.task_impact)
        required = self._required_snapshot(transition.capability_id, task_id)
        event = self._event(
            "degradation.started",
            episode_id,
            stamp,
            Severity.ERROR if severity is DegradationSeverity.CRITICAL else Severity.WARNING,
            {
                "state": DegradationState.DEGRADING.value,
                "lost_capabilities": [transition.capability_id],
                "cause_event_ids": [transition.event_id],
                "severity": severity.value,
                "active_profile": active_profile,
            },
            task_id=task_id,
            causation_id=transition.event_id,
            monotonic_ns=monotonic_ns,
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "INSERT INTO degradation_episodes(episode_id,state,started_at,nominal_profile,"
                "active_profile,severity,lost_capabilities_json,affected_tasks_json,fallback_model,"
                "cause_event_ids_json,last_transition_event_id,required_capabilities_json,"
                "timer_boot_id,started_monotonic_ns) "
                "VALUES(?, 'DEGRADING', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    episode_id,
                    event.occurred_at_utc,
                    self.nominal_profile,
                    active_profile,
                    severity.value,
                    json.dumps([transition.capability_id]),
                    json.dumps([task_id] if task_id else []),
                    fallback_model,
                    json.dumps([transition.event_id]),
                    event.event_id,
                    json.dumps(required),
                    self.boot_id,
                    monotonic_ns,
                ),
            )
            if severity is DegradationSeverity.CRITICAL and task_id:
                connection.execute(
                    "UPDATE tasks SET state='checkpoint_required',updated_at=? WHERE task_id=? "
                    "AND state NOT IN ('completed','cancelled')",
                    (event.occurred_at_utc, task_id),
                )

        self.database.append_exact_event(event, projection=project)

    def _aggregate_loss(
        self,
        row,
        transition: CapabilityTransition,
        active_profile: str,
        fallback_model: str | None,
        stamp: datetime,
        monotonic_ns: int,
    ) -> None:
        episode_id = str(row["episode_id"])
        lost = list(json.loads(str(row["lost_capabilities_json"])))
        causes = list(json.loads(str(row["cause_event_ids_json"])))
        tasks = list(json.loads(str(row["affected_tasks_json"])))
        required = list(json.loads(str(row["required_capabilities_json"])))
        task_id = self._event_task(transition)
        for sequence, value in (
            (lost, transition.capability_id),
            (causes, transition.event_id),
            (tasks, task_id),
        ):
            if value and value not in sequence:
                sequence.append(value)
        for capability in self._required_snapshot(transition.capability_id, task_id):
            if capability not in required:
                required.append(capability)
        severity = maximum(DegradationSeverity(str(row["severity"])), from_impact(transition.task_impact))
        event = self._event(
            "degradation.loss.aggregated",
            episode_id,
            stamp,
            Severity.ERROR if severity is DegradationSeverity.CRITICAL else Severity.WARNING,
            {"lost_capability": transition.capability_id, "severity": severity.value},
            task_id=task_id,
            causation_id=transition.event_id,
            monotonic_ns=monotonic_ns,
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "UPDATE degradation_episodes SET state=?,active_profile=?,severity=?,"
                "lost_capabilities_json=?,affected_tasks_json=?,fallback_model=COALESCE(?,fallback_model),"
                "cause_event_ids_json=?,required_capabilities_json=?,recovery_started_at=NULL,"
                "recovered_at=NULL,recovered_monotonic_ns=NULL,last_transition_event_id=? "
                "WHERE episode_id=?",
                (
                    DegradationState.DEGRADED.value
                    if str(row["state"]) in {"RECOVERING", "NOMINAL"}
                    else str(row["state"]),
                    active_profile,
                    severity.value,
                    json.dumps(sorted(lost)),
                    json.dumps(sorted(tasks)),
                    fallback_model,
                    json.dumps(causes),
                    json.dumps(sorted(required)),
                    event.event_id,
                    episode_id,
                ),
            )
            if severity is DegradationSeverity.CRITICAL and task_id:
                connection.execute(
                    "UPDATE tasks SET state='checkpoint_required',updated_at=? WHERE task_id=? "
                    "AND state NOT IN ('completed','cancelled')",
                    (event.occurred_at_utc, task_id),
                )

        self.database.append_exact_event(event, projection=project)

    def _all_required_restored(self, row) -> bool:
        required = tuple(json.loads(str(row["required_capabilities_json"])))
        if not required:
            return True
        placeholders = ",".join("?" for _ in required)
        with self.database.read_connection() as connection:
            states = {
                str(item["capability_id"]): str(item["lifecycle_state"])
                for item in connection.execute(
                    f"SELECT capability_id,lifecycle_state FROM capability_current "
                    f"WHERE capability_id IN ({placeholders})",
                    required,
                ).fetchall()
            }
        return all(states.get(capability) in _RESTORED for capability in required)

    def _reconcile_recovery(
        self, row, active_profile: str, stamp: datetime, monotonic_ns: int
    ) -> DegradationStatus:
        if not self._all_required_restored(row):
            if str(row["state"]) == DegradationState.RECOVERING.value:
                self._return_to_degraded(row, active_profile, stamp, monotonic_ns)
            return self.status()
        if str(row["state"]) == DegradationState.DEGRADING.value:
            self._cancel_transient(row, stamp, monotonic_ns)
        elif str(row["state"]) == DegradationState.DEGRADED.value:
            self._start_recovery(row, active_profile, stamp, monotonic_ns)
        return self.status()

    def _enter_degraded(self, row, stamp: datetime, monotonic_ns: int) -> None:
        episode_id = str(row["episode_id"])
        outbox_id = str(row["phrase_outbox_id"] or uuid.uuid4())
        event = self._event(
            "degradation.aggregation.closed",
            episode_id,
            stamp,
            Severity.ERROR if str(row["severity"]) == "critical" else Severity.WARNING,
            {"state": "DEGRADED", "outbox_id": outbox_id},
            monotonic_ns=monotonic_ns,
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "UPDATE degradation_episodes SET state='DEGRADED',aggregation_closed_at=?,"
                "phrase_outbox_id=?,last_transition_event_id=? WHERE episode_id=?",
                (event.occurred_at_utc, outbox_id, event.event_id, episode_id),
            )
            connection.execute(
                "INSERT OR IGNORE INTO outbox(outbox_id,idempotency_key,kind,payload_json,"
                "created_at,available_at) VALUES(?,?,?,?,?,?)",
                (
                    outbox_id,
                    f"degradation-phrase:{episode_id}",
                    "degradation_phrase",
                    json.dumps(
                        {"episode_id": episode_id, "text": self.settings.phrase},
                        separators=(",", ":"),
                    ),
                    event.occurred_at_utc,
                    event.occurred_at_utc,
                ),
            )

        self.database.append_exact_event(event, projection=project)

    def _cancel_transient(self, row, stamp: datetime, monotonic_ns: int) -> None:
        episode_id = str(row["episode_id"])
        event = self._event(
            "degradation.transient.recovered",
            episode_id,
            stamp,
            Severity.INFO,
            {"state": "NOMINAL", "phrase_emitted": False},
            monotonic_ns=monotonic_ns,
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "UPDATE degradation_episodes SET state='NOMINAL',recovered_at=?,"
                "recovered_monotonic_ns=?,last_transition_event_id=? WHERE episode_id=?",
                (event.occurred_at_utc, monotonic_ns, event.event_id, episode_id),
            )

        self.database.append_exact_event(event, projection=project)

    def _start_recovery(
        self, row, active_profile: str, stamp: datetime, monotonic_ns: int
    ) -> None:
        episode_id = str(row["episode_id"])
        event = self._event(
            "degradation.recovery.started",
            episode_id,
            stamp,
            Severity.INFO,
            {"state": "RECOVERING", "active_profile": active_profile},
            monotonic_ns=monotonic_ns,
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "UPDATE degradation_episodes SET state='RECOVERING',active_profile=?,"
                "recovery_started_at=?,recovery_started_monotonic_ns=?,timer_boot_id=?,"
                "last_transition_event_id=? WHERE episode_id=?",
                (
                    active_profile,
                    event.occurred_at_utc,
                    monotonic_ns,
                    self.boot_id,
                    event.event_id,
                    episode_id,
                ),
            )

        self.database.append_exact_event(event, projection=project)

    def _return_to_degraded(
        self, row, active_profile: str, stamp: datetime, monotonic_ns: int
    ) -> None:
        episode_id = str(row["episode_id"])
        event = self._event(
            "degradation.recovery.flapped",
            episode_id,
            stamp,
            Severity.WARNING,
            {"state": "DEGRADED", "active_profile": active_profile},
            monotonic_ns=monotonic_ns,
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "UPDATE degradation_episodes SET state='DEGRADED',active_profile=?,"
                "recovery_started_at=NULL,recovery_started_monotonic_ns=NULL,"
                "last_transition_event_id=? WHERE episode_id=?",
                (active_profile, event.event_id, episode_id),
            )

        self.database.append_exact_event(event, projection=project)

    def _finish_recovery(self, row, stamp: datetime, monotonic_ns: int) -> None:
        episode_id = str(row["episode_id"])
        outbox_id = str(row["recovery_phrase_outbox_id"] or uuid.uuid4())
        event = self._event(
            "degradation.recovery.stable",
            episode_id,
            stamp,
            Severity.INFO,
            {"state": "NOMINAL", "outbox_id": outbox_id},
            monotonic_ns=monotonic_ns,
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "UPDATE degradation_episodes SET state='NOMINAL',active_profile=?,recovered_at=?,"
                "recovery_phrase_outbox_id=?,recovered_monotonic_ns=?,"
                "last_transition_event_id=? WHERE episode_id=?",
                (
                    self.nominal_profile,
                    event.occurred_at_utc,
                    outbox_id,
                    monotonic_ns,
                    event.event_id,
                    episode_id,
                ),
            )
            connection.execute(
                "INSERT OR IGNORE INTO outbox(outbox_id,idempotency_key,kind,payload_json,"
                "created_at,available_at) VALUES(?,?,?,?,?,?)",
                (
                    outbox_id,
                    f"degradation-recovery-phrase:{episode_id}",
                    "recovery_phrase",
                    json.dumps(
                        {"episode_id": episode_id, "text": self.settings.recovery_phrase},
                        separators=(",", ":"),
                    ),
                    event.occurred_at_utc,
                    event.occurred_at_utc,
                ),
            )

        self.database.append_exact_event(event, projection=project)

    def _event(
        self,
        event_type: str,
        episode_id: str,
        stamp: datetime,
        severity: Severity,
        payload: dict,
        *,
        task_id: str | None = None,
        causation_id: str | None = None,
        monotonic_ns: int | None = None,
    ) -> EventEnvelope:
        return EventEnvelope.new(
            boot_id=self.boot_id,
            source="hal.degradation",
            event_type=event_type,
            subject=episode_id,
            severity=severity,
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.INTERNAL,
            origin=EventOrigin.OBSERVATION,
            payload=payload,
            task_id=task_id,
            causation_id=causation_id,
            occurred_at=stamp,
            received_at=stamp,
            monotonic_ns=monotonic_ns,
        )

    def _timing(
        self, at: datetime | None, monotonic_ns: int | None
    ) -> tuple[datetime, int]:
        if at is None:
            return datetime.now(UTC), time.monotonic_ns() if monotonic_ns is None else int(monotonic_ns)
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("degradation timestamps must be timezone-aware")
        stamp = at.astimezone(UTC)
        # An explicit wall clock is a deterministic-test/restore surface. Its
        # mapped monotonic value preserves ordering without affecting the
        # production path, which always uses time.monotonic_ns().
        mapped = int(stamp.timestamp() * 1_000_000_000)
        return stamp, mapped if monotonic_ns is None else int(monotonic_ns)

    def _elapsed_seconds(
        self,
        row,
        *,
        wall_field: str,
        monotonic_field: str,
        stamp: datetime,
        monotonic_ns: int,
    ) -> float:
        started_monotonic = row[monotonic_field]
        if row["timer_boot_id"] == self.boot_id and started_monotonic is not None:
            return max(0.0, (monotonic_ns - int(started_monotonic)) / 1_000_000_000)
        wall = row[wall_field]
        if wall is None:
            return 0.0
        return max(0.0, (stamp - _parse(str(wall))).total_seconds())
