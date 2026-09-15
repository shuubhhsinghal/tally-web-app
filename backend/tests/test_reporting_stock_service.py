import sqlite3
import os
import tempfile
import pytest
from backend.services.reporting_stock_service import get_closing_stock, get_opening_stock
from backend.routers.reporting_pl import calculate_pl_for_store

@pytest.fixture
def temp_db():
    fd, temp_db_path = tempfile.mkstemp()
    conn = sqlite3.connect(temp_db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    
    conn.execute('''
        CREATE TABLE IF NOT EXISTS reporting_ledger_closing_balances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ledger_name TEXT NOT NULL,
            date TEXT NOT NULL,
            amount REAL NOT NULL,
            UNIQUE(ledger_name, date)
        )
    ''')
    
    conn.execute('''
        CREATE TABLE IF NOT EXISTS ledgers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            parent TEXT,
            opening_balance REAL
        )
    ''')
    
    conn.execute('CREATE TABLE reporting_vouchers (id INTEGER PRIMARY KEY, date TEXT)')
    conn.execute('CREATE TABLE reporting_ledger_entries (id INTEGER PRIMARY KEY, voucher_id INTEGER, ledger_name TEXT, amount REAL, is_deemed_positive BOOLEAN)')
    conn.execute('CREATE TABLE reporting_cost_centre_allocations (id INTEGER PRIMARY KEY, ledger_entry_id INTEGER, cost_centre_name TEXT, amount REAL)')
    
    # Insert test data matching the multi-month example
    # FY opening stock = 80000
    conn.execute("INSERT INTO ledgers (name, parent, opening_balance) VALUES ('stock mahagun', 'Stock-in-Hand', 80000)")
    # We add a 0401 entry to prove it gets ignored for closing stock!
    conn.execute("INSERT INTO reporting_ledger_closing_balances (ledger_name, date, amount) VALUES ('stock mahagun', '20260401', 85000)")
    
    # 31-May closing stock = 90000
    conn.execute("INSERT INTO reporting_ledger_closing_balances (ledger_name, date, amount) VALUES ('stock mahagun', '20260531', 90000)")
    
    # Other stores empty
    conn.execute("INSERT INTO ledgers (name, parent, opening_balance) VALUES ('stock gulshan', 'Stock-in-Hand', 0)")
    conn.execute("INSERT INTO ledgers (name, parent, opening_balance) VALUES ('stock vvip', 'Stock-in-Hand', 0)")
    
    # Add expense entries for Unallocated to test Signed Expense Handling
    # Sales: 10000 (credit)
    # Purchases: -5000 (debit)
    # Direct Exp: -1000 (debit)
    # Indirect Exp: +500 (credit/income)
    
    # We won't insert actual vouchers, we'll just mock get_group_sum for the test, 
    # but calculate_pl_for_store runs the actual query.
    # So let's insert real rows for Unallocated!
    conn.execute("INSERT INTO reporting_vouchers (id, date) VALUES (1, '20260515')")
    
    # Sales Accounts
    conn.execute("INSERT INTO ledgers (name, parent) VALUES ('sales', 'Sales Accounts')")
    conn.execute("INSERT INTO reporting_ledger_entries (id, voucher_id, ledger_name, amount, is_deemed_positive) VALUES (1, 1, 'sales', 10000, 0)")
    
    # Purchase Accounts
    conn.execute("INSERT INTO ledgers (name, parent) VALUES ('purchases', 'Purchase Accounts')")
    conn.execute("INSERT INTO reporting_ledger_entries (id, voucher_id, ledger_name, amount, is_deemed_positive) VALUES (2, 1, 'purchases', -5000, 1)")
    
    # Direct Expenses
    conn.execute("INSERT INTO ledgers (name, parent) VALUES ('direct exp', 'Direct Expenses')")
    conn.execute("INSERT INTO reporting_ledger_entries (id, voucher_id, ledger_name, amount, is_deemed_positive) VALUES (3, 1, 'direct exp', -1000, 1)")
    
    # Indirect Expenses (Credit balance!)
    conn.execute("INSERT INTO ledgers (name, parent) VALUES ('rounding off', 'Indirect Expenses')")
    conn.execute("INSERT INTO reporting_ledger_entries (id, voucher_id, ledger_name, amount, is_deemed_positive) VALUES (4, 1, 'rounding off', 500, 0)")
    
    conn.commit()
    
    from unittest import mock
    with mock.patch('backend.services.reporting_stock_service.get_db') as mock_get_db:
        mock_get_db.return_value.__enter__.return_value = conn
        with mock.patch('backend.routers.reporting_pl.get_db') as mock_get_db_pl:
            mock_get_db_pl.return_value.__enter__.return_value = conn
            yield conn
    
    conn.close()
    os.close(fd)
    os.remove(temp_db_path)


def test_final_stock_semantics(temp_db):
    ledger = "stock mahagun"
    
    # 1. FY opening balance on Apr 1
    assert get_opening_stock(ledger, "20260401") == 80000.0
    
    # 2. Monthly closing lookup (31-May)
    assert get_closing_stock(ledger, "20260531") == 90000.0
    
    # Prove 1-Apr entry is VALID for monthly closing and carry forward
    # Apr 30 closing stock uses the 0401 closing entry (85000)
    assert get_closing_stock(ledger, "20260430") == 85000.0
    
    # 3. Opening rollover to the next month
    # May 1 opening should carry forward the 0401 closing entry (85000)
    assert get_opening_stock(ledger, "20260501") == 85000.0
    # Jun opening should use 31-May
    assert get_opening_stock(ledger, "20260601") == 90000.0
    
    # 4. Apr-Jun multi-month period
    # Opening for Apr 1 = FY opening
    assert get_opening_stock(ledger, "20260401") == 80000.0
    # Closing for Jun 30 = latest entry <= Jun 30 -> 31-May = 90000
    assert get_closing_stock(ledger, "20260630") == 90000.0
    
    # 5. Latest closing <= period end
    assert get_closing_stock(ledger, "20260731") == 90000.0
    
    # 6. Future stock entry must never be used
    assert get_closing_stock(ledger, "20260331") is None
    
    # 7. Missing stock for an individual store returns null
    assert get_opening_stock("stock gulshan", "20260501") is None
    assert get_closing_stock("stock gulshan", "20260531") is None
    
def test_pl_missing_data_and_signed_expenses(temp_db):
    # For Mahagun, Apr-Jun period
    res = calculate_pl_for_store("Mahagun", "20260401", "20260630", temp_db)
    # Mahagun has Opening (80000) and Closing (90000)
    assert res['cost_of_goods_sold']['opening_stock'] == 80000.0
    assert res['cost_of_goods_sold']['closing_stock'] == 90000.0
    
    # 8. Missing stock for one store causes Combined P&L stock metrics to be null
    res_comb = calculate_pl_for_store("Combined", "20260401", "20260630", temp_db)
    assert res_comb['cost_of_goods_sold']['opening_stock'] == 80000.0 # Valid: [80000, 0, 0]
    assert res_comb['cost_of_goods_sold']['closing_stock'] is None    # Missing for Gulshan/VVIP
    assert res_comb['cost_of_goods_sold']['cogs'] is None
    assert res_comb['gross_profit'] is None
    assert res_comb['net_profit'] is None
    
    # 9. Signed indirect expense / credit income is handled correctly
    # Let's test Unallocated which has expenses but no stock
    res_unalloc = calculate_pl_for_store("Unallocated", "20260401", "20260630", temp_db)
    
    # Sales = 10000
    # Purchases = -5000 -> abs(Purchases) = 5000
    # COGS = 0 + 5000 - 0 = 5000
    # Gross Profit = 10000 - 5000 = 5000
    # Direct Exp = -1000
    # Indirect Exp = 500
    # Net Profit = 5000 + (-1000) + 500 = 4500
    
    assert res_unalloc['cost_of_goods_sold']['cogs'] == 5000.0
    assert res_unalloc['gross_profit'] == 5000.0
    assert res_unalloc['expenses']['direct_expenses'] == -1000.0
    assert res_unalloc['expenses']['indirect_expenses'] == 500.0
    assert res_unalloc['net_profit'] == 4500.0

