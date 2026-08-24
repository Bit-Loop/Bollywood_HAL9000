"""Exact-event exception routing kept independent from approximate awareness."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from hal9000.sentience.models import Severity


class EventRoute(StrEnum):
    EXACT = "exact"
    EVENT_RUN = "event_run"
    SKETCH_ONLY = "sketch_only"
    DROP = "drop"


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    route: EventRoute
    reason: str
    also_update_sketches: bool = False


_EXACT_PREFIXES = (
    "identity.",
    "continuity.",
    "task.",
    "approval.",
    "capability.",
    "action.",
    "commitment.",
    "contradiction.",
    "memory.correction",
    "degradation.",
    "recovery.",
    "security.",
    "integrity.",
    "telemetry.dropped",
    "clock.jump",
)
_SKETCH_PREFIXES = ("resource.", "latency.", "queue.", "utilization.")
_TRANSIENT_TYPES = {"model.token.delta", "ui.animation.frame", "audio.level.frame"}


def route_event(
    event_type: str,
    severity: Severity,
    *,
    first_occurrence: bool = False,
    causal_boundary: bool = False,
    consequential: bool = False,
) -> RoutingDecision:
    if consequential or causal_boundary or event_type.startswith(_EXACT_PREFIXES):
        return RoutingDecision(EventRoute.EXACT, "exact control or causal event", True)
    if first_occurrence and Severity(severity) in {Severity.ERROR, Severity.CRITICAL}:
        return RoutingDecision(EventRoute.EXACT, "first high-severity fingerprint", True)
    if event_type in _TRANSIENT_TYPES:
        return RoutingDecision(EventRoute.DROP, "live-only transient")
    if event_type.startswith(_SKETCH_PREFIXES) or event_type.endswith(".sample"):
        return RoutingDecision(EventRoute.SKETCH_ONLY, "high-rate metric", False)
    return RoutingDecision(EventRoute.EVENT_RUN, "repeatable observation", True)
