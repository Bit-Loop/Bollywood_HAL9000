"""Versioned deterministic formulas over named exact/approximate inputs."""

from __future__ import annotations

from dataclasses import dataclass

from hal9000.sentience.interoception.model import InteroceptionSnapshot, MachineDimension
from hal9000.sentience.models import CapabilityLifecycle


@dataclass(frozen=True, slots=True)
class CapabilityInput:
    capability_id: str
    category: str
    configured: bool
    weight: float
    state: CapabilityLifecycle
    required_for_task: bool = False


@dataclass(frozen=True, slots=True)
class ContinuityInput:
    chain_valid: bool
    checkpoint_replayed: bool
    lease_held: bool
    missing_events: int
    database_valid: bool
    recovered_unclean: bool


@dataclass(frozen=True, slots=True)
class ContextInput:
    conversation_tokens: int
    system_prompt_tokens: int
    tool_schema_tokens: int
    retrieved_memory_tokens: int
    maximum_context_tokens: int
    recent_truncations: int
    historical_p95_utilization: float | None


@dataclass(frozen=True, slots=True)
class EpistemicInput:
    unresolved_contradictions: int
    stale_facts: int
    low_confidence_inferences: int
    missing_evidence: int
    uncertain_actions: int
    retrieval_disagreements: int


@dataclass(frozen=True, slots=True)
class FailureInput:
    estimate: float | None
    exact: bool | None
    lower_bound: float | None
    upper_bound: float | None
    baseline_state: str
    baseline_median: float | None
    baseline_mad: float | None
    heavy_hitter_ratio: float | None
    severity: float | None
    novelty: float | None
    persistence: float | None
    unresolved_episodes: int
    current_task_relevance: float | None


@dataclass(frozen=True, slots=True)
class ResourceInput:
    current_pressure: dict[str, float]
    percentile_position: dict[str, float]
    queue_depth: int
    audio_underrun_rate: float | None


@dataclass(frozen=True, slots=True)
class InteroceptionInputs:
    active_model_class: str | None
    capabilities: tuple[CapabilityInput, ...]
    continuity: ContinuityInput
    context: ContextInput
    epistemic: EpistemicInput
    failures: FailureInput
    resources: ResourceInput
    observed_at: str


_STATE_SCORE = {
    CapabilityLifecycle.READY: 1.0,
    CapabilityLifecycle.RECOVERING: 0.70,
    CapabilityLifecycle.DEGRADED: 0.65,
    CapabilityLifecycle.UNRELIABLE: 0.45,
    CapabilityLifecycle.INITIALIZING: 0.35,
    CapabilityLifecycle.DISCOVERED: 0.25,
    CapabilityLifecycle.UNKNOWN: None,
    CapabilityLifecycle.DENIED: 0.0,
    CapabilityLifecycle.UNAVAILABLE: 0.0,
    CapabilityLifecycle.DISCONNECTED: 0.0,
    CapabilityLifecycle.FAILED: 0.0,
    CapabilityLifecycle.STALE: 0.0,
}
_MODEL_SCORE = {
    "frontier": 1.0,
    "capable": 0.82,
    "local": 0.55,
    "emergency": 0.25,
}


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _weighted_capabilities(
    capabilities: tuple[CapabilityInput, ...], categories: set[str]
) -> tuple[float | None, float, tuple[str, ...]]:
    selected = [item for item in capabilities if item.configured and item.category in categories]
    known = [(item, _STATE_SCORE[item.state]) for item in selected if _STATE_SCORE[item.state] is not None]
    if not selected or not known:
        return None, 0.0, tuple(item.capability_id for item in selected)
    denominator = sum(item.weight for item, _score in known)
    value = sum(item.weight * float(score) for item, score in known) / denominator if denominator else None
    confidence = len(known) / len(selected)
    return value, confidence, tuple(item.capability_id for item in selected)


class InteroceptionCalculator:
    def __init__(self, *, formula_version: int = 1) -> None:
        if formula_version != 1:
            raise ValueError("unsupported interoception formula version")
        self.formula_version = formula_version

    def calculate(self, inputs: InteroceptionInputs) -> InteroceptionSnapshot:
        cognitive_caps, cognitive_confidence, cognitive_sources = _weighted_capabilities(
            inputs.capabilities, {"cognition", "specialist"}
        )
        model_score = _MODEL_SCORE.get(inputs.active_model_class or "")
        cognitive_values = [value for value in (model_score, cognitive_caps) if value is not None]
        cognitive = sum(cognitive_values) / len(cognitive_values) if cognitive_values else None
        cognitive_confidence = (
            (cognitive_confidence + (1.0 if model_score is not None else 0.0)) / 2
            if cognitive_values
            else 0.0
        )

        continuity_values = [
            float(inputs.continuity.chain_valid),
            float(inputs.continuity.checkpoint_replayed),
            float(inputs.continuity.lease_held),
            1.0 if inputs.continuity.missing_events == 0 else _clamp(1 - inputs.continuity.missing_events / 10),
            float(inputs.continuity.database_valid),
            0.75 if inputs.continuity.recovered_unclean else 1.0,
        ]
        continuity = sum(continuity_values) / len(continuity_values)
        sensory, sensory_confidence, sensory_sources = _weighted_capabilities(
            inputs.capabilities, {"sensor"}
        )
        agency, agency_confidence, agency_sources = _weighted_capabilities(
            inputs.capabilities, {"agency", "safety"}
        )

        context = inputs.context
        if context.maximum_context_tokens > 0:
            used = (
                context.conversation_tokens
                + context.system_prompt_tokens
                + context.tool_schema_tokens
                + context.retrieved_memory_tokens
            )
            current_pressure = _clamp(used / context.maximum_context_tokens)
            history = context.historical_p95_utilization
            pressure = (
                _clamp(current_pressure * 0.8 + history * 0.2)
                if history is not None
                else current_pressure
            )
            pressure = _clamp(pressure + min(0.25, context.recent_truncations * 0.05))
            context_confidence = 1.0
        else:
            pressure, context_confidence = None, 0.0

        epistemic = inputs.epistemic
        epistemic_score = _clamp(
            epistemic.unresolved_contradictions * 0.12
            + epistemic.stale_facts * 0.03
            + epistemic.low_confidence_inferences * 0.04
            + epistemic.missing_evidence * 0.08
            + epistemic.uncertain_actions * 0.20
            + epistemic.retrieval_disagreements * 0.08
        )

        failures = inputs.failures
        failure_dimension = MachineDimension(
            failures.estimate,
            1.0 if failures.exact else 0.85 if failures.estimate is not None else 0.0,
            inputs.observed_at,
            ("unique_error_fingerprints",),
            approximate=failures.exact is False,
            baseline_state=failures.baseline_state,
            lower_bound=failures.lower_bound,
            upper_bound=failures.upper_bound,
        )
        anomaly_inputs = (
            failures.estimate,
            failures.baseline_median,
            failures.baseline_mad,
            failures.novelty,
            failures.severity,
            failures.persistence,
            failures.current_task_relevance,
        )
        if failures.baseline_state != "ready" or any(value is None for value in anomaly_inputs):
            anomaly = None
            anomaly_confidence = 0.0
        else:
            diversity_deviation = _clamp(
                (float(failures.estimate) - float(failures.baseline_median))
                / max(1.0, 3 * float(failures.baseline_mad))
            )
            heavy = _clamp(failures.heavy_hitter_ratio or 0.0)
            unresolved = _clamp(failures.unresolved_episodes / 5)
            # Repetition has only ten percent weight; diversity and novelty
            # dominate, so one noisy error cannot manufacture high anomaly.
            anomaly = _clamp(
                diversity_deviation * 0.30
                + float(failures.novelty) * 0.25
                + float(failures.severity) * 0.15
                + float(failures.persistence) * 0.10
                + unresolved * 0.05
                + float(failures.current_task_relevance) * 0.05
                + heavy * 0.10
            )
            anomaly_confidence = 0.85 if failures.exact is False else 1.0

        resources = inputs.resources
        resource_values = [
            _clamp(value) for value in resources.current_pressure.values()
        ] + [_clamp(value) for value in resources.percentile_position.values()]
        if resources.queue_depth > 0:
            resource_values.append(_clamp(resources.queue_depth / 100))
        if resources.audio_underrun_rate is not None:
            resource_values.append(_clamp(resources.audio_underrun_rate))
        resource_pressure = (
            max(resource_values) * 0.7 + sum(resource_values) / len(resource_values) * 0.3
            if resource_values
            else None
        )

        stamp = inputs.observed_at
        return InteroceptionSnapshot(
            self.formula_version,
            MachineDimension(cognitive, cognitive_confidence, stamp, ("active_model_class",) + cognitive_sources),
            MachineDimension(
                continuity,
                1.0,
                stamp,
                ("event_chain", "checkpoint", "canonical_lease", "database_integrity", "boot_recovery"),
            ),
            MachineDimension(sensory, sensory_confidence, stamp, sensory_sources),
            MachineDimension(agency, agency_confidence, stamp, agency_sources),
            MachineDimension(
                pressure,
                context_confidence,
                stamp,
                ("conversation_tokens", "system_prompt_tokens", "tool_schema_tokens", "retrieved_tokens", "context_limit"),
                approximate=context.historical_p95_utilization is not None,
            ),
            MachineDimension(
                epistemic_score,
                1.0,
                stamp,
                ("contradictions", "stale_facts", "missing_evidence", "uncertain_actions", "retrieval_disagreement"),
            ),
            MachineDimension(
                anomaly,
                anomaly_confidence,
                stamp,
                ("failure_diversity", "heavy_hitters", "theta_novelty", "severity", "persistence", "task_relevance"),
                approximate=True,
                baseline_state=failures.baseline_state,
            ),
            failure_dimension,
            MachineDimension(
                resource_pressure,
                0.9 if resource_values else 0.0,
                stamp,
                tuple(resources.current_pressure) + tuple(resources.percentile_position),
                approximate=bool(resources.percentile_position),
            ),
        )
