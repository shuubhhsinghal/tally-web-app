import os
import json
import pytest
import requests
from datetime import datetime, timedelta, date
from unittest.mock import patch, AsyncMock

os.environ["TESTING"] = "true"

from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db, init_db, compute_pending_interest_accruals, get_loan_by_id
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
        conn.execute("DELETE FROM loan_payments")
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


def _seed_ledger(name, parent="Loans (Liability)"):
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO ledgers (name, parent, cost_centre) VALUES (?, ?, 0)", (name, parent))
        conn.commit()


def _create_loan(lender_name="ABC Financiers", principal=100000.0, total_repayment=120000.0,
                  daily_amount=1200.0, start_date=None):
    start_date = start_date or datetime.now().strftime("%Y-%m-%d")
    res = client.post("/api/loans", json={
        "lender_name": lender_name,
        "principal_amount": principal,
        "total_repayment_amount": total_repayment,
        "daily_amount": daily_amount,
        "start_date": start_date,
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


# --- Creating a loan ---

def test_create_loan_records_principal_as_opening_balance():
    # A lender name not pre-seeded in `ledgers`, so ledger creation actually
    # gets queued and its XML can be inspected (the default "ABC Financiers"
    # is pre-seeded as already-synced for the other tests below).
    loan = _create_loan(lender_name="New Lender Co", principal=100000.0, total_repayment=120000.0, daily_amount=1200.0)
    assert loan["ledger_name"] == "Loan - New Lender Co"
    assert loan["status"] == "active"

    row = _latest_queue_row("%Loan - New Lender Co%")
    assert "<OPENINGBALANCE>100000.0</OPENINGBALANCE>" in row["xml_data"]


def test_create_loan_rejects_total_less_than_principal():
    res = client.post("/api/loans", json={
        "lender_name": "XYZ", "principal_amount": 100000.0, "total_repayment_amount": 90000.0,
        "daily_amount": 1000.0, "start_date": "2026-01-01",
    })
    assert res.status_code == 400


def test_create_loan_rejects_zero_amount():
    res = client.post("/api/loans", json={
        "lender_name": "XYZ", "principal_amount": 0, "total_repayment_amount": 90000.0,
        "daily_amount": 1000.0, "start_date": "2026-01-01",
    })
    assert res.status_code == 400


def test_second_loan_from_same_lender_gets_its_own_ledger():
    # A lender who already has a loan (e.g. a top-up, or a fresh loan taken
    # before the old one closed) must not silently share that loan's ledger.
    first = _create_loan(lender_name="Repeat Lender", principal=100000.0, total_repayment=120000.0,
                          daily_amount=1200.0, start_date="2026-09-01")
    second = _create_loan(lender_name="Repeat Lender", principal=50000.0, total_repayment=60000.0,
                           daily_amount=600.0, start_date="2026-10-20")
    assert first["ledger_name"] == "Loan - Repeat Lender"
    assert second["ledger_name"] != first["ledger_name"]
    assert second["ledger_name"] == "Loan - Repeat Lender (2026-10-20)"


# --- Repaying a loan: just reduces the balance by whatever was actually
# paid -- no principal/interest split happens here anymore, that's handled
# separately by the monthly interest accrual job below ---

@patch('backend.routers.loans.tally_transport.post', new_callable=AsyncMock)
def test_repay_posts_the_full_amount_with_no_split(mock_post):
    mock_post.return_value.text = "<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></IMPORTRESULT></DATA></BODY></ENVELOPE>"
    mock_post.return_value.status_code = 200

    loan = _create_loan(principal=100000.0, total_repayment=120000.0, daily_amount=1200.0)

    res = client.post(f"/api/loans/{loan['id']}/repay", json={
        "amount": 1200.0, "date": datetime.now().strftime("%Y-%m-%d"), "paid_from": "Cash Mahagun",
    })
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "success"

    row = _latest_queue_row("Loan Repayment:%")
    assert row["xml_data"].count("<LEDGERNAME>") == 2
    assert f"<LEDGERNAME>{loan['ledger_name']}</LEDGERNAME>" in row["xml_data"]
    assert "<AMOUNT>-1200.0</AMOUNT>" in row["xml_data"]
    assert "<AMOUNT>1200.0</AMOUNT>" in row["xml_data"]

    detail = client.get(f"/api/loans/{loan['id']}").json()
    assert detail["paid_to_date"] == 1200.0
    assert detail["balance"] == 100000.0 - 1200.0


@patch('backend.routers.loans.tally_transport.post', new_callable=AsyncMock)
def test_repay_partial_or_irregular_amount_just_reduces_balance(mock_post):
    mock_post.return_value.text = "<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></IMPORTRESULT></DATA></BODY></ENVELOPE>"
    mock_post.return_value.status_code = 200

    loan = _create_loan(principal=100000.0, total_repayment=120000.0, daily_amount=1200.0)

    # A short payment (e.g. only 700 of the usual 1200/day) is posted as-is --
    # nothing computes or enforces a "correct" split against it.
    res = client.post(f"/api/loans/{loan['id']}/repay", json={
        "amount": 700.0, "date": datetime.now().strftime("%Y-%m-%d"), "paid_from": "Cash Mahagun",
    })
    assert res.status_code == 200, res.text

    detail = client.get(f"/api/loans/{loan['id']}").json()
    assert detail["paid_to_date"] == 700.0
    assert detail["balance"] == 100000.0 - 700.0


@patch('backend.routers.loans.tally_transport.post', new_callable=AsyncMock)
def test_repay_closes_loan_once_fully_paid(mock_post):
    mock_post.return_value.text = "<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></IMPORTRESULT></DATA></BODY></ENVELOPE>"
    mock_post.return_value.status_code = 200

    loan = _create_loan(principal=1000.0, total_repayment=1200.0, daily_amount=1200.0)

    res = client.post(f"/api/loans/{loan['id']}/repay", json={
        "amount": 1200.0, "date": datetime.now().strftime("%Y-%m-%d"), "paid_from": "Cash Mahagun",
    })
    assert res.status_code == 200, res.text

    detail = client.get(f"/api/loans/{loan['id']}").json()
    assert detail["status"] == "closed"


def test_repay_rejects_on_closed_loan():
    loan = _create_loan(principal=1000.0, total_repayment=1200.0, daily_amount=1200.0)
    with get_db() as conn:
        conn.execute("UPDATE loans SET status = 'closed' WHERE id = ?", (loan["id"],))
        conn.commit()

    res = client.post(f"/api/loans/{loan['id']}/repay", json={
        "amount": 100.0, "date": datetime.now().strftime("%Y-%m-%d"), "paid_from": "Cash Mahagun",
    })
    assert res.status_code == 400


# --- Combined repayment: one lender, more than one active loan, one real
# handover of cash split across them in a single voucher ---

@patch('backend.routers.loans.tally_transport.post', new_callable=AsyncMock)
def test_repay_combined_posts_one_voucher_split_across_loans(mock_post):
    mock_post.return_value.text = "<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></IMPORTRESULT></DATA></BODY></ENVELOPE>"
    mock_post.return_value.status_code = 200

    loan1 = _create_loan(lender_name="Repeat Lender", principal=210000.0, total_repayment=238000.0,
                          daily_amount=3400.0, start_date="2026-09-01")
    loan2 = _create_loan(lender_name="Repeat Lender", principal=100000.0, total_repayment=120000.0,
                          daily_amount=1200.0, start_date="2026-10-20")
    # Both ledgers pre-seeded as already-synced -- same reasoning as the
    # module-level "Loan - ABC Financiers" seed (see clean_db fixture).
    # Creating the loan queues its ledger and leaves a `pending_masters` row
    # behind (that's what makes it show as still-syncing); clear it too, or
    # get_master_dependency_state keeps reporting PENDING regardless of the
    # `ledgers` row just inserted.
    _seed_ledger(loan1["ledger_name"])
    _seed_ledger(loan2["ledger_name"])
    with get_db() as conn:
        conn.execute("DELETE FROM pending_masters")
        conn.commit()

    res = client.post("/api/loans/repay-combined", json={
        "lender_name": "Repeat Lender",
        "date": "2026-10-24",
        "paid_from": "Cash Mahagun",
        "allocations": [
            {"loan_id": loan1["id"], "amount": 3800.0},
            {"loan_id": loan2["id"], "amount": 1200.0},
        ],
    })
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "success"

    row = _latest_queue_row("Loan Repayment: 5000.0 to Repeat Lender")
    assert row["xml_data"].count("<LEDGERNAME>") == 3  # 2 loan debits + 1 credit
    assert f"<LEDGERNAME>{loan1['ledger_name']}</LEDGERNAME>" in row["xml_data"]
    assert f"<LEDGERNAME>{loan2['ledger_name']}</LEDGERNAME>" in row["xml_data"]
    assert "<AMOUNT>-3800.0</AMOUNT>" in row["xml_data"]
    assert "<AMOUNT>-1200.0</AMOUNT>" in row["xml_data"]
    assert "<AMOUNT>5000.0</AMOUNT>" in row["xml_data"]

    detail1 = client.get(f"/api/loans/{loan1['id']}").json()
    detail2 = client.get(f"/api/loans/{loan2['id']}").json()
    assert detail1["paid_to_date"] == 3800.0
    assert detail2["paid_to_date"] == 1200.0


def test_repay_combined_rejects_loan_not_belonging_to_lender():
    loan1 = _create_loan(lender_name="Repeat Lender")
    other = _create_loan(lender_name="Someone Else")

    res = client.post("/api/loans/repay-combined", json={
        "lender_name": "Repeat Lender",
        "date": "2026-10-24",
        "paid_from": "Cash Mahagun",
        "allocations": [{"loan_id": other["id"], "amount": 100.0}],
    })
    assert res.status_code == 400


def test_staff_cannot_repay_combined():
    owner = _setup_owner()
    loan1 = _create_loan(lender_name="Repeat Lender")
    loan2 = _create_loan(lender_name="Repeat Lender", start_date="2026-10-20")
    _create_staff(owner["token"])
    staff = _login("Staff One", "staffpass")

    res = client.post("/api/loans/repay-combined", json={
        "lender_name": "Repeat Lender",
        "date": "2026-10-24",
        "paid_from": "Cash Mahagun",
        "allocations": [{"loan_id": loan1["id"], "amount": 100.0}],
    }, headers=_auth_headers(staff["token"]))
    assert res.status_code == 403


# --- Behind/ahead-of-schedule indicator (display-only) ---

def test_loan_behind_schedule_when_no_payments_made():
    start = (datetime.now() - timedelta(days=4)).strftime("%Y-%m-%d")
    loan = _create_loan(principal=100000.0, total_repayment=120000.0, daily_amount=1200.0, start_date=start)

    listing = client.get("/api/loans").json()
    found = next(l for l in listing["loans"] if l["id"] == loan["id"])
    # 5 elapsed days (inclusive of start day) * 1200 expected, 0 paid
    assert found["behind_by"] == 5 * 1200.0
    assert found["paid_to_date"] == 0.0


@patch('backend.routers.loans.tally_transport.post', new_callable=AsyncMock)
def test_loan_ahead_of_schedule_after_overpaying(mock_post):
    mock_post.return_value.text = "<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></IMPORTRESULT></DATA></BODY></ENVELOPE>"
    mock_post.return_value.status_code = 200

    loan = _create_loan(principal=100000.0, total_repayment=120000.0, daily_amount=1200.0)

    client.post(f"/api/loans/{loan['id']}/repay", json={
        "amount": 6000.0, "date": datetime.now().strftime("%Y-%m-%d"), "paid_from": "Cash Mahagun",
    })

    listing = client.get("/api/loans").json()
    found = next(l for l in listing["loans"] if l["id"] == loan["id"])
    assert found["behind_by"] < 0  # ahead of schedule


# --- Timeout / connectivity safety (mirrors test_router_delivery_uncertain.py's Payment coverage) ---

@patch('backend.routers.loans.tally_transport.post', new_callable=AsyncMock)
def test_repay_timeout_sets_delivery_uncertain(mock_post):
    mock_post.side_effect = requests.exceptions.Timeout()
    loan = _create_loan()

    res = client.post(f"/api/loans/{loan['id']}/repay", json={
        "amount": 1200.0, "date": datetime.now().strftime("%Y-%m-%d"), "paid_from": "Cash Mahagun",
    })
    assert res.status_code == 200
    row = _latest_queue_row("Loan Repayment:%")
    assert row["status"] == "PENDING"
    assert _delivery_uncertain(row) is True


@patch('backend.routers.loans.tally_transport.post', new_callable=AsyncMock)
def test_repay_connect_timeout_clears_delivery_uncertain(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectTimeout()
    loan = _create_loan()

    res = client.post(f"/api/loans/{loan['id']}/repay", json={
        "amount": 1200.0, "date": datetime.now().strftime("%Y-%m-%d"), "paid_from": "Cash Mahagun",
    })
    assert res.status_code == 200
    row = _latest_queue_row("Loan Repayment:%")
    assert row["status"] == "PENDING"
    assert not _delivery_uncertain(row)


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
        "lender_name": "XYZ", "principal_amount": 1000.0, "total_repayment_amount": 1200.0,
        "daily_amount": 100.0, "start_date": "2026-01-01",
    }, headers=_auth_headers(staff["token"]))
    assert res.status_code == 403


def test_staff_cannot_repay_loan():
    owner = _setup_owner()
    loan = _create_loan()
    _create_staff(owner["token"])
    staff = _login("Staff One", "staffpass")

    res = client.post(f"/api/loans/{loan['id']}/repay", json={
        "amount": 100.0, "date": "2026-01-01", "paid_from": "Cash Mahagun",
    }, headers=_auth_headers(staff["token"]))
    assert res.status_code == 403


def test_owner_can_list_loans_with_real_session():
    owner = _setup_owner()
    _create_loan()

    res = client.get("/api/loans", headers=_auth_headers(owner["token"]))
    assert res.status_code == 200
    assert len(res.json()["loans"]) == 1


# --- Computed stats: days_left, this month's paid/accrued amounts ---

def test_days_left_reflects_actual_pace_not_just_original_tenure():
    # 100-day loan (100,000 -> 120,000 at 1,200/day), but paying double the
    # daily rate should meaningfully shrink days left, not stay at ~100.
    loan = _create_loan(principal=100000.0, total_repayment=120000.0, daily_amount=1200.0)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO loan_payments (loan_id, date, amount, principal_portion, interest_portion, queue_id, created_by, created_at) "
            "VALUES (?, ?, ?, ?, 0, NULL, 'Owner', ?)",
            (loan["id"], "2026-09-21", 60000.0, 60000.0, datetime.now().isoformat())
        )
        conn.commit()

    detail = client.get(f"/api/loans/{loan['id']}").json()
    # No interest has accrued yet (that's a separate monthly job), so balance
    # is just principal minus what's actually been paid: 100,000 - 60,000 =
    # 40,000 -> 34 days left at 1,200/day, nowhere near the original ~100.
    assert detail["balance"] == 40000.0
    assert detail["days_left"] == 34


def test_this_month_reflects_actual_payments_and_accruals_separately():
    loan = _create_loan(principal=100000.0, total_repayment=120000.0, daily_amount=1200.0)
    this_month = datetime.now().strftime("%Y-%m")
    with get_db() as conn:
        # This month: 1,200 actually paid.
        conn.execute(
            "INSERT INTO loan_payments (loan_id, date, amount, principal_portion, interest_portion, queue_id, created_by, created_at) "
            "VALUES (?, ?, ?, ?, 0, NULL, 'Owner', ?)",
            (loan["id"], f"{this_month}-05", 1200.0, 1200.0, datetime.now().isoformat())
        )
        # A different (past) month shouldn't count toward this month's total.
        conn.execute(
            "INSERT INTO loan_payments (loan_id, date, amount, principal_portion, interest_portion, queue_id, created_by, created_at) "
            "VALUES (?, ?, ?, ?, 0, NULL, 'Owner', ?)",
            (loan["id"], "2020-01-05", 1200.0, 1200.0, datetime.now().isoformat())
        )
        # This month's accrued interest (as if the monthly job already ran).
        conn.execute(
            "INSERT INTO loan_interest_accruals (loan_id, period_start, period_end, amount, queue_id, created_at) "
            "VALUES (?, ?, ?, ?, NULL, ?)",
            (loan["id"], f"{this_month}-01", f"{this_month}-05", 200.0, datetime.now().isoformat())
        )
        conn.commit()

    detail = client.get(f"/api/loans/{loan['id']}").json()
    assert detail["paid_this_month"] == 1200.0
    assert detail["interest_accrued_this_month"] == 200.0
    assert detail["accrued_interest_to_date"] == 200.0


# --- Monthly interest accrual: known-fixed interest, spread over the loan's
# tenure in days, recognized one completed calendar month at a time ---

def test_compute_pending_interest_accruals_splits_by_calendar_month():
    # 1,00,000 principal, 100-day tenure at 1,200/day -> 1,20,000 total,
    # 20,000 interest, i.e. 200/day of interest.
    loan_dict = {
        "principal_amount": 100000.0, "total_repayment_amount": 120000.0,
        "daily_amount": 1200.0, "start_date": "2026-10-20", "interest_accrued_through": None,
    }

    # Nothing has completed yet while we're still inside October.
    assert compute_pending_interest_accruals(loan_dict, date(2026, 10, 25)) == []

    # Once November has started, October (12 days: 20th-31st) is complete.
    periods = compute_pending_interest_accruals(loan_dict, date(2026, 11, 5))
    assert periods == [{"period_start": "2026-10-20", "period_end": "2026-10-31", "amount": 2400.0}]

    # Simulate that period having been posted, then ask again a couple of
    # months later -- both November (30 days) and December (31 days) should
    # now be pending, in order, with nothing double-counted from October.
    loan_dict["interest_accrued_through"] = "2026-10-31"
    periods = compute_pending_interest_accruals(loan_dict, date(2027, 1, 5))
    assert periods == [
        {"period_start": "2026-11-01", "period_end": "2026-11-30", "amount": 6000.0},
        {"period_start": "2026-12-01", "period_end": "2026-12-31", "amount": 6200.0},
    ]


def test_compute_pending_interest_accruals_skips_loans_with_no_known_interest():
    # A legacy Oct-1 backfilled balance entered without a known split
    # (total_repayment == principal) has nothing to accrue.
    loan_dict = {
        "principal_amount": 400000.0, "total_repayment_amount": 400000.0,
        "daily_amount": 10000.0, "start_date": "2026-10-01", "interest_accrued_through": None,
    }
    assert compute_pending_interest_accruals(loan_dict, date(2026, 12, 1)) == []


@patch('backend.services.loan_interest_accrual.queue_operation')
def test_post_pending_interest_accruals_queues_a_journal_per_completed_month(mock_queue_operation):
    mock_queue_operation.side_effect = lambda *a, **k: 999  # fake queue_id

    loan = _create_loan(principal=100000.0, total_repayment=120000.0, daily_amount=1200.0, start_date="2026-10-20")

    posted = post_pending_interest_accruals(as_of=date(2026, 11, 5))
    assert posted == 1

    with get_db() as conn:
        rows = conn.execute("SELECT * FROM loan_interest_accruals WHERE loan_id = ?", (loan["id"],)).fetchall()
    assert len(rows) == 1
    assert rows[0]["amount"] == 2400.0
    assert rows[0]["period_end"] == "2026-10-31"

    refreshed = get_loan_by_id(loan["id"])
    assert refreshed["interest_accrued_through"] == "2026-10-31"

    # Running it again for the same as_of date is a no-op -- nothing new has
    # completed since the last accrual.
    assert post_pending_interest_accruals(as_of=date(2026, 11, 5)) == 0


@patch('backend.services.loan_interest_accrual.queue_operation')
def test_post_pending_interest_accruals_skips_legacy_no_interest_loans(mock_queue_operation):
    mock_queue_operation.side_effect = lambda *a, **k: 999

    _create_loan(lender_name="Legacy Lender", principal=400000.0, total_repayment=400000.0,
                 daily_amount=10000.0, start_date="2026-10-01")

    posted = post_pending_interest_accruals(as_of=date(2026, 12, 1))
    assert posted == 0
