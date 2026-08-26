from __future__ import annotations

from hal9000.config import AppConfig
from hal9000.paths import AppPaths
from hal9000.sentience.identity.lease import LeaseConflict
from hal9000.sentience.models import DegradationState
from hal9000.sentience.service import MachineSelfService


def _paths(tmp_path) -> AppPaths:
    return AppPaths(
        config=tmp_path / "config",
        data=tmp_path / "data",
        state=tmp_path / "state",
        cache=tmp_path / "cache",
        logs=tmp_path / "state" / "logs",
    )


def _session_info(model="gpt-5.6-sol", provider="openai-codex") -> dict:
    return {
        "type": "session.info",
        "session_id": "hermes-session-1",
        "payload": {
            "model": model,
            "provider": provider,
            "profile_name": "codex-cloud",
            "lazy": False,
            "running": False,
            "desktop_contract": 6,
            "tools": {
                "terminal": {},
                "read_file": {},
                "write_file": {},
                "browser_navigate": {},
                "web_search": {},
                "delegate_task": {},
            },
        },
    }


def test_machine_self_lifecycle_capsule_structured_events_and_forensic_action(tmp_path) -> None:
    config = AppConfig()
    service = MachineSelfService(_paths(tmp_path), config, tmp_path)
    service.start()
    try:
        service.update_operator_preferred_name("Isaiah")
        prepared = service.prepare_prompt("Inspect and repair the repository")
        assert prepared.task_id
        assert "<hal_machine_self" in prepared.text
        assert prepared.capsule.token_count <= config.sentience.retrieval.self_capsule_tokens
        assert prepared.capsule.data["operator"]["preferred_name"] == "Isaiah"

        service.observe_hermes_event(_session_info()).result(timeout=10)
        task_id = service.mapper._task_by_session["hermes-session-1"]
        assert task_id == prepared.task_id
        assert service.registry.current("primary_reasoning").state.value == "READY"
        assert service.registry.current("terminal").state.value == "READY"
        assert service.registry.current("codex").state.value == "READY"

        service.observe_hermes_event(
            {
                "type": "tool.start",
                "session_id": "hermes-session-1",
                "payload": {
                    "tool_id": "tool-1",
                    "name": "terminal",
                    "args": {"command": "printf safe"},
                },
            }
        ).result(timeout=10)
        service.observe_hermes_event(
            {
                "type": "tool.complete",
                "session_id": "hermes-session-1",
                "payload": {
                    "tool_id": "tool-1",
                    "name": "terminal",
                    "result": {"stdout": "ok", "token": "must-not-persist"},
                    "summary": "command completed",
                },
            }
        ).result(timeout=10)
        service.observe_hermes_event(
            {
                "type": "approval.request",
                "session_id": "hermes-session-1",
                "payload": {"request_id": "approval-1", "description": "allow write"},
            }
        ).result(timeout=10)
        service.resolve_approval("approval-1", "once").result(timeout=10)
        service.observe_hermes_event(
            {
                "type": "message.complete",
                "session_id": "hermes-session-1",
                "payload": {"status": "complete", "text": "done", "usage": {"total_tokens": 9}},
            }
        ).result(timeout=10)
        service.event_bus.flush()

        with service.database.read_connection() as connection:
            action = connection.execute(
                "SELECT * FROM consequential_actions WHERE tool_call_id='tool-1'"
            ).fetchone()
            approval = connection.execute(
                "SELECT * FROM approvals WHERE hermes_request_id='approval-1'"
            ).fetchone()
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            raw = service.database.path.read_bytes()
        assert action["state"] == "completed_unverified"
        assert action["payload_ref"].startswith("sha256:")
        assert approval["choice"] == "once"
        assert task["state"] == "completed_unverified"
        assert b"must-not-persist" not in raw
    finally:
        service.stop()


def test_structured_weaker_fallback_degrades_but_expected_manual_selection_does_not(tmp_path) -> None:
    config = AppConfig()
    config.sentience.degradation.aggregation_window_seconds = 0
    service = MachineSelfService(_paths(tmp_path), config, tmp_path)
    service.start()
    try:
        service.prepare_prompt("Continue the active task")
        service.observe_hermes_event(_session_info()).result(timeout=10)
        service.observe_hermes_event(_session_info("qwen-local", "local")).result(timeout=10)
        service.degradation.tick()
        assert service.degradation.status().state is DegradationState.DEGRADED
    finally:
        service.stop()

    other_root = tmp_path / "manual"
    second = MachineSelfService(_paths(other_root), config, other_root)
    second.start()
    try:
        second.observe_hermes_event(_session_info()).result(timeout=10)
        second.expect_model_selection("local", "qwen-local")
        second.observe_hermes_event(_session_info("qwen-local", "local")).result(timeout=10)
        assert second.degradation.status().state is DegradationState.NOMINAL
    finally:
        second.stop()


def test_duplicate_canonical_writer_is_rejected(tmp_path) -> None:
    config = AppConfig()
    first = MachineSelfService(_paths(tmp_path), config, tmp_path)
    second = MachineSelfService(_paths(tmp_path), config, tmp_path)
    first.start()
    try:
        try:
            second.start()
        except LeaseConflict:
            pass
        else:
            raise AssertionError("second canonical writer was not rejected")
    finally:
        second.stop()
        first.stop()


def test_model_route_decision_is_exact_and_transactionally_projected(tmp_path) -> None:
    service = MachineSelfService(_paths(tmp_path), AppConfig(), tmp_path)
    service.start()
    try:
        prepared = service.prepare_prompt("Implement the requested repository change")
        service.observe_hermes_event(
            {
                "type": "hal.model.route.decided",
                "session_id": "session-route",
                "payload": {
                    "decision_id": "route-1",
                    "policy_version": 1,
                    "intent_class": "complex_or_coding",
                    "provider": "openai-codex",
                    "model": "gpt-5.6-sol",
                    "reasoning": "medium",
                    "available": True,
                    "user_override": False,
                    "reason": "exact task policy selected the complex/coding route",
                    "rejected_candidates": [],
                    "task_id": prepared.task_id,
                },
            }
        ).result(timeout=10)
        with service.database.read_connection() as connection:
            route = connection.execute(
                "SELECT * FROM model_route_decisions WHERE decision_id='route-1'"
            ).fetchone()
            event = connection.execute(
                "SELECT * FROM exact_events WHERE event_id=?",
                (route["evidence_event_id"],),
            ).fetchone()
        assert route["selected_model"] == "gpt-5.6-sol"
        assert route["available"] == 1
        assert route["task_id"] == prepared.task_id
        assert event["type"] == "model.route.decided"
        assert event["retention_class"] == "forever"

        service.observe_hermes_event(
            {
                "type": "hal.model.provider_health.changed",
                "session_id": "session-route",
                "payload": {
                    "health_transition_id": "health-1",
                    "provider": "openai-codex",
                    "model": "gpt-5.6-sol",
                    "state": "cooldown",
                    "detail": "rate_limit",
                    "cooldownUntil": "2026-08-26T04:00:00Z",
                },
            }
        ).result(timeout=10)
        with service.database.read_connection() as connection:
            health = connection.execute(
                "SELECT * FROM model_provider_health WHERE provider='openai-codex' "
                "AND model='gpt-5.6-sol'"
            ).fetchone()
            health_event = connection.execute(
                "SELECT * FROM exact_events WHERE event_id=?",
                (health["evidence_event_id"],),
            ).fetchone()
        assert health["state"] == "cooldown"
        assert health_event["type"] == "model.provider.health.changed"
    finally:
        service.stop()
