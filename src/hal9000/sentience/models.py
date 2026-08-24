"""Immutable domain values shared by the machine-self planes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RetentionClass(StrEnum):
    FOREVER = "forever"
    LONG = "long"
    EPISODIC = "episodic"
    SHORT = "short"
    TRANSIENT = "transient"
    NEVER = "never"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
    PROHIBITED = "prohibited"


class Severity(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    NOTICE = "notice"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class EventOrigin(StrEnum):
    OBSERVATION = "observation"
    INFERENCE = "inference"
    USER_ASSERTION = "user_assertion"
    MODEL_ASSERTION = "model_assertion"
    CONFIGURATION = "configuration"


class CapabilityLifecycle(StrEnum):
    UNKNOWN = "UNKNOWN"
    DISCOVERED = "DISCOVERED"
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    UNRELIABLE = "UNRELIABLE"
    DENIED = "DENIED"
    UNAVAILABLE = "UNAVAILABLE"
    DISCONNECTED = "DISCONNECTED"
    FAILED = "FAILED"
    RECOVERING = "RECOVERING"
    STALE = "STALE"


class DegradationState(StrEnum):
    NOMINAL = "NOMINAL"
    DEGRADING = "DEGRADING"
    DEGRADED = "DEGRADED"
    RECOVERING = "RECOVERING"


class DegradationSeverity(StrEnum):
    COSMETIC = "cosmetic"
    PERIPHERAL = "peripheral"
    COGNITIVE = "cognitive"
    CRITICAL = "critical"


class Exactness(StrEnum):
    EXACT = "exact"
    APPROXIMATE = "approximate"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class EvidenceHandle:
    reference: str
    kind: str
    exact: bool
    description: str = ""
    expansion_views: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CardinalityEstimate:
    estimate: float
    exact: bool
    lower_bound: float | None
    upper_bound: float | None
    sample_count: int
    mode: str
    metric_name: str = ""
    bucket: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IntegrityResult:
    valid: bool
    detail: str
    checked: int = 0


@dataclass(frozen=True, slots=True)
class StoredEvent:
    sequence: int
    event_id: str
    inserted: bool
