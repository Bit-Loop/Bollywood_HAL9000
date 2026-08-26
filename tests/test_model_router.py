from __future__ import annotations

from hal9000.config import HermesSettings
from hal9000.hermes.router import ModelOption, ResourceSnapshot, TaskAwareModelRouter


def _inventory() -> tuple[ModelOption, ...]:
    return (
        ModelOption("openai-codex", "gpt-5.6-terra", "ChatGPT", authenticated=True),
        ModelOption("openai-codex", "gpt-5.6-sol", "ChatGPT", authenticated=True),
        ModelOption(
            "qwen-ollama",
            "qwen3-coder-hermes:30b-a3b-q8_0",
            "Ollama",
            authenticated=True,
            local=True,
            resident_memory_mb=32768,
        ),
        ModelOption(
            "qwen-ollama",
            "qwen2.5-coder-hermes:7b-q6_K",
            "Ollama",
            authenticated=True,
            local=True,
            resident_memory_mb=7000,
        ),
    )


def test_task_aware_router_uses_terra_for_conversation_and_sol_for_coding() -> None:
    router = TaskAwareModelRouter(HermesSettings().router)

    ordinary = router.decide("What is next?", _inventory())
    coding = router.decide("Implement and test this repository-wide change", _inventory())

    assert (ordinary.provider, ordinary.model, ordinary.reasoning) == (
        "openai-codex",
        "gpt-5.6-terra",
        "medium",
    )
    assert ordinary.intent_class == "general"
    assert (coding.provider, coding.model, coding.reasoning) == (
        "openai-codex",
        "gpt-5.6-sol",
        "medium",
    )
    assert coding.intent_class == "complex_or_coding"


def test_constrained_policy_keeps_complex_work_on_default_route() -> None:
    settings = HermesSettings().router
    settings.resource_policy = "constrained"

    decision = TaskAwareModelRouter(settings).decide(
        "Implement and test this repository change", _inventory()
    )

    assert decision.intent_class == "complex_or_coding"
    assert decision.model == "gpt-5.6-terra"


def test_unhealthy_remote_route_defers_fallback_authority_to_hermes() -> None:
    inventory = (
        ModelOption(
            "openai-codex",
            "gpt-5.6-terra",
            "ChatGPT",
            authenticated=False,
            available=False,
        ),
    )

    decision = TaskAwareModelRouter(HermesSettings().router).decide(
        "What is next?", inventory
    )

    assert decision.available is True
    assert "Hermes retains authority" in decision.reason


def test_manual_selection_is_sticky_until_automatic_is_restored() -> None:
    settings = HermesSettings().router
    settings.enabled = False
    router = TaskAwareModelRouter(settings)

    decision = router.decide(
        "Implement this",
        _inventory(),
        manual_provider="qwen-ollama",
        manual_model="qwen2.5-coder-hermes:7b-q6_K",
    )

    assert decision.user_override is True
    assert decision.model == "qwen2.5-coder-hermes:7b-q6_K"


def test_offline_local_constrained_mode_chooses_only_a_fitting_small_model() -> None:
    settings = HermesSettings().router
    settings.resource_policy = "offline_local"
    router = TaskAwareModelRouter(settings)
    resources = ResourceSnapshot(
        free_ram_mb=12_000,
        free_vram_mb=8_500,
        observed_monotonic=100.0,
    )

    decision = router.decide(
        "Implement a small patch",
        _inventory(),
        resources=resources,
        now_monotonic=105.0,
    )

    assert decision.provider == "qwen-ollama"
    assert decision.model == "qwen2.5-coder-hermes:7b-q6_K"
    assert any("30b" in item for item in decision.rejected_candidates)


def test_offline_local_never_invents_capacity_when_resource_evidence_is_stale() -> None:
    settings = HermesSettings().router
    settings.resource_policy = "offline_local"
    router = TaskAwareModelRouter(settings)

    decision = router.decide(
        "Hello",
        _inventory(),
        resources=ResourceSnapshot(64_000, 24_000, observed_monotonic=0.0),
        now_monotonic=20.0,
    )

    assert decision.available is False
    assert decision.model == ""
    assert "fresh resource evidence" in decision.reason
