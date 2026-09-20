import asyncio
import json

import pytest
import requests
from unittest.mock import AsyncMock, MagicMock

from backend.connector.manager import ConnectorManager
from backend.connector.protocol import build_job_result_ok, build_job_result_error, ErrorKind

pytestmark = pytest.mark.anyio


def make_fake_ws():
    ws = MagicMock()
    ws.send_text = AsyncMock()
    ws.close = AsyncMock()
    return ws


async def test_no_connector_connected_raises_connect_timeout():
    manager = ConnectorManager()
    with pytest.raises(requests.exceptions.ConnectTimeout):
        await manager.send_job("<xml/>", timeout=1)


async def test_successful_round_trip():
    manager = ConnectorManager()
    ws = make_fake_ws()
    await manager.register(ws)

    async def respond():
        await asyncio.sleep(0.01)
        sent = json.loads(ws.send_text.call_args[0][0])
        await manager.handle_incoming(json.dumps(build_job_result_ok(sent["id"], 200, "<ENVELOPE>ok</ENVELOPE>")))

    task = asyncio.create_task(respond())
    response = await manager.send_job("<xml/>", timeout=2)
    await task

    assert response.status_code == 200
    assert response.text == "<ENVELOPE>ok</ENVELOPE>"


async def test_reply_never_arrives_raises_timeout():
    manager = ConnectorManager()
    ws = make_fake_ws()
    await manager.register(ws)
    with pytest.raises(requests.exceptions.Timeout):
        await manager.send_job("<xml/>", timeout=0.05)


async def test_job_result_connection_error_kind_raises_connection_error():
    manager = ConnectorManager()
    ws = make_fake_ws()
    await manager.register(ws)

    async def respond():
        await asyncio.sleep(0.01)
        sent = json.loads(ws.send_text.call_args[0][0])
        await manager.handle_incoming(json.dumps(
            build_job_result_error(sent["id"], ErrorKind.CONNECTION_ERROR.value, "Tally not running")
        ))

    task = asyncio.create_task(respond())
    with pytest.raises(requests.exceptions.ConnectionError):
        await manager.send_job("<xml/>", timeout=2)
    await task


async def test_job_result_timeout_kind_raises_timeout():
    manager = ConnectorManager()
    ws = make_fake_ws()
    await manager.register(ws)

    async def respond():
        await asyncio.sleep(0.01)
        sent = json.loads(ws.send_text.call_args[0][0])
        await manager.handle_incoming(json.dumps(
            build_job_result_error(sent["id"], ErrorKind.TIMEOUT.value, "Tally never replied")
        ))

    task = asyncio.create_task(respond())
    with pytest.raises(requests.exceptions.Timeout):
        await manager.send_job("<xml/>", timeout=2)
    await task


async def test_disconnect_without_ack_is_safe_connect_timeout():
    manager = ConnectorManager()
    ws = make_fake_ws()
    await manager.register(ws)

    async def disconnect_mid_flight():
        await asyncio.sleep(0.01)
        # No job_ack received -- the connector never even started sending to Tally.
        await manager.unregister(ws)

    task = asyncio.create_task(disconnect_mid_flight())
    with pytest.raises(requests.exceptions.ConnectTimeout):
        await manager.send_job("<xml/>", timeout=2)
    await task


async def test_disconnect_after_ack_is_ambiguous_timeout():
    manager = ConnectorManager()
    ws = make_fake_ws()
    await manager.register(ws)

    async def ack_then_disconnect():
        await asyncio.sleep(0.01)
        sent = json.loads(ws.send_text.call_args[0][0])
        await manager.handle_incoming(json.dumps({"type": "job_ack", "id": sent["id"]}))
        await manager.unregister(ws)

    task = asyncio.create_task(ack_then_disconnect())
    with pytest.raises(requests.exceptions.Timeout):
        await manager.send_job("<xml/>", timeout=2)
    await task


async def test_new_connection_evicts_existing_registered_connector():
    manager = ConnectorManager()
    old_ws = make_fake_ws()
    new_ws = make_fake_ws()

    await manager.register(old_ws)
    assert manager.is_connected()

    await manager.register(new_ws)

    old_ws.close.assert_awaited()
    assert manager._ws is new_ws


async def test_bytes_payload_is_sent_as_json_safe_text():
    # Several call sites pass xml_data.encode('utf-8') (bytes), which used to
    # be fine for requests.post(data=...) but can't go straight into a JSON
    # text frame -- must be decoded before being serialized.
    manager = ConnectorManager()
    ws = make_fake_ws()
    await manager.register(ws)

    async def respond():
        await asyncio.sleep(0.01)
        sent = json.loads(ws.send_text.call_args[0][0])
        assert sent["xml"] == "<ENVELOPE>hello</ENVELOPE>"
        await manager.handle_incoming(json.dumps(build_job_result_ok(sent["id"], 200, "ok")))

    task = asyncio.create_task(respond())
    response = await manager.send_job(b"<ENVELOPE>hello</ENVELOPE>", timeout=2)
    await task
    assert response.status_code == 200


async def test_tally_reachable_defaults_false_until_reported():
    # Conservative default: a freshly-registered connector hasn't told us
    # anything about Tally's own reachability yet.
    manager = ConnectorManager()
    ws = make_fake_ws()
    await manager.register(ws)
    assert manager.is_connected() is True
    assert manager.is_tally_reachable() is False


async def test_tally_reachable_reflects_latest_status_report():
    manager = ConnectorManager()
    ws = make_fake_ws()
    await manager.register(ws)

    await manager.handle_incoming(json.dumps({"type": "tally_status", "reachable": True}))
    assert manager.is_tally_reachable() is True

    await manager.handle_incoming(json.dumps({"type": "tally_status", "reachable": False}))
    assert manager.is_tally_reachable() is False


async def test_tally_reachable_resets_on_disconnect():
    # Connector alive but Tally reachable -- then it disconnects. Reachability
    # must not keep reporting stale "True" once we can no longer verify it.
    manager = ConnectorManager()
    ws = make_fake_ws()
    await manager.register(ws)
    await manager.handle_incoming(json.dumps({"type": "tally_status", "reachable": True}))
    assert manager.is_tally_reachable() is True

    await manager.unregister(ws)
    assert manager.is_tally_reachable() is False


async def test_is_connected_reflects_registration_state():
    manager = ConnectorManager()
    assert manager.is_connected() is False
    ws = make_fake_ws()
    await manager.register(ws)
    assert manager.is_connected() is True
    await manager.unregister(ws)
    assert manager.is_connected() is False
