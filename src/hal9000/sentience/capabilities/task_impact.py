"""Deterministic capability-loss impact derived only from exact task state."""

from __future__ import annotations

from enum import StrEnum


class ImpactLevel(StrEnum):
    NONE = "none"
    COSMETIC = "cosmetic"
    PERIPHERAL = "peripheral"
    COGNITIVE = "cognitive"
    CRITICAL = "critical"


def classify_task_impact(
    *,
    nominal_requirement: str,
    material_class: str,
    task_requires: bool,
    unsafe_if_lost: bool,
) -> ImpactLevel:
    if task_requires and unsafe_if_lost:
        return ImpactLevel.CRITICAL
    if task_requires:
        return ImpactLevel.COGNITIVE
    if nominal_requirement == "required" and material_class in {"cognitive", "operational"}:
        return ImpactLevel.COGNITIVE
    if material_class == "cosmetic":
        return ImpactLevel.COSMETIC
    if nominal_requirement == "optional":
        return ImpactLevel.PERIPHERAL
    return ImpactLevel.NONE
