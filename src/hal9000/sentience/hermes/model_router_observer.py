"""Exact, versioned model-profile observer for structured session.info events."""

from __future__ import annotations

from dataclasses import dataclass

from hal9000.sentience.capabilities.registry import CapabilityRegistry, CapabilityTransition
from hal9000.sentience.models import CapabilityLifecycle


@dataclass(frozen=True, slots=True)
class ModelClass:
    name: str
    tier: int | None


# This is a configuration taxonomy, not a statistical guess. Unknown models
# remain unknown and therefore cannot manufacture an authority transition.
_MODEL_TIERS: tuple[tuple[str, ModelClass], ...] = (
    ("gpt-5.6-sol", ModelClass("frontier", 4)),
    ("gpt-5.6-terra", ModelClass("frontier", 4)),
    ("gpt-5.6-luna", ModelClass("capable", 3)),
    ("gpt-5.5", ModelClass("frontier", 4)),
    ("qwen", ModelClass("local", 1)),
    ("llama", ModelClass("local", 1)),
    ("devstral", ModelClass("local", 1)),
    ("local", ModelClass("local", 1)),
)


def classify_model(model: str, provider: str = "") -> ModelClass:
    lowered = f"{provider}/{model}".lower()
    for marker, classification in _MODEL_TIERS:
        if marker in lowered:
            return classification
    return ModelClass("unknown", None)


class ModelRouterObserver:
    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        nominal_model: str,
        nominal_provider: str,
    ) -> None:
        self.registry = registry
        self.nominal_model = nominal_model
        self.nominal_provider = nominal_provider
        self.last_model = ""
        self.last_provider = ""
        self._expected_next: tuple[str, str] | None = None

    def expect_selection(self, provider: str, model: str) -> None:
        self._expected_next = (provider.strip(), model.strip())

    def observe(
        self,
        info: dict,
        *,
        task_id: str | None,
    ) -> tuple[CapabilityTransition, ...]:
        model = str(info.get("model") or "").strip()
        provider = str(info.get("provider") or "").strip()
        if not model:
            return ()
        initial = not self.last_model
        explicitly_selected = self._expected_next == (provider, model)
        expected = explicitly_selected or (
            initial
            and model == self.nominal_model
            and (not self.nominal_provider or provider == self.nominal_provider)
        )
        if explicitly_selected:
            self._expected_next = None
        nominal = classify_model(self.nominal_model, self.nominal_provider)
        active = classify_model(model, provider)
        transitions: list[CapabilityTransition] = []
        if active.tier is not None and nominal.tier is not None and active.tier < nominal.tier and not expected:
            state = CapabilityLifecycle.DEGRADED
            profile = "hal-local-fallback" if active.name == "local" else "hal-reduced"
            reason = f"Hermes reported an automatic model change from {self.nominal_model} to {model}"
        else:
            state = CapabilityLifecycle.READY
            profile = "hal-full" if model == self.nominal_model else "hal-selected"
            reason = f"Hermes session reports {provider or 'automatic'}/{model}"
        current = self._current_or_unknown("primary_reasoning")
        if current != state or model != self.last_model or provider != self.last_provider:
            transitions.append(
                self.registry.transition(
                    "primary_reasoning",
                    state,
                    reason=reason,
                    evidence={"event": "session.info", "provider": provider, "model": model},
                    task_id=task_id,
                    expected=expected,
                    active_profile=profile,
                    replacement_capability=model if state is CapabilityLifecycle.DEGRADED else None,
                    trust_state="verified" if active.tier is not None else "observed_unclassified",
                )
            )
        if "codex" in provider.lower() or "codex" in model.lower():
            current_codex = self._current_or_unknown("codex")
            if current_codex is not CapabilityLifecycle.READY:
                transitions.append(
                    self.registry.transition(
                        "codex",
                        CapabilityLifecycle.READY,
                        reason="Hermes primary provider exposes the Codex runtime",
                        evidence={"provider": provider, "model": model},
                        task_id=task_id,
                        expected=initial,
                    )
                )
        self.last_model, self.last_provider = model, provider
        return tuple(transitions)

    def _current_or_unknown(self, capability: str) -> CapabilityLifecycle:
        try:
            return self.registry.current(capability).state
        except KeyError:
            return CapabilityLifecycle.UNKNOWN
