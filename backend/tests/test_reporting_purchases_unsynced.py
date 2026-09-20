import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db, queue_operation, update_queue_status, set_delivery_uncertain
from backend.services.reporting_purchase_service import get_unsynced_purchases, get_pending_purchases_trend

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        conn.commit()
    yield
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        conn.commit()


def _queue_accounting_purchase(amount, tally_date, cost_center="Mahagun", status=None):
    payload = {"supplier": "Test Supplier", "invoice_number": "INV-1", "amount": amount, "tally_date": tally_date, "cost_center": cost_center, "narration": ""}
    qid = queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Purchase Invoice: {payload['invoice_number']} from {payload['supplier']}")
    if status:
        update_queue_status(qid, status)
    return qid


def _queue_item_wise_purchase(items, tally_date, cost_center="Mahagun", cgst=0.0, sgst=0.0, adjustment=None, status=None):
    payload = {
        "supplier": "Test Supplier", "invoice_number": "INV-2", "tally_date": tally_date,
        "cost_center": cost_center, "cgst": cgst, "sgst": sgst, "igst": 0.0, "rounding_off": 0.0,
        "items": items, "adjustment": adjustment,
    }
    qid = queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Purchase Item Invoice: {payload['invoice_number']} from {payload['supplier']}")
    if status:
        update_queue_status(qid, status)
    return qid


def _queue_debit_note(adjustment_items, tally_date, adjustment_store="Mahagun", status=None):
    payload = {
        "supplier": "Test Supplier", "invoice_number": "INV-3", "tally_date": tally_date,
        "cost_center": "Mahagun", "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "rounding_off": 0.0,
        "items": [{"name": "Item A", "qty": 1, "uom": "PCS", "rate": 100.0, "amount": 100.0}],
        "adjustment": {"store": adjustment_store, "items": adjustment_items},
    }
    qid = queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Purchase Return (Debit Note): {payload['invoice_number']} from {payload['supplier']}")
    if status:
        update_queue_status(qid, status)
    return qid


def test_accounting_mode_pending_purchase_counted():
    _queue_accounting_purchase(1000.0, "20260115")
    result = get_unsynced_purchases("20260101", "20260131")
    assert result["pending_count"] == 1
    assert result["pending_amount"] == 1000.0


def test_item_wise_pending_purchase_sums_items_and_gst():
    items = [{"name": "Item A", "qty": 2, "uom": "PCS", "rate": 100.0, "amount": 200.0}]
    _queue_item_wise_purchase(items, "20260115", cgst=18.0, sgst=18.0)
    result = get_unsynced_purchases("20260101", "20260131")
    assert result["pending_count"] == 1
    assert result["pending_amount"] == 236.0  # 200 + 18 + 18


def test_item_wise_pending_purchase_gst_included_in_rate():
    # When GST is included in the item rate, cgst/sgst/igst are already 0 --
    # the formula must not double-count or miss anything.
    items = [{"name": "Item A", "qty": 1, "uom": "PCS", "rate": 118.0, "amount": 118.0}]
    _queue_item_wise_purchase(items, "20260115", cgst=0.0, sgst=0.0)
    result = get_unsynced_purchases("20260101", "20260131")
    assert result["pending_amount"] == 118.0


def test_debit_note_return_is_deducted_not_added():
    _queue_accounting_purchase(1000.0, "20260115")
    _queue_debit_note([{"name": "Item A", "qty": 2, "uom": "PCS", "rate": 50.0, "amount": 100.0}], "20260116")
    result = get_unsynced_purchases("20260101", "20260131")
    assert result["pending_count"] == 2
    assert result["pending_amount"] == 900.0  # 1000 - (2*50)


def test_failed_purchase_counted_separately():
    _queue_accounting_purchase(500.0, "20260115", status="FAILED")
    result = get_unsynced_purchases("20260101", "20260131")
    assert result["pending_count"] == 0
    assert result["failed_count"] == 1
    assert result["failed_amount"] == 500.0


def test_cost_centre_filter_uses_adjustment_store_for_returns():
    _queue_debit_note([{"name": "Item A", "qty": 1, "uom": "PCS", "rate": 100.0, "amount": 100.0}], "20260115", adjustment_store="Gulshan")
    result_mahagun = get_unsynced_purchases("20260101", "20260131", cost_centre="Mahagun")
    result_gulshan = get_unsynced_purchases("20260101", "20260131", cost_centre="Gulshan")
    assert result_mahagun["pending_count"] == 0
    assert result_gulshan["pending_count"] == 1
    assert result_gulshan["pending_amount"] == -100.0


def test_pending_purchases_trend_groups_by_date_and_nets_returns():
    _queue_accounting_purchase(1000.0, "20260115", cost_center="Mahagun")
    _queue_debit_note([{"name": "Item A", "qty": 1, "uom": "PCS", "rate": 300.0, "amount": 300.0}], "20260115", adjustment_store="Mahagun")

    result = get_pending_purchases_trend("20260101", "20260131")
    assert len(result) == 1
    assert result[0]["date"] == "20260115"
    assert result[0]["Mahagun"] == 700.0
    assert result[0]["Combined"] == 700.0


def test_pending_purchases_trend_excludes_delivery_uncertain():
    qid = _queue_accounting_purchase(1000.0, "20260115")
    set_delivery_uncertain(qid, True)
    result = get_pending_purchases_trend("20260101", "20260131")
    assert result == []


def test_non_purchase_queue_row_ignored():
    queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", {"amount": 999.0}, "Sales: 999.0 from Cash")
    result = get_unsynced_purchases("20260101", "20260131")
    assert result["pending_count"] == 0
    assert result["pending_amount"] == 0.0


def test_purchases_endpoint_blends_pending_and_returns():
    _queue_accounting_purchase(1000.0, "20260115")
    _queue_debit_note([{"name": "Item A", "qty": 1, "uom": "PCS", "rate": 200.0, "amount": 200.0}], "20260116")

    resp = client.get("/api/reporting/purchases", params={"start_date": "20260101", "end_date": "20260131"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["net_purchases_confirmed"] == 0.0
    assert data["summary"]["pending_amount"] == 800.0  # 1000 - 200
    assert data["summary"]["net_purchases"] == 800.0
    assert data["unsynced_purchases"]["pending_count"] == 2
