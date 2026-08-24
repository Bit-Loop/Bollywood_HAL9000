from __future__ import annotations


def test_gateway_client_imports_without_qt_websockets() -> None:
    from hal9000.hermes.client import HermesGatewayClient

    assert HermesGatewayClient.__name__ == "HermesGatewayClient"
