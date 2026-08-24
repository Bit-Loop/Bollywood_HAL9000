"""Hermes JSON-RPC client using a dependency-light WebSocket worker."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal, Slot


@dataclass(slots=True)
class PendingRequest:
    method: str
    started: float
    timeout: QTimer


class _WebSocketWorker:
    """One reconnecting socket thread; Qt never depends on QtWebSockets."""

    def __init__(
        self,
        url: str,
        on_state: Callable[[str], None],
        on_message: Callable[[str], None],
        on_error: Callable[[str], None],
    ) -> None:
        self.url = url
        self.on_state = on_state
        self.on_message = on_message
        self.on_error = on_error
        self.outgoing: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="hal9000-hermes-websocket",
        )
        self._socket = None
        self._socket_lock = threading.Lock()

    def start(self) -> None:
        self.thread.start()

    def send(self, message: str) -> None:
        self.outgoing.put(message)

    def stop(self) -> None:
        self.stop_event.set()
        with self._socket_lock:
            socket = self._socket
        if socket is not None:
            try:
                socket.close()
            except Exception as exc:
                logging.getLogger("hal9000.hermes.transport").debug(
                    "WebSocket close failed: %s", exc
                )
        if self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=1.2)

    def _run(self) -> None:
        from websockets.sync.client import connect

        attempt = 0
        while not self.stop_event.is_set():
            self.on_state("connecting" if attempt == 0 else "reconnecting")
            try:
                with connect(
                    self.url,
                    open_timeout=15,
                    close_timeout=2,
                    ping_interval=None,
                    max_size=64 * 1024 * 1024,
                ) as socket:
                    with self._socket_lock:
                        self._socket = socket
                    attempt = 0
                    self.on_state("connected")
                    self._pump(socket)
            except Exception as exc:
                if not self.stop_event.is_set():
                    self.on_error(str(exc))
            finally:
                with self._socket_lock:
                    self._socket = None
            if self.stop_event.is_set():
                break
            attempt += 1
            self.on_state("reconnecting")
            delay = min(15.0, 0.5 * (2 ** min(attempt - 1, 5)))
            self.stop_event.wait(delay)
        self.on_state("offline")

    def _pump(self, socket) -> None:
        from websockets.exceptions import ConnectionClosed

        while not self.stop_event.is_set():
            while True:
                try:
                    outgoing = self.outgoing.get_nowait()
                except queue.Empty:
                    break
                socket.send(outgoing)
            try:
                message = socket.recv(timeout=0.08)
            except TimeoutError:
                continue
            except ConnectionClosed:
                return
            if message is None:
                return
            if isinstance(message, bytes):
                message = message.decode("utf-8", errors="replace")
            self.on_message(str(message))


class HermesGatewayClient(QObject):
    connectionChanged = Signal(str)
    eventReceived = Signal(dict)
    rpcResult = Signal(str, str, object)
    rpcError = Signal(str, str, str, int)
    latencyChanged = Signal(float)
    transportError = Signal(str)
    _transportState = Signal(str)
    _transportMessage = Signal(str)
    _transportError = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._state = "offline"
        self._url = ""
        self._next_id = 0
        self._pending: dict[str, PendingRequest] = {}
        self._worker: _WebSocketWorker | None = None
        self._transportState.connect(self._on_transport_state)
        self._transportMessage.connect(self._on_message)
        self._transportError.connect(self._on_transport_error)

    @property
    def state(self) -> str:
        return self._state

    @property
    def connected(self) -> bool:
        return self._state == "connected"

    @Slot(str)
    def connectTo(self, ws_url: str) -> None:
        if not ws_url.startswith(("ws://", "wss://")):
            self._set_state("error")
            self.rpcError.emit("", "connect", "Invalid Hermes WebSocket URL", 0)
            return
        self._url = ws_url
        old, self._worker = self._worker, None
        if old is not None:
            old.stop()
        worker = _WebSocketWorker(
            ws_url,
            self._transportState.emit,
            self._transportMessage.emit,
            self._transportError.emit,
        )
        self._worker = worker
        self._set_state("connecting")
        worker.start()

    @Slot()
    def close(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.stop()
        self._reject_all("Hermes connection closed")
        self._set_state("offline")

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout_ms: int = 120_000,
    ) -> str:
        self._next_id += 1
        request_id = f"hal-{self._next_id}"
        worker = self._worker
        if not self.connected or worker is None:
            self.rpcError.emit(request_id, method, "Hermes gateway is not connected", 0)
            return request_id
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(timeout_ms)
        timer.timeout.connect(lambda rid=request_id: self._on_timeout(rid))
        self._pending[request_id] = PendingRequest(method, time.perf_counter(), timer)
        timer.start()
        frame = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        worker.send(json.dumps(frame, separators=(",", ":")))
        return request_id

    @Slot(str)
    def _on_transport_state(self, state: str) -> None:
        if state in {"reconnecting", "offline"} and self._state == "connected":
            self._reject_all("Hermes gateway disconnected")
        self._set_state(state)

    @Slot(str)
    def _on_transport_error(self, message: str) -> None:
        logging.getLogger("hal9000.hermes.transport").warning(
            "Hermes WebSocket transport: %s", message
        )
        self.transportError.emit(message)
        if self._state != "reconnecting":
            self._set_state("error")

    @Slot(str)
    def _on_message(self, raw: str) -> None:
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError as exc:
            logging.getLogger("hal9000.hermes.transport").warning(
                "Ignored malformed Hermes frame: %s", exc
            )
            return
        if not isinstance(frame, dict):
            return
        request_id = frame.get("id")
        if request_id is not None:
            key = str(request_id)
            pending = self._pending.pop(key, None)
            if not pending:
                return
            pending.timeout.stop()
            pending.timeout.deleteLater()
            self.latencyChanged.emit((time.perf_counter() - pending.started) * 1000)
            error = frame.get("error")
            if isinstance(error, dict):
                self.rpcError.emit(
                    key,
                    pending.method,
                    str(error.get("message") or "Hermes RPC failed"),
                    int(error.get("code") or 0),
                )
            else:
                self.rpcResult.emit(key, pending.method, frame.get("result"))
            return
        if frame.get("method") == "event" and isinstance(frame.get("params"), dict):
            self.eventReceived.emit(frame["params"])

    def _on_timeout(self, request_id: str) -> None:
        pending = self._pending.pop(request_id, None)
        if not pending:
            return
        pending.timeout.deleteLater()
        self.rpcError.emit(
            request_id,
            pending.method,
            f"Hermes request timed out: {pending.method}",
            0,
        )

    def _reject_all(self, message: str) -> None:
        pending = list(self._pending.items())
        self._pending.clear()
        for request_id, item in pending:
            item.timeout.stop()
            item.timeout.deleteLater()
            self.rpcError.emit(request_id, item.method, message, 0)

    def _set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        self.connectionChanged.emit(state)
