import pytest
from datetime import datetime
from backend.services.reporting_creditors_service import get_creditor_ledger_movements
from backend.database import get_db

@pytest.fixture
def mock_db_data():
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Clear existing data for isolation
        cursor.execute("DELETE FROM ledgers")
        cursor.execute("DELETE FROM reporting_ledger_entries")
        cursor.execute("DELETE FROM reporting_vouchers")
        
        # Insert mock ledger with FY opening balance
        cursor.execute("INSERT INTO ledgers (name, parent, opening_balance) VALUES (?, ?, ?)",
                       ("Test Supplier", "Sundry Creditors", 5000.0))
        
        # Insert a pre-FY voucher (Should be ignored by pre_movement calculation)
        cursor.execute("INSERT INTO reporting_vouchers (id, tally_guid, date, voucher_type, voucher_number) VALUES (?, ?, ?, ?, ?)",
                       (101, 'guid-101', '20260315', 'Purchase', 'P-PRE'))
        cursor.execute("INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive) VALUES (?, ?, ?, ?)",
                       (101, "Test Supplier", 10000.0, 0))
                       
        # Insert a voucher between FY start and period start
        cursor.execute("INSERT INTO reporting_vouchers (id, tally_guid, date, voucher_type, voucher_number) VALUES (?, ?, ?, ?, ?)",
                       (102, 'guid-102', '20260515', 'Purchase', 'P-MAY'))
        cursor.execute("INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive) VALUES (?, ?, ?, ?)",
                       (102, "Test Supplier", 2000.0, 0))
                       
        # Insert a voucher DURING the period
        cursor.execute("INSERT INTO reporting_vouchers (id, tally_guid, date, voucher_type, voucher_number) VALUES (?, ?, ?, ?, ?)",
                       (103, 'guid-103', '20260715', 'Purchase', 'P-JUL'))
        cursor.execute("INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive) VALUES (?, ?, ?, ?)",
                       (103, "Test Supplier", 3000.0, 0))
        
        conn.commit()

def test_mid_year_opening_balance(mock_db_data):
    res = get_creditor_ledger_movements("Test Supplier", "2026-07-01", "2026-09-30")
    assert res['period_opening'] == 7000.0
    assert res['summary']['purchases'] == 3000.0
    assert res['period_closing'] == 10000.0

def test_fy_start_opening_balance(mock_db_data):
    res = get_creditor_ledger_movements("Test Supplier", "2026-04-01", "2026-09-30")
    assert res['period_opening'] == 5000.0
    assert res['summary']['purchases'] == 5000.0
    assert res['period_closing'] == 10000.0

def test_previous_year_protection(mock_db_data):
    res = get_creditor_ledger_movements("Test Supplier", "2026-04-01", "2026-04-30")
    assert res['period_opening'] == 5000.0

def test_closing_balance_formula(mock_db_data):
    res = get_creditor_ledger_movements("Test Supplier", "2026-07-01", "2026-07-31")
    assert res['period_closing'] == res['period_opening'] + res['summary']['net_movement']
