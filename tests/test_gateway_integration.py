from __future__ import annotations

import json
import shutil
import threading

from websockets.sync.server import serve

from hal9000.config import HermesSettings
from hal9000.hermes.client import HermesGatewayClient
from hal9000.hermes.discovery import discover_hermes
from hal9000.hermes.process import HermesProcessManager
from hal9000.hermes.service import HermesService


class RpcServer:
    def __init__(self) -> None:
        self.ready = threading.Event()
        self.server = None
        self.port = 0
        self.methods: list[str] = []
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        def handler(socket) -> None:
            socket.send(json.dumps({"jsonrpc": "2.0", "method": "event", "params": {"type": "gateway.ready", "payload": {}}}))
            for raw in socket:
                request = json.loads(raw)
                method = request["method"]
                self.methods.append(method)
                socket.send(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {"ok": True}}))
                if method == "prompt.submit":
                    for event_type, payload in (
                        ("message.start", {}),
                        ("message.delta", {"text": "Good "}),
                        ("message.delta", {"text": "morning."}),
                        ("message.complete", {"text": "Good morning."}),
                    ):
                        socket.send(json.dumps({"jsonrpc": "2.0", "method": "event", "params": {"type": event_type, "session_id": "test", "payload": payload}}))

        with serve(handler, "127.0.0.1", 0) as server:
            self.server = server
            self.port = server.socket.getsockname()[1]
            self.ready.set()
            server.serve_forever()

    def start(self) -> None:
        self.thread.start()
        assert self.ready.wait(3)

    def close(self) -> None:
        if self.server is not None:
            self.server.shutdown()
        self.thread.join(timeout=3)


def test_gateway_connection_streaming_rpc_and_cancellation(qtbot) -> None:
    server = RpcServer()
    server.start()
    client = HermesGatewayClient()
    results: list[tuple[str, str, object]] = []
    events: list[dict] = []
    client.rpcResult.connect(lambda request_id, method, result: results.append((request_id, method, result)))
    client.eventReceived.connect(events.append)
    try:
        client.connectTo(f"ws://127.0.0.1:{server.port}/api/ws")
        qtbot.waitUntil(lambda: client.connected, timeout=2500)
        client.request("prompt.submit", {"session_id": "test", "text": "hello"})
        qtbot.waitUntil(lambda: any(item[1] == "prompt.submit" for item in results), timeout=1500)
        qtbot.waitUntil(lambda: any(item.get("type") == "message.complete" for item in events), timeout=1500)
        client.request("session.interrupt", {"session_id": "test"})
        qtbot.waitUntil(lambda: "session.interrupt" in server.methods, timeout=1500)
        assert "".join(
            event.get("payload", {}).get("text", "")
            for event in events
            if event.get("type") == "message.delta"
        ) == "Good morning."
    finally:
        client.close()
        server.close()
        qtbot.wait(80)
        client.deleteLater()
        qtbot.wait(20)


def test_gateway_rejects_invalid_url_without_crashing(qtbot) -> None:
    client = HermesGatewayClient()
    errors: list[tuple] = []
    client.rpcError.connect(lambda *values: errors.append(values))
    client.connectTo("http://not-a-websocket")
    qtbot.waitUntil(lambda: bool(errors), timeout=500)
    assert client.state == "error"
    client.close()
    client.deleteLater()
    qtbot.wait(20)


def test_hermes_discovery_matches_live_host_installation() -> None:
    installation = discover_hermes()
    if shutil.which("hermes"):
        assert installation.available is True
        assert installation.version != "unknown"
        assert installation.executable and installation.executable.is_file()


def test_graceful_hermes_unavailability_keeps_service_controllable(qtbot, tmp_path) -> None:
    settings = HermesSettings(executable=str(tmp_path / "missing-hermes"))
    service = HermesService(settings, tmp_path)
    errors: list[str] = []
    service.errorOccurred.connect(errors.append)
    service.start()
    qtbot.waitUntil(lambda: service.status == "unavailable", timeout=500)
    assert errors
    service.close()
    qtbot.wait(80)
    service.deleteLater()
    qtbot.wait(20)


def test_owned_backend_launch_is_pinned_to_hals_profile(qtbot, tmp_path) -> None:
    executable = tmp_path / "fake-hermes"
    executable.write_text(
        "#!/bin/sh\nprintf 'ARGS=%s\\n' \"$*\"\nprintf 'HERMES_BACKEND_READY port=41234\\n'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    manager = HermesProcessManager(executable, profile="codex-cloud")
    output: list[str] = []
    manager.outputReceived.connect(output.append)

    manager.start()

    qtbot.waitUntil(lambda: any("ARGS=" in item for item in output), timeout=1500)
    assert "--profile codex-cloud serve --host 127.0.0.1 --port 0 --skip-build" in " ".join(output)
    manager.stop()
    manager.deleteLater()
    qtbot.wait(20)


def test_model_inventory_and_switch_use_supported_gateway_methods(qtbot, tmp_path) -> None:
    settings = HermesSettings(executable=str(tmp_path / "missing-hermes"))
    service = HermesService(settings, tmp_path)
    service._runtime_session_id = "runtime-session"
    service.client._state = "connected"
    requests: list[tuple[str, dict]] = []

    def request(method: str, params: dict, timeout_ms: int = 120_000) -> str:
        requests.append((method, params))
        return f"request-{len(requests)}"

    service.client.request = request
    options: list[dict] = []
    switched: list[tuple[str, str]] = []
    service.modelOptionsReady.connect(options.append)
    service.modelChanged.connect(lambda provider, model: switched.append((provider, model)))
    service.settings.reasoning_effort = ""

    service.requestModelOptions()
    assert requests[-1] == (
        "model.options",
        {"session_id": "runtime-session", "explicit_only": False},
    )

    service.switchModel("custom:local lab", "qwen/model 32b")
    request_id = "request-2"
    assert requests[-1][0] == "config.set"
    assert requests[-1][1]["session_id"] == "runtime-session"
    assert requests[-1][1]["key"] == "model"
    assert requests[-1][1]["value"] == (
        "'qwen/model 32b' --session --provider 'custom:local lab'"
    )

    service._on_rpc_result(
        request_id,
        "config.set",
        {"output": "Switched model"},
    )
    assert switched == [("custom:local lab", "qwen/model 32b")]
    assert requests[-1][0] == "model.options"

    service._on_rpc_result(
        "inventory",
        "model.options",
        {"providers": [], "provider": "copilot", "model": "gpt-5.6"},
    )
    assert options[-1]["model"] == "gpt-5.6"
    service.close()
    service.deleteLater()
    qtbot.wait(20)


def test_failed_required_model_switch_does_not_send_prompt_on_global_model(
    qtbot, tmp_path
) -> None:
    settings = HermesSettings(executable=str(tmp_path / "missing-hermes"))
    service = HermesService(settings, tmp_path)
    service._runtime_session_id = "runtime-session"
    service.client._state = "connected"
    service._queued_prompt = "Use the requested model"
    requests: list[tuple[str, dict]] = []
    errors: list[str] = []

    def request(method: str, params: dict, timeout_ms: int = 120_000) -> str:
        requests.append((method, params))
        return f"request-{len(requests)}"

    service.client.request = request
    service.errorOccurred.connect(errors.append)
    service.switchModel("openai-codex", "gpt-5.6-sol")
    service._on_rpc_error("request-1", "config.set", "subscription unavailable", 5001)
    service.sendPrompt("A later prompt must also stay blocked")

    assert service._queued_prompt == ""
    assert not any(method == "prompt.submit" for method, _params in requests)
    assert "did not send" in errors[-1]
    service.close()
    service.deleteLater()
    qtbot.wait(20)


def test_cancel_discards_prompt_waiting_for_session_preferences(qtbot, tmp_path) -> None:
    service = HermesService(
        HermesSettings(executable=str(tmp_path / "missing-hermes")),
        tmp_path,
    )
    service._runtime_session_id = "runtime-session"
    service.client._state = "connected"
    service._preferences_pending = True
    requests: list[tuple[str, dict]] = []

    def request(method: str, params: dict, timeout_ms: int = 120_000) -> str:
        requests.append((method, params))
        return f"request-{len(requests)}"

    service.client.request = request
    service.sendPrompt("Do not run after stop")
    service.cancel()
    service._finish_session_preferences()

    assert service._queued_prompt == ""
    assert [method for method, _params in requests] == [
        "session.interrupt",
        "model.options",
    ]
    service.close()
    service.deleteLater()
    qtbot.wait(20)


def test_failed_self_mcp_probe_stays_degraded_and_does_not_block_chat_session(
    qtbot, tmp_path
) -> None:
    service = HermesService(
        HermesSettings(executable=str(tmp_path / "missing-hermes")),
        tmp_path,
    )
    service.client._state = "connected"
    service._self_mcp_enabled = True
    service._self_mcp_stage = "testing"
    service._self_mcp_request = "probe-1"
    requests: list[tuple[str, dict]] = []

    def request(method: str, params: dict | None = None, timeout_ms: int = 120_000, **kwargs) -> str:
        requests.append((method, params or kwargs.get("params") or {}))
        return f"request-{len(requests)}"

    service.client.request = request
    service._on_rpc_result(
        "probe-1",
        "mcp.servers.test",
        {"ok": False, "error": "Connection closed", "tools": []},
    )

    assert service.selfMcpStatus in {"degraded", "retrying"}
    assert service._self_mcp_stage != "ready"
    assert any(method in {"session.create", "session.resume"} for method, _ in requests)
    assert service._self_mcp_retry_timer.isActive()
    service.close()
    service.deleteLater()
    qtbot.wait(20)


def test_repeated_self_mcp_failures_do_not_create_duplicate_sessions(
    qtbot, tmp_path
) -> None:
    service = HermesService(
        HermesSettings(executable=str(tmp_path / "missing-hermes")), tmp_path
    )
    service.client._state = "connected"
    service._self_mcp_enabled = True
    requests: list[tuple[str, dict]] = []
    service.client.request = lambda method, params, *args, **kwargs: (
        requests.append((method, params)) or f"request-{len(requests)}"
    )

    service._self_mcp_failed("first failure")
    service._self_mcp_failed("second failure")

    session_requests = [
        method for method, _params in requests if method in {"session.create", "session.resume"}
    ]
    assert session_requests == ["session.create"]
    service.close()
    service.deleteLater()
    qtbot.wait(20)


def test_late_self_mcp_is_ready_only_after_active_session_reload(qtbot, tmp_path) -> None:
    service = HermesService(
        HermesSettings(executable=str(tmp_path / "missing-hermes")),
        tmp_path,
    )
    service.client._state = "connected"
    service._runtime_session_id = "runtime-session"
    service._self_mcp_enabled = True
    service._self_mcp_stage = "testing"
    service._self_mcp_request = "probe"
    requests: list[tuple[str, dict]] = []
    service.client.request = lambda method, params, *args, **kwargs: (
        requests.append((method, params)) or "reload"
    )

    service._on_rpc_result("probe", "mcp.servers.test", {"ok": True})

    assert service.selfMcpStatus == "reloading"
    assert requests[-1][0] == "reload.mcp"
    assert requests[-1][1]["confirm"] is True
    service._on_rpc_result("reload", "reload.mcp", {"status": "ok"})
    assert service.selfMcpStatus == "ready"
    service.close()
    service.deleteLater()
    qtbot.wait(20)


def test_task_route_selected_before_session_is_applied_when_session_connects(
    qtbot, tmp_path
) -> None:
    service = HermesService(
        HermesSettings(executable=str(tmp_path / "missing-hermes")),
        tmp_path,
    )
    service.client._state = "connected"
    requests: list[tuple[str, dict]] = []

    def request(method: str, params: dict | None = None, timeout_ms: int = 120_000, **kwargs) -> str:
        requests.append((method, params or kwargs.get("params") or {}))
        return f"request-{len(requests)}"

    service.client.request = request
    service.ensureRoute("openai-codex", "gpt-5.6-sol", "medium")
    service._on_rpc_result(
        "session-create",
        "session.create",
        {"session_id": "runtime", "info": {"tools": {}}},
    )

    method, params = requests[-1]
    assert method == "config.set"
    assert params["key"] == "model"
    assert "gpt-5.6-sol" in params["value"]
    service.close()
    service.deleteLater()
    qtbot.wait(20)


def test_reconnected_transport_resumes_existing_runtime_session(qtbot, tmp_path) -> None:
    service = HermesService(
        HermesSettings(executable=str(tmp_path / "missing-hermes")),
        tmp_path,
    )
    service._runtime_session_id = "runtime-session"
    service._stored_session_id = "stored-session"
    service.client._state = "connected"
    requests: list[tuple[str, dict]] = []
    service.client.request = lambda method, params, *args, **kwargs: (
        requests.append((method, params)) or "request"
    )

    service._on_connection("connected")

    assert requests[0] == (
        "session.resume",
        {"session_id": "stored-session", "profile": "codex-cloud"},
    )
    service.close()
    service.deleteLater()
    qtbot.wait(20)


def test_unstable_connection_does_not_reset_backend_restart_budget(
    qtbot, tmp_path
) -> None:
    service = HermesService(
        HermesSettings(executable=str(tmp_path / "missing-hermes")), tmp_path
    )
    service.client.request = lambda *args, **kwargs: "session-request"
    service._restart_attempts = 3
    service._backend_failures.extend((1.0, 2.0, 3.0))

    service._on_connection("connected")
    assert service._restart_stability_timer.isActive()
    service._on_connection("reconnecting")

    assert not service._restart_stability_timer.isActive()
    assert service._restart_attempts == 3
    assert list(service._backend_failures) == [1.0, 2.0, 3.0]
    service.close()
    service.deleteLater()
    qtbot.wait(20)


def test_automatic_route_reasoning_does_not_mutate_sticky_manual_default(
    qtbot, tmp_path
) -> None:
    settings = HermesSettings(executable=str(tmp_path / "missing-hermes"))
    service = HermesService(settings, tmp_path)
    service._runtime_session_id = "runtime-session"
    service.client._state = "connected"
    requests: list[tuple[str, dict]] = []

    def request(method: str, params: dict, timeout_ms: int = 120_000) -> str:
        requests.append((method, params))
        return f"request-{len(requests)}"

    service.client.request = request
    service.ensureRoute("openai-codex", "gpt-5.6-sol", "high")
    service._on_rpc_result("request-1", "config.set", {"output": "model applied"})
    service._on_rpc_result("request-2", "config.set", {"output": "reasoning applied"})

    assert settings.model == "gpt-5.6-terra"
    assert settings.reasoning_effort == "medium"
    service.close()
    service.deleteLater()
    qtbot.wait(20)


def test_failed_automatic_route_hands_queued_prompt_back_to_hermes_fallback(
    qtbot, tmp_path
) -> None:
    settings = HermesSettings(executable=str(tmp_path / "missing-hermes"))
    service = HermesService(settings, tmp_path)
    service._runtime_session_id = "runtime-session"
    service.client._state = "connected"
    requests: list[tuple[str, dict]] = []

    def request(method: str, params: dict, timeout_ms: int = 120_000) -> str:
        requests.append((method, params))
        return f"request-{len(requests)}"

    service.client.request = request
    service.ensureRoute("openai-codex", "gpt-5.6-sol", "medium")
    service.sendPrompt("Continue through Hermes fallback")
    service._on_rpc_error("request-1", "config.set", "route unavailable", 5001)

    assert any(
        method == "prompt.submit"
        and params["text"] == "Continue through Hermes fallback"
        for method, params in requests
    )
    assert settings.model == "gpt-5.6-terra"

    service.ensureRoute("openai-codex", "gpt-5.6-sol", "medium")
    assert [method for method, _params in requests].count("config.set") == 2
    service.close()
    service.deleteLater()
    qtbot.wait(20)


def test_offline_local_route_failure_does_not_escape_to_hermes_fallback(
    qtbot, tmp_path
) -> None:
    service = HermesService(
        HermesSettings(executable=str(tmp_path / "missing-hermes")), tmp_path
    )
    service._runtime_session_id = "runtime-session"
    service.client._state = "connected"
    requests: list[tuple[str, dict]] = []
    errors: list[str] = []
    service.client.request = lambda method, params, *args, **kwargs: (
        requests.append((method, params)) or f"request-{len(requests)}"
    )
    service.errorOccurred.connect(errors.append)

    service.ensureRoute(
        "ollama",
        "qwen-local",
        "medium",
        allow_hermes_fallback=False,
    )
    service.sendPrompt("Remain offline")
    service._on_rpc_error("request-1", "config.set", "local model vanished", 5001)

    assert not any(method == "prompt.submit" for method, _params in requests)
    assert any("did not send" in message for message in errors)
    service.close()
    service.deleteLater()
    qtbot.wait(20)


def test_offline_local_reasoning_failure_does_not_release_queued_prompt(
    qtbot, tmp_path
) -> None:
    service = HermesService(
        HermesSettings(executable=str(tmp_path / "missing-hermes")), tmp_path
    )
    service._runtime_session_id = "runtime-session"
    service.client._state = "connected"
    requests: list[tuple[str, dict]] = []
    service.client.request = lambda method, params, *args, **kwargs: (
        requests.append((method, params)) or f"request-{len(requests)}"
    )

    service.ensureRoute(
        "ollama",
        "qwen-local",
        "medium",
        allow_hermes_fallback=False,
    )
    service.sendPrompt("Remain offline after reasoning setup")
    service._on_rpc_result("request-1", "config.set", {"output": "model applied"})
    service._on_rpc_result(
        "request-2", "config.set", {"warning": "reasoning failed locally"}
    )

    assert not any(method == "prompt.submit" for method, _params in requests)
    assert service._preference_state == "failed"
    service.close()
    service.deleteLater()
    qtbot.wait(20)


def test_session_info_reports_observed_model_and_reload_failure_is_not_ready(
    qtbot, tmp_path
) -> None:
    service = HermesService(
        HermesSettings(executable=str(tmp_path / "missing-hermes")), tmp_path
    )
    observed: list[tuple[str, str]] = []
    observed_reasoning: list[str] = []
    service.activeModelChanged.connect(
        lambda provider, model: observed.append((provider, model))
    )
    service.activeReasoningChanged.connect(observed_reasoning.append)

    service._on_event(
        {
            "type": "session.info",
            "session_id": "",
            "payload": {
                "provider": "ollama",
                "model": "qwen-local",
                "reasoning": "low",
            },
        }
    )
    assert observed == [("ollama", "qwen-local")]
    assert observed_reasoning == ["low"]

    service.client._state = "connected"
    service._self_mcp_enabled = True
    service._self_mcp_reload_request = "reload-1"
    service._on_rpc_result(
        "reload-1", "reload.mcp", {"ok": False, "error": "connection closed"}
    )
    assert service.selfMcpStatus == "degraded"
    service.close()
    service.deleteLater()
    qtbot.wait(20)


def test_disabling_automatic_recovery_disables_mcp_and_backend_retry_timers(
    qtbot, tmp_path
) -> None:
    settings = HermesSettings(executable=str(tmp_path / "missing-hermes"))
    settings.router.auto_recovery = False
    service = HermesService(settings, tmp_path)
    service.client._state = "connected"
    service._self_mcp_enabled = True
    service._want_running = True
    service.client.request = lambda *args, **kwargs: "request"

    service._self_mcp_failed("probe failed")
    service._on_process_running(False)

    assert not service._self_mcp_retry_timer.isActive()
    assert not service._restart_timer.isActive()
    assert service.status == "error"
    service.close()
    service.deleteLater()
    qtbot.wait(20)


def test_only_latest_model_workflow_can_release_a_queued_prompt(qtbot, tmp_path) -> None:
    service = HermesService(
        HermesSettings(executable=str(tmp_path / "missing-hermes")),
        tmp_path,
    )
    service._runtime_session_id = "runtime-session"
    service.client._state = "connected"
    requests: list[tuple[str, dict]] = []
    switched: list[tuple[str, str]] = []

    def request(method: str, params: dict, timeout_ms: int = 120_000) -> str:
        requests.append((method, params))
        return f"request-{len(requests)}"

    service.client.request = request
    service.modelChanged.connect(lambda provider, model: switched.append((provider, model)))

    service.switchModel("openai-codex", "gpt-5.6-terra")
    service.switchModel("openai-codex", "gpt-5.6-sol")
    service.sendPrompt("Use only the latest model")

    service._on_rpc_result("request-1", "config.set", {"output": "old model applied"})
    assert [method for method, _params in requests] == ["config.set", "config.set"]

    service._on_rpc_result("request-2", "config.set", {"output": "new model applied"})

    assert not any(method == "prompt.submit" for method, _params in requests)

    service._on_rpc_result("request-3", "config.set", {"output": "new reasoning applied"})

    prompt_requests = [params for method, params in requests if method == "prompt.submit"]
    assert prompt_requests == [
        {"session_id": "runtime-session", "text": "Use only the latest model"}
    ]
    assert switched == [("openai-codex", "gpt-5.6-sol")]
    service.close()
    service.deleteLater()
    qtbot.wait(20)


def test_interim_and_completion_metadata_reach_speech_reconciliation(
    qtbot, tmp_path
) -> None:
    service = HermesService(
        HermesSettings(executable=str(tmp_path / "missing-hermes")),
        tmp_path,
    )
    service._runtime_session_id = "runtime-session"
    interims: list[tuple[str, bool]] = []
    completions: list[tuple[str, bool]] = []
    service.assistantInterim.connect(
        lambda text, already_streamed: interims.append((text, already_streamed))
    )
    service.assistantCompleted.connect(
        lambda text, previewed: completions.append((text, previewed))
    )

    service._on_event(
        {
            "type": "message.interim",
            "session_id": "runtime-session",
            "payload": {"text": "Working note.", "already_streamed": True},
        }
    )
    service._on_event(
        {
            "type": "message.complete",
            "session_id": "runtime-session",
            "payload": {"text": "Final answer.", "response_previewed": True},
        }
    )

    assert interims == [("Working note.", True)]
    assert completions == [("Final answer.", True)]
    service.close()
    service.deleteLater()
    qtbot.wait(20)
