import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db, queue_operation, update_queue_status, set_delivery_uncertain
from backend.routers.reporting_pl import calculate_pl_for_store, _fetch_pending_pl_movements

client = TestClient(app)

_TEST_LEDGERS = [
    ("PLTest Rent", "Direct Expenses"),
    ("PLTest Electricity", "Indirect Expenses"),
    ("PLTest Supplier", "Sundry Creditors"),
]


@pytest.fixture(autouse=True)
def setup_db():
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        for name, parent in _TEST_LEDGERS:
            conn.execute("INSERT OR REPLACE INTO ledgers (name, parent) VALUES (?, ?)", (name, parent))
        # The full endpoint (fetch_stock_balances_from_db) requires a stock
        # snapshot to exist for the requested months -- unrelated to the
        # pending-queue folding this file tests, just a precondition for the
        # one full-endpoint integration test below.
        for store in ("mahagun", "gulshan", "vvip"):
            conn.execute(
                "INSERT OR REPLACE INTO reporting_monthly_stock (year_month, ledger_name, opening_balance, debit_movement, credit_movement, closing_balance) VALUES (?, ?, 0, 0, 0, 0)",
                ("2026-01", f"stock {store}")
            )
        conn.commit()
    yield
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        for name, _ in _TEST_LEDGERS:
            conn.execute("DELETE FROM ledgers WHERE name = ?", (name,))
        conn.execute("DELETE FROM reporting_monthly_stock WHERE year_month = ?", ("2026-01",))
        conn.commit()


def _queue_sale(amount, tally_date, cost_center=None, status=None):
    payload = {"ledger": "Cash", "amount": amount, "tally_date": tally_date, "cost_center": cost_center, "narration": ""}
    qid = queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Sales: {amount} from Cash")
    if status:
        update_queue_status(qid, status)
    return qid


def _queue_purchase(amount, tally_date, cost_center=None, status=None):
    payload = {"supplier": "PLTest Supplier", "invoice_number": "INV-1", "amount": amount, "tally_date": tally_date, "cost_center": cost_center, "narration": ""}
    qid = queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Purchase Invoice: {payload['invoice_number']} from {payload['supplier']}")
    if status:
        update_queue_status(qid, status)
    return qid


def _queue_debit_note(adjustment_items, tally_date, adjustment_store=None, status=None):
    payload = {
        "supplier": "PLTest Supplier", "invoice_number": "INV-2", "tally_date": tally_date,
        "cost_center": adjustment_store, "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "rounding_off": 0.0,
        "items": [{"name": "Item A", "qty": 1, "uom": "PCS", "rate": 100.0, "amount": 100.0}],
        "adjustment": {"store": adjustment_store, "items": adjustment_items},
    }
    qid = queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Purchase Return (Debit Note): {payload['invoice_number']} from {payload['supplier']}")
    if status:
        update_queue_status(qid, status)
    return qid


def _queue_payment(debit_ledger, amount, tally_date, cost_center=None, status=None):
    payload = {"debit_ledger": debit_ledger, "credit_ledger": "Cash", "amount": amount, "tally_date": tally_date, "cost_center": cost_center}
    qid = queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Payment: {amount} to {debit_ledger}")
    if status:
        update_queue_status(qid, status)
    return qid


def _pl(store, start_date, end_date):
    with get_db() as db:
        pending = _fetch_pending_pl_movements(start_date, end_date, db)
        return calculate_pl_for_store(store, start_date, end_date, {}, db, pending)


def test_pending_sale_increases_net_sales():
    _queue_sale(1000.0, "20260115")
    result = _pl("Combined", "20260101", "20260131")
    assert result["revenue"]["net_sales"] == 1000.0


def test_pending_purchase_increases_net_purchases():
    _queue_purchase(500.0, "20260115")
    result = _pl("Combined", "20260101", "20260131")
    assert result["cost_of_goods_sold"]["net_purchases"] == 500.0


def test_pending_purchase_return_decreases_net_purchases():
    _queue_purchase(500.0, "20260115")
    _queue_debit_note([{"name": "Item A", "qty": 1, "uom": "PCS", "rate": 200.0, "amount": 200.0}], "20260116")
    result = _pl("Combined", "20260101", "20260131")
    assert result["cost_of_goods_sold"]["net_purchases"] == 300.0  # 500 - 200


def test_pending_payment_to_direct_expense_ledger_counted():
    _queue_payment("PLTest Rent", 300.0, "20260115")
    result = _pl("Combined", "20260101", "20260131")
    assert result["expenses"]["direct_expenses"] == 300.0
    assert result["expenses"]["indirect_expenses"] == 0.0


def test_pending_payment_to_indirect_expense_ledger_counted():
    _queue_payment("PLTest Electricity", 150.0, "20260115")
    result = _pl("Combined", "20260101", "20260131")
    assert result["expenses"]["indirect_expenses"] == 150.0
    assert result["expenses"]["direct_expenses"] == 0.0


def test_pending_payment_to_non_expense_ledger_excluded():
    # Paying off a supplier is a balance-sheet movement (see Creditors
    # reporting), not a P&L expense -- must not show up here at all.
    _queue_payment("PLTest Supplier", 5000.0, "20260115")
    result = _pl("Combined", "20260101", "20260131")
    assert result["expenses"]["direct_expenses"] == 0.0
    assert result["expenses"]["indirect_expenses"] == 0.0


def test_failed_entry_excluded():
    _queue_sale(1000.0, "20260115", status="FAILED")
    result = _pl("Combined", "20260101", "20260131")
    assert result["revenue"]["net_sales"] == 0.0


def test_delivery_uncertain_excluded():
    qid = _queue_sale(1000.0, "20260115")
    set_delivery_uncertain(qid, True)
    result = _pl("Combined", "20260101", "20260131")
    assert result["revenue"]["net_sales"] == 0.0


def test_pending_entry_filtered_by_store():
    _queue_sale(1000.0, "20260115", cost_center="Gulshan")
    result_mahagun = _pl("Mahagun", "20260101", "20260131")
    assert result_mahagun["revenue"]["net_sales"] == 0.0
    result_gulshan = _pl("Gulshan", "20260101", "20260131")
    assert result_gulshan["revenue"]["net_sales"] == 1000.0
    result_combined = _pl("Combined", "20260101", "20260131")
    assert result_combined["revenue"]["net_sales"] == 1000.0


def test_pending_entry_outside_date_range_excluded():
    _queue_sale(1000.0, "20260215")
    result = _pl("Combined", "20260101", "20260131")
    assert result["revenue"]["net_sales"] == 0.0


def test_endpoint_reflects_pending_sale():
    _queue_sale(1000.0, "20260115")
    resp = client.get("/api/reporting/profit-loss", params={"start_month": "2026-01", "end_month": "2026-01"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["combined"]["revenue"]["net_sales"] == 1000.0


# --- Stock Transfer's accounting leg: also affects P&L purchases (receiving
# store up, sending store down), same as it already affects the confirmed
# side once synced via plain ledger-group summation.

def _queue_stock_transfer(total_amount, tally_date, from_store="Mahagun", to_store="Gulshan", leg="Accounting", status=None):
    payload = {
        "item_name": "Item A", "qty": 5.0, "rate": total_amount / 5.0, "total_amount": total_amount,
        "from_store": from_store, "to_store": to_store, "tally_date": tally_date,
    }
    qid = queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Stock Transfer: 5.0 pcs of Item A ({leg})")
    if status:
        update_queue_status(qid, status)
    return qid


def test_pending_stock_transfer_moves_pl_purchases_both_sides():
    _queue_stock_transfer(2000.0, "20260115", from_store="Mahagun", to_store="Gulshan")
    result_gulshan = _pl("Gulshan", "20260101", "20260131")
    assert result_gulshan["cost_of_goods_sold"]["net_purchases"] == 2000.0
    result_mahagun = _pl("Mahagun", "20260101", "20260131")
    assert result_mahagun["cost_of_goods_sold"]["net_purchases"] == -2000.0
    result_combined = _pl("Combined", "20260101", "20260131")
    assert result_combined["cost_of_goods_sold"]["net_purchases"] == 0.0


def test_stock_transfer_physical_leg_ignored_in_pl():
    _queue_stock_transfer(2000.0, "20260115", leg="Physical")
    result = _pl("Combined", "20260101", "20260131")
    assert result["cost_of_goods_sold"]["net_purchases"] == 0.0
