"""Immutable machine-interoception output model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MachineDimension:
    value: float | None
    confidence: float
    observed_at: str
    sources: tuple[str, ...]
    approximate: bool = False
    baseline_state: str | None = None
    lower_bound: float | None = None
    upper_bound: float | None = None


@dataclass(frozen=True, slots=True)
class InteroceptionSnapshot:
    formula_version: int
    cognitive_capacity: MachineDimension
    continuity_integrity: MachineDimension
    sensory_coverage: MachineDimension
    agency_reach: MachineDimension
    context_pressure: MachineDimension
    epistemic_uncertainty: MachineDimension
    anomaly_pressure: MachineDimension
    failure_diversity: MachineDimension
    resource_pressure: MachineDimension
