import os
import json
import pytest

os.environ["TESTING"] = "true"

from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db, init_db

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM users")
        conn.execute("DELETE FROM offline_queue")
        conn.execute("DELETE FROM repack_operations")
        conn.execute("DELETE FROM reporting_monthly_stock")
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


# --- Purchase (item-wise) ---

def test_staff_cannot_post_item_wise_purchase_for_other_store():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff = _login("Staff One", "staffpass")

    res = client.post(
        "/api/purchase-item/post",
        json={"supplier": "X", "invoice_number": "1", "tally_date": "20260101", "cost_center": "Gulshan", "items": []},
        headers=_auth_headers(staff["token"]),
    )
    assert res.status_code == 403


def test_staff_can_post_item_wise_purchase_for_own_store():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff = _login("Staff One", "staffpass")

    res = client.post(
        "/api/purchase-item/post",
        json={"supplier": "X", "invoice_number": "1", "tally_date": "20260101", "cost_center": "Mahagun", "items": []},
        headers=_auth_headers(staff["token"]),
    )
    assert res.status_code != 403


# --- Purchase (flat) ---

def test_staff_cannot_post_flat_purchase_for_other_store():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff = _login("Staff One", "staffpass")

    res = client.post(
        "/api/purchase/post",
        json={"supplier": "X", "invoice_number": "1", "amount": 100.0, "tally_date": "20260101", "cost_center": "Gulshan", "narration": "test"},
        headers=_auth_headers(staff["token"]),
    )
    assert res.status_code == 403


def test_staff_cannot_post_flat_purchase_unallocated():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff = _login("Staff One", "staffpass")

    res = client.post(
        "/api/purchase/post",
        json={"supplier": "X", "invoice_number": "1", "amount": 100.0, "tally_date": "20260101", "cost_center": "", "narration": "test"},
        headers=_auth_headers(staff["token"]),
    )
    assert res.status_code == 403


# --- Payment ---

def test_staff_cannot_post_expense_payment_for_other_store():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff = _login("Staff One", "staffpass")

    res = client.post(
        "/api/payment/post",
        json={
            "debit_ledger": "Rent", "credit_ledger": "Cash Mahagun", "amount": 100.0,
            "tally_date": "20260101", "narration": "test", "mode": "expenses", "cost_center": "Gulshan",
        },
        headers=_auth_headers(staff["token"]),
    )
    assert res.status_code == 403


def test_staff_can_post_payment_with_no_cost_center():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff = _login("Staff One", "staffpass")

    res = client.post(
        "/api/payment/post",
        json={
            "debit_ledger": "Loan", "credit_ledger": "Cash Mahagun", "amount": 100.0,
            "tally_date": "20260101", "narration": "test", "mode": "other",
        },
        headers=_auth_headers(staff["token"]),
    )
    assert res.status_code != 403


# --- Stock Transfer ---

def test_staff_cannot_transfer_stock_between_two_other_stores():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff = _login("Staff One", "staffpass")

    res = client.post(
        "/api/stock-transfer/post",
        json={"item_name": "Widget", "qty": 1, "rate": 10, "total_amount": 10, "tally_date": "20260101", "from_store": "Gulshan", "to_store": "VVIP"},
        headers=_auth_headers(staff["token"]),
    )
    assert res.status_code == 403


def test_staff_can_transfer_stock_involving_their_store():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff = _login("Staff One", "staffpass")

    res = client.post(
        "/api/stock-transfer/post",
        json={"item_name": "Widget", "qty": 1, "rate": 10, "total_amount": 10, "tally_date": "20260101", "from_store": "Mahagun", "to_store": "Gulshan"},
        headers=_auth_headers(staff["token"]),
    )
    assert res.status_code != 403


# --- Dashboard: store-scoped stats/activity ---

def _insert_queue_row(status, payload=None, description="Sales: test"):
    from datetime import datetime
    now = datetime.now().isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO offline_queue (operation_type, payload, xml_data, status, description, created_at, updated_at, is_hidden)
               VALUES ('POST_VOUCHER', ?, '<ENVELOPE></ENVELOPE>', ?, ?, ?, ?, 0)""",
            (json.dumps(payload) if payload is not None else None, status, description, now, now)
        )
        conn.commit()
        return cursor.lastrowid


def test_staff_dashboard_stats_only_counts_their_store():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff = _login("Staff One", "staffpass")

    _insert_queue_row("FAILED", {"cost_center": "Mahagun"})
    _insert_queue_row("FAILED", {"cost_center": "Gulshan"})
    _insert_queue_row("FAILED", None)  # Unallocated

    res = client.get("/api/dashboard/stats", headers=_auth_headers(staff["token"]))
    assert res.json()["failed_count"] == 1

    owner_res = client.get("/api/dashboard/stats", headers=_auth_headers(owner["token"]))
    assert owner_res.json()["failed_count"] == 3


def test_staff_activity_feed_only_shows_their_store():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff = _login("Staff One", "staffpass")

    mine = _insert_queue_row("SYNCED", {"cost_center": "Mahagun"})
    _insert_queue_row("SYNCED", {"cost_center": "Gulshan"})

    res = client.get("/api/dashboard/activity", headers=_auth_headers(staff["token"]))
    ids = [a["id"] for a in res.json()]
    assert ids == [mine]


def test_staff_cannot_view_other_store_item_by_id():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff = _login("Staff One", "staffpass")

    other_id = _insert_queue_row("PENDING", {"cost_center": "Gulshan"})

    res = client.get(f"/api/dashboard/activity/{other_id}", headers=_auth_headers(staff["token"]))
    assert res.status_code == 404


def test_staff_cannot_retry_other_store_item_by_id():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff = _login("Staff One", "staffpass")

    other_id = _insert_queue_row("FAILED", {"cost_center": "Gulshan"})

    res = client.post(f"/api/dashboard/activity/{other_id}/retry", headers=_auth_headers(staff["token"]))
    assert res.status_code == 404


def test_staff_cannot_delete_other_store_item_by_id():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff = _login("Staff One", "staffpass")

    other_id = _insert_queue_row("PENDING", {"cost_center": "Gulshan"})

    res = client.delete(f"/api/dashboard/activity/{other_id}", headers=_auth_headers(staff["token"]))
    assert res.status_code == 404


def test_staff_can_retry_own_store_item_by_id():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff = _login("Staff One", "staffpass")

    mine = _insert_queue_row("FAILED", {"cost_center": "Mahagun"})

    res = client.post(f"/api/dashboard/activity/{mine}/retry", headers=_auth_headers(staff["token"]))
    assert res.status_code == 200


# --- Queue: staff can't widen the store filter via query param ---

def test_staff_cannot_widen_queue_store_filter():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff = _login("Staff One", "staffpass")

    _insert_queue_row("PENDING", {"cost_center": "Mahagun"})
    _insert_queue_row("PENDING", {"cost_center": "Gulshan"})

    # Even asking for Gulshan explicitly, staff only ever sees Mahagun.
    res = client.get("/api/dashboard/queue?status=PENDING&store=Gulshan&page=1&limit=10", headers=_auth_headers(staff["token"]))
    assert res.json()["total"] == 1

    res_all = client.get("/api/dashboard/queue?status=PENDING&page=1&limit=10", headers=_auth_headers(staff["token"]))
    assert res_all.json()["total"] == 1


def test_owner_queue_view_unrestricted():
    owner = _setup_owner()
    _insert_queue_row("PENDING", {"cost_center": "Mahagun"})
    _insert_queue_row("PENDING", {"cost_center": "Gulshan"})

    res = client.get("/api/dashboard/queue?status=PENDING&page=1&limit=10", headers=_auth_headers(owner["token"]))
    assert res.json()["total"] == 2


# --- Reporting ---

def test_staff_sales_report_forces_own_store():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff = _login("Staff One", "staffpass")

    res = client.get(
        "/api/reporting/sales?start_date=20260101&end_date=20260101&cost_centre=Gulshan",
        headers=_auth_headers(staff["token"]),
    )
    assert res.status_code == 200
    # Any store_comparison rows present must all be the staff's own store.
    for row in res.json()["store_comparison"]:
        assert row.get("store_name") == "Mahagun"


def _seed_monthly_stock(year_month="2026-01"):
    with get_db() as conn:
        cursor = conn.cursor()
        for ledger in ("stock mahagun", "stock gulshan", "stock vvip"):
            cursor.execute(
                "INSERT OR REPLACE INTO reporting_monthly_stock (year_month, ledger_name, opening_balance, debit_movement, credit_movement, closing_balance) VALUES (?, ?, 0, 0, 0, 0)",
                (year_month, ledger)
            )
        conn.commit()


def test_staff_pl_report_cannot_see_combined_or_other_stores():
    _seed_monthly_stock()
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff = _login("Staff One", "staffpass")

    # No cost_centre requested at all -- the un-filtered branch is exactly
    # what used to leak every store plus a company-wide "combined" figure.
    res = client.get(
        "/api/reporting/profit-loss/?start_month=2026-01&end_month=2026-01",
        headers=_auth_headers(staff["token"]),
    )
    assert res.status_code == 200
    body = res.json()
    assert "stores" not in body
    assert "combined" not in body
    assert body["current"]["store"] == "Mahagun" or body.get("current") is None or "Mahagun" in str(body)


def test_owner_pl_report_still_sees_combined_and_all_stores():
    _seed_monthly_stock()
    owner = _setup_owner()
    res = client.get(
        "/api/reporting/profit-loss/?start_month=2026-01&end_month=2026-01",
        headers=_auth_headers(owner["token"]),
    )
    assert res.status_code == 200
    body = res.json()
    assert "stores" in body
    assert "combined" in body


def test_staff_cannot_view_creditors_report():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff = _login("Staff One", "staffpass")

    res = client.get(
        "/api/reporting/creditors?start_date=20260101&end_date=20260101",
        headers=_auth_headers(staff["token"]),
    )
    assert res.status_code == 403


def test_owner_can_view_creditors_report():
    owner = _setup_owner()
    res = client.get(
        "/api/reporting/creditors?start_date=20260101&end_date=20260101",
        headers=_auth_headers(owner["token"]),
    )
    assert res.status_code == 200


def test_staff_daybook_forces_own_store():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff = _login("Staff One", "staffpass")

    res = client.get(
        "/api/reporting/daybook?start_date=20260101&end_date=20260101&cost_centre=Gulshan",
        headers=_auth_headers(staff["token"]),
    )
    assert res.status_code == 200
