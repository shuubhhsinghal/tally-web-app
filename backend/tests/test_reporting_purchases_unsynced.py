import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db, queue_operation, update_queue_status, set_delivery_uncertain
from backend.services.reporting_purchase_service import get_unsynced_purchases, get_queue_purchases_trend, calculate_purchases, get_purchase_bills, get_supplier_purchase_analysis

client = TestClient(app)


_SEEDED_VOUCHER_GUID_PREFIX = "guid-V-"


def _cleanup_seeded_vouchers(conn):
    # Only cleans up rows this file's own _seed_confirmed_purchase creates
    # (matched by its distinctive tally_guid prefix), so it can't clobber
    # confirmed-voucher data left behind by other test files sharing the same
    # session-scoped test database.
    conn.execute(
        "DELETE FROM reporting_ledger_entries WHERE voucher_id IN (SELECT id FROM reporting_vouchers WHERE tally_guid LIKE ?)",
        (f"{_SEEDED_VOUCHER_GUID_PREFIX}%",)
    )
    conn.execute("DELETE FROM reporting_vouchers WHERE tally_guid LIKE ?", (f"{_SEEDED_VOUCHER_GUID_PREFIX}%",))


@pytest.fixture(autouse=True)
def setup_db():
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        _cleanup_seeded_vouchers(conn)
        conn.commit()
    yield
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        _cleanup_seeded_vouchers(conn)
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

    result = get_queue_purchases_trend("20260101", "20260131")
    assert len(result) == 1
    assert result[0]["date"] == "20260115"
    assert result[0]["Mahagun"] == 700.0
    assert result[0]["Combined"] == 700.0


def test_pending_purchases_trend_excludes_delivery_uncertain():
    qid = _queue_accounting_purchase(1000.0, "20260115")
    set_delivery_uncertain(qid, True)
    result = get_queue_purchases_trend("20260101", "20260131")
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


# --- get_purchase_bills / get_supplier_purchase_analysis: the summary total
# already includes pending purchases/returns (tests above), but these
# drill-down lists didn't -- so "Total Purchases: X" could not reconcile with
# what the bills/supplier lists actually showed. These tests cover the fix.

from backend.services.reporting_purchase_service import get_purchase_bills, get_supplier_purchase_analysis


def _seed_confirmed_purchase(supplier, amount, date, voucher_number="V-1"):
    # Represents a purchase that went through this app and has since been
    # confirmed by Tally -- which now means it has BOTH a SYNCED queue row
    # (the summary total's actual source for a VCHTYPE=Purchase voucher,
    # see _PURCHASE_QUEUE_OWNED_VOUCHER_EXCLUSION_SQL) and a real
    # reporting_vouchers row (Bills'/Supplier-analysis' source, which still
    # reads confirmed data directly and isn't affected by that exclusion).
    payload = {"supplier": supplier, "invoice_number": voucher_number, "amount": amount, "tally_date": date, "cost_center": None, "narration": ""}
    qid = queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Purchase Invoice: {voucher_number} from {supplier}")
    update_queue_status(qid, "SYNCED")

    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO ledgers (name, parent) VALUES (?, 'Purchase Accounts')", ("Purchases",))
        cur = conn.execute(
            "INSERT INTO reporting_vouchers (tally_guid, date, voucher_number, voucher_type, party_ledger_name) VALUES (?, ?, ?, 'Purchase', ?)",
            (f"guid-{voucher_number}-{date}", date, voucher_number, supplier)
        )
        voucher_id = cur.lastrowid
        conn.execute(
            "INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive) VALUES (?, 'Purchases', ?, 1)",
            (voucher_id, -amount)
        )
        conn.commit()


def test_purchase_bills_includes_pending_purchase():
    _queue_accounting_purchase(1000.0, "20260115")
    result = get_purchase_bills("20260101", "20260131")
    assert result["total_count"] == 1
    assert result["bills"][0]["is_pending"] is True
    assert result["bills"][0]["net_purchases"] == 1000.0


def test_purchase_bills_excludes_failed():
    _queue_accounting_purchase(1000.0, "20260115", status="FAILED")
    result = get_purchase_bills("20260101", "20260131")
    assert result["total_count"] == 0


def test_purchase_bills_excludes_delivery_uncertain():
    qid = _queue_accounting_purchase(1000.0, "20260115")
    set_delivery_uncertain(qid, True)
    result = get_purchase_bills("20260101", "20260131")
    assert result["total_count"] == 0


def test_purchase_bills_filtered_by_supplier_name():
    _queue_accounting_purchase(1000.0, "20260115")  # supplier: "Test Supplier"
    result_match = get_purchase_bills("20260101", "20260131", supplier_name="Test Supplier")
    assert result_match["total_count"] == 1
    result_other = get_purchase_bills("20260101", "20260131", supplier_name="Someone Else")
    assert result_other["total_count"] == 0


def test_purchase_bills_reconciles_with_summary_total():
    _seed_confirmed_purchase("Confirmed Supplier", 5000.0, "20260110")
    _queue_accounting_purchase(1000.0, "20260115")
    _queue_debit_note([{"name": "Item A", "qty": 1, "uom": "PCS", "rate": 200.0, "amount": 200.0}], "20260116")

    summary_resp = client.get("/api/reporting/purchases", params={"start_date": "20260101", "end_date": "20260131"})
    bills = get_purchase_bills("20260101", "20260131", limit=100)

    bills_sum = round(sum(b["net_purchases"] for b in bills["bills"]), 2)
    assert bills_sum == summary_resp.json()["summary"]["net_purchases"]


def test_supplier_analysis_includes_pending_only_supplier():
    _queue_accounting_purchase(750.0, "20260115")  # supplier: "Test Supplier", no confirmed voucher exists
    result = get_supplier_purchase_analysis("20260101", "20260131")
    names = {s["supplier_name"]: s for s in result["suppliers"]}
    assert "Test Supplier" in names
    assert names["Test Supplier"]["net_purchases"] == 750.0
    assert names["Test Supplier"]["vouchers_count"] == 1


def test_supplier_analysis_merges_pending_into_existing_confirmed_supplier():
    _seed_confirmed_purchase("Test Supplier", 5000.0, "20260110")
    _queue_accounting_purchase(1000.0, "20260115")  # same supplier: "Test Supplier"
    result = get_supplier_purchase_analysis("20260101", "20260131")
    names = {s["supplier_name"]: s for s in result["suppliers"]}
    assert names["Test Supplier"]["net_purchases"] == 6000.0
    assert names["Test Supplier"]["vouchers_count"] == 2


# --- Stock Transfer's accounting leg: posts a Journal against the "inter
# store transfer" ledger (Purchase Accounts parent, cost-centre allocated) --
# receiving store's purchases increase, sending store's decrease. The
# confirmed side already includes this via plain ledger-group summation
# (no supplier restriction); these tests cover the matching pending-side fix.

def _queue_stock_transfer(total_amount, tally_date, from_store="Mahagun", to_store="Gulshan", leg="Accounting", status=None):
    payload = {
        "item_name": "Item A", "qty": 5.0, "rate": total_amount / 5.0, "total_amount": total_amount,
        "from_store": from_store, "to_store": to_store, "tally_date": tally_date,
    }
    qid = queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Stock Transfer: 5.0 pcs of Item A ({leg})")
    if status:
        update_queue_status(qid, status)
    return qid


def test_pending_stock_transfer_increases_receiver_decreases_sender():
    _queue_stock_transfer(2000.0, "20260115", from_store="Mahagun", to_store="Gulshan")
    result_gulshan = get_queue_purchases_trend("20260101", "20260131", cost_centre="Gulshan")
    assert result_gulshan[0]["Combined"] == 2000.0
    result_mahagun = get_queue_purchases_trend("20260101", "20260131", cost_centre="Mahagun")
    assert result_mahagun[0]["Combined"] == -2000.0


def test_pending_stock_transfer_nets_to_zero_combined():
    _queue_stock_transfer(2000.0, "20260115")
    result = get_queue_purchases_trend("20260101", "20260131")
    assert result[0]["Combined"] == 0.0


def test_stock_transfer_physical_leg_ignored():
    _queue_stock_transfer(2000.0, "20260115", leg="Physical")
    result = get_queue_purchases_trend("20260101", "20260131")
    assert result == []


def test_pending_stock_transfer_excludes_delivery_uncertain():
    qid = _queue_stock_transfer(2000.0, "20260115")
    set_delivery_uncertain(qid, True)
    result = get_queue_purchases_trend("20260101", "20260131")
    assert result == []


def test_stock_transfer_not_in_bills_or_supplier_analysis():
    # Not a supplier invoice -- must stay out of these supplier-invoice-
    # specific drill-downs even though it counts toward the summary total.
    _queue_stock_transfer(2000.0, "20260115", to_store="Gulshan")
    bills = get_purchase_bills("20260101", "20260131", cost_centre="Gulshan")
    assert bills["total_count"] == 0
    suppliers = get_supplier_purchase_analysis("20260101", "20260131", cost_centre="Gulshan")
    assert suppliers["suppliers"] == []


def test_purchases_endpoint_includes_pending_stock_transfer():
    _queue_stock_transfer(2000.0, "20260115", from_store="Mahagun", to_store="Gulshan")
    resp = client.get("/api/reporting/purchases", params={"start_date": "20260101", "end_date": "20260131", "cost_centre": "Gulshan"})
    assert resp.status_code == 200
    assert resp.json()["summary"]["net_purchases"] == 2000.0


# --- A SYNCED (already live in Tally) purchase must not vanish while
# waiting for the separate reporting sync to re-confirm it, and must not be
# double counted once that sync does eventually catch up. ---

def test_synced_purchase_is_included_in_queue_purchases_trend():
    # The exact gap this guards against: before this fix, only PENDING rows
    # were counted here, so a purchase that posted live and immediately
    # flipped to SYNCED disappeared from every report total until the next
    # ~30-minute reporting sync.
    qid = _queue_accounting_purchase(500.0, "20260115")
    update_queue_status(qid, "SYNCED")

    result = get_queue_purchases_trend("20260101", "20260131")
    assert result == [{"date": "20260115", "Combined": 500.0, "Combined_invoices": 1, "Mahagun": 500.0}]


def test_synced_purchase_is_not_double_counted_once_reporting_sync_catches_up():
    # Once the separate reporting sync eventually re-pulls the same Purchase
    # voucher from Tally into reporting_ledger_entries, calculate_purchases
    # must exclude it (it's a VCHTYPE=Purchase voucher) so the queue-sourced
    # count above is the only place it's ever counted -- never both.
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO ledgers (name, parent, cost_centre) VALUES ('Purchases', 'Purchase Accounts', 0)")
        conn.commit()

    qid = _queue_accounting_purchase(500.0, "20260115")
    update_queue_status(qid, "SYNCED")

    with get_db() as conn:
        conn.execute("""
            INSERT INTO reporting_vouchers (tally_guid, date, voucher_type, party_ledger_name)
            VALUES ('guid-V-purch-1', '20260115', 'Purchase', 'Test Supplier')
        """)
        voucher_id = conn.execute("SELECT id FROM reporting_vouchers WHERE tally_guid = 'guid-V-purch-1'").fetchone()[0]
        conn.execute(
            "INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive) VALUES (?, ?, ?, ?)",
            (voucher_id, "Purchases", -500.0, 1),
        )
        conn.commit()

    assert calculate_purchases("20260101", "20260131") == 0.0  # excluded -- still only counted via the queue

    resp = client.get("/api/reporting/purchases", params={"start_date": "20260101", "end_date": "20260131"})
    data = resp.json()
    assert data["summary"]["net_purchases"] == 500.0  # not 1000.0


def test_synced_purchase_still_shown_in_bills_and_supplier_analysis_from_confirmed_data():
    # Bills/Supplier-analysis deliberately were NOT switched to the
    # permanent-queue-exclusion model (see
    # _PURCHASE_QUEUE_OWNED_VOUCHER_EXCLUSION_SQL's docstring): they still
    # read a Purchase/Debit Note straight from confirmed data once it's
    # really there, since that's the only place GST/item-level bill detail
    # exists. This just confirms that path still works after the summary's
    # exclusion was added elsewhere.
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO ledgers (name, parent, cost_centre) VALUES ('Purchases', 'Purchase Accounts', 0)")
        conn.execute("""
            INSERT INTO reporting_vouchers (tally_guid, date, voucher_number, voucher_type, party_ledger_name)
            VALUES ('guid-V-purch-2', '20260116', 'INV-9', 'Purchase', 'Test Supplier')
        """)
        voucher_id = conn.execute("SELECT id FROM reporting_vouchers WHERE tally_guid = 'guid-V-purch-2'").fetchone()[0]
        conn.execute(
            "INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive) VALUES (?, ?, ?, ?)",
            (voucher_id, "Purchases", -700.0, 1),
        )
        conn.commit()

    bills = get_purchase_bills("20260101", "20260131")
    assert any(b["voucher_number"] == "INV-9" and b["net_purchases"] == 700.0 for b in bills["bills"])

    suppliers = get_supplier_purchase_analysis("20260101", "20260131")
    names = {s["supplier_name"]: s for s in suppliers["suppliers"]}
    assert names["Test Supplier"]["net_purchases"] == 700.0
