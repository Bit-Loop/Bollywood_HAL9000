"""Semantic Hermes service used by the HAL controller."""

from __future__ import annotations

import logging
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


class HermesService(QObject):
    statusChanged = Signal(str)
    versionChanged = Signal(str)
    sessionChanged = Signal(str)
    latencyChanged = Signal(float)
    assistantStarted = Signal(str)
    assistantDelta = Signal(str)
    assistantCompleted = Signal(str)
    toolActivity = Signal(dict)
    approvalRequested = Signal(dict)
    approvalResolved = Signal(str)
    turnFinished = Signal()
    errorOccurred = Signal(str)
    integrationsChanged = Signal(list)
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
        self.client.close()
        if self.process is not None:
            self.process.stop()
        self._set_status("offline")

    def close(self) -> None:
        self.stop()
        self._probe_executor.shutdown(wait=False, cancel_futures=True)

    def set_token(self, token: str) -> None:
        self._token = token.strip()

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
        if not self._runtime_session_id:
            self._queued_prompt = clean
            if self.client.connected:
                self._create_or_resume_session()
            else:
                self.errorOccurred.emit("Hermes is not connected")
            return
        self.client.request(
            "prompt.submit",
            {"session_id": self._runtime_session_id, "text": clean},
        )

    @Slot()
    def cancel(self) -> None:
        if self._runtime_session_id:
            self.client.request(
                "session.interrupt", {"session_id": self._runtime_session_id}, 20_000
            )

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
            self.process = HermesProcessManager(self.installation.executable, self)
            self.process.backendReady.connect(self._on_backend_ready)
            self.process.errorOccurred.connect(self._on_process_error)
            self.process.outputReceived.connect(self._log_process_output)
            self.process.runningChanged.connect(self._on_process_running)
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

    def _on_rpc_result(self, _request_id: str, method: str, result: Any) -> None:
        payload = result if isinstance(result, dict) else {}
        if method in {"session.create", "session.resume"}:
            self._runtime_session_id = str(payload.get("session_id") or "")
            self._stored_session_id = str(
                payload.get("stored_session_id") or payload.get("session_key") or self._stored_session_id
            )
            self.settings.last_session_id = self._stored_session_id
            self.sessionChanged.emit(self.sessionId)
            info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
            tools = info.get("tools") if isinstance(info.get("tools"), dict) else {}
            self.integrationsChanged.emit(sorted(map(str, tools.keys())))
            queued = self._queued_prompt
            self._queued_prompt = ""
            if queued:
                self.sendPrompt(queued)
        elif method == "approval.respond":
            self.approvalResolved.emit("")

    def _on_rpc_error(
        self, _request_id: str, method: str, message: str, _code: int
    ) -> None:
        if method == "session.resume" and self._stored_session_id:
            self._stored_session_id = ""
            self._runtime_session_id = ""
            self._create_or_resume_session()
            return
        self.errorOccurred.emit(message)

    def _on_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        session_id = str(event.get("session_id") or "")
        if session_id and self._runtime_session_id and session_id != self._runtime_session_id:
            return
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event_type == "session.info":
            tools = payload.get("tools") if isinstance(payload.get("tools"), dict) else {}
            self.integrationsChanged.emit(sorted(map(str, tools.keys())))
        elif event_type == "message.start":
            self.assistantStarted.emit(str(payload.get("message_id") or payload.get("id") or ""))
        elif event_type in {"message.delta", "message.interim"}:
            self.assistantDelta.emit(
                str(payload.get("delta") or payload.get("text") or payload.get("content") or "")
            )
        elif event_type == "message.complete":
            self.assistantCompleted.emit(
                str(payload.get("text") or payload.get("content") or payload.get("message") or "")
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
