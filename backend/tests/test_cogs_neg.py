import sqlite3
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
    
    conn.execute("INSERT INTO ledgers (name, parent, opening_balance) VALUES ('stock mahagun', 'Stock-in-Hand', 0)")
    conn.execute("INSERT INTO reporting_ledger_closing_balances (ledger_name, date, amount) VALUES ('stock mahagun', '20260430', 20000.0)")
    conn.execute("INSERT INTO ledgers (name, parent, opening_balance) VALUES ('purchase', 'Purchase Accounts', 0)")
    
    # Purchases (Debit negative) -> Mahagun: -5,000
    conn.execute("INSERT INTO reporting_vouchers (id, date, voucher_type) VALUES (1, '20260410', 'Purchase')")
    conn.execute("INSERT INTO reporting_ledger_entries (id, voucher_id, ledger_name, amount) VALUES (1, 1, 'purchase', -5000.0)")
    conn.execute("INSERT INTO reporting_cost_centre_allocations (ledger_entry_id, cost_centre_name, amount) VALUES (1, 'Mahagun', -5000.0)")
    conn.commit()

    with mock.patch('backend.routers.reporting_pl.get_db') as mock_get_db, mock.patch('backend.services.reporting_stock_service.get_db') as mock_stock_db:
        mock_get_db.return_value.__enter__.return_value = conn
        mock_stock_db.return_value.__enter__.return_value = conn
        mahagun = calculate_pl_for_store("Mahagun", "20260401", "20260430", conn)
        
        # OpStock = 0
        # Purchases = 5000 (abs)
        # ClStock = 20000
        # COGS = 0 + 5000 - 20000 = -15000
        print("COGS Output:", mahagun["cost_of_goods_sold"]["cogs"])

    conn.close()
    os.close(fd)
    os.remove(temp_db)

if __name__ == '__main__':
    run_tests()
