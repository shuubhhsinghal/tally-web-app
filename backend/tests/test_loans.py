import os
import json
import pytest
from datetime import datetime, date
from unittest.mock import patch, AsyncMock

os.environ["TESTING"] = "true"

from fastapi.testclient import TestClient
from backend.main import app
from backend.database import (
    get_db, init_db, compute_pending_interest_accruals, get_ledger_current_balance,
    compute_current_month_pending_interest,
)
from backend.services.loan_interest_accrual import post_pending_interest_accruals

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM users")
        conn.execute("DELETE FROM offline_queue")
        conn.execute("DELETE FROM pending_masters")
        conn.execute("DELETE FROM loans")
        conn.execute("DELETE FROM loan_interest_accruals")
        conn.execute("INSERT OR IGNORE INTO ledgers (name, parent, cost_centre) VALUES ('Cash Mahagun', 'Cash-in-Hand', 0)")
        # Pre-seeded as already-synced, matching a ledger that was created and
        # confirmed in an earlier session -- creating the loan while its own
        # ledger name is still unconfirmed would instead queue the ledger and
        # return "pending_master_dependency" (correct real behavior; not what
        # most of these tests are checking).
        conn.execute("INSERT OR IGNORE INTO ledgers (name, parent, cost_centre) VALUES ('Loan - ABC Financiers', 'Loans (Liability)', 0)")
        conn.execute("INSERT OR IGNORE INTO ledgers (name, parent, cost_centre) VALUES ('Loan Interest', 'Indirect Expenses', 0)")
        conn.commit()
    yield


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def _setup_owner():
    res = client.post("/api/auth/setup", json={"name": "Owner", "password": "ownerpass"})
    assert res.status_code == 200
    return res.json()


def _create_staff(owner_token, name="Staff One", password="staffpass", store="Mahagun"):
    res = client.post(
        "/api/auth/users",
        json={"name": name, "password": password, "store_name": store},
        headers=_auth_headers(owner_token),
    )
    assert res.status_code == 200, res.text
    return res.json()


def _login(name, password):
    res = client.post("/api/auth/login", json={"name": name, "password": password})
    assert res.status_code == 200, res.text
    return res.json()


def _seed_ledger(name, parent="Loans (Liability)", opening_balance=0.0):
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO ledgers (name, parent, cost_centre, opening_balance) VALUES (?, ?, 0, ?)",
            (name, parent, opening_balance),
        )
        conn.commit()


def _create_loan(lender_name="ABC Financiers", principal=100000.0, daily_amount=1200.0,
                  number_of_days=100, start_date=None, received_into="Cash Mahagun"):
    start_date = start_date or datetime.now().strftime("%Y-%m-%d")
    res = client.post("/api/loans", json={
        "lender_name": lender_name,
        "principal_amount": principal,
        "daily_amount": daily_amount,
        "number_of_days": number_of_days,
        "start_date": start_date,
        "received_into_ledger": received_into,
    })
    assert res.status_code == 200, res.text
    return res.json()


def _latest_queue_row(description_like):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM offline_queue WHERE description LIKE ? ORDER BY id DESC LIMIT 1",
            (description_like,)
        )
        return dict(cursor.fetchone())


def _delivery_uncertain(row):
    payload = json.loads(row["payload"]) if row["payload"] else {}
    return payload.get("delivery_uncertain")


# --- Taking a loan: a real disbursement, not just an opening balance ---

@patch('backend.routers.loans.tally_transport.post', new_callable=AsyncMock)
def test_create_loan_posts_a_receipt_voucher(mock_post):
    mock_post.return_value.text = "<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></IMPORTRESULT></DATA></BODY></ENVELOPE>"
    mock_post.return_value.status_code = 200

    result = _create_loan(principal=100000.0, daily_amount=1200.0, number_of_days=100, received_into="Cash Mahagun")
    assert result["status"] == "success"
    loan = result["loan"]
    assert loan["ledger_name"] == "Loan - ABC Financiers"
    assert loan["total_repayment_amount"] == 120000.0

    row = _latest_queue_row("Loan Received:%")
    assert 'VCHTYPE="Receipt"' in row["xml_data"]
    assert "<LEDGERNAME>Cash Mahagun</LEDGERNAME>" in row["xml_data"]
    assert "<LEDGERNAME>Loan - ABC Financiers</LEDGERNAME>" in row["xml_data"]
    assert "<AMOUNT>-100000.0</AMOUNT>" in row["xml_data"]
    assert "<AMOUNT>100000.0</AMOUNT>" in row["xml_data"]


def test_create_loan_rejects_zero_amount():
    res = client.post("/api/loans", json={
        "lender_name": "XYZ", "principal_amount": 0, "daily_amount": 1000.0,
        "number_of_days": 90, "start_date": "2026-01-01", "received_into_ledger": "Cash Mahagun",
    })
    assert res.status_code == 400


def test_create_loan_requires_received_into_ledger():
    res = client.post("/api/loans", json={
        "lender_name": "XYZ", "principal_amount": 1000.0, "daily_amount": 100.0,
        "number_of_days": 10, "start_date": "2026-01-01", "received_into_ledger": "",
    })
    assert res.status_code == 400


def test_second_loan_from_same_lender_shares_the_ledger():
    # A lender who already has a loan just gets another entry against their
    # existing ledger -- not a second, separately-named one.
    with patch('backend.routers.loans.tally_transport.post', new_callable=AsyncMock) as mock_post:
        mock_post.return_value.text = "<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></IMPORTRESULT></DATA></BODY></ENVELOPE>"
        mock_post.return_value.status_code = 200
        first = _create_loan(lender_name="Repeat Lender", principal=100000.0, daily_amount=1200.0, number_of_days=100)
        second = _create_loan(lender_name="Repeat Lender", principal=50000.0, daily_amount=600.0, number_of_days=100, start_date="2026-10-20")
    assert first["loan"]["ledger_name"] == "Loan - Repeat Lender"
    assert second["loan"]["ledger_name"] == "Loan - Repeat Lender"


# --- Owner-only access ---

def test_staff_cannot_list_loans():
    owner = _setup_owner()
    _create_staff(owner["token"])
    staff = _login("Staff One", "staffpass")

    res = client.get("/api/loans", headers=_auth_headers(staff["token"]))
    assert res.status_code == 403


def test_staff_cannot_create_loan():
    owner = _setup_owner()
    _create_staff(owner["token"])
    staff = _login("Staff One", "staffpass")

    res = client.post("/api/loans", json={
        "lender_name": "XYZ", "principal_amount": 1000.0, "daily_amount": 100.0,
        "number_of_days": 10, "start_date": "2026-01-01", "received_into_ledger": "Cash Mahagun",
    }, headers=_auth_headers(staff["token"]))
    assert res.status_code == 403


def test_owner_can_list_loans_with_real_session():
    owner = _setup_owner()
    with patch('backend.routers.loans.tally_transport.post', new_callable=AsyncMock) as mock_post:
        mock_post.return_value.text = "<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></IMPORTRESULT></DATA></BODY></ENVELOPE>"
        mock_post.return_value.status_code = 200
        _create_loan()

    res = client.get("/api/loans", headers=_auth_headers(owner["token"]))
    assert res.status_code == 200
    assert len(res.json()["lenders"]) == 1


# --- Listing: grouped by lender, live ledger balance, loan-wise details only ---

@patch('backend.routers.loans.tally_transport.post', new_callable=AsyncMock)
def test_list_loans_groups_by_lender_with_loan_details(mock_post):
    mock_post.return_value.text = "<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></IMPORTRESULT></DATA></BODY></ENVELOPE>"
    mock_post.return_value.status_code = 200

    _create_loan(lender_name="Repeat Lender", principal=100000.0, daily_amount=1200.0, number_of_days=100, start_date="2026-09-01")
    _create_loan(lender_name="Repeat Lender", principal=50000.0, daily_amount=600.0, number_of_days=100, start_date="2026-10-20")

    listing = client.get("/api/loans").json()
    lender = next(l for l in listing["lenders"] if l["lender_name"] == "Repeat Lender")
    assert len(lender["loans"]) == 2
    amounts = sorted(l["principal_amount"] for l in lender["loans"])
    assert amounts == [50000.0, 100000.0]
    # No per-loan balance field should exist anymore -- outstanding is lender-wide only.
    assert "balance" not in lender["loans"][0]


def test_lender_total_outstanding_reflects_live_ledger_balance():
    # A lender ledger that already has an opening balance set directly in
    # Tally (e.g. carried in from before this app was used) plus a new loan
    # taken through the app should sum together in the total shown.
    _seed_ledger("Loan - Legacy Lender", opening_balance=500000.0)
    with patch('backend.routers.loans.tally_transport.post', new_callable=AsyncMock) as mock_post:
        mock_post.return_value.text = "<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></IMPORTRESULT></DATA></BODY></ENVELOPE>"
        mock_post.return_value.status_code = 200
        _create_loan(lender_name="Legacy Lender", principal=100000.0, daily_amount=1200.0, number_of_days=100)

    # The loan's own voucher delivered successfully (status SYNCED), but the
    # *separate* reporting sync hasn't re-pulled it from Tally yet -- counted
    # anyway, straight from the queue, so there's no window where a
    # successfully-posted loan is invisible in the total.
    assert get_ledger_current_balance("Loan - Legacy Lender") == 600000.0


def test_lender_total_outstanding_includes_not_yet_synced_loans():
    # No connector/Tally reachable at all -- the new loan's ledger has never
    # been confirmed, so it sits in the offline queue as PENDING. The total
    # should still reflect it immediately rather than showing 0 until some
    # future sync catches up.
    _create_loan(lender_name="Fresh Lender", principal=100000.0, daily_amount=1200.0, number_of_days=100)
    assert get_ledger_current_balance("Loan - Fresh Lender") == 100000.0


@patch('backend.routers.loans.tally_transport.post', new_callable=AsyncMock)
def test_lender_total_outstanding_does_not_double_count_once_reporting_sync_catches_up(mock_post):
    # Once the separate reporting sync eventually re-pulls the same Receipt
    # voucher from Tally into reporting_ledger_entries, the loan's amount
    # must not be added a second time on top of the queue-based count.
    mock_post.return_value.text = "<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></IMPORTRESULT></DATA></BODY></ENVELOPE>"
    mock_post.return_value.status_code = 200
    _create_loan(lender_name="Synced Lender", principal=100000.0, daily_amount=1200.0, number_of_days=100)

    with get_db() as conn:
        conn.execute("""
            INSERT INTO reporting_vouchers (tally_guid, date, voucher_type, narration)
            VALUES ('guid-1', '20260922', 'Receipt', 'Loan received from Synced Lender')
        """)
        voucher_id = conn.execute("SELECT id FROM reporting_vouchers WHERE tally_guid = 'guid-1'").fetchone()[0]
        conn.execute(
            "INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive) VALUES (?, ?, ?, ?)",
            (voucher_id, "Loan - Synced Lender", 100000.0, 0),
        )
        conn.commit()

    assert get_ledger_current_balance("Loan - Synced Lender") == 100000.0


# --- Timeout / connectivity safety ---

@patch('backend.routers.loans.tally_transport.post', new_callable=AsyncMock)
def test_create_loan_timeout_sets_delivery_uncertain(mock_post):
    import requests
    mock_post.side_effect = requests.exceptions.Timeout()

    result = _create_loan()
    assert result["status"] == "queued"
    row = _latest_queue_row("Loan Received:%")
    assert row["status"] == "PENDING"
    assert _delivery_uncertain(row) is True


@patch('backend.routers.loans.tally_transport.post', new_callable=AsyncMock)
def test_create_loan_connect_timeout_clears_delivery_uncertain(mock_post):
    import requests
    mock_post.side_effect = requests.exceptions.ConnectTimeout()

    result = _create_loan()
    assert result["status"] == "queued"
    row = _latest_queue_row("Loan Received:%")
    assert row["status"] == "PENDING"
    assert not _delivery_uncertain(row)


# --- Monthly interest accrual: unchanged mechanism, now shared ledgers ---

def test_compute_pending_interest_accruals_splits_by_calendar_month():
    loan_dict = {
        "principal_amount": 100000.0, "total_repayment_amount": 120000.0,
        "daily_amount": 1200.0, "start_date": "2026-10-20", "interest_accrued_through": None,
    }
    assert compute_pending_interest_accruals(loan_dict, date(2026, 10, 25)) == []
    periods = compute_pending_interest_accruals(loan_dict, date(2026, 11, 5))
    assert periods == [{"period_start": "2026-10-20", "period_end": "2026-10-31", "amount": 2400.0}]


def test_compute_current_month_pending_interest_previews_the_full_month():
    # Same loan as above: 1,00,000 principal, 100 days at 1,200/day ->
    # 20,000 interest, 200/day. Even mid-month, this should show the FULL
    # amount that will post once the month closes, not just days-so-far.
    loan_dict = {
        "principal_amount": 100000.0, "total_repayment_amount": 120000.0,
        "daily_amount": 1200.0, "start_date": "2026-10-20", "interest_accrued_through": None,
    }
    preview = compute_current_month_pending_interest(loan_dict, as_of=date(2026, 10, 22))
    assert preview == {"period_start": "2026-10-20", "period_end": "2026-10-31", "amount": 2400.0}

    # After October has actually been posted, November's preview is the
    # full 30 days even on Nov 1st, before any of it has technically elapsed.
    loan_dict["interest_accrued_through"] = "2026-10-31"
    preview = compute_current_month_pending_interest(loan_dict, as_of=date(2026, 11, 1))
    assert preview == {"period_start": "2026-11-01", "period_end": "2026-11-30", "amount": 6000.0}


def test_compute_current_month_pending_interest_none_once_tenure_is_over():
    loan_dict = {
        "principal_amount": 100000.0, "total_repayment_amount": 120000.0,
        "daily_amount": 1200.0, "start_date": "2026-10-20", "interest_accrued_through": "2027-01-27",
    }
    assert compute_current_month_pending_interest(loan_dict, as_of=date(2027, 2, 5)) is None


def test_compute_current_month_pending_interest_none_for_no_interest_loan():
    loan_dict = {
        "principal_amount": 100000.0, "total_repayment_amount": 100000.0,
        "daily_amount": 1000.0, "start_date": "2026-10-01", "interest_accrued_through": None,
    }
    assert compute_current_month_pending_interest(loan_dict, as_of=date(2026, 10, 15)) is None


@patch('backend.services.loan_interest_accrual.queue_operation')
def test_post_pending_interest_accruals_shares_the_lender_ledger(mock_queue_operation):
    mock_queue_operation.side_effect = lambda *a, **k: 999

    with patch('backend.routers.loans.tally_transport.post', new_callable=AsyncMock) as mock_post:
        mock_post.return_value.text = "<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></IMPORTRESULT></DATA></BODY></ENVELOPE>"
        mock_post.return_value.status_code = 200
        loan1 = _create_loan(lender_name="Repeat Lender", principal=100000.0, daily_amount=1200.0, number_of_days=100, start_date="2026-10-20")["loan"]
        loan2 = _create_loan(lender_name="Repeat Lender", principal=50000.0, daily_amount=600.0, number_of_days=100, start_date="2026-10-20")["loan"]

    posted = post_pending_interest_accruals(as_of=date(2026, 11, 5))
    assert posted == 2  # one accrual per loan, same underlying ledger

    with get_db() as conn:
        rows = conn.execute("SELECT loan_id FROM loan_interest_accruals").fetchall()
    assert {r["loan_id"] for r in rows} == {loan1["id"], loan2["id"]}


@patch('backend.services.loan_interest_accrual.queue_operation')
def test_post_pending_interest_accruals_skips_loans_without_known_interest(mock_queue_operation):
    mock_queue_operation.side_effect = lambda *a, **k: 999
    with patch('backend.routers.loans.tally_transport.post', new_callable=AsyncMock) as mock_post:
        mock_post.return_value.text = "<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></IMPORTRESULT></DATA></BODY></ENVELOPE>"
        mock_post.return_value.status_code = 200
        # daily * days == principal -> zero interest, e.g. entered as a plain disbursement with no markup.
        _create_loan(lender_name="No Interest Lender", principal=100000.0, daily_amount=1000.0, number_of_days=100)

    posted = post_pending_interest_accruals(as_of=date(2026, 12, 1))
    assert posted == 0
