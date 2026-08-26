"""Deterministic, evidence-labelled model intent routing for HAL.

Hermes remains responsible for sessions, provider fallback, retry, approvals,
and model execution.  This module only chooses the desired pre-turn route.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from hal9000.config import HermesRouterSettings


@dataclass(frozen=True, slots=True)
class ModelOption:
    provider: str
    model: str
    provider_name: str = ""
    authenticated: bool = True
    available: bool = True
    local: bool = False
    resident_memory_mb: int | None = None

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    free_ram_mb: int
    free_vram_mb: int
    observed_monotonic: float


@dataclass(frozen=True, slots=True)
class RouteDecision:
    decision_id: str
    policy_version: int
    intent_class: str
    provider: str
    model: str
    reasoning: str
    available: bool
    user_override: bool
    reason: str
    rejected_candidates: tuple[str, ...] = ()

    def event_payload(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "policy_version": self.policy_version,
            "intent_class": self.intent_class,
            "provider": self.provider,
            "model": self.model,
            "reasoning": self.reasoning,
            "available": self.available,
            "user_override": self.user_override,
            "reason": self.reason,
            "rejected_candidates": list(self.rejected_candidates),
        }


class TaskAwareModelRouter:
    POLICY_VERSION = 1
    _COMPLEX_MARKERS = (
        "implement",
        "debug",
        "fix ",
        "bug",
        "repository",
        "repo",
        "code",
        "test",
        "build",
        "commit",
        "push",
        "deploy",
        "install",
        "migration",
        "terminal",
        "filesystem",
        "consequential",
    )

    def __init__(self, settings: HermesRouterSettings) -> None:
        self.settings = settings

    def decide(
        self,
        prompt: str,
        inventory: tuple[ModelOption, ...] | list[ModelOption],
        *,
        manual_provider: str = "",
        manual_model: str = "",
        resources: ResourceSnapshot | None = None,
        now_monotonic: float | None = None,
    ) -> RouteDecision:
        options = tuple(inventory)
        if not self.settings.enabled:
            available = bool(manual_model)
            return self._decision(
                "manual",
                manual_provider,
                manual_model,
                self.settings.default_reasoning,
                available,
                True,
                "sticky user-selected route" if available else "no manual model is selected",
            )
        intent = self._intent(prompt)
        if self.settings.resource_policy == "offline_local":
            return self._local_decision(
                intent,
                options,
                resources=resources,
                now_monotonic=time.monotonic() if now_monotonic is None else now_monotonic,
            )
        use_complex = (
            intent == "complex_or_coding"
            and self.settings.policy == "task_aware"
            and self.settings.resource_policy != "constrained"
        )
        provider = self.settings.complex_provider if use_complex else self.settings.default_provider
        model = self.settings.complex_model if use_complex else self.settings.default_model
        reasoning = (
            self.settings.complex_reasoning if use_complex else self.settings.default_reasoning
        )
        match = self._find(options, provider, model)
        desired_healthy = match is None or (match.available and match.authenticated)
        reason = (
            "exact task policy selected the complex/coding route"
            if use_complex
            else "exact task policy selected the normal remote route"
        )
        if not desired_healthy:
            reason = (
                "the desired remote route is unhealthy; Hermes retains authority "
                "for provider and model fallback"
            )
        return self._decision(intent, provider, model, reasoning, True, False, reason)

    def _local_decision(
        self,
        intent: str,
        options: tuple[ModelOption, ...],
        *,
        resources: ResourceSnapshot | None,
        now_monotonic: float,
    ) -> RouteDecision:
        if (
            resources is None
            or now_monotonic - resources.observed_monotonic
            > self.settings.resource_freshness_seconds
        ):
            return self._decision(
                intent,
                "",
                "",
                "medium",
                False,
                False,
                "offline-local routing requires fresh resource evidence",
            )
        reserve = 1.0 - self.settings.local_memory_reserve_ratio
        available_memory = max(resources.free_ram_mb, resources.free_vram_mb) * reserve
        accepted: list[ModelOption] = []
        rejected: list[str] = []
        for option in options:
            if not option.local or not option.available or not option.authenticated:
                continue
            estimate = option.resident_memory_mb
            if estimate is None:
                rejected.append(f"{option.key}: missing resident-memory estimate")
                continue
            if estimate > available_memory:
                rejected.append(f"{option.key}: exceeds reserved memory headroom")
                continue
            if self.settings.resource_policy in {"offline_local", "constrained"} and estimate > 10_240:
                rejected.append(f"{option.key}: excluded by constrained local policy")
                continue
            accepted.append(option)
        if not accepted:
            return self._decision(
                intent,
                "",
                "",
                "medium",
                False,
                False,
                "no local model satisfies exact availability and resource guards",
                tuple(rejected),
            )
        selected = min(accepted, key=lambda item: int(item.resident_memory_mb or 2**31))
        return self._decision(
            intent,
            selected.provider,
            selected.model,
            "medium",
            True,
            False,
            "offline-local policy selected the smallest fitting configured model",
            tuple(rejected),
        )

    @classmethod
    def _intent(cls, prompt: str) -> str:
        lowered = " " + " ".join(prompt.lower().split()) + " "
        return (
            "complex_or_coding"
            if any(marker in lowered for marker in cls._COMPLEX_MARKERS)
            else "general"
        )

    @staticmethod
    def _find(
        options: tuple[ModelOption, ...], provider: str, model: str
    ) -> ModelOption | None:
        return next(
            (
                option
                for option in options
                if option.provider == provider and option.model == model
            ),
            None,
        )

    @classmethod
    def _decision(
        cls,
        intent: str,
        provider: str,
        model: str,
        reasoning: str,
        available: bool,
        user_override: bool,
        reason: str,
        rejected: tuple[str, ...] = (),
    ) -> RouteDecision:
        return RouteDecision(
            decision_id=str(uuid.uuid4()),
            policy_version=cls.POLICY_VERSION,
            intent_class=intent,
            provider=provider,
            model=model,
            reasoning=reasoning,
            available=available,
            user_override=user_override,
            reason=reason,
            rejected_candidates=rejected,
        )
