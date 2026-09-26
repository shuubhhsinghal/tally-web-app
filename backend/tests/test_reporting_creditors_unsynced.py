import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db, queue_operation, set_delivery_uncertain, update_queue_status
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
        conn.execute("DELETE FROM reporting_ledger_entries")
        conn.execute("DELETE FROM reporting_vouchers")
        conn.execute("DELETE FROM ledgers WHERE name = ?", (SUPPLIER,))
        conn.commit()


def _queue_purchase(amount, tally_date, supplier=SUPPLIER, status=None, invoice_number="INV-1"):
    payload = {"supplier": supplier, "invoice_number": invoice_number, "amount": amount, "tally_date": tally_date, "cost_center": "Mahagun", "narration": ""}
    qid = queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Purchase Invoice: {payload['invoice_number']} from {supplier}")
    if status:
        update_queue_status(qid, status)
    return qid


def _queue_payment(amount, tally_date, debit_ledger=SUPPLIER, narration="", status=None):
    payload = {"debit_ledger": debit_ledger, "credit_ledger": "Cash Mahagun", "amount": amount, "tally_date": tally_date, "narration": narration, "cost_center": "Mahagun"}
    qid = queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Payment: {amount} to {debit_ledger}")
    if status:
        update_queue_status(qid, status)
    return qid


def _insert_confirmed_purchase(supplier, amount, date, voucher_number, invoice_number, voucher_type="Purchase", guid="guid-V-creditor-test"):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO reporting_vouchers (tally_guid, date, voucher_number, voucher_type, party_ledger_name, narration, reference)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (guid, date, voucher_number, voucher_type, supplier, "", invoice_number))
        voucher_id = cursor.lastrowid
        cursor.execute("""
            INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive)
            VALUES (?, ?, ?, ?)
        """, (voucher_id, supplier, amount, 1))
        conn.commit()
    return voucher_id


def _insert_confirmed_payment(debit_ledger, amount, date, voucher_number, narration="", guid="guid-V-creditor-payment-test"):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO reporting_vouchers (tally_guid, date, voucher_number, voucher_type, party_ledger_name, narration)
            VALUES (?, ?, ?, 'Payment', ?, ?)
        """, (guid, date, voucher_number, debit_ledger, narration))
        voucher_id = cursor.lastrowid
        cursor.execute("""
            INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive)
            VALUES (?, ?, ?, ?)
        """, (voucher_id, debit_ledger, -amount, 1))
        conn.commit()
    return voucher_id


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


def test_synced_purchase_not_yet_reporting_synced_still_shows_in_balance():
    # A purchase whose voucher already went through to Tally (status SYNCED)
    # but hasn't been picked up by the periodic reporting sync yet used to
    # vanish from the supplier's balance entirely -- this is the bug fix.
    _queue_purchase(1000.0, "20260115", status="SYNCED", invoice_number="INV-SYNCED-1")
    result = get_creditor_ledger_movements(SUPPLIER, "20260101", "20260131")
    assert result["pending_amount"] == 1000.0
    assert result["period_closing"] == 1000.0
    assert len(result["movements"]) == 1
    assert result["movements"][0]["is_pending"] is True


def test_synced_purchase_not_double_counted_once_reporting_sync_catches_up():
    qid = _queue_purchase(1000.0, "20260115", status="SYNCED", invoice_number="INV-SYNCED-2")
    _insert_confirmed_purchase(SUPPLIER, 1000.0, "20260115", "236", "INV-SYNCED-2")

    result = get_creditor_ledger_movements(SUPPLIER, "20260101", "20260131")
    assert result["pending_amount"] == 0.0, "confirmed voucher now covers this, queue row must not also count"
    assert result["period_closing"] == 1000.0
    assert result["period_closing_confirmed"] == 1000.0
    assert len(result["movements"]) == 1
    assert result["movements"][0]["is_pending"] is False
    assert result["movements"][0]["voucher_number"] == "236"


def test_overview_synced_purchase_not_double_counted_when_queue_payload_ledger_has_different_casing():
    # A posting form's own metadata endpoint shows suppliers .title()-cased
    # for display, and that's the string that actually gets submitted and
    # saved into the queue payload's 'supplier' field -- which can therefore
    # differ in case from the ledger's real stored name (used for
    # ledgers.name, reporting_vouchers.party_ledger_name, and this overview's
    # own per-supplier grouping). Caught live against a real "cash mahagun"
    # ledger in Daybook; an exact-case dedup match would silently never fire
    # here and double count forever once the confirmed voucher appears.
    _queue_purchase(1000.0, "20260115", status="SYNCED", invoice_number="INV-SYNCED-CASE", supplier=SUPPLIER.upper())
    _insert_confirmed_purchase(SUPPLIER, 1000.0, "20260115", "236", "INV-SYNCED-CASE")

    overview = get_all_creditors_overview("20260101", "20260131")
    row = next(r for r in overview if r["supplier_name"] == SUPPLIER)
    assert row["pending_amount"] == 0.0, "confirmed voucher now covers this, queue row must not also count"
    assert row["period_closing"] == 1000.0


def test_synced_debit_note_not_double_counted_once_reporting_synced():
    _queue_return([{"name": "Item A", "qty": 2, "uom": "PCS", "rate": 50.0, "amount": 100.0}], "20260116")
    from backend.database import update_queue_status
    with get_db() as conn:
        row = conn.execute("SELECT id FROM offline_queue ORDER BY id DESC LIMIT 1").fetchone()
    update_queue_status(row['id'], "SYNCED")
    _insert_confirmed_purchase(SUPPLIER, -100.0, "20260116", "DN-5", "INV-2", voucher_type="Debit Note", guid="guid-V-creditor-dn-test")

    result = get_creditor_ledger_movements(SUPPLIER, "20260101", "20260131")
    assert result["pending_amount"] == 0.0
    assert result["period_closing"] == -100.0
    assert len(result["movements"]) == 1
    assert result["movements"][0]["is_pending"] is False


def test_synced_payment_not_double_counted_once_reporting_synced():
    _queue_payment(400.0, "20260117", narration="Cash paid", status="SYNCED")
    _insert_confirmed_payment(SUPPLIER, 400.0, "20260117", "PMT-9", narration="Cash paid")

    result = get_creditor_ledger_movements(SUPPLIER, "20260101", "20260131")
    assert result["pending_amount"] == 0.0
    assert result["period_closing"] == -400.0
    assert len(result["movements"]) == 1
    assert result["movements"][0]["is_pending"] is False


def test_synced_payment_still_shown_when_not_yet_reporting_synced():
    _queue_payment(400.0, "20260117", narration="Cash paid", status="SYNCED")
    result = get_creditor_ledger_movements(SUPPLIER, "20260101", "20260131")
    assert result["pending_amount"] == -400.0
    assert result["period_closing"] == -400.0


def test_synced_purchase_before_period_not_double_counted_in_opening_balance():
    _queue_purchase(1000.0, "20260615", status="SYNCED", invoice_number="INV-SYNCED-3")
    _insert_confirmed_purchase(SUPPLIER, 1000.0, "20260615", "300", "INV-SYNCED-3")

    result = get_creditor_ledger_movements(SUPPLIER, "20260701", "20260731")
    assert result["pending_opening_amount"] == 0.0
    assert result["period_opening"] == 1000.0
    assert result["period_opening_confirmed"] == 1000.0


def test_overview_does_not_double_count_synced_purchase():
    _queue_purchase(1000.0, "20260115", status="SYNCED", invoice_number="INV-SYNCED-4")
    _insert_confirmed_purchase(SUPPLIER, 1000.0, "20260115", "301", "INV-SYNCED-4")

    overview = get_all_creditors_overview("20260101", "20260131")
    row = next(r for r in overview if r["supplier_name"] == SUPPLIER)
    assert row["pending_amount"] == 0.0
    assert row["period_closing"] == 1000.0
    assert row["period_closing_confirmed"] == 1000.0


def test_synced_payment_before_period_not_double_counted_in_opening_balance():
    _queue_payment(400.0, "20260620", narration="Advance paid", status="SYNCED")
    _insert_confirmed_payment(SUPPLIER, 400.0, "20260620", "150", narration="Advance paid")

    result = get_creditor_ledger_movements(SUPPLIER, "20260701", "20260731")
    assert result["pending_opening_amount"] == 0.0, "confirmed payment now covers this, queue row must not also count"
    assert result["period_opening"] == -400.0
    assert result["period_opening_confirmed"] == -400.0


def test_overview_opening_balance_does_not_double_count_once_reporting_synced():
    # Exercises get_all_creditors_overview's own bulk pre-period fetch
    # (_fetch_pending_supplier_movements(fy_start, pre_period_end), shared
    # across all suppliers) rather than a single supplier's ledger call --
    # the overview computes this independently, so it needs its own check.
    _queue_purchase(1000.0, "20260615", status="SYNCED", invoice_number="INV-SYNCED-OB")
    _insert_confirmed_purchase(SUPPLIER, 1000.0, "20260615", "303", "INV-SYNCED-OB")

    overview = get_all_creditors_overview("20260701", "20260731")
    row = next(r for r in overview if r["supplier_name"] == SUPPLIER)
    assert row["pending_opening_amount"] == 0.0
    assert row["period_opening"] == 1000.0


def test_synced_purchase_for_different_invoice_number_not_treated_as_duplicate():
    # Two separate purchases against the same supplier -- a SYNCED queue row
    # for one invoice must not be swallowed just because *some* confirmed
    # purchase exists for that supplier; the invoice number must also match.
    _queue_purchase(500.0, "20260115", status="SYNCED", invoice_number="INV-A")
    _insert_confirmed_purchase(SUPPLIER, 1000.0, "20260115", "302", "INV-B")

    result = get_creditor_ledger_movements(SUPPLIER, "20260101", "20260131")
    assert result["pending_amount"] == 500.0
    assert result["period_closing"] == 1500.0
    assert len(result["movements"]) == 2
