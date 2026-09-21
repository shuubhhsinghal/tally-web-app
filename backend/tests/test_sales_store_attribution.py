import os
import json
import pytest
import requests
from unittest.mock import patch, AsyncMock

os.environ["TESTING"] = "true"

from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db, init_db

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        conn.commit()
    yield


def _latest_sales_row():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM offline_queue WHERE description LIKE 'Sales:%' ORDER BY id DESC LIMIT 1")
        return dict(cursor.fetchone())


@patch('backend.routers.sales.tally_transport.post', new_callable=AsyncMock)
def test_sales_persists_cost_center_for_explicit_store(mock_post):
    # Store is picked explicitly on the form now, not guessed from the ledger
    # name -- a ledger with no store hint in it must still attribute
    # correctly as long as a store was selected.
    mock_post.side_effect = requests.exceptions.ConnectionError()
    client.post("/api/sales/post", json={
        "ledger": "Generic Customer", "amount": 100.0, "tally_date": "20260101", "narration": "test", "store": "Mahagun"
    })
    row = _latest_sales_row()
    payload = json.loads(row["payload"])
    assert payload["cost_center"] == "Mahagun"


@patch('backend.routers.sales.tally_transport.post', new_callable=AsyncMock)
def test_sales_persists_correctly_cased_vvip(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectionError()
    client.post("/api/sales/post", json={
        "ledger": "Gpay Vvip", "amount": 100.0, "tally_date": "20260101", "narration": "test", "store": "VVIP"
    })
    row = _latest_sales_row()
    payload = json.loads(row["payload"])
    assert payload["cost_center"] == "VVIP"
    assert payload["cost_center"] != "Vvip"


def test_sales_missing_store_is_rejected():
    # Store is a required field now -- FastAPI/Pydantic rejects the request
    # before it ever reaches the handler.
    res = client.post("/api/sales/post", json={
        "ledger": "Cash Mahagun", "amount": 100.0, "tally_date": "20260101", "narration": "test"
    })
    assert res.status_code == 422


def test_sales_unmapped_store_is_rejected():
    res = client.post("/api/sales/post", json={
        "ledger": "Cash Mahagun", "amount": 100.0, "tally_date": "20260101", "narration": "test", "store": "Nonexistent Store"
    })
    assert res.status_code == 400
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as c FROM offline_queue WHERE description LIKE 'Sales:%'")
        assert cursor.fetchone()["c"] == 0


@patch('backend.routers.sales.tally_transport.post', new_callable=AsyncMock)
def test_sales_mahagun_matches_queue_store_filter(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectionError()
    client.post("/api/sales/post", json={
        "ledger": "Cash Mahagun", "amount": 100.0, "tally_date": "20260101", "narration": "test", "store": "Mahagun"
    })

    res = client.get("/api/dashboard/queue?status=PENDING&store=Mahagun&page=1&limit=10")
    data = res.json()
    assert data["total"] == 1
    assert "Sales" in data["items"][0]["description"]

    res_unallocated = client.get("/api/dashboard/queue?status=PENDING&store=Unallocated&page=1&limit=10")
    assert res_unallocated.json()["total"] == 0


@patch('backend.routers.sales.tally_transport.post', new_callable=AsyncMock)
def test_sales_vvip_matches_queue_store_filter(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectionError()
    client.post("/api/sales/post", json={
        "ledger": "Gpay Vvip", "amount": 100.0, "tally_date": "20260101", "narration": "test", "store": "VVIP"
    })

    res = client.get("/api/dashboard/queue?status=PENDING&store=VVIP&page=1&limit=10")
    assert res.json()["total"] == 1
