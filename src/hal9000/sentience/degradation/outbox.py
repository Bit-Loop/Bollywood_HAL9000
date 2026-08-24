"""Crash-safe idempotent delivery of degradation and recovery phrases."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from hal9000.sentience.event_envelope import EventEnvelope, utc_iso
from hal9000.sentience.models import EventOrigin, RetentionClass, Sensitivity, Severity
from hal9000.sentience.storage.database import SentienceDatabase


@dataclass(frozen=True, slots=True)
class OutboxDelivery:
    outbox_id: str
    kind: str
    channel: str
    text: str


class OutboxDispatcher:
    def __init__(self, database: SentienceDatabase, boot_id: str) -> None:
        self.database = database
        self.boot_id = boot_id
        self.owner = f"dispatcher-{uuid.uuid4()}"

    def dispatch_one(
        self,
        *,
        tts_available: bool,
        speak: Callable[[str], None],
        display: Callable[[str], None],
    ) -> OutboxDelivery | None:
        row = self._claim()
        if row is None:
            return None
        payload = json.loads(str(row["payload_json"]))
        text = str(payload["text"])
        channel = "speech" if tts_available else "transcript"
        try:
            if tts_available:
                try:
                    speak(text)
                except Exception:
                    display(text)
                    channel = "transcript"
            else:
                display(text)
            self._mark_emitted(row, payload, channel)
        except BaseException as exc:
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE outbox SET claimed_at=NULL,claim_owner=NULL,last_error=? "
                    "WHERE outbox_id=? AND emitted_at IS NULL",
                    (str(exc)[:2000], str(row["outbox_id"])),
                )
            raise
        return OutboxDelivery(str(row["outbox_id"]), str(row["kind"]), channel, text)

    def _claim(self):
        now = utc_iso()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM outbox WHERE emitted_at IS NULL AND claimed_at IS NULL "
                "AND (available_at<=? OR kind IN ('degradation_phrase','recovery_phrase')) "
                "ORDER BY created_at,outbox_id LIMIT 1",
                (now,),
            ).fetchone()
            if row is None:
                return None
            changed = connection.execute(
                "UPDATE outbox SET claimed_at=?,claim_owner=?,attempts=attempts+1 "
                "WHERE outbox_id=? AND emitted_at IS NULL AND claimed_at IS NULL",
                (now, self.owner, str(row["outbox_id"])),
            ).rowcount
            return row if changed == 1 else None

    def _mark_emitted(self, row, payload: dict, channel: str) -> None:
        outbox_id = str(row["outbox_id"])
        kind = str(row["kind"])
        episode_id = str(payload["episode_id"])
        event = EventEnvelope.new(
            boot_id=self.boot_id,
            source="hal.degradation.outbox",
            event_type="degradation.phrase.emitted",
            subject=episode_id,
            severity=Severity.INFO,
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.INTERNAL,
            origin=EventOrigin.OBSERVATION,
            payload={"outbox_id": outbox_id, "kind": kind, "channel": channel},
            idempotency_key=f"outbox-emitted:{outbox_id}",
        )

        def project(connection, _sequence: int) -> None:
            connection.execute(
                "UPDATE outbox SET emitted_at=?,delivery_channel=?,last_error=NULL "
                "WHERE outbox_id=? AND emitted_at IS NULL",
                (event.occurred_at_utc, channel, outbox_id),
            )
            column = (
                "phrase_emitted" if kind == "degradation_phrase" else "recovery_phrase_emitted"
            )
            connection.execute(
                f"UPDATE degradation_episodes SET {column}=1 WHERE episode_id=?",
                (episode_id,),
            )

        self.database.append_exact_event(event, projection=project)

    def pending_count(self) -> int:
        with self.database.read_connection() as connection:
            return int(
                connection.execute(
                    "SELECT count(*) FROM outbox WHERE emitted_at IS NULL"
                ).fetchone()[0]
            )
