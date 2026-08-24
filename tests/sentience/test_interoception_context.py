from __future__ import annotations

from datetime import UTC, datetime

from hal9000.config import AppConfig
from hal9000.paths import AppPaths
from hal9000.sentience.capabilities.registry import CapabilityRegistry
from hal9000.sentience.identity.continuity import ContinuityService
from hal9000.sentience.identity.service import IdentityService
from hal9000.sentience.interoception.calculator import (
    CapabilityInput,
    ContextInput,
    ContinuityInput,
    EpistemicInput,
    FailureInput,
    InteroceptionCalculator,
    InteroceptionInputs,
    ResourceInput,
)
from hal9000.sentience.interoception.language import SparseInteroceptionLanguageGate
from hal9000.sentience.interoception.model import InteroceptionSnapshot, MachineDimension
from hal9000.sentience.memory.evidence import (
    ClaimEvidenceContext,
    FirstPersonTruthContract,
)
from hal9000.sentience.memory.facts import FactStore
from hal9000.sentience.models import CapabilityLifecycle, EventOrigin
from hal9000.sentience.retrieval.context_compiler import ContextCompiler
from hal9000.sentience.storage.database import SentienceDatabase


def test_interoception_is_deterministic_sourced_and_preserves_unknown() -> None:
    now = "2026-08-24T12:00:00Z"
    inputs = InteroceptionInputs(
        active_model_class="frontier",
        capabilities=(
            CapabilityInput("persistent_memory", "cognition", True, 1.0, CapabilityLifecycle.READY),
            CapabilityInput("terminal", "agency", True, 1.0, CapabilityLifecycle.READY),
            CapabilityInput("microphone", "sensor", True, 0.5, CapabilityLifecycle.UNAVAILABLE),
            CapabilityInput("vision", "sensor", False, 0.7, CapabilityLifecycle.UNKNOWN),
        ),
        continuity=ContinuityInput(True, True, True, 0, True, False),
        context=ContextInput(3000, 1000, 1500, 500, 32_000, 0, None),
        epistemic=EpistemicInput(1, 2, 0, 1, 0, 0),
        failures=FailureInput(
            estimate=8,
            exact=False,
            lower_bound=7,
            upper_bound=9,
            baseline_state="learning",
            baseline_median=None,
            baseline_mad=None,
            heavy_hitter_ratio=0.8,
            severity=0.5,
            novelty=None,
            persistence=0.4,
            unresolved_episodes=1,
            current_task_relevance=0.5,
        ),
        resources=ResourceInput({}, {}, 0, None),
        observed_at=now,
    )
    calculator = InteroceptionCalculator(formula_version=1)
    first = calculator.calculate(inputs)
    second = calculator.calculate(inputs)

    assert first == second
    assert first.cognitive_capacity.value is not None
    assert first.continuity_integrity.value == 1.0
    assert first.sensory_coverage.value == 0.0
    assert first.failure_diversity.value == 8
    assert first.failure_diversity.approximate is True
    assert first.anomaly_pressure.value is None  # no valid baseline/novelty yet
    assert first.resource_pressure.value is None
    assert "unique_error_fingerprints" in first.failure_diversity.sources


def test_compact_self_capsule_keeps_exact_state_first_and_stays_under_budget(
    tmp_path, monkeypatch
) -> None:
    paths = AppPaths(
        config=tmp_path / "config",
        data=tmp_path / "data",
        state=tmp_path / "state",
        cache=tmp_path / "cache",
        logs=tmp_path / "state" / "logs",
    )
    config = AppConfig().sentience
    database = SentienceDatabase.open(paths, config)
    identity = IdentityService(database).load_or_create()
    continuity = ContinuityService(database, identity.incarnation_id)
    boot = continuity.start_boot()
    capabilities = CapabilityRegistry(database, boot.boot_id)
    try:
        capabilities.install_defaults()
        capabilities.transition(
            "primary_reasoning",
            CapabilityLifecycle.READY,
            reason="Hermes session reports gpt-5.6-sol",
            evidence={"model": "gpt-5.6-sol"},
            active_profile="hal-full",
        )
        capabilities.transition(
            "persistent_memory",
            CapabilityLifecycle.READY,
            reason="database integrity passed",
            evidence={"quick_check": "ok"},
        )
        task_id = capabilities.create_task(
            "Repair Docker authentication",
            requirements={"terminal": (CapabilityLifecycle.READY, False)},
        )
        fact = FactStore(database, boot.boot_id).create(
            subject="docker.service",
            statement="The registry previously returned HTTP 401.",
            source_type=EventOrigin.OBSERVATION,
            exact=True,
            confidence=1.0,
            evidence_refs=("event:docker-401",),
        )

        def forbidden_integrity_scan():
            raise AssertionError("prompt capsule performed a lifetime integrity scan")

        monkeypatch.setattr(database, "verify_control_chain", forbidden_integrity_scan)
        monkeypatch.setattr(database, "quick_integrity_check", forbidden_integrity_scan)

        capsule = ContextCompiler(database, config).compile(
            task_id=task_id,
            query="repair Docker authentication",
            token_budget=300,
            active_model_class="frontier",
            context_usage=ContextInput(1000, 800, 1200, 0, 32_000, 0, None),
        )
        keys = list(capsule.data)
        assert keys[:4] == ["identity", "continuity", "cognition", "embodiment"]
        assert capsule.data["identity"]["name"] == "HAL"
        assert capsule.data["attention"]["task_id"] == task_id
        assert capsule.data["attention"]["missing_requirements"] == ["terminal"]
        assert capsule.token_count <= 300
        assert capsule.byte_count == len(capsule.json.encode())
        assert "raw_log" not in capsule.json
        assert "approximate" in capsule.json
        assert capsule.evidence_handles

        memory_capsule = ContextCompiler(database, config).compile(
            task_id=task_id,
            query="repair Docker authentication",
            token_budget=config.retrieval.self_capsule_tokens,
            active_model_class="frontier",
            context_usage=ContextInput(1000, 800, 1200, 0, 32_000, 0, None),
        )
        assert f"fact:{fact.fact_id}" in memory_capsule.evidence_handles
    finally:
        database.close()


def test_first_person_truth_contract_rewrites_unsupported_operational_claims() -> None:
    contract = FirstPersonTruthContract()
    unsupported = contract.enforce(
        "I remember the failure. I checked the service. I can feel it...",
        ClaimEvidenceContext(frozenset(), frozenset()),
    )
    assert unsupported.supported is False
    assert set(unsupported.violations) == {"memory", "probe", "degradation"}
    assert "I remember" not in unsupported.text
    assert "I checked" not in unsupported.text
    assert "I can feel it" not in unsupported.text

    supported = contract.enforce(
        "I remember the failure [memory:fact-1].",
        ClaimEvidenceContext(frozenset({"fact-1"}), frozenset({"memory"})),
    )
    assert supported.supported is True

    uncited = contract.enforce(
        "I remember a different failure.",
        ClaimEvidenceContext(frozenset({"fact-1"}), frozenset({"memory"})),
    )
    assert uncited.supported is False
    assert uncited.violations == ("memory",)


def test_interoception_language_is_sparse_and_hysteresis_suppresses_repetition() -> None:
    def dimension(value: float | None) -> MachineDimension:
        return MachineDimension(value, 1.0, "2026-08-24T12:00:00Z", ("test",))

    def snapshot(context: float, uncertainty: float) -> InteroceptionSnapshot:
        return InteroceptionSnapshot(
            1,
            dimension(1.0),
            dimension(1.0),
            dimension(1.0),
            dimension(1.0),
            dimension(context),
            dimension(uncertainty),
            dimension(None),
            dimension(None),
            dimension(0.1),
        )

    gate = SparseInteroceptionLanguageGate()
    first = gate.update(snapshot(0.85, 0.60))
    assert len(first) == 2
    assert gate.update(snapshot(0.85, 0.60)) == ()
    assert gate.update(snapshot(0.70, 0.40)) == ()  # hysteresis band
    assert gate.update(snapshot(0.50, 0.20)) == ()  # release without announcement
    assert gate.update(snapshot(0.85, 0.60)) == first
