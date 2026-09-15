import pytest
import sqlite3
from backend.database import get_db
from backend.services.reporting_creditors_service import get_creditor_ledger_movements

@pytest.fixture(autouse=True)
def setup_teardown():
    # Setup test data
    with get_db() as conn:
        c = conn.cursor()
        
        # Clear existing data for isolation
        c.execute("DELETE FROM reporting_ledger_entries")
        c.execute("DELETE FROM reporting_vouchers")
        c.execute("DELETE FROM ledgers")
        
        # Insert Ledgers (including Master Opening Balance)
        # Positive opening balance = Credit balance (Owed to supplier)
        c.execute("INSERT INTO ledgers (name, parent, opening_balance) VALUES ('Supplier A', 'Sundry Creditors', 1000.0)")
        
        # Insert Vouchers and Ledger Entries
        
        # 1. Pre-period Purchase (Date: 2026-06-15) -> Increases balance by 500
        c.execute("INSERT INTO reporting_vouchers (id, tally_guid, date, voucher_number, voucher_type) VALUES (1, 'guid-1', '20260615', 'P1', 'Purchase')")
        c.execute("INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive) VALUES (1, 'Supplier A', 500.0, 0)") # Credit
        
        # 2. In-period Purchase (Date: 2026-07-05) -> Increases balance by 2000
        c.execute("INSERT INTO reporting_vouchers (id, tally_guid, date, voucher_number, voucher_type) VALUES (2, 'guid-2', '20260705', 'P2', 'Purchase')")
        c.execute("INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive) VALUES (2, 'Supplier A', 2000.0, 0)") # Credit
        
        # 3. In-period Payment (Date: 2026-07-10) -> Decreases balance by 1200
        c.execute("INSERT INTO reporting_vouchers (id, tally_guid, date, voucher_number, voucher_type) VALUES (3, 'guid-3', '20260710', 'PAY1', 'Payment')")
        c.execute("INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive) VALUES (3, 'Supplier A', -1200.0, 1)") # Debit
        
        # 4. In-period Debit Note / Return (Date: 2026-07-15) -> Decreases balance by 300
        c.execute("INSERT INTO reporting_vouchers (id, tally_guid, date, voucher_number, voucher_type) VALUES (4, 'guid-4', '20260715', 'DN1', 'Debit Note')")
        c.execute("INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive) VALUES (4, 'Supplier A', -300.0, 1)") # Debit
        
        # 5. In-period Journal Adjustment (Date: 2026-07-20) -> Increases balance by 100 (e.g. interest)
        c.execute("INSERT INTO reporting_vouchers (id, tally_guid, date, voucher_number, voucher_type) VALUES (5, 'guid-5', '20260720', 'J1', 'Journal')")
        c.execute("INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive) VALUES (5, 'Supplier A', 100.0, 0)") # Credit
        
        conn.commit()
        
    yield
    
    # Teardown
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM reporting_ledger_entries")
        c.execute("DELETE FROM reporting_vouchers")
        c.execute("DELETE FROM ledgers")
        conn.commit()

def test_get_creditor_ledger_movements():
    # Test Period: 20260701 to 20260731
    result = get_creditor_ledger_movements('Supplier A', '20260701', '20260731')
    
    # 1. Period Opening Balance
    # Master (1000) + Pre-period Purchase (500) = 1500
    assert result['period_opening'] == 1500.0
    
    # 2. Summary Classifications
    assert result['summary']['purchases'] == 2000.0
    assert result['summary']['returns'] == 300.0
    assert result['summary']['payments'] == 1200.0
    assert result['summary']['other_adjustments'] == 100.0
    
    # 3. Net Movement
    assert result['summary']['net_movement'] == 600.0
    
    # 4. Period Closing Balance
    assert result['period_closing'] == 2100.0
    
    # 5. Movements List
    assert len(result['movements']) == 4
    assert result['movements'][0]['voucher_type'] == 'Purchase'
    assert result['movements'][1]['voucher_type'] == 'Payment'
    assert result['movements'][2]['voucher_type'] == 'Debit Note'
    assert result['movements'][3]['voucher_type'] == 'Journal'
