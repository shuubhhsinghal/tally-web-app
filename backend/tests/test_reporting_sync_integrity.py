import sqlite3
import os
import tempfile
from unittest import mock
import xml.etree.ElementTree as ET

from backend.services.tally_reporting_sync import fetch_and_store_vouchers

def setup_db():
    fd, temp_db_path = tempfile.mkstemp()
    conn = sqlite3.connect(temp_db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    
    # 1. Create Schema
    conn.execute('''
        CREATE TABLE IF NOT EXISTS reporting_vouchers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tally_guid TEXT UNIQUE NOT NULL,
            date TEXT NOT NULL,
            voucher_number TEXT,
            voucher_type TEXT NOT NULL,
            party_ledger_name TEXT,
            narration TEXT,
            reference TEXT,
            reference_date TEXT,
            effective_date TEXT,
            cheque_number TEXT,
            cheque_date TEXT,
            bank_name TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS reporting_ledger_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            voucher_id INTEGER NOT NULL REFERENCES reporting_vouchers(id) ON DELETE CASCADE,
            ledger_name TEXT NOT NULL,
            amount REAL NOT NULL,
            is_deemed_positive BOOLEAN
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS reporting_cost_centre_allocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ledger_entry_id INTEGER NOT NULL REFERENCES reporting_ledger_entries(id) ON DELETE CASCADE,
            cost_centre_name TEXT NOT NULL,
            amount REAL NOT NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS reporting_inventory_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            voucher_id INTEGER NOT NULL REFERENCES reporting_vouchers(id) ON DELETE CASCADE,
            stock_item_name TEXT NOT NULL,
            godown_name TEXT,
            billed_qty REAL,
            amount REAL NOT NULL,
            rate REAL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS reporting_sync_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_date TEXT,
            end_date TEXT,
            synced_at TEXT
        )
    ''')
    
    # Seed with the ghost 27000 voucher (Zonutt)
    conn.execute("""
        INSERT INTO reporting_vouchers (id, tally_guid, date, voucher_number, voucher_type, party_ledger_name)
        VALUES (30, '29bfa85a-409b-4777-a82e-a4d733945520-00000162', '20260901', '55', 'Purchase', 'Zonutt Naturals Pvt.Ltd.')
    """)
    conn.execute("INSERT INTO reporting_ledger_entries (id, voucher_id, ledger_name, amount, is_deemed_positive) VALUES (1, 30, 'purchase', 27000.0, 1)")
    conn.execute("INSERT INTO reporting_cost_centre_allocations (ledger_entry_id, cost_centre_name, amount) VALUES (1, 'Mahagun', 27000.0)")
    conn.execute("INSERT INTO reporting_inventory_entries (id, voucher_id, stock_item_name, amount, rate) VALUES (1, 30, 'Item1', 27000.0, 0)")
    
    # Seed an active voucher
    conn.execute("""
        INSERT INTO reporting_vouchers (id, tally_guid, date, voucher_number, voucher_type, party_ledger_name)
        VALUES (31, 'active-guid-1', '20260902', '56', 'Sales', 'Customer A')
    """)
    
    conn.commit()
    
    return fd, conn, temp_db_path

def test_sync_integrity():
    fd, conn, temp_db_path = setup_db()
    
    mock_xml = """<ENVELOPE>
      <VOUCHER VCHKEY="active-guid-1">
        <GUID>active-guid-1</GUID>
        <DATE>20260902</DATE>
        <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
        <VOUCHERNUMBER>56</VOUCHERNUMBER>
        <PARTYLEDGERNAME>Customer A</PARTYLEDGERNAME>
      </VOUCHER>
      
      <VOUCHER VCHKEY="cancelled-guid-1">
        <GUID>cancelled-guid-1</GUID>
        <DATE>20260903</DATE>
        <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
        <ISCANCELLED>Yes</ISCANCELLED>
      </VOUCHER>
      
      <VOUCHER VCHKEY="optional-guid-1">
        <GUID>optional-guid-1</GUID>
        <DATE>20260904</DATE>
        <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
        <ISOPTIONAL>Yes</ISOPTIONAL>
      </VOUCHER>
    </ENVELOPE>"""
    
    class MockResponse:
        def __init__(self, text, status_code=200):
            self.text = text
            self.status_code = status_code
        def raise_for_status(self):
            if self.status_code != 200:
                raise Exception("HTTP Error")

    with mock.patch('backend.services.tally_reporting_sync.requests.post', return_value=MockResponse(mock_xml)):
        with mock.patch('backend.services.tally_reporting_sync.get_db') as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = conn
            
            # 1st Sync (I. Syncing the same period twice produces the same DB state)
            fetch_and_store_vouchers('20260901', '20260930')
            
            # Let's verify DB state after first sync
            cursor = conn.cursor()
            
            # A. Existing active voucher remains
            cursor.execute("SELECT * FROM reporting_vouchers WHERE tally_guid='active-guid-1'")
            assert cursor.fetchone() is not None
            
            # B. Voucher removed from Tally payload is removed from SQLite (Ghost deletion)
            # J. The specific deleted 27,000 Zonutt voucher would be removed
            cursor.execute("SELECT * FROM reporting_vouchers WHERE tally_guid='29bfa85a-409b-4777-a82e-a4d733945520-00000162'")
            assert cursor.fetchone() is None
            
            # C, D, E. Ledger entries, cost centre allocations, and inventory entries are removed via CASCADE
            cursor.execute("SELECT * FROM reporting_ledger_entries WHERE voucher_id=30")
            assert cursor.fetchone() is None
            cursor.execute("SELECT * FROM reporting_cost_centre_allocations WHERE ledger_entry_id=1")
            assert cursor.fetchone() is None
            cursor.execute("SELECT * FROM reporting_inventory_entries WHERE voucher_id=30")
            assert cursor.fetchone() is None
            
            # F, G. Cancelled and Optional vouchers are skipped/not stored
            cursor.execute("SELECT * FROM reporting_vouchers WHERE tally_guid='cancelled-guid-1'")
            assert cursor.fetchone() is None
            cursor.execute("SELECT * FROM reporting_vouchers WHERE tally_guid='optional-guid-1'")
            assert cursor.fetchone() is None

            # 2nd Sync to prove idempotency
            fetch_and_store_vouchers('20260901', '20260930')
            cursor.execute("SELECT count(*) as c FROM reporting_vouchers")
            assert cursor.fetchone()[0] == 1 # Only active-guid-1 remains

    conn.close()
    os.close(fd)
    os.remove(temp_db_path)
    print("test_sync_integrity passed!")

def test_failed_sync_does_not_delete():
    fd, conn, temp_db_path = setup_db()
    
    # H. Failed/partial sync does NOT delete existing vouchers
    with mock.patch('backend.services.tally_reporting_sync.requests.post') as mock_post:
        mock_post.side_effect = Exception("Network timeout")
        
        with mock.patch('backend.services.tally_reporting_sync.get_db') as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = conn
            
            try:
                fetch_and_store_vouchers('20260901', '20260930')
            except Exception:
                pass
                
            cursor = conn.cursor()
            # The Zonutt ghost voucher SHOULD STILL EXIST because the sync failed before cleanup!
            cursor.execute("SELECT * FROM reporting_vouchers WHERE tally_guid='29bfa85a-409b-4777-a82e-a4d733945520-00000162'")
            assert cursor.fetchone() is not None

    conn.close()
    os.close(fd)
    os.remove(temp_db_path)
    print("test_failed_sync_does_not_delete passed!")

if __name__ == '__main__':
    test_sync_integrity()
    test_failed_sync_does_not_delete()
    print("All sync integrity tests passed!")
