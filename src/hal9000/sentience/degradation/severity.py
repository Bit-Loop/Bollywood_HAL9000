"""Exact task-impact to normative degradation severity mapping."""

from __future__ import annotations

from hal9000.sentience.capabilities.task_impact import ImpactLevel
from hal9000.sentience.models import DegradationSeverity

_ORDER = {
    DegradationSeverity.COSMETIC: 0,
    DegradationSeverity.PERIPHERAL: 1,
    DegradationSeverity.COGNITIVE: 2,
    DegradationSeverity.CRITICAL: 3,
}


def from_impact(impact: ImpactLevel) -> DegradationSeverity:
    return {
        ImpactLevel.NONE: DegradationSeverity.PERIPHERAL,
        ImpactLevel.COSMETIC: DegradationSeverity.COSMETIC,
        ImpactLevel.PERIPHERAL: DegradationSeverity.PERIPHERAL,
        ImpactLevel.COGNITIVE: DegradationSeverity.COGNITIVE,
        ImpactLevel.CRITICAL: DegradationSeverity.CRITICAL,
    }[impact]


def maximum(
    first: DegradationSeverity, second: DegradationSeverity
) -> DegradationSeverity:
    return first if _ORDER[first] >= _ORDER[second] else second
