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
        conn.execute("DELETE FROM repack_operations WHERE id LIKE 'test-repack-%'")
        conn.commit()
    yield
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        conn.execute("DELETE FROM reporting_vouchers")
        conn.execute("DELETE FROM repack_operations WHERE id LIKE 'test-repack-%'")
        conn.commit()


def _queue_sale(amount, tally_date, cost_center="Mahagun", status=None, ledger="Cash"):
    payload = {"ledger": ledger, "amount": amount, "tally_date": tally_date, "cost_center": cost_center, "narration": ""}
    qid = queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Sales: {amount} from {ledger}")
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


def _queue_purchase(amount, tally_date, supplier="Test Supplier", invoice_number="INV-1", status=None):
    payload = {"supplier": supplier, "invoice_number": invoice_number, "amount": amount, "tally_date": tally_date, "cost_center": "Mahagun", "narration": ""}
    qid = queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Purchase Invoice: {invoice_number} from {supplier}")
    if status:
        update_queue_status(qid, status)
    return qid


def _insert_confirmed_voucher(voucher_type, party, amount, date, voucher_number, reference=None, narration="", guid="guid-daybook-test"):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO reporting_vouchers (tally_guid, date, voucher_number, voucher_type, party_ledger_name, narration, reference)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (guid, date, voucher_number, voucher_type, party, narration, reference))
        voucher_id = cursor.lastrowid
        signed_amount = -amount if voucher_type in ('Sales', 'Payment') else amount
        cursor.execute("""
            INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive)
            VALUES (?, ?, ?, ?)
        """, (voucher_id, party, signed_amount, 1))
        conn.commit()
    return voucher_id


def test_synced_purchase_not_yet_reporting_synced_still_appears():
    # A purchase whose voucher already went through to Tally (status SYNCED)
    # but hasn't been picked up by the periodic reporting sync yet used to be
    # completely invisible in the daybook -- this is the bug fix.
    _queue_purchase(1000.0, "20260115", status="SYNCED", invoice_number="INV-SYNCED-1")
    result = get_daybook("20260101", "20260131")
    assert result["total"] == 1
    assert result["vouchers"][0]["is_pending"] is True


def test_synced_purchase_not_double_counted_once_reporting_synced():
    _queue_purchase(1000.0, "20260115", status="SYNCED", invoice_number="INV-SYNCED-2")
    _insert_confirmed_voucher("Purchase", "Test Supplier", 1000.0, "20260115", "500", reference="INV-SYNCED-2")

    result = get_daybook("20260101", "20260131")
    assert result["total"] == 1, "confirmed voucher now covers this, queue row must not also count"
    assert result["vouchers"][0]["is_pending"] is False
    assert result["vouchers"][0]["voucher_number"] == "500"


def test_synced_debit_note_not_double_counted_once_reporting_synced():
    _queue_debit_note([{"name": "Item A", "qty": 1, "uom": "PCS", "rate": 300.0, "amount": 300.0}], "20260115", status="SYNCED")
    _insert_confirmed_voucher("Debit Note", "Test Supplier", 300.0, "20260115", "DN-1", reference="INV-3")

    result = get_daybook("20260101", "20260131")
    assert result["total"] == 1
    assert result["vouchers"][0]["is_pending"] is False


def test_synced_sale_not_double_counted_once_reporting_synced():
    _queue_sale(1000.0, "20260115", status="SYNCED")
    _insert_confirmed_voucher("Sales", "Cash", 1000.0, "20260115", "S-1")

    result = get_daybook("20260101", "20260131")
    assert result["total"] == 1
    assert result["vouchers"][0]["is_pending"] is False
    assert result["vouchers"][0]["credit"] == 1000.0


def test_synced_payment_not_double_counted_once_reporting_synced():
    _queue_payment(500.0, "20260115", status="SYNCED")
    _insert_confirmed_voucher("Payment", "Rent", 500.0, "20260115", "P-1")

    result = get_daybook("20260101", "20260131")
    assert result["total"] == 1
    assert result["vouchers"][0]["is_pending"] is False


def test_synced_sale_not_double_counted_when_tally_returns_different_casing():
    # Sales' own /metadata endpoint .title()-cases ledgers for display (e.g.
    # a real ledger "cash mahagun" is shown and submitted back as "Cash
    # Mahagun"), and Tally accepts that case-insensitively but its Day Book
    # export returns the ledger's real stored casing -- so the confirmed
    # party_ledger_name can differ in case from the queue payload's own
    # ledger value. Caught live against a real "cash mahagun" ledger; an
    # exact-case dedup match would silently never fire and double count
    # forever once the confirmed voucher appears.
    _queue_sale(1000.0, "20260115", status="SYNCED", ledger="Cash Mahagun")
    _insert_confirmed_voucher("Sales", "cash mahagun", 1000.0, "20260115", "S-2")

    result = get_daybook("20260101", "20260131")
    assert result["total"] == 1
    assert result["vouchers"][0]["is_pending"] is False


def test_synced_purchase_for_different_invoice_number_not_treated_as_duplicate():
    _queue_purchase(500.0, "20260115", supplier="Test Supplier", invoice_number="INV-A", status="SYNCED")
    _insert_confirmed_voucher("Purchase", "Test Supplier", 1000.0, "20260115", "501", reference="INV-B")

    result = get_daybook("20260101", "20260131")
    assert result["total"] == 2


def _queue_stock_transfer_accounting(total_amount, tally_date, qty=5.0, item_name="Test Item", from_store="Mahagun", to_store="Gulshan", status=None):
    payload = {
        # qty is a Python float here on purpose -- matches stock_transfer.py's
        # own pydantic-enforced `qty: float`, which is what actually gets
        # rendered into the posted XML's narration (see the service's own
        # comment on why this reconstruction forces float() too).
        "item_name": item_name, "qty": qty, "rate": total_amount / qty if qty else 0, "total_amount": total_amount,
        "from_store": from_store, "to_store": to_store, "tally_date": tally_date,
    }
    qid = queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Stock Transfer: {qty} pcs of {item_name} (Accounting)")
    if status:
        update_queue_status(qid, status)
    return qid


def _insert_confirmed_inter_store_journal(amount, date, voucher_number, qty, item_name, guid="guid-daybook-st-test"):
    narration = f"Inter-store transfer: {qty} pcs of {item_name}"
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO reporting_vouchers (tally_guid, date, voucher_number, voucher_type, party_ledger_name, narration)
            VALUES (?, ?, ?, 'Journal', NULL, ?)
        """, (guid, date, voucher_number, narration))
        voucher_id = cursor.lastrowid
        cursor.executemany("""
            INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive)
            VALUES (?, ?, ?, ?)
        """, [
            (voucher_id, "inter store transfer", amount, 1),
            (voucher_id, "inter store transfer", -amount, 0),
        ])
        conn.commit()
    return voucher_id


def test_pending_stock_transfer_accounting_leg_appears_in_daybook():
    _queue_stock_transfer_accounting(50.0, "20260115", qty=5, item_name="Test Item")
    result = get_daybook("20260101", "20260131")
    assert result["total"] == 1
    row = result["vouchers"][0]
    assert row["is_pending"] is True
    assert row["voucher_type"] == "Journal"
    assert row["amount"] == 50.0
    assert row["store_display"] == "Multiple"


def test_pending_stock_transfer_accounting_leg_filtered_by_either_store():
    _queue_stock_transfer_accounting(50.0, "20260115", from_store="Mahagun", to_store="Gulshan")
    assert get_daybook("20260101", "20260131", cost_centre="Mahagun")["total"] == 1
    assert get_daybook("20260101", "20260131", cost_centre="Gulshan")["total"] == 1
    assert get_daybook("20260101", "20260131", cost_centre="VVIP")["total"] == 0


def test_synced_stock_transfer_not_double_counted_once_reporting_synced():
    _queue_stock_transfer_accounting(50.0, "20260115", qty=5, item_name="Test Item", status="SYNCED")
    _insert_confirmed_inter_store_journal(50.0, "20260115", "ST-1", qty=5.0, item_name="Test Item")

    result = get_daybook("20260101", "20260131")
    assert result["total"] == 1
    assert result["vouchers"][0]["is_pending"] is False


def _queue_repack(repack_id, tally_date, status=None):
    payload = {"repack_id": repack_id, "created_by": "Tester", "tally_date": tally_date}
    qid = queue_operation("REPACK_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Repack: 1 pcs at Mahagun")
    if status:
        update_queue_status(qid, status)
    return qid


def _insert_repack_operation(repack_id, store_name="Mahagun", dest_item_name="Output Item", dest_qty=1.0):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO repack_operations (
                id, store_name, source_item_name, dest_item_name,
                source_qty, source_rate, source_amount, dest_qty, conversion_id
            ) VALUES (?, ?, 'Source Item', ?, 1, 10, 10, ?, 1)
        """, (repack_id, store_name, dest_item_name, dest_qty))
        conn.commit()


def test_pending_repack_appears_in_daybook_with_zero_amount():
    _insert_repack_operation("test-repack-1")
    _queue_repack("test-repack-1", "20260115")

    result = get_daybook("20260101", "20260131")
    assert result["total"] == 1
    row = result["vouchers"][0]
    assert row["is_pending"] is True
    assert row["voucher_type"] == "Stock Journal"
    assert row["amount"] == 0.0
    assert row["party_ledger_name"] == "Output Item"


def test_pending_repack_filtered_by_cost_centre():
    _insert_repack_operation("test-repack-2", store_name="Gulshan")
    _queue_repack("test-repack-2", "20260115")

    assert get_daybook("20260101", "20260131", cost_centre="Mahagun")["total"] == 0
    assert get_daybook("20260101", "20260131", cost_centre="Gulshan")["total"] == 1


def test_synced_repack_not_double_counted_once_reporting_synced():
    _insert_repack_operation("test-repack-3")
    _queue_repack("test-repack-3", "20260115", status="SYNCED")
    with get_db() as conn:
        conn.execute("""
            INSERT INTO reporting_vouchers (tally_guid, date, voucher_number, voucher_type, party_ledger_name, narration)
            VALUES ('REPACK-test-repack-3', '20260115', 'SJ-1', 'Stock Journal', NULL, 'Repack: 1.0 PCS of Output Item at Mahagun')
        """)
        conn.commit()

    result = get_daybook("20260101", "20260131")
    assert result["total"] == 1
    assert result["vouchers"][0]["is_pending"] is False
    assert result["vouchers"][0]["voucher_number"] == "SJ-1"


def test_repack_missing_from_repack_operations_is_skipped_gracefully():
    # A queued REPACK_VOUCHER row whose repack_operations row is somehow gone
    # must not crash the whole daybook -- just be silently omitted.
    _queue_repack("test-repack-missing", "20260115")
    result = get_daybook("20260101", "20260131")
    assert result["total"] == 0
