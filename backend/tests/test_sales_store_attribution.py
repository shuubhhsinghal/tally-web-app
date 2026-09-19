import os
import json
import pytest
import requests
from unittest.mock import patch

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


@patch('requests.post')
def test_sales_persists_cost_center_for_mahagun(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectionError()
    client.post("/api/sales/post", json={
        "ledger": "Cash Mahagun", "amount": 100.0, "tally_date": "20260101", "narration": "test"
    })
    row = _latest_sales_row()
    payload = json.loads(row["payload"])
    assert payload["cost_center"] == "Mahagun"


@patch('requests.post')
def test_sales_persists_correctly_cased_vvip(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectionError()
    client.post("/api/sales/post", json={
        "ledger": "Gpay Vvip", "amount": 100.0, "tally_date": "20260101", "narration": "test"
    })
    row = _latest_sales_row()
    payload = json.loads(row["payload"])
    assert payload["cost_center"] == "VVIP"
    assert payload["cost_center"] != "Vvip"


@patch('requests.post')
def test_sales_with_no_store_in_ledger_name_has_no_cost_center(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectionError()
    client.post("/api/sales/post", json={
        "ledger": "Generic Customer", "amount": 100.0, "tally_date": "20260101", "narration": "test"
    })
    row = _latest_sales_row()
    payload = json.loads(row["payload"])
    assert "cost_center" not in payload


@patch('requests.post')
def test_sales_mahagun_now_matches_queue_store_filter(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectionError()
    client.post("/api/sales/post", json={
        "ledger": "Cash Mahagun", "amount": 100.0, "tally_date": "20260101", "narration": "test"
    })

    res = client.get("/api/dashboard/queue?status=PENDING&store=Mahagun&page=1&limit=10")
    data = res.json()
    assert data["total"] == 1
    assert "Sales" in data["items"][0]["description"]

    res_unallocated = client.get("/api/dashboard/queue?status=PENDING&store=Unallocated&page=1&limit=10")
    assert res_unallocated.json()["total"] == 0


@patch('requests.post')
def test_sales_vvip_matches_queue_store_filter(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectionError()
    client.post("/api/sales/post", json={
        "ledger": "Gpay Vvip", "amount": 100.0, "tally_date": "20260101", "narration": "test"
    })

    res = client.get("/api/dashboard/queue?status=PENDING&store=VVIP&page=1&limit=10")
    assert res.json()["total"] == 1
