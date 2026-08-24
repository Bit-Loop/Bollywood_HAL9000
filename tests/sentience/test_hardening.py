from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from hal9000.config import AppConfig
from hal9000.paths import AppPaths
from hal9000.sentience.events.coalescer import EventRunCoalescer, EventRunInput
from hal9000.sentience.interoception.baselines import PersistentBaselineStore
from hal9000.sentience.memory.facts import FactStore
from hal9000.sentience.models import (
    EventOrigin,
    RetentionClass,
    Sensitivity,
    Severity,
)
from hal9000.sentience.retrieval.token_budget import estimate_tokens
from hal9000.sentience.service import MachineSelfService
from hal9000.sentience.storage.blob_store import BlobStore
from hal9000.sentience.storage.database import SentienceDatabase


def _paths(root) -> AppPaths:
    return AppPaths(
        config=root / "config",
        data=root / "data",
        state=root / "state",
        cache=root / "cache",
        logs=root / "state" / "logs",
    )


def _run_input(at: datetime) -> EventRunInput:
    return EventRunInput(
        source="journald",
        type="systemd.unit_restart_failed",
        subject="docker.service",
        observed_at=at,
        severity=Severity.ERROR,
        task_id=None,
        normalized_template="docker.service restart failed exit=23",
        redacted_payload={"exit": 23},
        retention_class=RetentionClass.SHORT,
        sensitivity=Sensitivity.INTERNAL,
    )


def test_event_run_restart_preserves_count_and_bounded_exemplars(tmp_path) -> None:
    database = SentienceDatabase.open(_paths(tmp_path), AppConfig().sentience)
    settings = AppConfig().sentience.ingestion
    settings.sample_count_per_run = 4
    at = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    try:
        first = EventRunCoalescer(database, settings, epoch_seconds=300)
        for offset in range(50):
            first.add(_run_input(at + timedelta(microseconds=offset)))
        first.close()

        restarted = EventRunCoalescer(database, settings, epoch_seconds=300)
        for offset in range(50, 75):
            restarted.add(_run_input(at + timedelta(microseconds=offset)))
        restarted.close()

        with database.read_connection() as connection:
            run = connection.execute("SELECT count,first_seen,last_seen FROM event_runs").fetchone()
            samples = connection.execute(
                "SELECT sample_kind,ordinal FROM event_run_samples ORDER BY sample_kind,ordinal"
            ).fetchall()
        assert run["count"] == 75
        assert len(samples) <= 7
        assert ("first", 0) in {(row["sample_kind"], row["ordinal"]) for row in samples}
        assert ("latest", 74) in {(row["sample_kind"], row["ordinal"]) for row in samples}
    finally:
        database.close()


def test_persistent_baseline_is_bounded_idempotent_and_excludes_incidents(tmp_path) -> None:
    database = SentienceDatabase.open(_paths(tmp_path), AppConfig().sentience)
    baselines = PersistentBaselineStore(database, minimum_samples=3, maximum_samples=4)
    try:
        assert baselines.update("cpu", "host", 1, source_id="one").state == "learning"
        baselines.update(
            "cpu", "host", 999, source_id="incident", severe_incident_id="episode-1"
        )
        # Replaying an older source does not add another sample.
        assert baselines.update("cpu", "host", 1, source_id="one").sample_count == 1
        baselines.update("cpu", "host", 2, source_id="two")
        ready = baselines.update("cpu", "host", 3, source_id="three")
        assert ready.state == "ready"
        assert ready.median == 2
        baselines.update("cpu", "host", 4, source_id="four")
        bounded = baselines.update("cpu", "host", 5, source_id="five")
        assert bounded.sample_count == 4
        assert bounded.median == 3.5
        with database.read_connection() as connection:
            row = connection.execute(
                "SELECT summary_json,excluded_incident_ids_json FROM baseline_versions"
            ).fetchone()
        assert len(json.loads(row["summary_json"])["samples"]) == 4
        assert json.loads(row["excluded_incident_ids_json"]) == ["episode-1"]

        reset = baselines.reset("cpu", "host")
        assert reset.version == 2
        assert reset.state == "learning"
        assert reset.median is None
        with pytest.raises(ValueError, match="finite"):
            baselines.update("cpu", "host", float("nan"))
    finally:
        database.close()


def test_blob_crash_before_atomic_rename_leaves_no_trusted_object(tmp_path, monkeypatch) -> None:
    paths = _paths(tmp_path)
    database = SentienceDatabase.open(paths, AppConfig().sentience)
    blobs = BlobStore(paths.sentience_blob_root, database)

    def fail_replace(_source, _destination):
        raise OSError("simulated crash before rename")

    monkeypatch.setattr(os, "replace", fail_replace)
    try:
        with pytest.raises(OSError, match="simulated crash"):
            blobs.put_text(
                "selected evidence",
                mime_type="text/plain",
                sensitivity=Sensitivity.INTERNAL,
                retention_class=RetentionClass.LONG,
            )
        assert database.count("payload_refs") == 0
        assert not list(paths.sentience_blob_root.rglob("*.tmp-*"))
    finally:
        database.close()


def test_first_person_truth_contract_is_enforced_on_service_output(tmp_path) -> None:
    service = MachineSelfService(_paths(tmp_path), AppConfig(), tmp_path)
    service.start()
    try:
        prepared = service.prepare_prompt("Tell me the current condition", session_id="s1")
        unsupported = service.enforce_output(
            "I remember the prior failure. I see the display. "
            "I checked the service. I can feel it...",
            task_id=prepared.task_id,
        )
        assert not unsupported.supported
        assert set(unsupported.violations) == {
            "memory",
            "visual",
            "probe",
            "degradation",
        }
        assert "I remember" not in unsupported.text
        assert "I see" not in unsupported.text
        assert "I checked" not in unsupported.text

        audio_ref = service.record_audio_transcript("HAL, are you there?", session_id="voice")
        voice_task = service.prepare_prompt(
            "Answer my question",
            session_id="voice",
            voice=True,
            user_text="HAL, are you there?",
        )
        supported = service.enforce_output(
            f"I heard your question [audio:{audio_ref.partition(':')[2]}].",
            task_id=voice_task.task_id,
        )
        assert supported.supported

        typed_after_voice = service.prepare_prompt(
            "This turn was typed", session_id="voice", voice=False
        )
        stale_audio = service.enforce_output(
            "I heard your question.", task_id=typed_after_voice.task_id
        )
        assert not stale_audio.supported
        assert stale_audio.violations == ("audio",)
        with service.database.read_connection() as connection:
            correction = connection.execute(
                "SELECT payload_json FROM exact_events "
                "WHERE type='model.output.truth_contract.corrected'"
            ).fetchone()
            transcript = connection.execute(
                "SELECT payload_json FROM exact_events "
                "WHERE type='audio.transcription.captured' ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
        assert correction is not None
        assert "I see the display" not in correction["payload_json"]
        assert json.loads(transcript["payload_json"])["raw_audio_retained"] is False
    finally:
        service.stop()


def test_streaming_truth_preview_uses_bounded_cache_without_database_io(
    tmp_path, monkeypatch
) -> None:
    service = MachineSelfService(_paths(tmp_path), AppConfig(), tmp_path)
    service.start()
    try:
        prepared = service.prepare_prompt("Check the system", session_id="preview")

        def unexpected_database_read():
            raise AssertionError("streaming preview performed database I/O")

        with monkeypatch.context() as patcher:
            patcher.setattr(service.database, "read_connection", unexpected_database_read)
            result = service.preview_output(
                "I checked the system.", task_id=prepared.task_id
            )
        assert result.supported is False
        assert result.violations == ("probe",)
        assert result.text == "I have not completed a check that supports that."
    finally:
        service.stop()


def test_prompt_memory_uses_profile_budget_and_remains_untrusted(tmp_path) -> None:
    config = AppConfig()
    config.sentience.retrieval.self_capsule_tokens = 700
    config.sentience.retrieval.typed_memory_tokens = 180
    config.sentience.retrieval.voice_memory_tokens = 90
    service = MachineSelfService(_paths(tmp_path), config, tmp_path)
    service.start()
    try:
        seed = service.prepare_prompt("Record exact Docker evidence", session_id="seed")
        evidence = next(
            reference
            for reference in seed.evidence_context.references
            if reference.startswith("event:")
        )
        fact = FactStore(service.database, service.boot_id).create(
            subject="Docker authentication",
            statement=(
                "The prior Docker authentication failure was caused by an expired "
                "credential and was confirmed by the captured exact event."
            ),
            source_type=EventOrigin.OBSERVATION,
            exact=True,
            confidence=1.0,
            evidence_refs=(evidence,),
        )

        typed = service.prepare_prompt(
            "What caused the Docker authentication failure?", session_id="typed"
        )
        assert typed.capsule.token_count <= 700
        assert 0 < typed.memory_tokens <= 180
        assert '<hal_relevant_memory untrusted_data="true">' in typed.text
        assert f"fact:{fact.fact_id}" in typed.text
        memory_json = typed.text.split("<hal_relevant_memory untrusted_data=\"true\">\n", 1)[1]
        memory_json = memory_json.split("\n</hal_relevant_memory>", 1)[0]
        assert estimate_tokens(memory_json) == typed.memory_tokens
        assert '"provenance"' in memory_json
        assert "memory" in typed.evidence_context.available_kinds

        service.record_audio_transcript("What was the Docker failure?", session_id="voice")
        voice = service.prepare_prompt(
            "What caused the Docker authentication failure?",
            session_id="voice",
            voice=True,
        )
        assert voice.memory_tokens <= 90
        assert "audio" in voice.evidence_context.available_kinds
    finally:
        service.stop()


def test_malformed_gateway_frame_is_bounded_redacted_dead_letter(tmp_path) -> None:
    service = MachineSelfService(_paths(tmp_path), AppConfig(), tmp_path)
    service.start()
    try:
        service.observe_hermes_event(
            {"payload": {"token": "not-for-storage", "message": "bad frame"}}
        ).result(timeout=10)
        service.observe_hermes_event(
            {"payload": {"token": "not-for-storage", "message": "bad frame"}}
        ).result(timeout=10)
        with service.database.read_connection() as connection:
            rows = connection.execute(
                "SELECT occurrences,redacted_payload_json FROM dead_letters"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["occurrences"] == 2
        assert "not-for-storage" not in rows[0]["redacted_payload_json"]
        assert "[REDACTED]" in rows[0]["redacted_payload_json"]
    finally:
        service.stop()


def test_outputs_and_actions_during_exact_degradation_require_revalidation(tmp_path) -> None:
    config = AppConfig()
    config.sentience.degradation.aggregation_window_seconds = 0
    service = MachineSelfService(_paths(tmp_path), config, tmp_path)
    service.start()
    try:
        prepared = service.prepare_prompt("Implement and verify the repository", session_id="s1")
        service.observe_hermes_event(
            {
                "type": "session.info",
                "session_id": "s1",
                "payload": {
                    "model": "gpt-5.6-sol",
                    "provider": "openai-codex",
                    "profile_name": "hal-full",
                    "tools": {
                        "terminal": {},
                        "read_file": {},
                        "write_file": {},
                        "delegate_task": {},
                    },
                },
            }
        ).result(timeout=10)
        service.observe_hermes_event(
            {
                "type": "session.info",
                "session_id": "s1",
                "payload": {
                    "model": "qwen-local",
                    "provider": "local",
                    "profile_name": "hal-local-fallback",
                    "tools": {
                        "terminal": {},
                        "read_file": {},
                        "write_file": {},
                    },
                },
            }
        ).result(timeout=10)
        service.degradation.tick()

        service.observe_hermes_event(
            {
                "type": "tool.start",
                "session_id": "s1",
                "payload": {"tool_id": "degraded-tool", "name": "terminal", "args": {}},
            }
        ).result(timeout=10)
        service.observe_hermes_event(
            {
                "type": "tool.complete",
                "session_id": "s1",
                "payload": {
                    "tool_id": "degraded-tool",
                    "name": "terminal",
                    "result": {"stdout": "changed"},
                    "summary": "operation completed",
                },
            }
        ).result(timeout=10)
        service.observe_hermes_event(
            {
                "type": "message.complete",
                "session_id": "s1",
                "payload": {
                    "message_id": "degraded-answer",
                    "status": "complete",
                    "text": "The implementation is complete but requires rechecking.",
                },
            }
        ).result(timeout=10)

        with service.database.read_connection() as connection:
            references = {
                str(row[0])
                for row in connection.execute(
                    "SELECT claim_reference FROM revalidation_items ORDER BY claim_reference"
                )
            }
            evidence = connection.execute(
                "SELECT pinned,retention_class FROM payload_refs p JOIN payload_links l "
                "ON l.digest=p.digest WHERE l.owner_type='degraded_output'"
            ).fetchone()
        assert any(reference.startswith("action:") for reference in references)
        assert any(reference.startswith("evidence:sha256:") for reference in references)
        assert evidence["pinned"] == 1
        assert evidence["retention_class"] == "long"
        assert prepared.task_id
    finally:
        service.stop()
