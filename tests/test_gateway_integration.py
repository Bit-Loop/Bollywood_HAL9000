from __future__ import annotations

import json
import shutil
import threading

from websockets.sync.server import serve

from hal9000.config import HermesSettings
from hal9000.hermes.client import HermesGatewayClient
from hal9000.hermes.discovery import discover_hermes
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
