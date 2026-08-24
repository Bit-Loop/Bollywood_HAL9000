"""Evidence-preserving conclusions made under a reduced capability profile."""

from __future__ import annotations

import uuid
from datetime import datetime

from hal9000.sentience.event_envelope import EventEnvelope
from hal9000.sentience.models import EventOrigin, RetentionClass, Sensitivity, Severity
from hal9000.sentience.storage.database import SentienceDatabase

_INLINE_REFERENCE_LIMIT = 256
_REVALIDATION_NAMESPACE = uuid.UUID("d611244f-a870-4fd5-bc46-c0f8d6331b48")


class RevalidationService:
    def __init__(self, database: SentienceDatabase, boot_id: str) -> None:
        self.database = database
        self.boot_id = boot_id

    def add(
        self,
        episode_id: str,
        claim_reference: str,
        reason: str,
        *,
        at: datetime | None = None,
    ) -> str:
        if not claim_reference.strip() or not reason.strip():
            raise ValueError("revalidation claim and reason are required")
        claim_reference = claim_reference.strip()[:1024]
        reason = reason.strip()[:2000]
        # A deterministic row identifier makes the exact projection safe to
        # retry after a process crash, including a retry with a new event UUID.
        identifier = str(
            uuid.uuid5(_REVALIDATION_NAMESPACE, f"{episode_id}\0{claim_reference}")
        )
        event = EventEnvelope.new(
            boot_id=self.boot_id,
            source="hal.degradation",
            event_type="degradation.conclusion.revalidation_required",
            subject=episode_id,
            severity=Severity.WARNING,
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.INTERNAL,
            origin=EventOrigin.OBSERVATION,
            payload={"claim_reference": claim_reference, "reason": reason},
            idempotency_key=f"revalidation:{episode_id}:{claim_reference}",
            occurred_at=at,
            received_at=at,
        )

        def project(connection, _sequence: int) -> None:
            row = connection.execute(
                "SELECT conclusions_requiring_revalidation_json FROM degradation_episodes "
                "WHERE episode_id=?",
                (episode_id,),
            ).fetchone()
            if row is None:
                raise KeyError(episode_id)
            import json

            claims = list(json.loads(str(row[0])))[:_INLINE_REFERENCE_LIMIT]
            if (
                claim_reference not in claims
                and len(claims) < _INLINE_REFERENCE_LIMIT
            ):
                claims.append(claim_reference)
            connection.execute(
                "INSERT OR IGNORE INTO revalidation_items(revalidation_id,degradation_episode_id,"
                "claim_reference,reason,state,created_at) VALUES(?,?,?,?,'pending',?)",
                (identifier, episode_id, claim_reference, reason, event.occurred_at_utc),
            )
            connection.execute(
                "UPDATE degradation_episodes SET conclusions_requiring_revalidation_json=? "
                "WHERE episode_id=?",
                (json.dumps(claims, separators=(",", ":")), episode_id),
            )

        self.database.append_exact_event(event, projection=project)
        return identifier

    def resolve(
        self,
        revalidation_id: str,
        *,
        outcome: str,
        result_event_id: str,
        at: datetime | None = None,
    ) -> None:
        event = EventEnvelope.new(
            boot_id=self.boot_id,
            source="hal.degradation",
            event_type="degradation.conclusion.revalidated",
            subject=revalidation_id,
            severity=Severity.INFO,
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.INTERNAL,
            origin=EventOrigin.OBSERVATION,
            payload={"outcome": outcome, "result_event_id": result_event_id},
            idempotency_key=f"revalidation-resolved:{revalidation_id}:{result_event_id}",
            occurred_at=at,
            received_at=at,
        )

        def project(connection, _sequence: int) -> None:
            cursor = connection.execute(
                "UPDATE revalidation_items SET state=?,resolved_at=?,result_event_id=? "
                "WHERE revalidation_id=? AND state='pending'",
                (outcome, event.occurred_at_utc, result_event_id, revalidation_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(revalidation_id)

        self.database.append_exact_event(event, projection=project)
