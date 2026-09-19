import os
import json
import pytest
from datetime import datetime

os.environ["TESTING"] = "true"

from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db, queue_operation

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        conn.execute("DELETE FROM pending_masters")
        conn.commit()
    yield


def _insert_queue_row(operation_type, status, payload=None, is_hidden=0):
    now = datetime.now().isoformat()
    payload_str = json.dumps(payload) if payload is not None else None
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO offline_queue (operation_type, payload, xml_data, status, description, created_at, updated_at, is_hidden)
               VALUES (?, ?, '<ENVELOPE></ENVELOPE>', ?, 'Test item', ?, ?, ?)""",
            (operation_type, payload_str, status, now, now, is_hidden)
        )
        conn.commit()
        return cursor.lastrowid


# --- Retry guard for delivery_uncertain ---

def test_retry_blocks_when_delivery_uncertain():
    item_id = _insert_queue_row("POST_VOUCHER", "FAILED", payload={"delivery_uncertain": True})

    res = client.post(f"/api/dashboard/activity/{item_id}/retry")

    assert res.status_code == 409
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM offline_queue WHERE id = ?", (item_id,))
        assert cursor.fetchone()["status"] == "FAILED"


def test_retry_allows_ordinary_failed_item():
    item_id = _insert_queue_row("POST_VOUCHER", "FAILED", payload={"invoice_number": "INV-1"})

    res = client.post(f"/api/dashboard/activity/{item_id}/retry")

    assert res.status_code == 200
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM offline_queue WHERE id = ?", (item_id,))
        assert cursor.fetchone()["status"] == "PENDING"


def test_retry_unhides_a_previously_hidden_item():
    item_id = _insert_queue_row("POST_VOUCHER", "FAILED", payload={}, is_hidden=1)

    res = client.post(f"/api/dashboard/activity/{item_id}/retry")

    assert res.status_code == 200
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status, is_hidden FROM offline_queue WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        assert row["status"] == "PENDING"
        assert row["is_hidden"] == 0

    # Now visible again in the default (non-hidden) activity feed.
    activity_res = client.get("/api/dashboard/activity")
    assert any(a["id"] == item_id for a in activity_res.json())


# --- Delete guard: allowed for PENDING/FAILED vouchers, blocked when unconfirmed ---

def test_delete_allows_pending_voucher():
    item_id = _insert_queue_row("POST_VOUCHER", "PENDING", payload={"ledger": "Cash Mahagun"})

    res = client.delete(f"/api/dashboard/activity/{item_id}")

    assert res.status_code == 200
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM offline_queue WHERE id = ?", (item_id,))
        assert cursor.fetchone()["cnt"] == 0


def test_delete_blocks_pending_voucher_when_delivery_uncertain():
    item_id = _insert_queue_row("POST_VOUCHER", "PENDING", payload={"delivery_uncertain": True})

    res = client.delete(f"/api/dashboard/activity/{item_id}")

    assert res.status_code == 409
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM offline_queue WHERE id = ?", (item_id,))
        assert cursor.fetchone()["cnt"] == 1


def test_delete_blocks_failed_voucher_when_delivery_uncertain():
    item_id = _insert_queue_row("POST_VOUCHER", "FAILED", payload={"delivery_uncertain": True})

    res = client.delete(f"/api/dashboard/activity/{item_id}")

    assert res.status_code == 409
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM offline_queue WHERE id = ?", (item_id,))
        assert cursor.fetchone()["cnt"] == 1


def test_delete_allows_failed_voucher():
    item_id = _insert_queue_row("POST_VOUCHER", "FAILED")

    res = client.delete(f"/api/dashboard/activity/{item_id}")

    assert res.status_code == 200
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM offline_queue WHERE id = ?", (item_id,))
        assert cursor.fetchone()["cnt"] == 0


def test_delete_blocks_pending_master():
    item_id = _insert_queue_row("CREATE_LEDGER", "PENDING", payload={"name": "Test Ledger"})
    now = datetime.now().isoformat()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO pending_masters (entity_type, normalized_name, original_name, queue_id, status, created_at, updated_at)
               VALUES ('LEDGER', 'test ledger', 'Test Ledger', ?, 'PENDING', ?, ?)""",
            (item_id, now, now)
        )
        conn.commit()

    res = client.delete(f"/api/dashboard/activity/{item_id}")

    assert res.status_code == 400
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM offline_queue WHERE id = ?", (item_id,))
        assert cursor.fetchone()["cnt"] == 1
        cursor.execute("SELECT COUNT(*) as cnt FROM pending_masters WHERE queue_id = ?", (item_id,))
        assert cursor.fetchone()["cnt"] == 1


def test_delete_allows_failed_master():
    item_id = _insert_queue_row("CREATE_LEDGER", "FAILED", payload={"name": "Test Ledger"})
    now = datetime.now().isoformat()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO pending_masters (entity_type, normalized_name, original_name, queue_id, status, created_at, updated_at)
               VALUES ('LEDGER', 'test ledger', 'Test Ledger', ?, 'FAILED', ?, ?)""",
            (item_id, now, now)
        )
        conn.commit()

    res = client.delete(f"/api/dashboard/activity/{item_id}")

    assert res.status_code == 200
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM offline_queue WHERE id = ?", (item_id,))
        assert cursor.fetchone()["cnt"] == 0
        cursor.execute("SELECT COUNT(*) as cnt FROM pending_masters WHERE queue_id = ?", (item_id,))
        assert cursor.fetchone()["cnt"] == 0


# --- Failed-count and visibility ---

def test_failed_count_reflects_true_total_beyond_cap():
    for _ in range(15):
        _insert_queue_row("POST_VOUCHER", "FAILED")
    for _ in range(3):
        _insert_queue_row("POST_VOUCHER", "PENDING")

    res = client.get("/api/dashboard/stats")

    assert res.status_code == 200
    assert res.json()["failed_count"] == 15


def test_failed_count_includes_hidden_items():
    for _ in range(5):
        _insert_queue_row("POST_VOUCHER", "FAILED", is_hidden=1)

    res = client.get("/api/dashboard/stats")

    assert res.json()["failed_count"] == 5


def test_activity_default_excludes_hidden_items():
    hidden_id = _insert_queue_row("POST_VOUCHER", "FAILED", is_hidden=1)
    visible_id = _insert_queue_row("POST_VOUCHER", "FAILED", is_hidden=0)

    res = client.get("/api/dashboard/activity")

    ids = [a["id"] for a in res.json()]
    assert visible_id in ids
    assert hidden_id not in ids


def test_activity_with_include_hidden_and_status_filter_returns_all_failed():
    for _ in range(12):
        _insert_queue_row("POST_VOUCHER", "FAILED")
    hidden_id = _insert_queue_row("POST_VOUCHER", "FAILED", is_hidden=1)
    _insert_queue_row("POST_VOUCHER", "PENDING")

    res = client.get("/api/dashboard/activity?status=FAILED&include_hidden=true&limit=500")

    data = res.json()
    assert len(data) == 13
    assert all(a["status"] == "FAILED" for a in data)
    assert hidden_id in [a["id"] for a in data]
