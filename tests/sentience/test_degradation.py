from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hal9000.config import AppConfig
from hal9000.paths import AppPaths
from hal9000.sentience.capabilities.registry import CapabilityRegistry
from hal9000.sentience.degradation.engine import DegradationEngine
from hal9000.sentience.degradation.outbox import OutboxDispatcher
from hal9000.sentience.identity.continuity import ContinuityService
from hal9000.sentience.identity.service import IdentityService
from hal9000.sentience.models import CapabilityLifecycle, DegradationState
from hal9000.sentience.storage.database import SentienceDatabase


@pytest.fixture
def machine(tmp_path):
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
    boot = ContinuityService(database, identity.incarnation_id).start_boot()
    registry = CapabilityRegistry(database, boot.boot_id)
    registry.install_defaults()
    engine = DegradationEngine(database, boot.boot_id, config.degradation)
    yield database, registry, engine, boot.boot_id
    database.close()


def _at(seconds: int) -> datetime:
    return datetime(2026, 8, 24, 12, 0, tzinfo=UTC) + timedelta(seconds=seconds)


def _ready(registry: CapabilityRegistry, capability: str, task_id: str | None = None):
    return registry.transition(
        capability,
        CapabilityLifecycle.READY,
        reason="verified available",
        evidence={"probe": "pass"},
        task_id=task_id,
    )


def _loss(
    registry: CapabilityRegistry,
    capability: str,
    *,
    task_id: str | None = None,
    expected: bool = False,
    profile: str = "hal-local-fallback",
):
    return registry.transition(
        capability,
        CapabilityLifecycle.UNAVAILABLE,
        reason="structured runtime loss",
        evidence={"gateway_event": "verified"},
        task_id=task_id,
        expected=expected,
        replacement_capability="local_reasoning" if capability == "primary_reasoning" else None,
        active_profile=profile,
    )


def test_rule_frontier_fallback_emits_once_after_aggregation(machine) -> None:
    database, registry, engine, boot_id = machine
    _ready(registry, "primary_reasoning")
    transition = _loss(registry, "primary_reasoning")
    status = engine.on_transition(
        transition,
        active_profile="hal-local-fallback",
        fallback_model="local-model",
        at=_at(0),
    )
    assert status.state is DegradationState.DEGRADING
    assert engine.tick(at=_at(2)).state is DegradationState.DEGRADING
    assert engine.tick(at=_at(3)).state is DegradationState.DEGRADED

    spoken: list[str] = []
    dispatcher = OutboxDispatcher(database, boot_id)
    assert dispatcher.dispatch_one(tts_available=True, speak=spoken.append, display=spoken.append)
    assert spoken == ["I can feel it..."]
    assert dispatcher.dispatch_one(tts_available=True, speak=spoken.append, display=spoken.append) is None
    assert engine.tick(at=_at(20)).state is DegradationState.DEGRADED


def test_rule_codex_loss_depends_on_active_task(machine) -> None:
    _database, registry, engine, _boot_id = machine
    _ready(registry, "codex")
    unrelated = _loss(registry, "codex")
    assert unrelated.material is False
    assert engine.on_transition(unrelated, active_profile="hal-full", at=_at(0)).state is DegradationState.NOMINAL

    _ready(registry, "codex")
    task_id = registry.create_task(
        "Repository-wide implementation",
        requirements={"codex": (CapabilityLifecycle.READY, False)},
    )
    dependent = _loss(registry, "codex", task_id=task_id)
    assert dependent.material is True
    assert engine.on_transition(dependent, active_profile="hal-general", at=_at(5)).state is DegradationState.DEGRADING


def test_rule_voice_fallback_and_manual_smaller_model_never_trigger(machine) -> None:
    _database, registry, engine, _boot_id = machine
    _ready(registry, "speech")
    voice = _loss(registry, "speech", profile="piper")
    assert engine.on_transition(voice, active_profile="piper", at=_at(0)).state is DegradationState.NOMINAL

    _ready(registry, "primary_reasoning")
    manual = _loss(registry, "primary_reasoning", expected=True)
    assert engine.on_transition(manual, active_profile="hal-small-manual", at=_at(1)).state is DegradationState.NOMINAL


def test_rule_context_or_required_memory_loss_triggers(machine) -> None:
    _database, registry, engine, _boot_id = machine
    _ready(registry, "persistent_memory")
    task_id = registry.create_task(
        "Continue project history",
        requirements={"persistent_memory": (CapabilityLifecycle.READY, False)},
    )
    status = engine.on_transition(
        _loss(registry, "persistent_memory", task_id=task_id),
        active_profile="hal-no-memory",
        at=_at(0),
    )
    assert status.state is DegradationState.DEGRADING


def test_rule_critical_terminal_loss_checkpoints_and_stops(machine) -> None:
    database, registry, engine, _boot_id = machine
    _ready(registry, "terminal")
    task_id = registry.create_task(
        "Modify system then verify",
        risk_level="consequential",
        requirements={"terminal": (CapabilityLifecycle.READY, True)},
    )
    transition = _loss(registry, "terminal", task_id=task_id)
    status = engine.on_transition(transition, active_profile="hal-restricted", at=_at(0))
    assert status.severity.value == "critical"
    with database.read_connection() as connection:
        task = connection.execute("SELECT state FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    assert task["state"] == "checkpoint_required"


def test_rule_multiple_losses_aggregate_into_one_episode_and_phrase(machine) -> None:
    database, registry, engine, _boot_id = machine
    _ready(registry, "primary_reasoning")
    _ready(registry, "persistent_memory")
    first = engine.on_transition(
        _loss(registry, "primary_reasoning"), active_profile="reduced", at=_at(0)
    )
    second = engine.on_transition(
        _loss(registry, "persistent_memory"), active_profile="reduced", at=_at(1)
    )
    assert first.episode_id == second.episode_id
    degraded = engine.tick(at=_at(4))
    assert set(degraded.lost_capabilities) == {"primary_reasoning", "persistent_memory"}
    with database.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM degradation_episodes").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM outbox WHERE kind='degradation_phrase'").fetchone()[0] == 1


def test_rule_recovery_requires_full_profile_and_stability_then_revalidates(machine) -> None:
    database, registry, engine, boot_id = machine
    _ready(registry, "primary_reasoning")
    _ready(registry, "persistent_memory")
    engine.on_transition(_loss(registry, "primary_reasoning"), active_profile="reduced", at=_at(0))
    engine.on_transition(_loss(registry, "persistent_memory"), active_profile="reduced", at=_at(1))
    engine.tick(at=_at(4))
    engine.record_conclusion("fact:during-degradation", "missing primary reasoning", at=_at(5))

    assert engine.on_transition(_ready(registry, "primary_reasoning"), active_profile="partial", at=_at(6)).state is DegradationState.DEGRADED
    recovering = engine.on_transition(_ready(registry, "persistent_memory"), active_profile="hal-full", at=_at(7))
    assert recovering.state is DegradationState.RECOVERING
    assert engine.tick(at=_at(36)).state is DegradationState.RECOVERING
    nominal = engine.tick(at=_at(37))
    assert nominal.state is DegradationState.NOMINAL

    emitted: list[str] = []
    dispatcher = OutboxDispatcher(database, boot_id)
    while dispatcher.dispatch_one(tts_available=True, speak=emitted.append, display=emitted.append):
        pass
    assert emitted == ["I can feel it...", "My higher functions have been restored."]
    with database.read_connection() as connection:
        item = connection.execute(
            "SELECT state FROM revalidation_items WHERE claim_reference='fact:during-degradation'"
        ).fetchone()
    assert item["state"] == "pending"


def test_rule_reconnect_flapping_does_not_repeat_phrase(machine) -> None:
    database, registry, engine, boot_id = machine
    _ready(registry, "primary_reasoning")
    engine.on_transition(_loss(registry, "primary_reasoning"), active_profile="reduced", at=_at(0))
    engine.tick(at=_at(3))
    engine.on_transition(_ready(registry, "primary_reasoning"), active_profile="hal-full", at=_at(4))
    assert engine.on_transition(_loss(registry, "primary_reasoning"), active_profile="reduced", at=_at(10)).state is DegradationState.DEGRADED
    engine.tick(at=_at(70))
    with database.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM outbox WHERE kind='degradation_phrase'").fetchone()[0] == 1

    displayed: list[str] = []
    dispatcher = OutboxDispatcher(database, boot_id)
    assert dispatcher.dispatch_one(tts_available=False, speak=displayed.append, display=displayed.append).channel == "transcript"
    assert displayed == ["I can feel it..."]
    assert dispatcher.dispatch_one(tts_available=False, speak=displayed.append, display=displayed.append) is None


def test_rule_restart_after_phrase_persistence_does_not_duplicate(machine) -> None:
    database, registry, engine, boot_id = machine
    _ready(registry, "primary_reasoning")
    engine.on_transition(_loss(registry, "primary_reasoning"), active_profile="reduced", at=_at(0))
    engine.tick(at=_at(3))

    restarted_engine = DegradationEngine(database, boot_id, AppConfig().sentience.degradation)
    emitted: list[str] = []
    first_dispatcher = OutboxDispatcher(database, boot_id)
    assert first_dispatcher.dispatch_one(tts_available=True, speak=emitted.append, display=emitted.append)
    second_dispatcher = OutboxDispatcher(database, boot_id)
    assert second_dispatcher.dispatch_one(tts_available=True, speak=emitted.append, display=emitted.append) is None
    assert emitted == ["I can feel it..."]
    assert restarted_engine.status().phrase_emitted is True


def test_rule_approximate_inputs_have_no_degradation_entry_point(machine) -> None:
    _database, _registry, engine, _boot_id = machine
    assert not hasattr(engine, "on_sketch")
    assert not hasattr(engine, "on_interoception")
    assert engine.status().state is DegradationState.NOMINAL
