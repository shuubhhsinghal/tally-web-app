import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db

client = TestClient(app)

SUCCESS_XML = """<ENVELOPE>
  <HEADER><STATUS>1</STATUS></HEADER>
  <BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></IMPORTRESULT></DATA></BODY>
</ENVELOPE>"""

REJECTED_XML = """<ENVELOPE>
  <HEADER><STATUS>1</STATUS></HEADER>
  <BODY><DATA><IMPORTRESULT><CREATED>0</CREATED><ALTERED>0</ALTERED><ERRORS>1</ERRORS></IMPORTRESULT>
  <LINEERROR>Could not find ledger 'Bad Ledger'</LINEERROR></DATA></BODY>
</ENVELOPE>"""


@pytest.fixture(autouse=True)
def clean_queue():
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        conn.commit()
    yield


def _txn(**overrides):
    txn = {
        "date": "20260101",
        "raw_narration": "TEST TXN",
        "withdraw": 100.0,
        "deposit": 0.0,
        "ledger": "Rent",
        "cost_center": None,
    }
    txn.update(overrides)
    return txn


def _queue_rows():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT status, description FROM offline_queue WHERE description LIKE 'Bank Stmt:%' ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def test_post_all_succeed(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.text = SUCCESS_XML
    monkeypatch.setattr("backend.routers.bank_statement.tally_transport.post", AsyncMock(return_value=mock_resp))

    payload = {
        "transactions": [_txn(raw_narration="Txn A"), _txn(raw_narration="Txn B", withdraw=0, deposit=50.0)],
        "bank_ledger_name": "HDFC Bank",
    }
    res = client.post("/api/bank-statement/post", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["synced_count"] == 2
    assert data["failed"] == []

    rows = _queue_rows()
    assert len(rows) == 2
    assert all(r["status"] == "SYNCED" for r in rows)


def test_post_partial_failure_does_not_fail_the_already_synced_vouchers(monkeypatch):
    # The bug this guards against: batching every transaction into one
    # combined Tally import and blanket-marking ALL of them FAILED on any
    # single LINEERROR, even the ones Tally had already actually created --
    # which would duplicate them in Tally on a naive retry. Each transaction
    # must get its own independent, accurate outcome.
    mock_resp_success = MagicMock()
    mock_resp_success.text = SUCCESS_XML
    mock_resp_rejected = MagicMock()
    mock_resp_rejected.text = REJECTED_XML

    post_mock = AsyncMock(side_effect=[mock_resp_success, mock_resp_rejected, mock_resp_success])
    monkeypatch.setattr("backend.routers.bank_statement.tally_transport.post", post_mock)

    payload = {
        "transactions": [
            _txn(raw_narration="Good One"),
            _txn(raw_narration="Bad One", ledger="Bad Ledger"),
            _txn(raw_narration="Good Two"),
        ],
        "bank_ledger_name": "HDFC Bank",
    }
    res = client.post("/api/bank-statement/post", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "partial"
    assert data["synced_count"] == 2
    assert len(data["failed"]) == 1
    assert "Bad Ledger" in data["failed"][0]

    rows = _queue_rows()
    assert len(rows) == 3
    synced = [r for r in rows if r["status"] == "SYNCED"]
    failed = [r for r in rows if r["status"] == "FAILED"]
    assert len(synced) == 2
    assert len(failed) == 1


def test_post_stops_and_queues_remaining_when_tally_goes_offline(monkeypatch):
    import requests

    async def _raise_connection_error(*a, **k):
        raise requests.exceptions.ConnectionError("simulated offline")
    monkeypatch.setattr("backend.routers.bank_statement.tally_transport.post", _raise_connection_error)

    payload = {
        "transactions": [_txn(raw_narration="Txn A"), _txn(raw_narration="Txn B")],
        "bank_ledger_name": "HDFC Bank",
    }
    res = client.post("/api/bank-statement/post", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "partial"
    assert data["queued_count"] == 2
    assert data["synced_count"] == 0

    rows = _queue_rows()
    assert len(rows) == 2
    assert all(r["status"] == "PENDING" for r in rows)
