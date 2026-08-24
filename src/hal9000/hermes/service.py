"""Semantic Hermes service used by the HAL controller."""

from __future__ import annotations

import logging
import shlex
import hashlib
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from hal9000.config import HermesSettings
from hal9000.hermes.client import HermesGatewayClient
from hal9000.hermes.discovery import (
    HermesInstallation,
    discover_hermes,
    is_loopback_url,
    probe_backend,
)
from hal9000.hermes.process import HermesProcessManager
from hal9000.sentience.hermes.gateway_adapter import SelfMcpRegistration
from hal9000.sentience.events.redact import redact_text


class HermesService(QObject):
    statusChanged = Signal(str)
    versionChanged = Signal(str)
    sessionChanged = Signal(str)
    latencyChanged = Signal(float)
    assistantStarted = Signal(str)
    assistantDelta = Signal(str)
    assistantInterim = Signal(str, bool)
    assistantCompleted = Signal(str, bool)
    toolActivity = Signal(dict)
    approvalRequested = Signal(dict)
    approvalResolved = Signal(str)
    turnFinished = Signal()
    errorOccurred = Signal(str)
    integrationsChanged = Signal(list)
    modelOptionsReady = Signal(dict)
    modelChanged = Signal(str, str)
    reasoningChanged = Signal(str)
    modelOperationError = Signal(str)
    structuredEvent = Signal(dict)
    _probeFinished = Signal(object)

    def __init__(
        self,
        settings: HermesSettings,
        cwd: Path,
        token: str = "",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.cwd = cwd
        self.installation: HermesInstallation = discover_hermes(settings.executable)
        self.client = HermesGatewayClient(self)
        self.client.connectionChanged.connect(self._on_connection)
        self.client.rpcResult.connect(self._on_rpc_result)
        self.client.rpcError.connect(self._on_rpc_error)
        self.client.eventReceived.connect(self._on_event)
        self.client.latencyChanged.connect(self._set_latency)
        self.client.transportError.connect(self._on_transport_error)
        self.process: HermesProcessManager | None = None
        self._status = "offline"
        self._latency = 0.0
        self._runtime_session_id = ""
        self._stored_session_id = settings.last_session_id
        self._queued_prompt = ""
        self._ws_url = ""
        self._token = token
        self._want_running = False
        self._probe_pending = False
        self._restart_attempts = 0
        self._model_switch_requests: dict[str, tuple[int, str, str]] = {}
        self._reasoning_requests: dict[str, tuple[int, str]] = {}
        self._preference_generation = 0
        self._preference_state = "unconfigured"
        self._desired_provider = settings.provider
        self._desired_model = settings.model
        self._desired_reasoning = settings.reasoning_effort
        self._preferences_pending = False
        self._preferences_failed = False
        self._self_mcp_enabled = False
        self._self_mcp = SelfMcpRegistration()
        self._self_mcp_request = ""
        self._self_mcp_stage = "idle"
        self._probe_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="hal9000-hermes-probe"
        )
        self._probeFinished.connect(self._on_probe_finished)
        self._restart_timer = QTimer(self)
        self._restart_timer.setSingleShot(True)
        self._restart_timer.timeout.connect(self._restart_owned_backend)

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(str, notify=versionChanged)
    def version(self) -> str:
        return self.installation.version if self.installation.available else "not found"

    @Property(str, notify=sessionChanged)
    def sessionId(self) -> str:
        return self._stored_session_id or self._runtime_session_id

    @Property(float, notify=latencyChanged)
    def latency(self) -> float:
        return self._latency

    @Property(str, constant=True)
    def executable(self) -> str:
        return str(self.installation.executable or "")

    @Slot()
    def start(self) -> None:
        self._want_running = True
        if self.client.connected or self._status in {"connecting", "probing", "starting"}:
            return
        if not self.installation.available:
            self.installation = discover_hermes(self.settings.executable)
        if not self.installation.available or self.installation.executable is None:
            self._set_status("unavailable")
            self.errorOccurred.emit(self.installation.detail or "Hermes is not installed")
            return
        self.versionChanged.emit(self.version)
        if self.settings.mode == "remote":
            socket_url = self._socket_url_for_base(self.settings.backend_url, self._token)
            self._ws_url = socket_url
            self.client.connectTo(socket_url)
            return
        # A named HAL profile is an isolation boundary. Reusing a backend that
        # was launched under the desktop's sticky Hybrid-MoA profile would
        # inherit its local Qwen/Devstral advisors and defeat the cloud-only
        # route selected for HAL.
        if self.settings.profile:
            self._launch_owned_backend()
            return
        if is_loopback_url(self.settings.backend_url) and not self._probe_pending:
            self._set_status("probing")
            self._probe_pending = True
            future = self._probe_executor.submit(
                probe_backend, self.settings.backend_url, self._token, 0.75
            )
            future.add_done_callback(self._probe_done)
            return
        self._launch_owned_backend()

    @Slot()
    def stop(self) -> None:
        self._want_running = False
        self._restart_timer.stop()
        self._preference_generation += 1
        self._preference_state = "unconfigured"
        self._preferences_pending = False
        self._preferences_failed = False
        self._model_switch_requests.clear()
        self._reasoning_requests.clear()
        if self._queued_prompt:
            self._emit_undelivered(self._queued_prompt, "Hermes service stopped before delivery")
            self._queued_prompt = ""
        self._self_mcp_stage = "idle"
        self._self_mcp_request = ""
        self.client.close()
        if self.process is not None:
            self.process.stop()
        self._set_status("offline")

    def close(self) -> None:
        self.stop()
        self._probe_executor.shutdown(wait=False, cancel_futures=True)

    def set_token(self, token: str) -> None:
        self._token = token.strip()

    def enableSelfMcp(self) -> None:
        """Install HAL's narrow MCP server through Hermes' supported RPCs."""
        self._self_mcp_enabled = True

    def apply_settings(self) -> None:
        was_running = self._want_running
        self.stop()
        self._runtime_session_id = ""
        self._ws_url = ""
        if was_running:
            self.start()

    @Slot()
    def reconnect(self) -> None:
        self._want_running = True
        if self.process is not None and not self.process.owns_process:
            self._launch_owned_backend()
        elif self._ws_url:
            self.client.connectTo(self._ws_url)
        elif self.process is None:
            self.start()

    @Slot(str)
    def sendPrompt(self, text: str) -> None:
        clean = text.strip()
        if not clean:
            return
        if self._preferences_failed:
            self.errorOccurred.emit(
                "HAL did not send the prompt because its required session model is not active"
            )
            return
        if not self._runtime_session_id or self._preferences_pending:
            if self._queued_prompt and self._queued_prompt != clean:
                self._emit_undelivered(
                    self._queued_prompt,
                    "a newer bounded pending prompt replaced it",
                )
            self._queued_prompt = clean
            if self.client.connected and not self._runtime_session_id:
                self._create_or_resume_session()
            elif not self.client.connected:
                self.errorOccurred.emit("Hermes is not connected")
            return
        self.client.request(
            "prompt.submit",
            {"session_id": self._runtime_session_id, "text": clean},
        )

    @Slot()
    def cancel(self) -> None:
        if self._queued_prompt:
            self._emit_undelivered(self._queued_prompt, "user cancelled before delivery")
        self._queued_prompt = ""
        if self._runtime_session_id:
            self.client.request(
                "session.interrupt", {"session_id": self._runtime_session_id}, 20_000
            )

    @Slot(bool)
    def requestModelOptions(self, refresh: bool = False) -> None:
        if not self._runtime_session_id:
            self.modelOperationError.emit(
                "Hermes model list is unavailable until a session connects"
            )
            return
        if not self.client.connected:
            self.modelOperationError.emit("Hermes model list is unavailable while offline")
            return
        self.client.request(
            "model.options",
            {
                "session_id": self._runtime_session_id,
                "explicit_only": False,
                **({"refresh": True} if refresh else {}),
            },
            60_000,
        )

    @Slot(str, str)
    def switchModel(self, provider: str, model: str) -> None:
        clean_provider = provider.strip()
        clean_model = model.strip()
        if not self._runtime_session_id:
            self.modelOperationError.emit(
                "Hermes model cannot be changed until a session connects"
            )
            return
        if not self.client.connected:
            message = "Hermes model cannot be changed while offline"
            self.modelOperationError.emit(message)
            return
        if not clean_model:
            self.modelOperationError.emit("Choose a Hermes model first")
            return
        self._desired_provider = clean_provider
        self._desired_model = clean_model
        self._desired_reasoning = self.settings.reasoning_effort
        self._begin_preference_workflow()

    def _begin_preference_workflow(self) -> None:
        self._preference_generation += 1
        generation = self._preference_generation
        self._preferences_pending = True
        self._preferences_failed = False
        self._preference_state = "applying"
        if not self._runtime_session_id:
            self._fail_preference_workflow(
                generation,
                "Hermes preferences cannot be changed until a session connects",
            )
            return
        if not self.client.connected:
            self._fail_preference_workflow(
                generation,
                "Hermes preferences cannot be changed while offline",
            )
            return
        if not self._desired_model:
            if self._desired_reasoning:
                self._request_reasoning(generation, self._desired_reasoning)
            else:
                self._finish_session_preferences(generation)
            return
        self._request_model(
            generation,
            self._desired_provider,
            self._desired_model,
        )

    def _request_model(self, generation: int, provider: str, model: str) -> None:
        value = f"{shlex.quote(model)} --session"
        if provider:
            value += f" --provider {shlex.quote(provider)}"
        request_id = self.client.request(
            "config.set",
            {
                "session_id": self._runtime_session_id,
                "key": "model",
                "value": value,
            },
            120_000,
        )
        self._model_switch_requests[request_id] = (generation, provider, model)

    @Slot(str)
    def setReasoning(self, effort: str) -> None:
        normalized = effort.strip().lower()
        if normalized not in {
            "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"
        }:
            message = f"Unsupported reasoning effort: {effort}"
            self.modelOperationError.emit(message)
            self._preference_generation += 1
            self._fail_preference_workflow(self._preference_generation, message)
            return
        if not self._runtime_session_id:
            self.modelOperationError.emit(
                "Hermes reasoning cannot be changed until a session connects"
            )
            return
        if not self.client.connected:
            message = "Hermes reasoning cannot be changed while offline"
            self.modelOperationError.emit(message)
            return
        self._desired_reasoning = normalized
        self._begin_preference_workflow()

    def _request_reasoning(self, generation: int, effort: str) -> None:
        request_id = self.client.request(
            "config.set",
            {
                "session_id": self._runtime_session_id,
                "key": "reasoning",
                "value": effort,
            },
            60_000,
        )
        self._reasoning_requests[request_id] = (generation, effort)

    @Slot(str, str)
    def respondApproval(self, request_id: str, choice: str) -> None:
        if not self._runtime_session_id or not request_id:
            return
        self.client.request(
            "approval.respond",
            {
                "session_id": self._runtime_session_id,
                "request_id": request_id,
                "choice": {
                    "allow": "once",
                    "once": "once",
                    "session": "session",
                    "always": "always",
                    "deny": "deny",
                }.get(choice, "deny"),
            },
        )

    def _probe_done(self, future: Future) -> None:
        try:
            result = future.result()
        except Exception as exc:
            result = (False, 0.0, str(exc))
        self._probeFinished.emit(result)

    @Slot(object)
    def _on_probe_finished(self, result: object) -> None:
        self._probe_pending = False
        if not self._want_running or self.settings.mode != "local":
            return
        healthy, latency, detail = result if isinstance(result, tuple) else (False, 0.0, "")
        self._set_latency(float(latency))
        if healthy:
            self._ws_url = self._socket_url_for_base(self.settings.backend_url, self._token)
            self.client.connectTo(self._ws_url)
            return
        logging.getLogger("hal9000.hermes").debug(
            "No reusable Hermes backend at %s: %s", self.settings.backend_url, detail
        )
        if not self.settings.auto_start:
            self._set_status("offline")
            self.errorOccurred.emit("Hermes backend is unavailable and automatic start is disabled")
            return
        self._launch_owned_backend()

    def _launch_owned_backend(self) -> None:
        if not self._want_running or self.installation.executable is None:
            return
        self._set_status("starting")
        if self.process is None:
            self.process = HermesProcessManager(
                self.installation.executable,
                self,
                profile=self.settings.profile,
            )
            self.process.backendReady.connect(self._on_backend_ready)
            self.process.errorOccurred.connect(self._on_process_error)
            self.process.outputReceived.connect(self._log_process_output)
            self.process.runningChanged.connect(self._on_process_running)
        else:
            self.process.profile = self.settings.profile.strip()
        self.process.start()

    def _on_backend_ready(self, _base_url: str, ws_url: str) -> None:
        self._restart_attempts = 0
        self._ws_url = ws_url
        self.client.connectTo(ws_url)

    def _on_connection(self, state: str) -> None:
        state_map = {
            "connected": "connected",
            "connecting": "connecting",
            "reconnecting": "reconnecting",
            "error": "error",
            "offline": "offline",
        }
        self._set_status(state_map.get(state, state))
        if state == "connected":
            self._restart_attempts = 0
            if self._self_mcp_enabled:
                if self._self_mcp_stage == "ready":
                    self._create_or_resume_session()
                else:
                    self._ensure_self_mcp()
            else:
                self._create_or_resume_session()

    def _ensure_self_mcp(self) -> None:
        if self._self_mcp_stage not in {"idle", "failed"}:
            return
        self._self_mcp_stage = "listing"
        self._self_mcp_request = self.client.request(
            "mcp.servers.list",
            **({"params": {"profile": self.settings.profile}} if self.settings.profile else {}),
            timeout_ms=30_000,
        )

    def _request_self_mcp_add(self) -> None:
        self._self_mcp_stage = "adding"
        self._self_mcp_request = self.client.request(
            "mcp.servers.add",
            self._self_mcp.add_params(profile=self.settings.profile),
            60_000,
        )

    def _request_self_mcp_test(self) -> None:
        self._self_mcp_stage = "testing"
        params = {"name": self._self_mcp.name}
        if self.settings.profile:
            params["profile"] = self.settings.profile
        self._self_mcp_request = self.client.request("mcp.servers.test", params, 60_000)

    def _finish_self_mcp_setup(self) -> None:
        self._self_mcp_stage = "ready"
        self._self_mcp_request = ""
        self._create_or_resume_session()

    def _create_or_resume_session(self) -> None:
        if self._stored_session_id:
            self.client.request(
                "session.resume",
                {"session_id": self._stored_session_id, "profile": self.settings.profile},
            )
            return
        self.client.request(
            "session.create",
            {
                "cwd": str(self.cwd),
                "profile": self.settings.profile,
                "source": "desktop",
                "title": "HAL 9000",
                "close_on_disconnect": False,
                "cols": 100,
            },
        )

    def _on_rpc_result(self, request_id: str, method: str, result: Any) -> None:
        payload = result if isinstance(result, dict) else {}
        if request_id == self._self_mcp_request and method == "mcp.servers.list":
            servers = [item for item in payload.get("servers") or [] if isinstance(item, dict)]
            existing = next(
                (item for item in servers if str(item.get("name") or "") == self._self_mcp.name),
                None,
            )
            if existing is None:
                self._request_self_mcp_add()
            elif self._self_mcp.matches(existing):
                self._request_self_mcp_test()
            else:
                self._self_mcp_stage = "removing"
                params = {"name": self._self_mcp.name}
                if self.settings.profile:
                    params["profile"] = self.settings.profile
                self._self_mcp_request = self.client.request(
                    "mcp.servers.remove", params, 30_000
                )
        elif request_id == self._self_mcp_request and method == "mcp.servers.remove":
            self._request_self_mcp_add()
        elif request_id == self._self_mcp_request and method == "mcp.servers.add":
            self._request_self_mcp_test()
        elif request_id == self._self_mcp_request and method == "mcp.servers.test":
            if not bool(payload.get("ok")):
                self.errorOccurred.emit(
                    "HAL self MCP failed its Hermes probe: "
                    + str(payload.get("error") or "unknown error")
                )
            self._finish_self_mcp_setup()
        elif method in {"session.create", "session.resume"}:
            self._runtime_session_id = str(payload.get("session_id") or "")
            self._stored_session_id = str(
                payload.get("stored_session_id") or payload.get("session_key") or self._stored_session_id
            )
            self.settings.last_session_id = self._stored_session_id
            self.sessionChanged.emit(self.sessionId)
            info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
            tools = info.get("tools") if isinstance(info.get("tools"), dict) else {}
            self.integrationsChanged.emit(sorted(map(str, tools.keys())))
            self._desired_provider = self.settings.provider
            self._desired_model = self.settings.model
            self._desired_reasoning = self.settings.reasoning_effort
            self._begin_preference_workflow()
        elif method == "model.options":
            logging.getLogger("hal9000.hermes").info(
                "Hermes model inventory loaded: %d providers",
                len(payload.get("providers") or []),
            )
            self.modelOptionsReady.emit(payload)
        elif method == "config.set" and request_id in self._model_switch_requests:
            generation, provider, model = self._model_switch_requests.pop(request_id)
            if generation != self._preference_generation:
                return
            warning = str(payload.get("warning") or "").strip()
            if bool(payload.get("confirm_required")):
                message = str(
                    payload.get("confirm_message")
                    or "Hermes requires confirmation for this model"
                )
                self.modelOperationError.emit(message)
                self._fail_preference_workflow(generation, message)
                return
            if warning and "failed" in warning.lower():
                self.modelOperationError.emit(warning)
                self._fail_preference_workflow(generation, warning)
                return
            self.settings.provider = provider
            self.settings.model = model
            logging.getLogger("hal9000.hermes").info(
                "HAL session model selected: %s // %s", provider or "automatic", model
            )
            self.modelChanged.emit(provider, model)
            if self._desired_reasoning:
                self._request_reasoning(generation, self._desired_reasoning)
            else:
                self._finish_session_preferences(generation)
        elif method == "config.set" and request_id in self._reasoning_requests:
            generation, effort = self._reasoning_requests.pop(request_id)
            if generation != self._preference_generation:
                return
            self.settings.reasoning_effort = effort
            logging.getLogger("hal9000.hermes").info(
                "HAL session reasoning selected: %s", effort
            )
            self.reasoningChanged.emit(effort)
            self._finish_session_preferences(generation)
        elif method == "approval.respond":
            self.approvalResolved.emit("")

    def _on_rpc_error(
        self, request_id: str, method: str, message: str, _code: int
    ) -> None:
        if request_id == self._self_mcp_request and method.startswith("mcp.servers."):
            self._self_mcp_stage = "failed"
            self._self_mcp_request = ""
            self.errorOccurred.emit(f"HAL self MCP registration failed: {message}")
            self._create_or_resume_session()
            return
        if method == "session.resume" and self._stored_session_id:
            self._stored_session_id = ""
            self._runtime_session_id = ""
            self._create_or_resume_session()
            return
        model_workflow = self._model_switch_requests.pop(request_id, None)
        reasoning_workflow = self._reasoning_requests.pop(request_id, None)
        if method == "model.options":
            self.modelOperationError.emit(message)
            return
        workflow = model_workflow or reasoning_workflow
        if workflow:
            generation = workflow[0]
            if generation != self._preference_generation:
                return
            self.modelOperationError.emit(message)
            self._fail_preference_workflow(generation, message)
            return
        self.errorOccurred.emit(message)

    def _finish_session_preferences(
        self,
        generation: int | None = None,
        deliver_queued: bool = True,
        failure: str = "",
    ) -> None:
        active_generation = self._preference_generation if generation is None else generation
        if active_generation != self._preference_generation:
            return
        self._preferences_pending = False
        self._preferences_failed = not deliver_queued
        self._preference_state = "ready" if deliver_queued else "failed"
        # A disconnected client reports request errors synchronously. Avoid a
        # model.options -> error -> finish -> model.options recursion while the
        # transport is unwinding.
        if self.client.connected:
            self.requestModelOptions()
        queued = self._queued_prompt
        self._queued_prompt = ""
        if queued and deliver_queued:
            self.sendPrompt(queued)
        elif queued:
            self._emit_undelivered(
                queued,
                failure or "required Hermes session model could not be applied",
            )
            self.errorOccurred.emit(
                "HAL did not send the prompt because its Hermes session model "
                f"could not be applied: {failure or 'unknown model error'}"
            )

    def _fail_preference_workflow(self, generation: int, message: str) -> None:
        self._finish_session_preferences(
            generation,
            deliver_queued=False,
            failure=message,
        )

    def _emit_undelivered(self, prompt: str, reason: str) -> None:
        encoded = redact_text(prompt).encode("utf-8")
        self.structuredEvent.emit(
            {
                "type": "hal.prompt.undelivered",
                "session_id": self._runtime_session_id or self._stored_session_id,
                "payload": {
                    "sha256": hashlib.sha256(encoded).hexdigest(),
                    "bytes": len(encoded),
                    "reason": reason[:1000],
                    "prompt_retained": False,
                },
            }
        )

    def _on_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        session_id = str(event.get("session_id") or "")
        if session_id and self._runtime_session_id and session_id != self._runtime_session_id:
            return
        self.structuredEvent.emit(dict(event))
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event_type == "session.info":
            tools = payload.get("tools") if isinstance(payload.get("tools"), dict) else {}
            self.integrationsChanged.emit(sorted(map(str, tools.keys())))
        elif event_type == "message.start":
            self.assistantStarted.emit(str(payload.get("message_id") or payload.get("id") or ""))
        elif event_type == "message.delta":
            self.assistantDelta.emit(
                str(payload.get("delta") or payload.get("text") or payload.get("content") or "")
            )
        elif event_type == "message.interim":
            self.assistantInterim.emit(
                str(payload.get("text") or payload.get("content") or ""),
                bool(payload.get("already_streamed")),
            )
        elif event_type == "message.complete":
            self.assistantCompleted.emit(
                str(payload.get("text") or payload.get("content") or payload.get("message") or ""),
                bool(payload.get("response_previewed")),
            )
            self.turnFinished.emit()
        elif event_type.startswith("tool."):
            self.toolActivity.emit({"type": event_type, **payload})
        elif event_type == "status.update":
            self.toolActivity.emit(
                {
                    "type": event_type,
                    "id": str(payload.get("kind") or "status"),
                    "name": str(payload.get("kind") or "status"),
                    "message": str(payload.get("text") or ""),
                }
            )
        elif event_type in {"approval.request", "sudo.request", "secret.request"}:
            self.approvalRequested.emit({"type": event_type, **payload})
            request_id = str(payload.get("request_id") or "")
            if event_type == "approval.request" and request_id:
                self.client.request(
                    "approval.received",
                    {"session_id": self._runtime_session_id, "request_id": request_id},
                    20_000,
                )
        elif event_type == "error":
            self.errorOccurred.emit(str(payload.get("message") or "Hermes reported an error"))
            self.turnFinished.emit()
        elif event_type == "gateway.ready":
            self._set_status("connected")

    def _set_latency(self, milliseconds: float) -> None:
        self._latency = milliseconds
        self.latencyChanged.emit(milliseconds)

    def _set_status(self, status: str) -> None:
        if status == self._status:
            return
        self._status = status
        self.statusChanged.emit(status)

    def _on_process_error(self, message: str) -> None:
        self._set_status("error")
        self.errorOccurred.emit(message)

    def _on_transport_error(self, message: str) -> None:
        if self.settings.mode == "remote":
            self.errorOccurred.emit(f"Hermes connection failed: {message}")

    def _on_process_running(self, running: bool) -> None:
        if running or not self._want_running:
            return
        self.client.close()
        self._restart_attempts += 1
        if self._restart_attempts > 5:
            self._set_status("error")
            self.errorOccurred.emit("Hermes backend repeatedly exited; use Reconnect to retry")
            return
        delay_ms = min(15_000, 500 * (2 ** (self._restart_attempts - 1)))
        self._set_status("restarting")
        self._restart_timer.start(delay_ms)

    def _restart_owned_backend(self) -> None:
        if self._want_running:
            self._launch_owned_backend()

    @staticmethod
    def _log_process_output(text: str) -> None:
        logging.getLogger("hal9000.hermes").debug("Hermes: %s", text)

    @staticmethod
    def _socket_url_for_base(base_url: str, token: str) -> str:
        parsed = urlparse(base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        query = f"token={quote(token)}" if token else parsed.query
        return urlunparse((scheme, parsed.netloc, "/api/ws", "", query, ""))
