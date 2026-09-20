import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.main import app
import backend.connector.router as connector_router_module

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    connector_router_module._handshake_attempts.clear()
    yield
    connector_router_module._handshake_attempts.clear()


@pytest.fixture
def valid_secret(monkeypatch):
    monkeypatch.setattr(connector_router_module, "TALLY_CONNECTOR_SECRET", "test-secret-token")
    return "test-secret-token"


def test_missing_token_is_rejected(monkeypatch):
    monkeypatch.setattr(connector_router_module, "TALLY_CONNECTOR_SECRET", "test-secret-token")
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/connector"):
            pass
    assert exc_info.value.code == 4401


def test_wrong_token_is_rejected(valid_secret):
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/connector", headers={"Authorization": "Bearer wrong-token"}):
            pass
    assert exc_info.value.code == 4401


def test_no_secret_configured_rejects_everything(monkeypatch):
    monkeypatch.setattr(connector_router_module, "TALLY_CONNECTOR_SECRET", "")
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/connector", headers={"Authorization": "Bearer anything"}):
            pass
    assert exc_info.value.code == 4401


def test_valid_token_is_accepted(valid_secret):
    with client.websocket_connect("/ws/connector", headers={"Authorization": f"Bearer {valid_secret}"}) as ws:
        assert connector_router_module.connector_manager.is_connected()
    assert not connector_router_module.connector_manager.is_connected()


def test_rate_limiter_rejects_burst_of_bad_attempts(valid_secret):
    for _ in range(10):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/connector", headers={"Authorization": "Bearer wrong"}):
                pass

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/connector", headers={"Authorization": "Bearer wrong"}):
            pass
    assert exc_info.value.code == 4429
