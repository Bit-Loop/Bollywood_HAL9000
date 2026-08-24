from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from hal9000.config import AppConfig, SentienceStorageSettings
from hal9000.paths import AppPaths
from hal9000.sentience.clock import ClockReading, MachineClock
from hal9000.sentience.event_envelope import EventEnvelope, EventValidationError
from hal9000.sentience.models import EventOrigin, RetentionClass, Sensitivity, Severity
from hal9000.sentience.storage.database import SentienceDatabase


def paths_for(tmp_path) -> AppPaths:
    return AppPaths(
        config=tmp_path / "config",
        data=tmp_path / "data",
        state=tmp_path / "state",
        cache=tmp_path / "cache",
        logs=tmp_path / "state" / "logs",
    )


def test_sentience_configuration_defaults_and_invalid_budget_ratios() -> None:
    config = AppConfig()

    assert config.sentience.enabled is True
    assert config.sentience.sketches.exact_threshold == 512
    assert config.sentience.retrieval.self_capsule_tokens == 700
    assert config.sentience.degradation.aggregation_window_seconds == 3

    config.sentience.storage = SentienceStorageSettings(
        state_db_ratio=0.5,
        blob_ratio=0.6,
        checkpoint_ratio=0.1,
        reserve_ratio=0.05,
    )
    with pytest.raises(ValueError, match="ratios"):
        config.normalize()


def test_migrations_create_required_schema_and_sqlite_policy(tmp_path) -> None:
    database = SentienceDatabase.open(paths_for(tmp_path), AppConfig().sentience)
    try:
        required = {
            "identity_state",
            "instance_leases",
            "boot_sessions",
            "exact_events",
            "capability_definitions",
            "capability_current",
            "tasks",
            "commitments",
            "approvals",
            "consequential_actions",
            "action_verifications",
            "semantic_facts",
            "contradictions",
            "episodes",
            "event_runs",
            "sketch_buckets",
            "payload_refs",
            "projection_checkpoints",
            "outbox",
            "retrieval_documents",
            "retention_tombstones",
        }
        assert required <= database.table_names()
        pragmas = database.pragmas()
        assert pragmas["foreign_keys"] == 1
        assert pragmas["journal_mode"] == "wal"
        assert pragmas["synchronous"] == 2
        assert database.migration_version >= 1
    finally:
        database.close()


def test_event_validation_idempotency_redaction_and_control_hash_chain(tmp_path) -> None:
    database = SentienceDatabase.open(paths_for(tmp_path), AppConfig().sentience)
    boot_id = "c6458814-8a70-4d97-be40-92518ad40675"
    try:
        with pytest.raises(EventValidationError, match="source"):
            EventEnvelope.new(
                boot_id=boot_id,
                source="",
                event_type="task.started",
                subject="task-1",
                severity=Severity.INFO,
                retention_class=RetentionClass.FOREVER,
                sensitivity=Sensitivity.INTERNAL,
                origin=EventOrigin.OBSERVATION,
                payload={},
            )

        event = EventEnvelope.new(
            event_id="28a218e4-3b79-489a-9c19-a41957ed03b2",
            boot_id=boot_id,
            source="hermes.gateway",
            event_type="approval.granted",
            subject="request-7",
            severity=Severity.INFO,
            retention_class=RetentionClass.FOREVER,
            sensitivity=Sensitivity.CONFIDENTIAL,
            origin=EventOrigin.OBSERVATION,
            payload={
                "authorization": "Bearer super-secret",
                "nested": {"api_key": "sk-do-not-store", "choice": "once"},
            },
            idempotency_key="approval:request-7:once",
        )
        first = database.append_exact_event(event)
        duplicate = database.append_exact_event(event)

        assert first.inserted is True
        assert duplicate.inserted is False
        assert duplicate.sequence == first.sequence
        stored = database.read_exact_event(first.sequence)
        assert stored is not None
        payload = json.loads(stored["payload_json"])
        assert "super-secret" not in stored["payload_json"]
        assert "sk-do-not-store" not in stored["payload_json"]
        assert payload["authorization"] == "[REDACTED]"
        assert payload["nested"]["api_key"] == "[REDACTED]"
        assert stored["event_hash"].startswith("sha256:")

        second = database.append_exact_event(
            EventEnvelope.new(
                boot_id=boot_id,
                source="hal.capabilities",
                event_type="capability.transitioned",
                subject="terminal",
                severity=Severity.WARNING,
                retention_class=RetentionClass.FOREVER,
                sensitivity=Sensitivity.INTERNAL,
                origin=EventOrigin.OBSERVATION,
                payload={"from": "READY", "to": "UNAVAILABLE"},
            )
        )
        chained = database.read_exact_event(second.sequence)
        assert chained is not None
        assert chained["previous_hash"] == stored["event_hash"]
        assert database.verify_control_chain().valid is True
    finally:
        database.close()


def test_event_and_projection_share_one_transaction(tmp_path) -> None:
    database = SentienceDatabase.open(paths_for(tmp_path), AppConfig().sentience)
    event = EventEnvelope.new(
        boot_id="d7200342-7803-41c5-a860-3a3c4bc9f126",
        source="hal.tasks",
        event_type="task.started",
        subject="task-atomic",
        severity=Severity.INFO,
        retention_class=RetentionClass.FOREVER,
        sensitivity=Sensitivity.INTERNAL,
        origin=EventOrigin.OBSERVATION,
        payload={"title": "atomic"},
    )
    try:
        def broken_projection(connection, _sequence: int) -> None:
            connection.execute(
                "INSERT INTO tasks(task_id, title, state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("task-atomic", "atomic", "active", event.occurred_at_utc, event.occurred_at_utc),
            )
            raise RuntimeError("projection failed")

        with pytest.raises(RuntimeError, match="projection failed"):
            database.append_exact_event(event, projection=broken_projection)

        assert database.count("exact_events") == 0
        assert database.count("tasks") == 0
    finally:
        database.close()


def test_clock_jump_is_detected_but_monotonic_duration_remains_authoritative() -> None:
    clock = MachineClock(jump_threshold_seconds=2)
    first = ClockReading(datetime(2026, 8, 24, 10, 0, tzinfo=UTC), 10_000_000_000)
    second = ClockReading(
        datetime(2026, 8, 24, 9, 0, tzinfo=UTC),
        13_000_000_000,
    )

    assert clock.observe(first) is None
    jump = clock.observe(second)
    assert jump is not None
    assert jump.direction == "backward"
    assert jump.monotonic_elapsed == timedelta(seconds=3)
    assert clock.duration(first, second) == timedelta(seconds=3)
