import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db, queue_operation, set_delivery_uncertain
from backend.services.reporting_creditors_service import get_creditor_ledger_movements, get_all_creditors_overview

client = TestClient(app)

SUPPLIER = "Pending Supplier Co"


@pytest.fixture(autouse=True)
def setup_db():
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        conn.execute("DELETE FROM reporting_ledger_entries")
        conn.execute("DELETE FROM reporting_vouchers")
        conn.execute("DELETE FROM ledgers WHERE name = ?", (SUPPLIER,))
        conn.execute("INSERT INTO ledgers (name, parent, opening_balance) VALUES (?, 'Sundry Creditors', 0.0)", (SUPPLIER,))
        conn.commit()
    yield
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        conn.execute("DELETE FROM ledgers WHERE name = ?", (SUPPLIER,))
        conn.commit()


def _queue_purchase(amount, tally_date, supplier=SUPPLIER, status=None):
    payload = {"supplier": supplier, "invoice_number": "INV-1", "amount": amount, "tally_date": tally_date, "cost_center": "Mahagun", "narration": ""}
    qid = queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Purchase Invoice: {payload['invoice_number']} from {supplier}")
    if status:
        from backend.database import update_queue_status
        update_queue_status(qid, status)
    return qid


def _queue_payment(amount, tally_date, debit_ledger=SUPPLIER):
    payload = {"debit_ledger": debit_ledger, "credit_ledger": "Cash Mahagun", "amount": amount, "tally_date": tally_date, "narration": "", "cost_center": "Mahagun"}
    return queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Payment: {amount} to {debit_ledger}")


def _queue_return(adjustment_items, tally_date, supplier=SUPPLIER):
    payload = {
        "supplier": supplier, "invoice_number": "INV-2", "tally_date": tally_date, "cost_center": "Mahagun",
        "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "rounding_off": 0.0,
        "items": [{"name": "Item A", "qty": 1, "uom": "PCS", "rate": 100.0, "amount": 100.0}],
        "adjustment": {"store": "Mahagun", "items": adjustment_items},
    }
    return queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Purchase Return (Debit Note): {payload['invoice_number']} from {supplier}")


def test_pending_purchase_increases_closing_balance():
    _queue_purchase(1000.0, "20260115")
    result = get_creditor_ledger_movements(SUPPLIER, "20260101", "20260131")
    assert result["pending_amount"] == 1000.0
    assert result["period_closing_confirmed"] == 0.0
    assert result["period_closing"] == 1000.0
    assert result["summary"]["purchases"] == 1000.0


def test_pending_payment_decreases_closing_balance():
    _queue_purchase(1000.0, "20260115")
    _queue_payment(400.0, "20260116")
    result = get_creditor_ledger_movements(SUPPLIER, "20260101", "20260131")
    assert result["pending_amount"] == 600.0
    assert result["period_closing"] == 600.0
    assert result["summary"]["payments"] == 400.0


def test_pending_return_decreases_closing_balance():
    _queue_purchase(1000.0, "20260115")
    _queue_return([{"name": "Item A", "qty": 2, "uom": "PCS", "rate": 50.0, "amount": 100.0}], "20260116")
    result = get_creditor_ledger_movements(SUPPLIER, "20260101", "20260131")
    assert result["pending_amount"] == 900.0  # 1000 - 100
    assert result["summary"]["returns"] == 100.0


def test_pending_movements_appear_in_movements_list_marked_pending():
    _queue_purchase(1000.0, "20260115")
    result = get_creditor_ledger_movements(SUPPLIER, "20260101", "20260131")
    assert len(result["movements"]) == 1
    assert result["movements"][0]["is_pending"] is True
    assert result["movements"][0]["voucher_id"] is None


def test_delivery_uncertain_purchase_excluded():
    qid = _queue_purchase(1000.0, "20260115")
    set_delivery_uncertain(qid, True)
    result = get_creditor_ledger_movements(SUPPLIER, "20260101", "20260131")
    assert result["pending_amount"] == 0.0
    assert result["movements"] == []


def test_other_supplier_not_affected():
    _queue_purchase(1000.0, "20260115", supplier="A Totally Different Supplier")
    result = get_creditor_ledger_movements(SUPPLIER, "20260101", "20260131")
    assert result["pending_amount"] == 0.0


def test_overview_includes_pending_for_correct_supplier():
    _queue_purchase(1000.0, "20260115")
    overview = get_all_creditors_overview("20260101", "20260131")
    row = next(r for r in overview if r["supplier_name"] == SUPPLIER)
    assert row["pending_amount"] == 1000.0
    assert row["period_closing"] == 1000.0
    assert row["period_closing_confirmed"] == 0.0


def test_pending_purchase_before_period_included_in_opening_balance():
    # Dated in June, report requested for July -- this purchase hasn't
    # synced to Tally yet, so it must still be rolled into period_opening,
    # not silently dropped just because it's outside the requested window.
    _queue_purchase(1000.0, "20260615")
    result = get_creditor_ledger_movements(SUPPLIER, "20260701", "20260731")
    assert result["period_opening_confirmed"] == 0.0
    assert result["pending_opening_amount"] == 1000.0
    assert result["period_opening"] == 1000.0
    assert result["movements"] == []  # not inside the requested period itself


def test_pending_payment_before_period_reduces_opening_balance():
    _queue_payment(400.0, "20260620")
    result = get_creditor_ledger_movements(SUPPLIER, "20260701", "20260731")
    assert result["pending_opening_amount"] == -400.0
    assert result["period_opening"] == -400.0


def test_delivery_uncertain_before_period_excluded_from_opening_balance():
    qid = _queue_purchase(1000.0, "20260615")
    set_delivery_uncertain(qid, True)
    result = get_creditor_ledger_movements(SUPPLIER, "20260701", "20260731")
    assert result["pending_opening_amount"] == 0.0
    assert result["period_opening"] == 0.0


def test_pending_before_period_not_double_counted_with_period_pending():
    # One pending purchase before the period, one inside it -- each must be
    # counted exactly once (opening vs. in-period movements), never both.
    _queue_purchase(1000.0, "20260615")
    _queue_purchase(500.0, "20260715")
    result = get_creditor_ledger_movements(SUPPLIER, "20260701", "20260731")
    assert result["pending_opening_amount"] == 1000.0
    assert result["period_opening"] == 1000.0
    assert result["pending_amount"] == 500.0
    assert result["period_closing"] == 1500.0
    assert len(result["movements"]) == 1


def test_overview_includes_pending_opening_amount():
    _queue_purchase(1000.0, "20260615")
    overview = get_all_creditors_overview("20260701", "20260731")
    row = next(r for r in overview if r["supplier_name"] == SUPPLIER)
    assert row["pending_opening_amount"] == 1000.0
    assert row["period_opening"] == 1000.0


def test_creditors_endpoint_shape_and_completeness_flag():
    resp = client.get("/api/reporting/creditors", params={"start_date": "20260101", "end_date": "20260131"})
    assert resp.status_code == 200
    data = resp.json()
    assert "is_data_complete" in data
    assert "creditors" in data


def test_creditor_detail_endpoint_includes_completeness_flag():
    resp = client.get(f"/api/reporting/creditors/{SUPPLIER}", params={"start_date": "20260101", "end_date": "20260131"})
    assert resp.status_code == 200
    data = resp.json()
    assert "is_data_complete" in data
