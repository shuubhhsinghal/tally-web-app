import sqlite3
import os
import tempfile
import xml.etree.ElementTree as ET
from unittest import mock
from backend.services.reporting_stock_service import get_closing_stock, get_opening_stock, STORE_STOCK_MAPPING

def run_tests():
    fd, temp_db = tempfile.mkstemp()
    conn = sqlite3.connect(temp_db)
    
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
        CREATE TABLE reporting_ledger_closing_balances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ledger_name TEXT NOT NULL,
            date TEXT NOT NULL,
            amount REAL NOT NULL,
            UNIQUE(ledger_name, date)
        )
    ''')
    
    conn.execute("INSERT INTO ledgers (name, parent, opening_balance) VALUES ('stock mahagun', 'Stock-in-Hand', 150.0)")
    conn.execute("INSERT INTO ledgers (name, parent, opening_balance) VALUES ('stock gulshan', 'Stock-in-Hand', 0.0)")
    conn.execute("INSERT INTO ledgers (name, parent, opening_balance) VALUES ('stock vvip', 'Stock-in-Hand', 0.0)")
    conn.commit()
    
    with mock.patch('backend.services.reporting_stock_service.get_db') as mock_get_db:
        mock_get_db.return_value.__enter__.return_value = conn
        
        # --- TEST 1: Parsing and Idempotency ---
        xml_response = """<ENVELOPE>
          <LEDGER NAME="stock mahagun">
            <NAME>stock mahagun</NAME>
            <LEDGERCLOSINGVALUES.LIST>
              <DATE>20260430</DATE>
              <AMOUNT>-15000.0</AMOUNT>
            </LEDGERCLOSINGVALUES.LIST>
            <LEDGERCLOSINGVALUES.LIST>
              <DATE>20260531</DATE>
              <AMOUNT>-16000.0</AMOUNT>
            </LEDGERCLOSINGVALUES.LIST>
          </LEDGER>
          <LEDGER NAME="stock gulshan">
            <NAME>stock gulshan</NAME>
            <LEDGERCLOSINGVALUES.LIST>
              <DATE>20260430</DATE>
              <AMOUNT>-12000.0</AMOUNT>
            </LEDGERCLOSINGVALUES.LIST>
          </LEDGER>
          <LEDGER NAME="Random Ledger">
            <NAME>Random Ledger</NAME>
            <LEDGERCLOSINGVALUES.LIST>
              <DATE>20260430</DATE>
              <AMOUNT>999</AMOUNT>
            </LEDGERCLOSINGVALUES.LIST>
          </LEDGER>
        </ENVELOPE>"""
        
        c_root = ET.fromstring(xml_response)
        closing_data = []
        for ledger_elem in c_root.findall('.//LEDGER'):
            name_node = ledger_elem.find('NAME')
            if name_node is None or not name_node.text: continue
            lname = name_node.text.strip()
            if not lname.lower().startswith('stock'): continue
                
            for cl in ledger_elem.findall('LEDGERCLOSINGVALUES.LIST'):
                date_node = cl.find('DATE')
                amount_node = cl.find('AMOUNT')
                if date_node is not None and date_node.text and amount_node is not None and amount_node.text:
                    closing_data.append((lname, date_node.text.strip(), float(amount_node.text.strip())))
                    
        conn.executemany('''
            INSERT INTO reporting_ledger_closing_balances (ledger_name, date, amount)
            VALUES (?, ?, ?)
            ON CONFLICT(ledger_name, date) DO UPDATE SET amount = excluded.amount
        ''', closing_data)
        conn.commit()
        
        assert conn.execute("SELECT COUNT(*) FROM reporting_ledger_closing_balances").fetchone()[0] == 3
        
        # Idempotency
        closing_data[0] = ('stock mahagun', '20260430', -18000.0)
        conn.executemany('''
            INSERT INTO reporting_ledger_closing_balances (ledger_name, date, amount)
            VALUES (?, ?, ?)
            ON CONFLICT(ledger_name, date) DO UPDATE SET amount = excluded.amount
        ''', closing_data)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM reporting_ledger_closing_balances").fetchone()[0] == 3
        assert conn.execute("SELECT amount FROM reporting_ledger_closing_balances WHERE ledger_name='stock mahagun' AND date='20260430'").fetchone()[0] == -18000.0
        
        print("Test 1 (Parsing and Idempotency) Passed!")
        
        # --- TEST 2: get_closing_stock ---
        conn.execute("DELETE FROM reporting_ledger_closing_balances")
        conn.execute("INSERT INTO reporting_ledger_closing_balances (ledger_name, date, amount) VALUES ('stock mahagun', '20260430', 500.0)")
        conn.execute("INSERT INTO reporting_ledger_closing_balances (ledger_name, date, amount) VALUES ('stock mahagun', '20260531', 600.0)")
        conn.execute("INSERT INTO reporting_ledger_closing_balances (ledger_name, date, amount) VALUES ('stock mahagun', '20260630', 700.0)")
        conn.commit()
        
        assert get_closing_stock('stock mahagun', '20260531') == 600.0
        assert get_closing_stock('stock mahagun', '20260615') == 600.0
        assert get_closing_stock('stock mahagun', '20260331') == 0.0
        assert get_closing_stock('stock mahagun', '20270331') == 700.0
        print("Test 2 (get_closing_stock) Passed!")
        
        # --- TEST 3: get_opening_stock ---
        conn.execute("DELETE FROM reporting_ledger_closing_balances")
        conn.execute("INSERT INTO reporting_ledger_closing_balances (ledger_name, date, amount) VALUES ('stock mahagun', '20260430', 500.0)")
        conn.execute("INSERT INTO reporting_ledger_closing_balances (ledger_name, date, amount) VALUES ('stock mahagun', '20260531', 600.0)")
        conn.commit()
        
        assert get_opening_stock('stock mahagun', '20260501') == 500.0
        assert get_opening_stock('stock mahagun', '20260601') == 600.0
        assert get_opening_stock('stock mahagun', '20260615') == 600.0
        assert get_opening_stock('stock mahagun', '20260401') == 150.0  # Fallback
        assert get_opening_stock('stock gulshan', '20260401') == 0.0
        print("Test 3 (get_opening_stock) Passed!")
        
        # --- TEST 4: Mapping ---
        assert 'mahagun' in STORE_STOCK_MAPPING
        assert 'gulshan' in STORE_STOCK_MAPPING
        assert 'vvip' in STORE_STOCK_MAPPING
        assert len(STORE_STOCK_MAPPING) == 3
        print("Test 4 (Store Mapping) Passed!")
        
    conn.close()
    os.close(fd)
    os.remove(temp_db)
    print("All tests passed!")

if __name__ == '__main__':
    run_tests()
