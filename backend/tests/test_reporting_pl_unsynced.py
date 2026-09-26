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
        conn.execute("DELETE FROM reporting_vouchers WHERE tally_guid LIKE 'guid-pl-%'")
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
        conn.execute("DELETE FROM reporting_vouchers WHERE tally_guid LIKE 'guid-pl-%'")
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


# --- SYNCED-but-not-yet-reporting-synced: the same up-to-30-minute gap
# already fixed for Sales/Purchases/Creditors/Daybook. P&L is a pure
# aggregate (nothing to drill into), so it uses the simpler permanent
# voucher-type exclusion pattern instead of per-voucher dedup -- once SYNCED,
# the confirmed side never counts these voucher types again, so there's
# nothing to double-count once the reporting sync catches up.

def _insert_confirmed_sale(amount, date, store, guid="guid-pl-sale-test"):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO reporting_vouchers (tally_guid, date, voucher_number, voucher_type, party_ledger_name, narration)
            VALUES (?, ?, '900', 'Sales', 'Cash', '')
        """, (guid, date))
        vid = cursor.lastrowid
        cursor.executemany("INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive) VALUES (?,?,?,?)", [
            (vid, "Cash", -amount, 1),
            (vid, "Sales", amount, 0),
        ])
        if store:
            cursor.execute("""
                INSERT INTO reporting_cost_centre_allocations (ledger_entry_id, cost_centre_name, amount)
                SELECT id, ?, amount FROM reporting_ledger_entries WHERE voucher_id = ? AND ledger_name = 'Sales'
            """, (store, vid))
        conn.commit()


def _insert_confirmed_purchase(amount, date, store, guid="guid-pl-purchase-test"):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO reporting_vouchers (tally_guid, date, voucher_number, voucher_type, party_ledger_name, narration, reference)
            VALUES (?, ?, '901', 'Purchase', 'PLTest Supplier', '', 'INV-1')
        """, (guid, date))
        vid = cursor.lastrowid
        cursor.executemany("INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive) VALUES (?,?,?,?)", [
            (vid, "PLTest Supplier", -amount, 0),
            (vid, "Purchase", amount, 1),
        ])
        if store:
            cursor.execute("""
                INSERT INTO reporting_cost_centre_allocations (ledger_entry_id, cost_centre_name, amount)
                SELECT id, ?, amount FROM reporting_ledger_entries WHERE voucher_id = ? AND ledger_name = 'Purchase'
            """, (store, vid))
        conn.commit()


def _insert_confirmed_payment(debit_ledger, amount, date, store, guid="guid-pl-payment-test"):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO reporting_vouchers (tally_guid, date, voucher_number, voucher_type, party_ledger_name, narration)
            VALUES (?, ?, '902', 'Payment', ?, '')
        """, (guid, date, debit_ledger))
        vid = cursor.lastrowid
        cursor.executemany("INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive) VALUES (?,?,?,?)", [
            (vid, debit_ledger, amount, 1),
            (vid, "Cash", -amount, 0),
        ])
        if store:
            cursor.execute("""
                INSERT INTO reporting_cost_centre_allocations (ledger_entry_id, cost_centre_name, amount)
                SELECT id, ?, amount FROM reporting_ledger_entries WHERE voucher_id = ? AND ledger_name = ?
            """, (store, vid, debit_ledger))
        conn.commit()


def test_synced_sale_not_yet_reporting_synced_still_shows_in_net_sales():
    _queue_sale(1000.0, "20260115", status="SYNCED")
    result = _pl("Combined", "20260101", "20260131")
    assert result["revenue"]["net_sales"] == 1000.0


def test_synced_sale_not_double_counted_once_reporting_synced():
    _queue_sale(1000.0, "20260115", cost_center="Mahagun", status="SYNCED")
    _insert_confirmed_sale(1000.0, "20260115", "Mahagun")

    result = _pl("Mahagun", "20260101", "20260131")
    assert result["revenue"]["net_sales"] == 1000.0, "confirmed voucher now covers this, queue row must not also count"


def test_synced_purchase_not_double_counted_once_reporting_synced():
    _queue_purchase(500.0, "20260115", cost_center="Mahagun", status="SYNCED")
    _insert_confirmed_purchase(500.0, "20260115", "Mahagun")

    result = _pl("Mahagun", "20260101", "20260131")
    assert result["cost_of_goods_sold"]["net_purchases"] == 500.0


def test_synced_payment_to_direct_expense_not_double_counted_once_reporting_synced():
    _queue_payment("PLTest Rent", 300.0, "20260115", cost_center="Mahagun", status="SYNCED")
    _insert_confirmed_payment("PLTest Rent", 300.0, "20260115", "Mahagun")

    result = _pl("Mahagun", "20260101", "20260131")
    assert result["expenses"]["direct_expenses"] == 300.0
    breakdown = {i["ledger_name"]: i["amount"] for i in result["expenses"]["direct_expenses_items"]}
    assert breakdown["PLTest Rent"] == 300.0


def test_synced_payment_to_non_expense_ledger_still_excluded():
    # A Payment voucher permanently excluded from Direct/Indirect Expenses
    # must not leak into those groups just because it's SYNCED and confirmed
    # -- it was never counted there to begin with (settling a supplier is a
    # balance-sheet movement), and the exclusion is scoped per ledger-group
    # query, not a blanket "ignore all Payment vouchers everywhere".
    _queue_payment("PLTest Supplier", 5000.0, "20260115", status="SYNCED")
    _insert_confirmed_payment("PLTest Supplier", 5000.0, "20260115", None)

    result = _pl("Combined", "20260101", "20260131")
    assert result["expenses"]["direct_expenses"] == 0.0
    assert result["expenses"]["indirect_expenses"] == 0.0


def test_synced_stock_transfer_not_double_counted_once_reporting_synced():
    _queue_stock_transfer(2000.0, "20260115", from_store="Mahagun", to_store="Gulshan", status="SYNCED")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO reporting_vouchers (tally_guid, date, voucher_number, voucher_type, party_ledger_name, narration)
            VALUES ('guid-pl-st-test', '20260115', '903', 'Journal', NULL, 'Inter-store transfer: 5.0 pcs of Item A')
        """)
        vid = cursor.lastrowid
        # Both legs share the exact same ledger_name ("inter store transfer",
        # per the real posted XML) -- inserted one at a time so each entry's
        # own lastrowid can be tied to its own cost-centre allocation,
        # since a WHERE ledger_name=... lookup can't tell the two apart.
        cursor.execute("INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive) VALUES (?, 'inter store transfer', 2000.0, 1)", (vid,))
        entry_id = cursor.lastrowid
        cursor.execute("INSERT INTO reporting_cost_centre_allocations (ledger_entry_id, cost_centre_name, amount) VALUES (?, 'Gulshan', 2000.0)", (entry_id,))
        cursor.execute("INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive) VALUES (?, 'inter store transfer', -2000.0, 0)", (vid,))
        entry_id = cursor.lastrowid
        cursor.execute("INSERT INTO reporting_cost_centre_allocations (ledger_entry_id, cost_centre_name, amount) VALUES (?, 'Mahagun', -2000.0)", (entry_id,))
        conn.commit()

    result_gulshan = _pl("Gulshan", "20260101", "20260131")
    assert result_gulshan["cost_of_goods_sold"]["net_purchases"] == 2000.0
    result_mahagun = _pl("Mahagun", "20260101", "20260131")
    assert result_mahagun["cost_of_goods_sold"]["net_purchases"] == -2000.0


# --- One store's stock data missing must degrade gracefully, not take the
# whole report down. fetch_stock_balances_from_db used to raise the instant
# ANY required ledger was missing for either boundary month, which the
# router's own except-Exception turned into a hard 400 for the ENTIRE
# request -- even though calculate_pl_for_store already has its own
# per-store "missing_stores"/None-cogs fallback, and the frontend already
# has a ready-built per-store banner for exactly this, neither of which ever
# got a chance to run.

def test_fetch_stock_balances_omits_only_the_missing_store():
    from backend.services.tally_group_stock import fetch_stock_balances_from_db
    with get_db() as conn:
        conn.execute("DELETE FROM reporting_monthly_stock WHERE year_month = '2026-01' AND ledger_name = 'stock gulshan'")
        conn.commit()

    bals = fetch_stock_balances_from_db("2026-01", "2026-01")
    assert "mahagun" in bals
    assert "vvip" in bals
    assert "gulshan" not in bals, "missing ledger must be omitted, not raise and blank out everything"


def test_endpoint_degrades_gracefully_when_one_store_stock_is_missing():
    _queue_sale(1000.0, "20260115", cost_center="Mahagun")
    with get_db() as conn:
        conn.execute("DELETE FROM reporting_monthly_stock WHERE year_month = '2026-01' AND ledger_name = 'stock gulshan'")
        conn.commit()

    resp = client.get("/api/reporting/profit-loss", params={"start_month": "2026-01", "end_month": "2026-01"})
    assert resp.status_code == 200, "one store's missing stock data must not 400 the entire report"
    data = resp.json()

    mahagun = next(s for s in data["stores"] if s["store"] == "Mahagun")
    assert mahagun["missing_stores"] == []
    assert mahagun["revenue"]["net_sales"] == 1000.0, "Mahagun's own figures are fully available and must still show"
    assert mahagun["gross_profit"] is not None

    gulshan = next(s for s in data["stores"] if s["store"] == "Gulshan")
    assert gulshan["missing_stores"] == ["Gulshan"]
    assert gulshan["cost_of_goods_sold"]["cogs"] is None
    assert gulshan["gross_profit"] is None
    assert gulshan["net_profit"] is None

    combined = data["combined"]
    assert "Gulshan" in combined["missing_stores"]
    assert combined["gross_profit"] is None, "Combined can't compute COGS without every store's stock"
    assert combined["revenue"]["net_sales"] == 1000.0, "revenue itself doesn't depend on stock and must still total correctly"
