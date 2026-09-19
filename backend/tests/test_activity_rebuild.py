import os
import json
import pytest
from datetime import datetime

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


def _insert_queue_row(operation_type, status, payload, description="Test item"):
    now = datetime.now().isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO offline_queue (operation_type, payload, xml_data, status, description, created_at, updated_at, is_hidden)
               VALUES (?, ?, '<ENVELOPE></ENVELOPE>', ?, ?, ?, ?, 0)""",
            (operation_type, json.dumps(payload), status, description, now, now)
        )
        conn.commit()
        return cursor.lastrowid


def _get_row(item_id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM offline_queue WHERE id = ?", (item_id,))
        return dict(cursor.fetchone())


# --- Sales rebuild ---

def test_rebuild_sales_updates_payload_and_xml():
    item_id = _insert_queue_row("POST_VOUCHER", "PENDING", {
        "ledger": "Cash Mahagun", "amount": 100.0, "tally_date": "20260101", "narration": "old"
    }, description="Sales: 100.0 from Cash Mahagun")

    res = client.post(f"/api/dashboard/activity/{item_id}/rebuild", json={
        "payload": {"ledger": "Cash Gulshan", "amount": 250.0, "tally_date": "20260215", "narration": "updated"}
    })

    assert res.status_code == 200
    row = _get_row(item_id)
    assert row["status"] == "PENDING"
    payload = json.loads(row["payload"])
    assert payload["ledger"] == "Cash Gulshan"
    assert payload["amount"] == 250.0
    assert payload["cost_center"] == "Gulshan"
    assert "Cash Gulshan" in row["xml_data"]
    assert "250.0" in row["xml_data"] or "250" in row["xml_data"]


def test_rebuild_sales_resolves_vvip_casing_correctly():
    item_id = _insert_queue_row("POST_VOUCHER", "PENDING", {
        "ledger": "Cash Mahagun", "amount": 100.0, "tally_date": "20260101", "narration": ""
    })

    res = client.post(f"/api/dashboard/activity/{item_id}/rebuild", json={
        "payload": {"ledger": "Gpay Vvip", "amount": 100.0, "tally_date": "20260101", "narration": ""}
    })

    assert res.status_code == 200
    payload = json.loads(_get_row(item_id)["payload"])
    assert payload["cost_center"] == "VVIP"


def test_rebuild_sales_no_store_in_ledger_clears_cost_center():
    item_id = _insert_queue_row("POST_VOUCHER", "PENDING", {
        "ledger": "Cash Mahagun", "amount": 100.0, "tally_date": "20260101", "narration": "", "cost_center": "Mahagun"
    })

    res = client.post(f"/api/dashboard/activity/{item_id}/rebuild", json={
        "payload": {"ledger": "Generic Customer", "amount": 100.0, "tally_date": "20260101", "narration": ""}
    })

    assert res.status_code == 200
    payload = json.loads(_get_row(item_id)["payload"])
    assert "cost_center" not in payload


def test_rebuild_sales_rejects_when_not_pending():
    item_id = _insert_queue_row("POST_VOUCHER", "SYNCED", {
        "ledger": "Cash Mahagun", "amount": 100.0, "tally_date": "20260101", "narration": ""
    })

    res = client.post(f"/api/dashboard/activity/{item_id}/rebuild", json={
        "payload": {"ledger": "Cash Gulshan", "amount": 250.0, "tally_date": "20260101", "narration": ""}
    })

    assert res.status_code == 409


# --- Purchase item rebuild still works (regression coverage for the dispatch refactor) ---

def test_rebuild_purchase_item_still_works():
    item_id = _insert_queue_row("POST_VOUCHER", "PENDING", {
        "supplier": "Test Supplier", "invoice_number": "INV-1", "cost_center": "Mahagun",
        "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "rounding_off": 0.0,
        "items": [{"name": "Item A", "qty": 1.0, "uom": "PCS", "rate": 10.0, "amount": 10.0}]
    }, description="Purchase Item Invoice: INV-1 from Test Supplier")

    res = client.post(f"/api/dashboard/activity/{item_id}/rebuild", json={
        "payload": {
            "supplier": "Test Supplier", "invoice_number": "INV-1", "tally_date": "20260101", "cost_center": "Mahagun",
            "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "rounding_off": 0.0,
            "items": [{"name": "Item A", "qty": 2.0, "uom": "PCS", "rate": 10.0, "amount": 20.0}]
        }
    })

    assert res.status_code == 200
    row = _get_row(item_id)
    assert row["status"] == "PENDING"
    payload = json.loads(row["payload"])
    assert payload["items"][0]["qty"] == 2.0


def test_rebuild_blocks_when_delivery_uncertain():
    item_id = _insert_queue_row("POST_VOUCHER", "PENDING", {
        "ledger": "Cash Mahagun", "amount": 100.0, "tally_date": "20260101", "narration": "",
        "delivery_uncertain": True
    })

    res = client.post(f"/api/dashboard/activity/{item_id}/rebuild", json={
        "payload": {"ledger": "Cash Gulshan", "amount": 250.0, "tally_date": "20260101", "narration": ""}
    })

    assert res.status_code == 409
    row = _get_row(item_id)
    assert json.loads(row["payload"])["ledger"] == "Cash Mahagun"  # unchanged


def test_rebuild_rejects_unrecognized_shape():
    item_id = _insert_queue_row("POST_VOUCHER", "PENDING", {
        "from_account": "Cash", "to_account": "Bank", "amount": 100.0
    })

    res = client.post(f"/api/dashboard/activity/{item_id}/rebuild", json={
        "payload": {"from_account": "Cash", "to_account": "Bank", "amount": 200.0}
    })

    assert res.status_code == 400
