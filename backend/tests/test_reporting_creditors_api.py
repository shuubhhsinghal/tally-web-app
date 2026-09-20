import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_teardown():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM reporting_ledger_entries")
        c.execute("DELETE FROM reporting_vouchers")
        c.execute("DELETE FROM ledgers")
        
        c.execute("INSERT INTO ledgers (name, parent, opening_balance) VALUES ('Supplier API', 'Sundry Creditors', 1000.0)")
        
        c.execute("INSERT INTO reporting_vouchers (id, tally_guid, date, voucher_number, voucher_type) VALUES (1, 'guid-1', '20260710', 'P1', 'Purchase')")
        c.execute("INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive) VALUES (1, 'Supplier API', 500.0, 0)")
        conn.commit()
    yield
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM reporting_ledger_entries")
        c.execute("DELETE FROM reporting_vouchers")
        c.execute("DELETE FROM ledgers")
        conn.commit()

def test_get_all_creditors_endpoint():
    response = client.get("/api/reporting/creditors?start_date=2026-07-01&end_date=2026-07-31")
    assert response.status_code == 200
    data = response.json()
    assert "is_data_complete" in data
    creditors = data['creditors']
    assert len(creditors) == 1
    assert creditors[0]['supplier_name'] == 'Supplier API'
    assert creditors[0]['period_opening'] == 1000.0
    assert creditors[0]['purchases'] == 500.0
    assert creditors[0]['period_closing'] == 1500.0

def test_get_creditor_ledger_endpoint():
    response = client.get("/api/reporting/creditors/Supplier API?start_date=2026-07-01&end_date=2026-07-31")
    assert response.status_code == 200
    data = response.json()
    assert data['period_opening'] == 1000.0
    assert data['period_closing'] == 1500.0
    assert len(data['movements']) == 1
    assert data['movements'][0]['voucher_type'] == 'Purchase'
