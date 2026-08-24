from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from hal9000.config import AppConfig
from hal9000.paths import AppPaths
from hal9000.sentience.event_envelope import EventEnvelope, EventValidationError
from hal9000.sentience.events.coalescer import EventRunCoalescer, EventRunInput
from hal9000.sentience.events.fingerprint import stable_fingerprint
from hal9000.sentience.events.normalize import normalize_observation
from hal9000.sentience.events.redact import redact_data
from hal9000.sentience.events.routing import EventRoute, route_event
from hal9000.sentience.models import (
    EventOrigin,
    RetentionClass,
    Sensitivity,
    Severity,
)
from hal9000.sentience.storage.blob_store import BlobStore, MissingBlobError
from hal9000.sentience.storage.database import SentienceDatabase


def stack_for(tmp_path):
    paths = AppPaths(
        config=tmp_path / "config",
        data=tmp_path / "data",
        state=tmp_path / "state",
        cache=tmp_path / "cache",
        logs=tmp_path / "state" / "logs",
    )
    database = SentienceDatabase.open(paths, AppConfig().sentience)
    return paths, database


def test_redaction_precedes_source_specific_normalization_and_fingerprinting() -> None:
    raw = {
        "MESSAGE": "service exited code=23 token=super-secret",
        "_PID": "11842",
        "_SOURCE_REALTIME_TIMESTAMP": "1787572800000000",
        "UNIT": "docker.service",
        "EXIT_CODE": 23,
        "PORT": 2375,
        "PATH": "/srv/docker/config.json",
    }
    redacted = redact_data(raw)
    normalized = normalize_observation(
        "journald", "systemd.unit_failed", "docker.service", redacted
    )

    assert "super-secret" not in normalized.canonical
    assert "11842" not in normalized.canonical
    assert "1787572800000000" not in normalized.canonical
    assert normalized.payload["EXIT_CODE"] == 23
    assert normalized.payload["PORT"] == 2375
    assert normalized.payload["PATH"] == "/srv/docker/config.json"
    assert stable_fingerprint(normalized).startswith("sha256:")


def test_exact_exception_router_never_sends_authority_to_sketches() -> None:
    for event_type in (
        "task.started",
        "approval.granted",
        "capability.transitioned",
        "action.verified",
        "telemetry.dropped",
        "integrity.corruption_detected",
    ):
        assert route_event(event_type, Severity.INFO).route is EventRoute.EXACT

    assert route_event("journald.message", Severity.INFO).route is EventRoute.EVENT_RUN
    assert route_event("model.token.delta", Severity.DEBUG).route is EventRoute.DROP
    assert route_event("resource.cpu.sample", Severity.INFO).route is EventRoute.SKETCH_ONLY


def test_repeated_event_runs_coalesce_and_samples_stay_bounded(tmp_path) -> None:
    _paths, database = stack_for(tmp_path)
    config = AppConfig().sentience.ingestion
    config.max_open_runs = 2
    config.sample_count_per_run = 4
    coalescer = EventRunCoalescer(database, config, epoch_seconds=60)
    started = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    try:
        for index in range(10_000):
            coalescer.add(
                EventRunInput(
                    source="journald",
                    type="systemd.unit_restart_failed",
                    subject="docker.service",
                    observed_at=started + timedelta(microseconds=index),
                    severity=Severity.ERROR,
                    task_id=None,
                    normalized_template="docker.service restart failed exit=23",
                    redacted_payload={"exit": 23},
                    retention_class=RetentionClass.SHORT,
                    sensitivity=Sensitivity.INTERNAL,
                )
            )
        coalescer.flush()

        with database.read_connection() as connection:
            run = connection.execute("SELECT * FROM event_runs").fetchone()
            sample_count = connection.execute("SELECT COUNT(*) FROM event_run_samples").fetchone()[0]
        assert run["count"] == 10_000
        assert sample_count <= 7  # first/latest/highest plus bounded uniform representatives
        assert coalescer.open_run_count <= 2
    finally:
        coalescer.close()
        database.close()


def test_blob_store_is_redacted_deduplicated_atomic_and_integrity_checked(tmp_path) -> None:
    paths, database = stack_for(tmp_path)
    store = BlobStore(paths.sentience_blob_root, database)
    try:
        first = store.put_text(
            "Authorization: Bearer this-is-secret\nverification passed",
            mime_type="text/plain",
            sensitivity=Sensitivity.CONFIDENTIAL,
            retention_class=RetentionClass.FOREVER,
            owner_type="action_verification",
            owner_id="verify-1",
            relation="output",
            pin=True,
        )
        second = store.put_text(
            "Authorization: Bearer this-is-secret\nverification passed",
            mime_type="text/plain",
            sensitivity=Sensitivity.CONFIDENTIAL,
            retention_class=RetentionClass.FOREVER,
            owner_type="action_verification",
            owner_id="verify-2",
            relation="output",
            pin=True,
        )
        assert first.digest == second.digest
        assert database.count("payload_refs") == 1
        assert store.get(first.digest).decode() == "Authorization: [REDACTED]\nverification passed"
        assert not list(paths.sentience_blob_root.rglob("*.tmp"))

        blob_path = paths.sentience_blob_root / first.relative_path
        blob_path.unlink()
        try:
            store.get(first.digest)
        except MissingBlobError:
            pass
        else:
            raise AssertionError("missing evidence must be surfaced")
        with database.read_connection() as connection:
            assert connection.execute(
                "SELECT missing FROM payload_refs WHERE digest=?", (first.digest,)
            ).fetchone()[0] == 1
    finally:
        database.close()


def test_raw_audio_and_never_retained_payloads_are_rejected(tmp_path) -> None:
    paths, database = stack_for(tmp_path)
    store = BlobStore(paths.sentience_blob_root, database)
    try:
        for mime, retention in (
            ("audio/wav", RetentionClass.SHORT),
            ("application/octet-stream", RetentionClass.NEVER),
        ):
            try:
                store.put(
                    b"payload",
                    mime_type=mime,
                    sensitivity=Sensitivity.INTERNAL,
                    retention_class=retention,
                )
            except ValueError:
                pass
            else:
                raise AssertionError("prohibited forensic payload was retained")
    finally:
        database.close()


def test_oversized_events_are_rejected_and_dead_letters_store_only_bounded_redacted_summary(
    tmp_path,
) -> None:
    _paths, database = stack_for(tmp_path)
    secret = "token=must-never-persist " + ("x" * 400_000)
    try:
        with pytest.raises(EventValidationError, match="bounded envelope"):
            EventEnvelope.new(
                boot_id="a9d4280e-2ace-4639-a75d-9b7a1172aa91",
                source="test.source",
                event_type="test.oversized",
                subject="payload",
                severity=Severity.INFO,
                retention_class=RetentionClass.SHORT,
                sensitivity=Sensitivity.INTERNAL,
                origin=EventOrigin.OBSERVATION,
                payload={"message": secret},
            )

        database.record_dead_letter("test.source", "oversized", {"message": secret})
        with database.read_connection() as connection:
            row = connection.execute(
                "SELECT redacted_payload_json,payload_digest FROM dead_letters"
            ).fetchone()
        assert len(row["redacted_payload_json"].encode()) < 32_768
        assert "must-never-persist" not in row["redacted_payload_json"]
        summary = json.loads(row["redacted_payload_json"])
        assert summary["payload_truncated"] is True
        assert summary["redacted_sha256"] == row["payload_digest"]
        assert summary["redacted_bytes"] > 32_768
    finally:
        database.close()
