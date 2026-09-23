import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db, queue_operation, update_queue_status, set_delivery_uncertain
from backend.services.reporting_daybook_service import get_daybook

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        conn.execute("DELETE FROM reporting_vouchers")
        conn.commit()
    yield
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        conn.execute("DELETE FROM reporting_vouchers")
        conn.commit()


def _queue_sale(amount, tally_date, cost_center="Mahagun", status=None):
    payload = {"ledger": "Cash", "amount": amount, "tally_date": tally_date, "cost_center": cost_center, "narration": ""}
    qid = queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Sales: {amount} from Cash")
    if status:
        update_queue_status(qid, status)
    return qid


def _queue_payment(amount, tally_date, cost_center="Mahagun", status=None):
    payload = {"debit_ledger": "Rent", "credit_ledger": "Cash", "amount": amount, "tally_date": tally_date, "cost_center": cost_center}
    qid = queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Payment: {amount} to Rent")
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


def test_pending_sale_appears_in_daybook():
    _queue_sale(1000.0, "20260115")
    result = get_daybook("20260101", "20260131")
    assert result["total"] == 1
    row = result["vouchers"][0]
    assert row["is_pending"] is True
    assert row["voucher_type"] == "Sales"
    assert row["credit"] == 1000.0
    assert row["debit"] == 0.0


def test_pending_payment_appears_as_debit():
    _queue_payment(500.0, "20260115")
    result = get_daybook("20260101", "20260131")
    assert result["total"] == 1
    row = result["vouchers"][0]
    assert row["voucher_type"] == "Payment"
    assert row["credit"] == 500.0
    assert row["debit"] == 0.0


def test_pending_debit_note_appears_with_positive_magnitude():
    _queue_debit_note([{"name": "Item A", "qty": 1, "uom": "PCS", "rate": 300.0, "amount": 300.0}], "20260115")
    result = get_daybook("20260101", "20260131")
    assert result["total"] == 1
    row = result["vouchers"][0]
    assert row["voucher_type"] == "Debit Note"
    assert row["debit"] == 300.0
    assert row["amount"] == 300.0


def test_failed_entry_excluded_from_daybook():
    _queue_sale(1000.0, "20260115", status="FAILED")
    result = get_daybook("20260101", "20260131")
    assert result["total"] == 0


def test_delivery_uncertain_excluded_from_daybook():
    qid = _queue_sale(1000.0, "20260115")
    set_delivery_uncertain(qid, True)
    result = get_daybook("20260101", "20260131")
    assert result["total"] == 0


def test_pending_entry_outside_date_range_excluded():
    _queue_sale(1000.0, "20260215")
    result = get_daybook("20260101", "20260131")
    assert result["total"] == 0


def test_pending_entry_filtered_by_cost_centre():
    _queue_sale(1000.0, "20260115", cost_center="Gulshan")
    result = get_daybook("20260101", "20260131", cost_centre="Mahagun")
    assert result["total"] == 0
    result_gulshan = get_daybook("20260101", "20260131", cost_centre="Gulshan")
    assert result_gulshan["total"] == 1


def test_daybook_endpoint_includes_pending():
    _queue_sale(1000.0, "20260115")
    resp = client.get("/api/reporting/daybook", params={"start_date": "20260101", "end_date": "20260131"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["vouchers"][0]["is_pending"] is True
