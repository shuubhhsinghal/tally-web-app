import os
import json
import pytest
from datetime import datetime

os.environ["TESTING"] = "true"

from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        conn.execute("DELETE FROM reporting_cost_centre_allocations")
        conn.execute("DELETE FROM reporting_ledger_entries")
        conn.execute("DELETE FROM reporting_vouchers")
        conn.execute("DELETE FROM ledgers")
        conn.commit()
    yield


def _today():
    return datetime.now().strftime("%Y%m%d")


def _insert_confirmed_sale(amount, date=None):
    date = date or _today()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO ledgers (name, parent) VALUES ('Sales', 'Sales Accounts')")
        cursor.execute(
            "INSERT INTO reporting_vouchers (tally_guid, date, voucher_type) VALUES (?, ?, 'Sales')",
            (f"guid-{date}-{amount}", date)
        )
        voucher_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive) VALUES (?, 'Sales', ?, 0)",
            (voucher_id, amount)
        )
        conn.commit()


def _insert_queued_sale(amount, status, date=None):
    date = date or _today()
    payload = json.dumps({"tally_date": date, "amount": amount, "ledger": "Cash Mahagun"})
    now = datetime.now().isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO offline_queue (operation_type, payload, xml_data, status, description, created_at, updated_at, is_hidden)
               VALUES ('POST_VOUCHER', ?, '<ENVELOPE></ENVELOPE>', ?, 'Sales: test', ?, ?, 0)""",
            (payload, status, now, now)
        )
        conn.commit()


def test_today_sales_combines_confirmed_and_pending():
    _insert_confirmed_sale(500)
    _insert_queued_sale(200, "PENDING")

    res = client.get("/api/dashboard/stats")
    data = res.json()

    assert data["today_sales"] == 700.0
    assert data["today_sales_pending_count"] == 1


def test_today_sales_excludes_failed_queue_entries():
    _insert_confirmed_sale(500)
    _insert_queued_sale(200, "FAILED")

    res = client.get("/api/dashboard/stats")
    data = res.json()

    assert data["today_sales"] == 500.0
    assert data["today_sales_pending_count"] == 0


def test_today_sales_excludes_other_dates():
    _insert_confirmed_sale(500, date="20200101")
    _insert_queued_sale(200, "PENDING", date="20200101")

    res = client.get("/api/dashboard/stats")
    data = res.json()

    assert data["today_sales"] == 0.0
    assert data["today_sales_pending_count"] == 0


def test_today_sales_zero_with_no_data():
    res = client.get("/api/dashboard/stats")
    data = res.json()

    assert data["today_sales"] == 0.0
    assert data["today_sales_pending_count"] == 0
