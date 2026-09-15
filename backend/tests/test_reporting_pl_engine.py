import sqlite3
import sys
from unittest.mock import MagicMock

# Mock fastapi BEFORE importing the router
sys.modules['fastapi'] = MagicMock()

import os
import tempfile
from unittest import mock
from backend.routers.reporting_pl import calculate_pl_for_store

def run_tests():
    fd, temp_db = tempfile.mkstemp()
    conn = sqlite3.connect(temp_db)
    
    # --- SCHEMA SETUP ---
    conn.execute('''
        CREATE TABLE ledgers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            parent TEXT,
            cost_centre BOOLEAN DEFAULT 0,
            opening_balance REAL DEFAULT 0.0
        )
    ''')
    conn.execute('''
        CREATE TABLE reporting_vouchers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            voucher_type TEXT NOT NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE reporting_ledger_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            voucher_id INTEGER NOT NULL,
            ledger_name TEXT NOT NULL,
            amount REAL NOT NULL,
            is_deemed_positive BOOLEAN
        )
    ''')
    conn.execute('''
        CREATE TABLE reporting_cost_centre_allocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ledger_entry_id INTEGER NOT NULL,
            cost_centre_name TEXT NOT NULL,
            amount REAL NOT NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE reporting_ledger_closing_balances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ledger_name TEXT NOT NULL,
            date TEXT NOT NULL,
            amount REAL NOT NULL,
            UNIQUE(ledger_name, date)
        )
    ''')
    
    # --- MOCK DATA ---
    conn.execute("INSERT INTO ledgers (name, parent, opening_balance) VALUES ('sales', 'Sales Accounts', 0)")
    conn.execute("INSERT INTO ledgers (name, parent, opening_balance) VALUES ('purchase', 'Purchase Accounts', 0)")
    conn.execute("INSERT INTO ledgers (name, parent, opening_balance) VALUES ('purchase return', 'Purchase Accounts', 0)")
    conn.execute("INSERT INTO ledgers (name, parent, opening_balance) VALUES ('packaging', 'Direct Expenses', 0)")
    conn.execute("INSERT INTO ledgers (name, parent, opening_balance) VALUES ('salary', 'Indirect Expenses', 0)")
    conn.execute("INSERT INTO ledgers (name, parent, opening_balance) VALUES ('stock mahagun', 'Stock-in-Hand', 15000.0)")
    conn.execute("INSERT INTO ledgers (name, parent, opening_balance) VALUES ('stock gulshan', 'Stock-in-Hand', 10000.0)")
    conn.execute("INSERT INTO ledgers (name, parent, opening_balance) VALUES ('stock vvip', 'Stock-in-Hand', 5000.0)")

    conn.execute("INSERT INTO reporting_ledger_closing_balances (ledger_name, date, amount) VALUES ('stock mahagun', '20260430', 20000.0)")
    conn.execute("INSERT INTO reporting_ledger_closing_balances (ledger_name, date, amount) VALUES ('stock gulshan', '20260430', 12000.0)")

    # 1. Sales
    conn.execute("INSERT INTO reporting_vouchers (id, date, voucher_type) VALUES (1, '20260415', 'Sales')")
    conn.execute("INSERT INTO reporting_ledger_entries (id, voucher_id, ledger_name, amount) VALUES (1, 1, 'sales', 80000.0)")
    conn.execute("INSERT INTO reporting_cost_centre_allocations (ledger_entry_id, cost_centre_name, amount) VALUES (1, 'Mahagun', 50000.0)")
    conn.execute("INSERT INTO reporting_cost_centre_allocations (ledger_entry_id, cost_centre_name, amount) VALUES (1, 'Vvip', 20000.0)")
    
    # Unallocated explicitly has NO CC mapping
    conn.execute("INSERT INTO reporting_vouchers (id, date, voucher_type) VALUES (2, '20260416', 'Sales')")
    conn.execute("INSERT INTO reporting_ledger_entries (id, voucher_id, ledger_name, amount) VALUES (2, 2, 'sales', 10000.0)")

    # 2. Purchases (Mahagun 30k, Gulshan 15k)
    conn.execute("INSERT INTO reporting_vouchers (id, date, voucher_type) VALUES (3, '20260410', 'Purchase')")
    conn.execute("INSERT INTO reporting_ledger_entries (id, voucher_id, ledger_name, amount) VALUES (3, 3, 'purchase', -30000.0)")
    conn.execute("INSERT INTO reporting_cost_centre_allocations (ledger_entry_id, cost_centre_name, amount) VALUES (3, 'Mahagun', -30000.0)")
    
    conn.execute("INSERT INTO reporting_vouchers (id, date, voucher_type) VALUES (4, '20260412', 'Purchase')")
    conn.execute("INSERT INTO reporting_ledger_entries (id, voucher_id, ledger_name, amount) VALUES (4, 4, 'purchase', -15000.0)")
    conn.execute("INSERT INTO reporting_cost_centre_allocations (ledger_entry_id, cost_centre_name, amount) VALUES (4, 'Gulshan', -15000.0)")

    # 3. Purchase Returns -> Mahagun: +5,000
    conn.execute("INSERT INTO reporting_vouchers (id, date, voucher_type) VALUES (5, '20260420', 'Debit Note')")
    conn.execute("INSERT INTO reporting_ledger_entries (id, voucher_id, ledger_name, amount) VALUES (5, 5, 'purchase return', 5000.0)")
    conn.execute("INSERT INTO reporting_cost_centre_allocations (ledger_entry_id, cost_centre_name, amount) VALUES (5, 'Mahagun', 5000.0)")

    # 4. Direct Expenses -> Mahagun: -2,000
    conn.execute("INSERT INTO reporting_vouchers (id, date, voucher_type) VALUES (6, '20260425', 'Journal')")
    conn.execute("INSERT INTO reporting_ledger_entries (id, voucher_id, ledger_name, amount) VALUES (6, 6, 'packaging', -2000.0)")
    conn.execute("INSERT INTO reporting_cost_centre_allocations (ledger_entry_id, cost_centre_name, amount) VALUES (6, 'Mahagun', -2000.0)")

    # 5. Indirect Expenses -> Unallocated: -1,000
    conn.execute("INSERT INTO reporting_vouchers (id, date, voucher_type) VALUES (7, '20260428', 'Payment')")
    conn.execute("INSERT INTO reporting_ledger_entries (id, voucher_id, ledger_name, amount) VALUES (7, 7, 'salary', -1000.0)")

    conn.commit()
    
    with mock.patch('backend.routers.reporting_pl.get_db') as mock_get_db, mock.patch('backend.services.reporting_stock_service.get_db') as mock_stock_db:
        mock_get_db.return_value.__enter__.return_value = conn
        mock_stock_db.return_value.__enter__.return_value = conn
        
        mahagun = calculate_pl_for_store("Mahagun", "20260401", "20260430", conn)
        assert mahagun["revenue"]["net_sales"] == 50000.0
        assert mahagun["cost_of_goods_sold"]["opening_stock"] == 15000.0
        assert mahagun["cost_of_goods_sold"]["closing_stock"] == 20000.0
        # Purchase -30k + Return 5k = -25k total amount. In abs() terms, net purchases = 25k.
        # Wait, the SQL SUMs everything. -30000 + 5000 = -25000.
        assert mahagun["cost_of_goods_sold"]["net_purchases"] == 25000.0
        assert mahagun["cost_of_goods_sold"]["cogs"] == 20000.0
        assert mahagun["gross_profit"] == 30000.0
        assert mahagun["expenses"]["direct_expenses"] == 2000.0
        assert mahagun["net_profit"] == 28000.0
        print("Mahagun passed")

        gulshan = calculate_pl_for_store("Gulshan", "20260401", "20260430", conn)
        assert gulshan["cost_of_goods_sold"]["cogs"] == 13000.0
        assert gulshan["net_profit"] == -13000.0
        print("Gulshan passed")
        
        unalloc = calculate_pl_for_store("Unallocated", "20260401", "20260430", conn)
        assert unalloc["revenue"]["net_sales"] == 10000.0
        assert unalloc["cost_of_goods_sold"]["cogs"] == 0.0
        assert unalloc["expenses"]["indirect_expenses"] == 1000.0
        assert unalloc["net_profit"] == 9000.0
        print("Unallocated passed")
        
        comb = calculate_pl_for_store("Combined", "20260401", "20260430", conn)
        assert comb["revenue"]["net_sales"] == 80000.0
        assert comb["cost_of_goods_sold"]["opening_stock"] == 30000.0
        
        # Closing stock is None because Vvip has no closing stock
        assert comb["cost_of_goods_sold"]["closing_stock"] is None
        assert comb["cost_of_goods_sold"]["cogs"] is None
        assert comb["net_profit"] is None
        print("Combined passed")

    conn.close()
    os.close(fd)
    os.remove(temp_db)
    print("All P&L Engine tests passed!")

if __name__ == '__main__':
    run_tests()
