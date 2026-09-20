import json
import pytest
from backend.database import get_db
from backend.services.bank_transfer_matching import find_transfer_match, get_own_bank_ledger_names, extract_reference_number

BANK_A = "Bank A"
BANK_B = "Bank B"


@pytest.fixture(autouse=True)
def setup_db():
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        conn.execute("DELETE FROM reporting_ledger_entries")
        conn.execute("DELETE FROM reporting_vouchers")
        conn.execute("DELETE FROM ledgers WHERE name IN (?, ?)", (BANK_A, BANK_B))
        conn.execute("INSERT INTO ledgers (name, parent, cost_centre) VALUES (?, 'Bank Accounts', 0)", (BANK_A,))
        conn.execute("INSERT INTO ledgers (name, parent, cost_centre) VALUES (?, 'Bank Accounts', 0)", (BANK_B,))
        conn.commit()
    yield
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        conn.execute("DELETE FROM reporting_ledger_entries")
        conn.execute("DELETE FROM reporting_vouchers")
        conn.execute("DELETE FROM ledgers WHERE name IN (?, ?)", (BANK_A, BANK_B))
        conn.commit()


def _queue_pending_bank_txn(bank_ledger_name, date, amount, debit_ledger, credit_ledger, narration="", delivery_uncertain=False):
    from backend.database import queue_operation
    payload = {
        "bank_ledger_name": bank_ledger_name,
        "debit_ledger": debit_ledger,
        "credit_ledger": credit_ledger,
        "amount": amount,
        "date": date,
        "narration": narration,
    }
    qid = queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Bank Stmt: {date} - {amount} - {debit_ledger}")
    if delivery_uncertain:
        from backend.database import set_delivery_uncertain
        set_delivery_uncertain(qid, True)
    return qid


def test_get_own_bank_ledger_names_includes_seeded_banks():
    names = get_own_bank_ledger_names()
    assert BANK_A in names
    assert BANK_B in names


def test_pending_match_found_opposite_direction_same_amount_within_window():
    # Bank A's statement already recorded money LEAVING Bank A (debit_ledger=Bank A)
    qid = _queue_pending_bank_txn(BANK_A, "20260101", 5000.0, debit_ledger=BANK_A, credit_ledger="Bank Suspense Account")

    # Now Bank B's statement shows a matching DEPOSIT (money arriving) 1 day later
    txn = {"date": "20260102", "withdraw": 0.0, "deposit": 5000.0, "raw_narration": "NEFT IN"}
    match = find_transfer_match(txn, BANK_B, [BANK_A, BANK_B])

    assert match is not None
    assert match["source"] == "pending"
    assert match["queue_id"] == qid
    assert match["bank_ledger_name"] == BANK_A


def test_no_match_outside_window():
    _queue_pending_bank_txn(BANK_A, "20260101", 5000.0, debit_ledger=BANK_A, credit_ledger="Bank Suspense Account")
    txn = {"date": "20260110", "withdraw": 0.0, "deposit": 5000.0, "raw_narration": "NEFT IN"}
    match = find_transfer_match(txn, BANK_B, [BANK_A, BANK_B])
    assert match is None


def test_no_match_different_amount():
    _queue_pending_bank_txn(BANK_A, "20260101", 5000.0, debit_ledger=BANK_A, credit_ledger="Bank Suspense Account")
    txn = {"date": "20260102", "withdraw": 0.0, "deposit": 4999.0, "raw_narration": "NEFT IN"}
    match = find_transfer_match(txn, BANK_B, [BANK_A, BANK_B])
    assert match is None


def test_no_match_same_direction_not_opposite():
    # Both are deposits -- not a transfer pair, should not match.
    _queue_pending_bank_txn(BANK_A, "20260101", 5000.0, debit_ledger="Bank Suspense Account", credit_ledger=BANK_A)
    txn = {"date": "20260102", "withdraw": 0.0, "deposit": 5000.0, "raw_narration": "NEFT IN"}
    match = find_transfer_match(txn, BANK_B, [BANK_A, BANK_B])
    assert match is None


def test_delivery_uncertain_pending_row_is_not_mergeable():
    _queue_pending_bank_txn(BANK_A, "20260101", 5000.0, debit_ledger=BANK_A, credit_ledger="Bank Suspense Account", delivery_uncertain=True)
    txn = {"date": "20260102", "withdraw": 0.0, "deposit": 5000.0, "raw_narration": "NEFT IN"}
    match = find_transfer_match(txn, BANK_B, [BANK_A, BANK_B])
    assert match is not None
    assert match["source"] == "synced"  # informational only, not mergeable
    assert "queue_id" not in match


def test_synced_match_found_via_reporting_tables():
    with get_db() as conn:
        conn.execute("""
            INSERT INTO reporting_vouchers (tally_guid, date, voucher_type, narration)
            VALUES ('guid-1', '20260101', 'Payment', 'NEFT OUT')
        """)
        voucher_id = conn.execute("SELECT id FROM reporting_vouchers WHERE tally_guid = 'guid-1'").fetchone()['id']
        # Bank A's leg: ISDEEMEDPOSITIVE=No, AMOUNT positive -> money LEFT Bank A
        conn.execute("""
            INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive)
            VALUES (?, ?, 5000.0, 0)
        """, (voucher_id, BANK_A))
        conn.commit()

    txn = {"date": "20260102", "withdraw": 0.0, "deposit": 5000.0, "raw_narration": "NEFT IN"}
    match = find_transfer_match(txn, BANK_B, [BANK_A, BANK_B])

    assert match is not None
    assert match["source"] == "synced"
    assert match["bank_ledger_name"] == BANK_A


def test_extract_reference_number_neft():
    assert extract_reference_number("NEFT:SHREE SHYAM SALES CNRBH00006754218") == "CNRBH00006754218"


def test_extract_reference_number_none_for_upi():
    # Deliberately NOT extracted -- unreliable across banks per design.
    assert extract_reference_number("UPIAR/500166367203/DR/ASHISH K/YESB/bharatpe099045") is None


def test_reference_match_flag_set_when_neft_utr_matches():
    qid = _queue_pending_bank_txn(
        BANK_A, "20260101", 5000.0, debit_ledger=BANK_A, credit_ledger="Bank Suspense Account",
        narration="NEFT:SHREE SHYAM SALES CNRBH00006754218"
    )
    txn = {"date": "20260102", "withdraw": 0.0, "deposit": 5000.0, "raw_narration": "NEFT:SHREE SHYAM SALES CNRBH00006754218"}
    match = find_transfer_match(txn, BANK_B, [BANK_A, BANK_B])
    assert match is not None
    assert match["queue_id"] == qid
    assert match["reference_match"] is True


def test_no_match_for_non_transfer_transaction_with_zero_amounts():
    txn = {"date": "20260102", "withdraw": 0.0, "deposit": 0.0, "raw_narration": "x"}
    match = find_transfer_match(txn, BANK_B, [BANK_A, BANK_B])
    assert match is None
