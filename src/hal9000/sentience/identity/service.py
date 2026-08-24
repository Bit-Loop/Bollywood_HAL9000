"""Persistent identity invariants independent of the active language model."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from hal9000.sentience.event_envelope import EventEnvelope, utc_iso
from hal9000.sentience.models import EventOrigin, RetentionClass, Sensitivity, Severity
from hal9000.sentience.storage.database import SentienceDatabase


@dataclass(frozen=True, slots=True)
class Identity:
    canonical_name: str
    role: str
    instance_id: str
    lineage_id: str
    lineage_verified: bool
    incarnation_id: str
    integrity_state: str
    evidence_event_id: str


class IdentityService:
    def __init__(
        self,
        database: SentienceDatabase,
        canonical_name: str = "HAL",
        role: str = "Resident intelligence of this workstation",
    ) -> None:
        self.database = database
        self.canonical_name = canonical_name.strip() or "HAL"
        self.role = role.strip() or "Resident intelligence of this workstation"

    def load_or_create(self) -> Identity:
        with self.database.read_connection() as connection:
            row = connection.execute("SELECT * FROM identity_state WHERE singleton=1").fetchone()
        incarnation_id = str(uuid.uuid4())
        if row is None:
            instance_id = "hal-" + uuid.uuid4().hex[:12]
            lineage_id = str(uuid.uuid4())
            event_type = "identity.created"
            previous_incarnation = None
        else:
            instance_id = str(row["instance_id"])
            lineage_id = str(row["lineage_id"])
            event_type = "identity.incarnation.changed"
            previous_incarnation = str(row["incarnation_id"])
        event = EventEnvelope.new(
            boot_id=incarnation_id,
            source="hal.identity",
            event_type=event_type,
            subject=instance_id,
            severity=Severity.INFO,
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.INTERNAL,
            origin=EventOrigin.OBSERVATION,
            payload={
                "canonical_name": self.canonical_name,
                "role": self.role,
                "instance_id": instance_id,
                "lineage_id": lineage_id,
                "lineage_verified": True,
                "incarnation_id": incarnation_id,
                "previous_incarnation_id": previous_incarnation,
            },
            idempotency_key=f"identity-incarnation:{incarnation_id}",
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "INSERT INTO identity_state(singleton,canonical_name,role,instance_id,lineage_id,"
                "lineage_verified,incarnation_id,integrity_state,updated_at,evidence_event_id) "
                "VALUES(1,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(singleton) DO UPDATE SET canonical_name=excluded.canonical_name,"
                "role=excluded.role,"
                "incarnation_id=excluded.incarnation_id,integrity_state=excluded.integrity_state,"
                "updated_at=excluded.updated_at,evidence_event_id=excluded.evidence_event_id",
                (
                    self.canonical_name,
                    self.role,
                    instance_id,
                    lineage_id,
                    1,
                    incarnation_id,
                    "verified",
                    event.occurred_at_utc,
                    event.event_id,
                ),
            )

        self.database.append_exact_event(event, projection=project)
        return Identity(
            canonical_name=self.canonical_name,
            role=self.role,
            instance_id=instance_id,
            lineage_id=lineage_id,
            lineage_verified=True,
            incarnation_id=incarnation_id,
            integrity_state="verified",
            evidence_event_id=event.event_id,
        )

    def current(self) -> Identity | None:
        with self.database.read_connection() as connection:
            row = connection.execute("SELECT * FROM identity_state WHERE singleton=1").fetchone()
        if row is None:
            return None
        return Identity(
            canonical_name=str(row["canonical_name"]),
            role=str(row["role"]),
            instance_id=str(row["instance_id"]),
            lineage_id=str(row["lineage_id"]),
            lineage_verified=bool(row["lineage_verified"]),
            incarnation_id=str(row["incarnation_id"]),
            integrity_state=str(row["integrity_state"]),
            evidence_event_id=str(row["evidence_event_id"] or ""),
        )

    def mark_integrity_degraded(self, *, boot_id: str, detail: str) -> Identity:
        """Fail continuity closed without inventing a new identity lineage."""

        current = self.current()
        if current is None:
            raise RuntimeError("identity must exist before integrity can be degraded")
        event = EventEnvelope.new(
            boot_id=boot_id,
            source="hal.identity",
            event_type="identity.integrity.degraded",
            subject=current.instance_id,
            severity=Severity.CRITICAL,
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.INTERNAL,
            origin=EventOrigin.OBSERVATION,
            payload={"detail": detail[:2000], "lineage_verified": False},
            idempotency_key=f"identity-integrity-degraded:{boot_id}",
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "UPDATE identity_state SET lineage_verified=0,integrity_state='degraded',"
                "updated_at=?,evidence_event_id=? WHERE singleton=1",
                (event.occurred_at_utc, event.event_id),
            )

        self.database.append_exact_event(event, projection=project)
        return Identity(
            current.canonical_name,
            current.role,
            current.instance_id,
            current.lineage_id,
            False,
            current.incarnation_id,
            "degraded",
            event.event_id,
        )
