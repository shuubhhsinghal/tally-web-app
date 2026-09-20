import os
import json
import pytest
import requests
from unittest.mock import patch, MagicMock, AsyncMock

os.environ["TESTING"] = "true"

from backend.database import get_db, queue_operation
from backend.services.tally_sync_worker import flush_offline_queue

pytestmark = pytest.mark.anyio

SUCCESS_XML = """<ENVELOPE>
  <HEADER><STATUS>1</STATUS></HEADER>
  <BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></IMPORTRESULT></DATA></BODY>
</ENVELOPE>"""


@pytest.fixture(autouse=True)
def clean_db():
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        conn.commit()
    yield


def _seed_voucher(operation_type="POST_VOUCHER"):
    return queue_operation(operation_type, "<ENVELOPE></ENVELOPE>", {"invoice_number": "INV-1"}, "Test voucher")


def _get_row(queue_id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM offline_queue WHERE id = ?", (queue_id,))
        return dict(cursor.fetchone())


@patch('backend.services.tally_sync_worker.tally_transport.post', new_callable=AsyncMock)
async def test_timeout_marks_failed_and_sets_delivery_uncertain(mock_post):
    queue_id = _seed_voucher()
    mock_post.side_effect = requests.exceptions.Timeout()

    await flush_offline_queue()

    row = _get_row(queue_id)
    assert row["status"] == "FAILED"
    assert "Verify this voucher in Tally" in row["error_message"]
    payload = json.loads(row["payload"])
    assert payload.get("delivery_uncertain") is True


@patch('backend.services.tally_sync_worker.tally_transport.post', new_callable=AsyncMock)
async def test_success_clears_delivery_uncertain_after_proactive_set(mock_post):
    queue_id = _seed_voucher()
    mock_resp = MagicMock()
    mock_resp.text = SUCCESS_XML
    mock_post.return_value = mock_resp

    await flush_offline_queue()

    row = _get_row(queue_id)
    assert row["status"] == "SYNCED"
    payload = json.loads(row["payload"])
    assert not payload.get("delivery_uncertain")


@patch('backend.services.tally_sync_worker.tally_transport.post', new_callable=AsyncMock)
async def test_connection_error_clears_flag_and_stays_pending(mock_post):
    queue_id = _seed_voucher()
    mock_post.side_effect = requests.exceptions.ConnectionError()

    await flush_offline_queue()

    row = _get_row(queue_id)
    assert row["status"] == "PENDING"
    payload = json.loads(row["payload"])
    assert not payload.get("delivery_uncertain")


@patch('backend.services.tally_sync_worker.tally_transport.post', new_callable=AsyncMock)
async def test_connect_timeout_clears_flag_and_stays_pending(mock_post):
    # ConnectTimeout means the connection itself never established (Tally
    # unreachable) -- must be treated the same as ConnectionError, NOT as an
    # ambiguous delivery. This is the real-world common case when TALLY_URL
    # points to an address that silently drops packets instead of refusing.
    queue_id = _seed_voucher()
    mock_post.side_effect = requests.exceptions.ConnectTimeout()

    await flush_offline_queue()

    row = _get_row(queue_id)
    assert row["status"] == "PENDING"
    payload = json.loads(row["payload"])
    assert not payload.get("delivery_uncertain")


@patch('backend.services.tally_sync_worker.tally_transport.post', new_callable=AsyncMock)
async def test_repack_voucher_also_gets_delivery_uncertain_protection(mock_post):
    queue_id = _seed_voucher(operation_type="REPACK_VOUCHER")
    mock_post.side_effect = requests.exceptions.Timeout()

    await flush_offline_queue()

    row = _get_row(queue_id)
    assert row["status"] == "FAILED"
    payload = json.loads(row["payload"])
    assert payload.get("delivery_uncertain") is True
