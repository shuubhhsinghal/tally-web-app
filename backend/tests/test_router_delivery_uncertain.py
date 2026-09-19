import os
import json
import pytest
import requests
from unittest.mock import patch, MagicMock

os.environ["TESTING"] = "true"

from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db, init_db

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        conn.execute("DELETE FROM pending_masters")
        conn.execute("INSERT OR IGNORE INTO ledgers (name, parent, cost_centre) VALUES ('Test Supplier', 'Sundry Creditors', 0)")
        conn.execute("INSERT OR IGNORE INTO stock_items (name, unit) VALUES ('Item A', 'PCS')")
        conn.execute("INSERT OR IGNORE INTO uoms (name) VALUES ('PCS')")
        conn.commit()
    yield


def _latest_row(description_like):
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


# --- Sales ---

@patch('requests.post')
def test_sales_timeout_sets_delivery_uncertain(mock_post):
    mock_post.side_effect = requests.exceptions.Timeout()
    res = client.post("/api/sales/post", json={
        "ledger": "Cash Mahagun", "amount": 100.0, "tally_date": "20260101", "narration": "test"
    })
    assert res.status_code == 200
    row = _latest_row("Sales:%")
    assert row["status"] == "PENDING"
    assert _delivery_uncertain(row) is True


@patch('requests.post')
def test_sales_connection_error_clears_delivery_uncertain(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectionError()
    res = client.post("/api/sales/post", json={
        "ledger": "Cash Mahagun", "amount": 100.0, "tally_date": "20260101", "narration": "test"
    })
    assert res.status_code == 200
    row = _latest_row("Sales:%")
    assert row["status"] == "PENDING"
    assert not _delivery_uncertain(row)


@patch('requests.post')
def test_sales_connect_timeout_clears_delivery_uncertain(mock_post):
    # ConnectTimeout means the connection itself never established (Tally
    # unreachable) -- as safe as ConnectionError, must NOT be treated as ambiguous.
    mock_post.side_effect = requests.exceptions.ConnectTimeout()
    res = client.post("/api/sales/post", json={
        "ledger": "Cash Mahagun", "amount": 100.0, "tally_date": "20260101", "narration": "test"
    })
    assert res.status_code == 200
    row = _latest_row("Sales:%")
    assert row["status"] == "PENDING"
    assert not _delivery_uncertain(row)


# --- Purchase ---

@patch('requests.post')
def test_purchase_timeout_sets_delivery_uncertain(mock_post):
    mock_post.side_effect = requests.exceptions.Timeout()
    res = client.post("/api/purchase/post", json={
        "supplier": "Test Supplier", "invoice_number": "INV-1", "amount": 100.0,
        "tally_date": "20260101", "cost_center": "Mahagun", "narration": "test"
    })
    assert res.status_code == 200
    row = _latest_row("Purchase Invoice:%")
    assert row["status"] == "PENDING"
    assert _delivery_uncertain(row) is True


@patch('requests.post')
def test_purchase_connection_error_clears_delivery_uncertain(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectionError()
    res = client.post("/api/purchase/post", json={
        "supplier": "Test Supplier", "invoice_number": "INV-2", "amount": 100.0,
        "tally_date": "20260101", "cost_center": "Mahagun", "narration": "test"
    })
    assert res.status_code == 200
    row = _latest_row("Purchase Invoice:%")
    assert row["status"] == "PENDING"
    assert not _delivery_uncertain(row)


@patch('requests.post')
def test_purchase_connect_timeout_clears_delivery_uncertain(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectTimeout()
    res = client.post("/api/purchase/post", json={
        "supplier": "Test Supplier", "invoice_number": "INV-2b", "amount": 100.0,
        "tally_date": "20260101", "cost_center": "Mahagun", "narration": "test"
    })
    assert res.status_code == 200
    row = _latest_row("Purchase Invoice:%INV-2b%")
    assert row["status"] == "PENDING"
    assert not _delivery_uncertain(row)


# --- Payment ---

@patch('requests.post')
def test_payment_timeout_sets_delivery_uncertain(mock_post):
    mock_post.side_effect = requests.exceptions.Timeout()
    res = client.post("/api/payment/post", json={
        "debit_ledger": "Rent", "credit_ledger": "Cash Mahagun", "amount": 100.0,
        "tally_date": "20260101", "narration": "test"
    })
    assert res.status_code == 200
    row = _latest_row("Payment:%")
    assert row["status"] == "PENDING"
    assert _delivery_uncertain(row) is True


@patch('requests.post')
def test_payment_connection_error_clears_delivery_uncertain(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectionError()
    res = client.post("/api/payment/post", json={
        "debit_ledger": "Rent", "credit_ledger": "Cash Mahagun", "amount": 100.0,
        "tally_date": "20260101", "narration": "test"
    })
    assert res.status_code == 200
    row = _latest_row("Payment:%")
    assert row["status"] == "PENDING"
    assert not _delivery_uncertain(row)


@patch('requests.post')
def test_payment_connect_timeout_clears_delivery_uncertain(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectTimeout()
    res = client.post("/api/payment/post", json={
        "debit_ledger": "Rent", "credit_ledger": "Cash Mahagun", "amount": 100.0,
        "tally_date": "20260101", "narration": "test"
    })
    assert res.status_code == 200
    row = _latest_row("Payment:%")
    assert row["status"] == "PENDING"
    assert not _delivery_uncertain(row)


# --- Transfer ---

@patch('requests.post')
def test_transfer_timeout_sets_delivery_uncertain(mock_post):
    mock_post.side_effect = requests.exceptions.Timeout()
    res = client.post("/api/transfer/post", json={
        "amount": 100.0, "from_account": "Cash Mahagun", "to_account": "Cash Gulshan",
        "tally_date": "20260101", "narration": "test"
    })
    assert res.status_code == 200
    row = _latest_row("Transfer:%")
    assert row["status"] == "PENDING"
    assert _delivery_uncertain(row) is True


@patch('requests.post')
def test_transfer_connection_error_clears_delivery_uncertain(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectionError()
    res = client.post("/api/transfer/post", json={
        "amount": 100.0, "from_account": "Cash Mahagun", "to_account": "Cash Gulshan",
        "tally_date": "20260101", "narration": "test"
    })
    assert res.status_code == 200
    row = _latest_row("Transfer:%")
    assert row["status"] == "PENDING"
    assert not _delivery_uncertain(row)


@patch('requests.post')
def test_transfer_connect_timeout_clears_delivery_uncertain(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectTimeout()
    res = client.post("/api/transfer/post", json={
        "amount": 100.0, "from_account": "Cash Mahagun", "to_account": "Cash Gulshan",
        "tally_date": "20260101", "narration": "test"
    })
    assert res.status_code == 200
    row = _latest_row("Transfer:%")
    assert row["status"] == "PENDING"
    assert not _delivery_uncertain(row)


# --- Stock Transfer ---

@patch('requests.post')
def test_stock_transfer_timeout_sets_delivery_uncertain(mock_post):
    mock_post.side_effect = requests.exceptions.Timeout()
    res = client.post("/api/stock-transfer/post", json={
        "item_name": "Item A", "qty": 5, "rate": 10, "total_amount": 50,
        "from_store": "Mahagun", "to_store": "Gulshan", "tally_date": "20260101"
    })
    assert res.status_code == 200
    row = _latest_row("Stock Transfer:%")
    assert row["status"] == "PENDING"
    assert _delivery_uncertain(row) is True


@patch('requests.post')
def test_stock_transfer_connection_error_clears_delivery_uncertain(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectionError()
    res = client.post("/api/stock-transfer/post", json={
        "item_name": "Item A", "qty": 5, "rate": 10, "total_amount": 50,
        "from_store": "Mahagun", "to_store": "Gulshan", "tally_date": "20260101"
    })
    assert res.status_code == 200
    row = _latest_row("Stock Transfer:%")
    assert row["status"] == "PENDING"
    assert not _delivery_uncertain(row)


@patch('requests.post')
def test_stock_transfer_connect_timeout_clears_delivery_uncertain(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectTimeout()
    res = client.post("/api/stock-transfer/post", json={
        "item_name": "Item A", "qty": 5, "rate": 10, "total_amount": 50,
        "from_store": "Mahagun", "to_store": "Gulshan", "tally_date": "20260101"
    })
    assert res.status_code == 200
    row = _latest_row("Stock Transfer:%")
    assert row["status"] == "PENDING"
    assert not _delivery_uncertain(row)


# --- Purchase Item (main invoice site) ---

def _purchase_item_payload(invoice_number):
    return {
        "supplier": "Test Supplier",
        "invoice_number": invoice_number,
        "tally_date": "2026-01-01",
        "cost_center": "Mahagun",
        "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "rounding_off": 0.0,
        "items": [{
            "name": "Item A", "qty": 1.0, "uom": "PCS", "rate": 10.0, "discount": 0.0,
            "amount": 10.0, "mapped_name": "Item A", "mapped_unit": "PCS", "is_mapped": True
        }],
        "adjustment": None
    }


@patch('requests.post')
def test_purchase_item_timeout_sets_delivery_uncertain(mock_post):
    mock_post.side_effect = requests.exceptions.Timeout()
    res = client.post("/api/purchase-item/post", json=_purchase_item_payload("PI-1"))
    assert res.status_code == 200
    row = _latest_row("Purchase Item Invoice:%PI-1%")
    assert row["status"] == "PENDING"
    assert _delivery_uncertain(row) is True


@patch('requests.post')
def test_purchase_item_connection_error_clears_delivery_uncertain(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectionError()
    res = client.post("/api/purchase-item/post", json=_purchase_item_payload("PI-2"))
    assert res.status_code == 200
    row = _latest_row("Purchase Item Invoice:%PI-2%")
    assert row["status"] == "PENDING"
    assert not _delivery_uncertain(row)


@patch('requests.post')
def test_purchase_item_connect_timeout_clears_delivery_uncertain(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectTimeout()
    res = client.post("/api/purchase-item/post", json=_purchase_item_payload("PI-2c"))
    assert res.status_code == 200
    row = _latest_row("Purchase Item Invoice:%PI-2c%")
    assert row["status"] == "PENDING"
    assert not _delivery_uncertain(row)


# --- Bank Statement (batch) ---

@patch('requests.post')
def test_bank_statement_timeout_flags_every_row_in_batch(mock_post):
    mock_post.side_effect = requests.exceptions.Timeout()
    res = client.post("/api/bank-statement/post", json={
        "transactions": [
            {"ledger": "Cash", "withdraw": 100, "deposit": 0, "date": "20260101", "raw_narration": "t1"},
            {"ledger": "Rent", "withdraw": 200, "deposit": 0, "date": "20260102", "raw_narration": "t2"},
        ],
        "bank_ledger_name": "HDFC Bank"
    })
    assert res.status_code == 200
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM offline_queue WHERE description LIKE 'Bank Stmt:%' ORDER BY id DESC LIMIT 2")
        rows = [dict(r) for r in cursor.fetchall()]
    assert len(rows) == 2
    for row in rows:
        assert row["status"] == "PENDING"
        assert _delivery_uncertain(row) is True


@patch('requests.post')
def test_bank_statement_connection_error_clears_every_row_in_batch(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectionError()
    res = client.post("/api/bank-statement/post", json={
        "transactions": [
            {"ledger": "Cash", "withdraw": 100, "deposit": 0, "date": "20260103", "raw_narration": "t3"},
        ],
        "bank_ledger_name": "HDFC Bank"
    })
    assert res.status_code == 200
    row = _latest_row("Bank Stmt:%")
    assert row["status"] == "PENDING"
    assert not _delivery_uncertain(row)


@patch('requests.post')
def test_bank_statement_connect_timeout_clears_every_row_in_batch(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectTimeout()
    res = client.post("/api/bank-statement/post", json={
        "transactions": [
            {"ledger": "Cash", "withdraw": 100, "deposit": 0, "date": "20260104", "raw_narration": "t4"},
        ],
        "bank_ledger_name": "HDFC Bank"
    })
    assert res.status_code == 200
    row = _latest_row("Bank Stmt:%")
    assert row["status"] == "PENDING"
    assert not _delivery_uncertain(row)
