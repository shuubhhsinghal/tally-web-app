import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db
import json

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    with get_db() as conn:
        conn.execute("DELETE FROM ledgers")
        conn.execute("DELETE FROM pending_masters")
        
        ledgers = [
            # A. EXPENSE COST CENTRE LEDGER
            ("Electricity Expense", "Indirect Expenses", 1),
            # B. COST CENTRE DISABLED
            ("Misc Expense", "Indirect Expenses", 0),
            # C. PURCHASE ACCOUNT
            ("Purchase 5%", "Purchase Accounts", 1),
            # D. SALES ACCOUNT
            ("Sales 5%", "Sales Accounts", 0),
            # E. DRAWINGS
            ("Drawings", "Capital Account", 0),
            # F. SUNDRY CREDITOR
            ("Supplier ABC", "Sundry Creditors", 0),
            # G. LOAN
            ("Director Loan", "Loans", 0),
            # H. DUTIES & TAXES
            ("GST Payable", "Duties & Taxes", 0),
            # I. BANK
            ("Federal Bank Gulshan", "Bank Accounts", 0),
            # J. BANK WITH COST CENTRE ENABLED
            ("Bank Ledger", "Bank Accounts", 1),
            # K. CASH
            ("Cash", "Cash-in-Hand", 0),
        ]
        
        for name, parent, cc in ledgers:
            conn.execute(
                "INSERT INTO ledgers (name, parent, cost_centre) VALUES (?, ?, ?)",
                (name, parent, cc)
            )
            
        conn.commit()
    yield
    with get_db() as conn:
        conn.execute("DELETE FROM ledgers")
        conn.execute("DELETE FROM pending_masters")
        conn.commit()

def test_metadata_categorization():
    resp = client.get("/api/payment/metadata")
    assert resp.status_code == 200
    data = resp.json()
    
    expense_paid_to = [l['name'] for l in data['expense_paid_to']]
    party_paid_to = [l['name'] for l in data['party_paid_to']]
    paid_from = [l['name'] for l in data['paid_from']]
    
    # A
    assert "Electricity Expense" in expense_paid_to
    
    # B
    assert "Misc Expense" not in expense_paid_to
    
    # C
    assert "Purchase 5%" not in expense_paid_to
    assert "Purchase 5%" not in party_paid_to
    
    # D
    assert "Sales 5%" not in expense_paid_to
    assert "Sales 5%" not in party_paid_to
    
    # E
    assert "Drawings" not in expense_paid_to
    assert "Drawings" in party_paid_to
    
    # F
    assert "Supplier Abc" in party_paid_to
    
    # G
    assert "Director Loan" in party_paid_to
    
    # H
    assert "Gst Payable" in party_paid_to
    
    # I
    assert "Federal Bank Gulshan" not in expense_paid_to
    assert "Federal Bank Gulshan" not in party_paid_to
    assert "Federal Bank Gulshan" in paid_from
    
    # J
    assert "Bank Ledger" not in expense_paid_to
    assert "Bank Ledger" not in party_paid_to
    assert "Bank Ledger" in paid_from
    
    # K
    assert "Cash" in paid_from

def test_create_account_queue_first():
    payload = {
        "name": "Drawings Test",
        "parent": "Capital Account"
    }
    resp = client.post("/api/payment/create-ledger", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM pending_masters WHERE entity_type = 'LEDGER' AND normalized_name = 'drawings test'")
        pending = cursor.fetchone()
        assert pending is not None
        
        cursor.execute("SELECT * FROM offline_queue WHERE id = ?", (pending['queue_id'],))
        queue_row = cursor.fetchone()
        assert queue_row is not None
        assert queue_row['operation_type'] == "CREATE_LEDGER"

def test_create_account_duplicate_protection():
    payload = {
        "name": "Drawings",
        "parent": "Capital Account"
    }
    resp = client.post("/api/payment/create-ledger", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert "already exists" in resp.json()["message"]
    
    # Create a pending one
    client.post("/api/payment/create-ledger", json={"name": "Pending Ledger", "parent": "Indirect Expenses"})
    # Do it again
    resp2 = client.post("/api/payment/create-ledger", json={"name": "Pending Ledger", "parent": "Indirect Expenses"})
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "success"
    assert "already exists" in resp2.json()["message"]

