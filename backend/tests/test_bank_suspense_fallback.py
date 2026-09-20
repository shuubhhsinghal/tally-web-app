import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db
import pandas as pd
import tempfile

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    with get_db() as conn:
        conn.execute("DELETE FROM bank_mappings")
        conn.execute("DELETE FROM ledgers")
        
        # Add basic ledgers
        conn.execute("INSERT INTO ledgers (name, parent, cost_centre) VALUES ('Bank Suspense Account', 'Suspense Accounts', 0)")
        conn.execute("INSERT INTO ledgers (name, parent, cost_centre) VALUES ('Expenses', 'Indirect Expenses', 0)")
        conn.execute("INSERT INTO ledgers (name, parent, cost_centre) VALUES ('HDFC Bank', 'Bank Accounts', 0)")
        
        conn.commit()
    yield
    with get_db() as conn:
        conn.execute("DELETE FROM bank_mappings")
        conn.execute("DELETE FROM ledgers")
        conn.commit()

def test_add_edit_rule_rejects_ignore():
    payload = {
        "bank": "ALL",
        "keyword": "SOME VENDOR",
        "target_ledger": "IGNORE"
    }
    resp = client.post("/api/bank-statement/mappings", json=payload)
    assert resp.status_code == 400
    
    payload["target_ledger"] = "IGNORE (Do not push)"
    resp = client.post("/api/bank-statement/mappings", json=payload)
    assert resp.status_code == 400
    
    payload["target_ledger"] = "Expenses"
    resp = client.post("/api/bank-statement/mappings", json=payload)
    assert resp.status_code == 200
    mapping_id = resp.json()["id"]
    
    update_payload = {
        "bank": "ALL",
        "keyword": "SOME VENDOR",
        "target_ledger": "IGNORE"
    }
    resp_update = client.put(f"/api/bank-statement/mappings/{mapping_id}", json=update_payload)
    assert resp_update.status_code == 400

def test_analyze_rule_match_and_fallback():
    with get_db() as conn:
        conn.execute("INSERT INTO bank_mappings (bank_account_name, keyword, target_ledger) VALUES ('ALL', 'AMAZON', 'Expenses')")
        conn.execute("INSERT INTO bank_mappings (bank_account_name, keyword, target_ledger) VALUES ('ALL', 'OLD IGNORE', 'IGNORE')")
        conn.execute("INSERT INTO bank_mappings (bank_account_name, keyword, target_ledger) VALUES ('ALL', 'OLD IGNORE 2', 'IGNORE (Do not push)')")
        conn.commit()
        
    df = pd.DataFrame([
        {"Date": "2024-01-01", "Narration": "AMAZON SELLER", "Withdrawal": 100, "Deposit": 0},
        {"Date": "2024-01-01", "Narration": "UNKNOWN PAYMENT", "Withdrawal": 100, "Deposit": 0},
        {"Date": "2024-01-01", "Narration": "OLD IGNORE PAYMENT", "Withdrawal": 100, "Deposit": 0},
        {"Date": "2024-01-01", "Narration": "OLD IGNORE 2 PAYMENT", "Withdrawal": 100, "Deposit": 0}
    ])
    
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as f:
        df.to_excel(f.name, index=False)
        f.seek(0)
        resp = client.post(
            "/api/bank-statement/upload",
            data={"bank_ledger_name": "HDFC Bank"},
            files={"file": ("test.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        )
        
    assert resp.status_code == 200
    
    txns = resp.json()["transactions"]
    
    # A. RULE MATCH
    assert txns[0]["ledger"] == "Expenses"
    
    # B. NO RULE
    assert txns[1]["ledger"] == "Bank Suspense Account"
    
    # C. OLD IGNORE RULE
    assert txns[2]["ledger"] == "Bank Suspense Account"
    
    # D. OLD "IGNORE (Do not push)"
    assert txns[3]["ledger"] == "Bank Suspense Account"

def test_bank_suspense_missing():
    with get_db() as conn:
        conn.execute("DELETE FROM ledgers WHERE name = 'Bank Suspense Account'")
        conn.commit()
        
    df = pd.DataFrame([
        {"Date": "2024-01-01", "Narration": "UNKNOWN", "Withdrawal": 100, "Deposit": 0}
    ])
    
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as f:
        df.to_excel(f.name, index=False)
        f.seek(0)
        resp = client.post(
            "/api/bank-statement/upload",
            data={"bank_ledger_name": "HDFC Bank"},
            files={"file": ("test.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        )
    
    assert resp.status_code == 400
    assert "Bank Suspense Account" in resp.json()["detail"]

@patch('backend.routers.bank_statement.tally_transport.post', new_callable=AsyncMock)
def test_posting_fallback_transactions(mock_post):
    payload = {
        "bank_ledger_name": "HDFC Bank",
        "transactions": [
            {
                "date": "20240101",
                "raw_narration": "UNKNOWN",
                "withdraw": 100,
                "deposit": 0,
                "ledger": "Bank Suspense Account"
            }
        ]
    }
    
    class MockResp:
        text = "<SUCCESS/>"
    mock_post.return_value = MockResp()
    
    resp = client.post("/api/bank-statement/post", json=payload)
    assert resp.status_code == 200
    
    mock_post.assert_called_once()
    called_xml = mock_post.call_args[0][0].decode('utf-8')
    assert "Bank Suspense Account" in called_xml
