"""Normalized event envelope for exact records and bounded telemetry inputs."""

from __future__ import annotations

import math
import json
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from hal9000.sentience.clock import MachineClock
from hal9000.sentience.models import EventOrigin, RetentionClass, Sensitivity, Severity

SCHEMA_VERSION = 1
MAX_EVENT_PAYLOAD_BYTES = 256 * 1024
_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,254}$")


class EventValidationError(ValueError):
    pass


def utc_iso(value: datetime | None = None) -> str:
    stamp = value or datetime.now(UTC)
    if stamp.tzinfo is None:
        raise EventValidationError("event timestamps must be timezone-aware")
    return stamp.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_subject(value: str, *, fallback: str = "subject") -> str:
    """Produce an envelope-safe label without changing stored display text."""
    clean = re.sub(r"[^a-zA-Z0-9_.:/-]+", "_", str(value).strip()).strip("_")[:255]
    if not clean:
        clean = fallback
    if not clean[0].isalnum():
        clean = "x" + clean[:254]
    return clean


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: str
    schema_version: int
    occurred_at_utc: str
    received_at_utc: str
    monotonic_ns: int
    boot_id: str
    source: str
    type: str
    subject: str
    severity: Severity
    correlation_id: str | None
    causation_id: str | None
    task_id: str | None
    origin: EventOrigin
    confidence: float
    retention_class: RetentionClass
    sensitivity: Sensitivity
    payload: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    internal: bool = False

    @property
    def observed(self) -> bool:
        return self.origin is EventOrigin.OBSERVATION

    @classmethod
    def new(
        cls,
        *,
        boot_id: str,
        source: str,
        event_type: str,
        subject: str,
        severity: Severity,
        retention_class: RetentionClass,
        sensitivity: Sensitivity,
        origin: EventOrigin,
        payload: dict[str, Any],
        confidence: float = 1.0,
        event_id: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        task_id: str | None = None,
        idempotency_key: str | None = None,
        occurred_at: datetime | None = None,
        received_at: datetime | None = None,
        monotonic_ns: int | None = None,
        internal: bool = False,
    ) -> "EventEnvelope":
        envelope = cls(
            event_id=event_id or str(uuid.uuid4()),
            schema_version=SCHEMA_VERSION,
            occurred_at_utc=utc_iso(occurred_at),
            received_at_utc=utc_iso(received_at),
            monotonic_ns=MachineClock.now().monotonic_ns if monotonic_ns is None else monotonic_ns,
            boot_id=boot_id,
            source=source,
            type=event_type,
            subject=subject,
            severity=Severity(severity),
            correlation_id=correlation_id,
            causation_id=causation_id,
            task_id=task_id,
            origin=EventOrigin(origin),
            confidence=float(confidence),
            retention_class=RetentionClass(retention_class),
            sensitivity=Sensitivity(sensitivity),
            payload=payload,
            idempotency_key=idempotency_key,
            internal=internal,
        )
        envelope.validate()
        return envelope

    def validate(self) -> None:
        for label, value in (("event_id", self.event_id), ("boot_id", self.boot_id)):
            try:
                uuid.UUID(value)
            except (ValueError, TypeError, AttributeError) as exc:
                raise EventValidationError(f"{label} must be a UUID") from exc
        for label, value in (("source", self.source), ("type", self.type), ("subject", self.subject)):
            if not isinstance(value, str) or not _NAME.fullmatch(value):
                raise EventValidationError(f"{label} is missing or malformed")
        if self.schema_version != SCHEMA_VERSION:
            raise EventValidationError(f"unsupported event schema version {self.schema_version}")
        if self.monotonic_ns < 0:
            raise EventValidationError("monotonic_ns must not be negative")
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise EventValidationError("confidence must be between 0 and 1")
        if not isinstance(self.payload, dict):
            raise EventValidationError("payload must be an object")
        try:
            payload_bytes = len(
                json.dumps(
                    self.payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            )
        except (TypeError, ValueError, RecursionError) as exc:
            raise EventValidationError("payload must be finite JSON data") from exc
        if payload_bytes > MAX_EVENT_PAYLOAD_BYTES:
            raise EventValidationError("event payload exceeds the bounded envelope limit")
        for label, value in (
            ("correlation_id", self.correlation_id),
            ("causation_id", self.causation_id),
            ("task_id", self.task_id),
            ("idempotency_key", self.idempotency_key),
        ):
            if value is not None and (not isinstance(value, str) or len(value) > 512):
                raise EventValidationError(f"{label} is malformed")
        for label, stamp in (
            ("occurred_at_utc", self.occurred_at_utc),
            ("received_at_utc", self.received_at_utc),
        ):
            try:
                parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except (ValueError, TypeError) as exc:
                raise EventValidationError(f"{label} is malformed") from exc
            if parsed.utcoffset() != datetime.min.replace(tzinfo=UTC).utcoffset():
                raise EventValidationError(f"{label} must use UTC")

    def with_payload(self, payload: dict[str, Any]) -> "EventEnvelope":
        updated = replace(self, payload=payload)
        updated.validate()
        return updated
