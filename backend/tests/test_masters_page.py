import sys
import os
import json
import sqlite3
from datetime import datetime
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from backend.main import app
from backend.database import get_db

client = TestClient(app)

def setup_module(module):
    now = datetime.now().isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM ledgers")
        cursor.execute("DELETE FROM stock_items")
        cursor.execute("DELETE FROM pending_masters")
        cursor.execute("DELETE FROM offline_queue")
        
        # A. Confirmed Ledger
        cursor.execute("INSERT INTO ledgers (name, parent, cost_centre) VALUES ('Maa Vaishno', 'Sundry Creditors', 0)")
        
        # B. Pending Ledger
        cursor.execute("INSERT INTO offline_queue (operation_type, payload, xml_data, status, created_at, updated_at) VALUES ('CREATE_LEDGER', ?, '...', 'PENDING', ?, ?)",
                       (json.dumps({"parent": "Sundry Creditors"}), now, now))
        q_id1 = cursor.lastrowid
        cursor.execute("INSERT INTO pending_masters (entity_type, normalized_name, original_name, queue_id, status, created_at, updated_at) VALUES ('LEDGER', 'pending ledger', 'Pending Ledger', ?, 'PENDING', ?, ?)", (q_id1, now, now))

        # C. Synced Waiting Confirmation Ledger
        cursor.execute("INSERT INTO offline_queue (operation_type, payload, xml_data, status, created_at, updated_at) VALUES ('CREATE_LEDGER', ?, '...', 'SYNCED_WAITING_CONFIRMATION', ?, ?)",
                       (json.dumps({"parent": "Duties & Taxes"}), now, now))
        q_id2 = cursor.lastrowid
        cursor.execute("INSERT INTO pending_masters (entity_type, normalized_name, original_name, queue_id, status, created_at, updated_at) VALUES ('LEDGER', 'syncing ledger', 'Syncing Ledger', ?, 'SYNCED_WAITING_CONFIRMATION', ?, ?)", (q_id2, now, now))

        # D. Failed Ledger
        cursor.execute("INSERT INTO offline_queue (operation_type, payload, xml_data, status, error_message, created_at, updated_at) VALUES ('CREATE_LEDGER', ?, '...', 'FAILED', 'Validation Error', ?, ?)",
                       (json.dumps({"parent": "Sundry Debtors"}), now, now))
        q_id3 = cursor.lastrowid
        cursor.execute("INSERT INTO pending_masters (entity_type, normalized_name, original_name, queue_id, status, error_message, created_at, updated_at) VALUES ('LEDGER', 'failed ledger', 'Failed Ledger', ?, 'FAILED', 'Validation Error', ?, ?)", (q_id3, now, now))

        # E. Confirmed Item
        cursor.execute("INSERT INTO stock_items (name, unit) VALUES ('Confirmed Item', 'PCS')")
        
        # F. Pending Item
        cursor.execute("INSERT INTO offline_queue (operation_type, payload, xml_data, status, created_at, updated_at) VALUES ('CREATE_ITEM', ?, '...', 'PENDING', ?, ?)",
                       (json.dumps({"uom": "PKT"}), now, now))
        q_id4 = cursor.lastrowid
        cursor.execute("INSERT INTO pending_masters (entity_type, normalized_name, original_name, queue_id, status, created_at, updated_at) VALUES ('ITEM', 'pending item', 'Pending Item', ?, 'PENDING', ?, ?)", (q_id4, now, now))

        # G. Duplicate Protection
        cursor.execute("INSERT INTO ledgers (name, parent, cost_centre) VALUES ('Duplicate Name', 'Sundry Creditors', 0)")
        cursor.execute("INSERT INTO offline_queue (operation_type, payload, xml_data, status, created_at, updated_at) VALUES ('CREATE_LEDGER', ?, '...', 'PENDING', ?, ?)",
                       (json.dumps({"parent": "Sundry Creditors"}), now, now))
        q_id5 = cursor.lastrowid
        cursor.execute("INSERT INTO pending_masters (entity_type, normalized_name, original_name, queue_id, status, created_at, updated_at) VALUES ('LEDGER', 'duplicate name', 'Duplicate Name', ?, 'PENDING', ?, ?)", (q_id5, now, now))

        # H. Malformed Queue Payload
        cursor.execute("INSERT INTO offline_queue (operation_type, payload, xml_data, status, created_at, updated_at) VALUES ('CREATE_LEDGER', 'NOT JSON', '...', 'PENDING', ?, ?)", (now, now))
        q_id6 = cursor.lastrowid
        cursor.execute("INSERT INTO pending_masters (entity_type, normalized_name, original_name, queue_id, status, created_at, updated_at) VALUES ('LEDGER', 'malformed ledger', 'Malformed Ledger', ?, 'PENDING', ?, ?)", (q_id6, now, now))

        conn.commit()

def test_masters_endpoint():
    response = client.get("/api/masters")
    assert response.status_code == 200
    data = response.json()
    
    ledgers = data["ledgers"]
    items = data["items"]
    
    assert "Maa Vaishno" in [l["name"] for l in ledgers]
    for l in ledgers:
        if l["name"] == "Maa Vaishno":
            assert l["status"] == "in_tally"
            assert l["parent"] == "Sundry Creditors"
        elif l["name"] == "Pending Ledger":
            assert l["status"] == "in_queue"
            assert l["parent"] == "Sundry Creditors"
        elif l["name"] == "Syncing Ledger":
            assert l["status"] == "syncing"
        elif l["name"] == "Failed Ledger":
            assert l["status"] == "failed"
            assert l["error"] == "Validation Error"
        elif l["name"] == "Malformed Ledger":
            assert l["status"] == "in_queue"
            assert l["parent"] == "Other / Unknown"
            
    assert len([l for l in ledgers if l["name"].lower() == "duplicate name"]) == 1
    for l in ledgers:
        if l["name"].lower() == "duplicate name":
            assert l["status"] == "in_tally"
            
    assert "Confirmed Item" in [i["name"] for i in items]
    for i in items:
        if i["name"] == "Confirmed Item":
            assert i["status"] == "in_tally"
        elif i["name"] == "Pending Item":
            assert i["status"] == "in_queue"
            assert i["unit"] == "PKT"

if __name__ == "__main__":
    setup_module(None)
    test_masters_endpoint()
    print("Test passed")

