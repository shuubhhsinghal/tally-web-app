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
        conn.execute("DELETE FROM repack_operations")
        conn.commit()
    yield


def _insert_rows(status, count, is_hidden=0):
    now = datetime.now().isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        for _ in range(count):
            cursor.execute(
                """INSERT INTO offline_queue (operation_type, payload, xml_data, status, description, created_at, updated_at, is_hidden)
                   VALUES ('POST_VOUCHER', NULL, '<ENVELOPE></ENVELOPE>', ?, 'Test item', ?, ?, ?)""",
                (status, now, now, is_hidden)
            )
        conn.commit()


def _insert_row(operation_type, status, payload):
    now = datetime.now().isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO offline_queue (operation_type, payload, xml_data, status, description, created_at, updated_at, is_hidden)
               VALUES (?, ?, '<ENVELOPE></ENVELOPE>', ?, 'Test item', ?, ?, 0)""",
            (operation_type, json.dumps(payload), status, now, now)
        )
        conn.commit()
        return cursor.lastrowid


def _insert_typed_row(operation_type, status, description, payload=None):
    now = datetime.now().isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO offline_queue (operation_type, payload, xml_data, status, description, created_at, updated_at, is_hidden)
               VALUES (?, ?, '<ENVELOPE></ENVELOPE>', ?, ?, ?, ?, 0)""",
            (operation_type, json.dumps(payload) if payload is not None else None, status, description, now, now)
        )
        conn.commit()
        return cursor.lastrowid


def _insert_repack_row(status, store_name):
    repack_id = f"repack-{store_name}-{status}"
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO repack_operations (id, store_name, source_item_name, dest_item_name,
                   source_qty, source_rate, source_amount, dest_qty, conversion_id)
               VALUES (?, ?, 'Source Item', 'Dest Item', 1, 1, 1, 1, 1)""",
            (repack_id, store_name)
        )
        conn.commit()
    return _insert_row("REPACK_VOUCHER", status, {"repack_id": repack_id})


def test_queue_page_returns_correct_slice():
    _insert_rows("PENDING", 15)

    res = client.get("/api/dashboard/queue?status=PENDING&page=1&limit=10")
    data = res.json()
    assert res.status_code == 200
    assert len(data["items"]) == 10
    assert data["total"] == 15

    res2 = client.get("/api/dashboard/queue?status=PENDING&page=2&limit=10")
    data2 = res2.json()
    assert len(data2["items"]) == 5
    assert data2["total"] == 15


def test_queue_page_caps_total_at_200():
    _insert_rows("FAILED", 205)

    res = client.get("/api/dashboard/queue?status=FAILED&page=1&limit=10")

    assert res.json()["total"] == 200


def test_queue_page_returns_empty_beyond_cap():
    _insert_rows("FAILED", 250)

    res = client.get("/api/dashboard/queue?status=FAILED&page=25&limit=10")
    data = res.json()

    assert data["items"] == []
    assert data["total"] == 200


def test_queue_page_includes_hidden_items():
    _insert_rows("FAILED", 3, is_hidden=1)

    res = client.get("/api/dashboard/queue?status=FAILED&page=1&limit=10")
    data = res.json()

    assert data["total"] == 3
    assert len(data["items"]) == 3


def test_queue_page_rejects_invalid_status():
    res = client.get("/api/dashboard/queue?status=BOGUS&page=1&limit=10")
    assert res.status_code == 400


def test_queue_page_requires_status():
    res = client.get("/api/dashboard/queue?page=1&limit=10")
    assert res.status_code == 422


def test_queue_page_orders_newest_first():
    _insert_rows("SYNCED", 3)

    res = client.get("/api/dashboard/queue?status=SYNCED&page=1&limit=10")
    ids = [item["id"] for item in res.json()["items"]]

    assert ids == sorted(ids, reverse=True)


# --- Store filtering ---

def test_store_filter_matches_purchase_cost_center():
    mahagun_id = _insert_row("POST_VOUCHER", "PENDING", {"cost_center": "Mahagun"})
    _insert_row("POST_VOUCHER", "PENDING", {"cost_center": "Gulshan"})

    res = client.get("/api/dashboard/queue?status=PENDING&store=Mahagun&page=1&limit=10")
    data = res.json()

    assert data["total"] == 1
    assert data["items"][0]["id"] == mahagun_id


def test_store_filter_stock_transfer_shows_under_both_stores():
    transfer_id = _insert_row("POST_VOUCHER", "PENDING", {"from_store": "Mahagun", "to_store": "Gulshan"})

    res_from = client.get("/api/dashboard/queue?status=PENDING&store=Mahagun&page=1&limit=10")
    res_to = client.get("/api/dashboard/queue?status=PENDING&store=Gulshan&page=1&limit=10")

    assert [i["id"] for i in res_from.json()["items"]] == [transfer_id]
    assert [i["id"] for i in res_to.json()["items"]] == [transfer_id]


def test_store_filter_unallocated_includes_storeless_rows():
    sales_id = _insert_row("POST_VOUCHER", "PENDING", {"ledger": "Cash", "amount": 100})
    ledger_id = _insert_row("CREATE_LEDGER", "PENDING", {"name": "Test Ledger"})
    _insert_row("POST_VOUCHER", "PENDING", {"cost_center": "Mahagun"})

    res = client.get("/api/dashboard/queue?status=PENDING&store=Unallocated&page=1&limit=10")
    ids = {i["id"] for i in res.json()["items"]}

    assert ids == {sales_id, ledger_id}
    assert res.json()["total"] == 2


def test_store_filter_matches_repack_via_join():
    repack_id = _insert_repack_row("PENDING", "VVIP")
    _insert_repack_row("PENDING", "Mahagun")

    res = client.get("/api/dashboard/queue?status=PENDING&store=VVIP&page=1&limit=10")
    data = res.json()

    assert data["total"] == 1
    assert data["items"][0]["id"] == repack_id


def test_store_filter_pagination_reflects_filtered_total():
    for _ in range(5):
        _insert_row("POST_VOUCHER", "PENDING", {"cost_center": "Mahagun"})
    for _ in range(3):
        _insert_row("POST_VOUCHER", "PENDING", {"cost_center": "Gulshan"})

    res = client.get("/api/dashboard/queue?status=PENDING&store=Mahagun&page=1&limit=10")
    data = res.json()

    assert data["total"] == 5
    assert len(data["items"]) == 5


def test_no_store_param_returns_all_stores():
    _insert_row("POST_VOUCHER", "PENDING", {"cost_center": "Mahagun"})
    _insert_row("POST_VOUCHER", "PENDING", {"ledger": "Cash", "amount": 50})

    res = client.get("/api/dashboard/queue?status=PENDING&page=1&limit=10")

    assert res.json()["total"] == 2


# --- Transaction type filtering ---

def test_type_filter_matches_by_description_prefix():
    sales_id = _insert_typed_row("POST_VOUCHER", "PENDING", "Sales: 100.0 from Cash")
    _insert_typed_row("POST_VOUCHER", "PENDING", "Payment: 100.0 to Cash")

    res = client.get("/api/dashboard/queue?status=PENDING&type=SALES&page=1&limit=10")
    data = res.json()

    assert data["total"] == 1
    assert data["items"][0]["id"] == sales_id


def test_type_filter_purchase_invoice_excludes_purchase_item_invoice():
    purchase_id = _insert_typed_row("POST_VOUCHER", "PENDING", "Purchase Invoice: INV-1 from Supplier A")
    _insert_typed_row("POST_VOUCHER", "PENDING", "Purchase Item Invoice: INV-2 from Supplier B")

    res = client.get("/api/dashboard/queue?status=PENDING&type=PURCHASE&page=1&limit=10")
    data = res.json()

    assert data["total"] == 1
    assert data["items"][0]["id"] == purchase_id


def test_type_filter_purchase_item_groups_returns_with_invoices():
    invoice_id = _insert_typed_row("POST_VOUCHER", "PENDING", "Purchase Item Invoice: INV-3 from Supplier C")
    return_id = _insert_typed_row("POST_VOUCHER", "PENDING", "Purchase Return (Adjustment): INV-3 from Supplier C")

    res = client.get("/api/dashboard/queue?status=PENDING&type=PURCHASE_ITEM&page=1&limit=10")
    ids = {i["id"] for i in res.json()["items"]}

    assert ids == {invoice_id, return_id}


def test_type_filter_repack_matches_by_operation_type_not_description():
    repack_id = _insert_typed_row("REPACK_VOUCHER", "PENDING", "Repack: 5.0 PCS of Widget at Mahagun")

    res = client.get("/api/dashboard/queue?status=PENDING&type=REPACK&page=1&limit=10")
    data = res.json()

    assert data["total"] == 1
    assert data["items"][0]["id"] == repack_id


def test_type_filter_excludes_master_creates_from_specific_types():
    _insert_typed_row("CREATE_LEDGER", "PENDING", "Create Ledger: Test Ledger")

    res = client.get("/api/dashboard/queue?status=PENDING&type=SALES&page=1&limit=10")
    assert res.json()["total"] == 0

    # But it still shows up with no type filter at all.
    res_all = client.get("/api/dashboard/queue?status=PENDING&page=1&limit=10")
    assert res_all.json()["total"] == 1


def test_type_filter_rejects_invalid_value():
    res = client.get("/api/dashboard/queue?status=PENDING&type=BOGUS&page=1&limit=10")
    assert res.status_code == 400


def test_status_store_type_combine_with_and():
    # Matches status + type but NOT store -> should not appear when all three are filtered.
    _insert_row("POST_VOUCHER", "PENDING", {"cost_center": "Gulshan"})
    with get_db() as conn:
        conn.execute("UPDATE offline_queue SET description = ? WHERE id = (SELECT MAX(id) FROM offline_queue)", ("Sales: 1 from Cash",))
        conn.commit()

    res = client.get("/api/dashboard/queue?status=PENDING&store=Mahagun&type=SALES&page=1&limit=10")
    assert res.json()["total"] == 0
