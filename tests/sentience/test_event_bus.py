from __future__ import annotations

from datetime import UTC, datetime

from hal9000.config import AppConfig
from hal9000.paths import AppPaths
from hal9000.sentience.event_bus import BoundedEventBus
from hal9000.sentience.event_envelope import EventEnvelope
from hal9000.sentience.events.coalescer import EventRunInput
from hal9000.sentience.models import EventOrigin, RetentionClass, Sensitivity, Severity
from hal9000.sentience.storage.database import SentienceDatabase


def test_exact_control_events_survive_queue_pressure_while_telemetry_is_summarized(tmp_path) -> None:
    paths = AppPaths(
        config=tmp_path / "config",
        data=tmp_path / "data",
        state=tmp_path / "state",
        cache=tmp_path / "cache",
        logs=tmp_path / "state" / "logs",
    )
    config = AppConfig().sentience
    config.ingestion.queue_capacity = 8
    config.ingestion.exact_reserve = 2
    config.ingestion.max_open_runs = 2
    database = SentienceDatabase.open(paths, config)
    boot_id = "2fd110ee-2b39-47b6-a4a3-86eb5dcd5e21"
    bus = BoundedEventBus(database, config.ingestion, boot_id=boot_id, autostart=False)
    try:
        telemetry = EventRunInput(
            source="journald",
            type="journald.message",
            subject="kernel",
            observed_at=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
            severity=Severity.INFO,
            task_id=None,
            normalized_template="routine duplicate",
            redacted_payload={"message": "routine duplicate"},
            retention_class=RetentionClass.SHORT,
            sensitivity=Sensitivity.INTERNAL,
        )
        accepted = [bus.publish_observation(telemetry) for _ in range(100)]
        assert accepted.count(False) > 0

        futures = []
        for index in range(20):
            futures.append(
                bus.publish_exact(
                    EventEnvelope.new(
                        boot_id=boot_id,
                        source="hal.tasks",
                        event_type="task.checkpointed",
                        subject=f"task-{index}",
                        severity=Severity.INFO,
                        retention_class=RetentionClass.FOREVER,
                        sensitivity=Sensitivity.INTERNAL,
                        origin=EventOrigin.OBSERVATION,
                        payload={"index": index},
                        idempotency_key=f"checkpoint:{index}",
                    )
                )
            )
        bus.start()
        for future in futures:
            assert future.result(timeout=5).inserted is True
        bus.close()

        with database.read_connection() as connection:
            checkpoints = connection.execute(
                "SELECT COUNT(*) FROM exact_events WHERE type='task.checkpointed'"
            ).fetchone()[0]
            dropped = connection.execute(
                "SELECT payload_json FROM exact_events WHERE type='telemetry.dropped'"
            ).fetchall()
        assert checkpoints == 20
        assert len(dropped) == 1
        assert '"count":94' in dropped[0][0]
    finally:
        bus.close()
        database.close()


def test_internal_event_sampling_setting_excludes_or_coalesces_under_a_strict_budget(
    tmp_path,
) -> None:
    paths = AppPaths(
        config=tmp_path / "config",
        data=tmp_path / "data",
        state=tmp_path / "state",
        cache=tmp_path / "cache",
        logs=tmp_path / "state" / "logs",
    )
    settings = AppConfig().sentience
    settings.ingestion.internal_event_sample_rate = 0.0
    database = SentienceDatabase.open(paths, settings)
    bus = BoundedEventBus(
        database,
        settings.ingestion,
        boot_id="ba6e4cf1-d382-4eb9-81a2-2d88e353012d",
    )
    event = EventRunInput(
        source="hal.sentience.debug",
        type="maintenance.poll",
        subject="retention",
        observed_at=datetime.now(UTC),
        severity=Severity.INFO,
        task_id=None,
        normalized_template="routine internal poll",
        redacted_payload={"state": "ok"},
        retention_class=RetentionClass.SHORT,
        sensitivity=Sensitivity.INTERNAL,
    )
    try:
        assert bus.publish_observation(event) is False
        settings.ingestion.internal_event_sample_rate = 1.0
        assert bus.publish_observation(event) is True
        bus.flush()
        assert database.count("event_runs") == 1
        with database.read_connection() as connection:
            dropped = connection.execute(
                "SELECT payload_json FROM exact_events WHERE type='telemetry.dropped'"
            ).fetchone()
        assert dropped is not None
        assert '"count":1' in dropped["payload_json"]
    finally:
        bus.close()
        database.close()
