import pytest
from unittest.mock import patch, ANY
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db
import datetime
import json

client = TestClient(app)

@pytest.fixture(autouse=True)
def clean_db():
    with get_db() as conn:
        conn.execute("DELETE FROM ledgers")
        conn.execute("DELETE FROM stock_items")
        conn.execute("DELETE FROM pending_masters")
        conn.execute("DELETE FROM offline_queue")
        conn.execute("DELETE FROM uoms")
        conn.commit()
    yield

@patch('requests.post')
def test_create_item_queue_first(mock_post):
    payload = {
        "name": "W Cl Vanilla Slice 45g",
        "parent_group": "Primary",
        "uom": "NOS"
    }
    
    with get_db() as conn:
        conn.execute("INSERT INTO uoms (name) VALUES ('NOS')")
        conn.commit()

    response = client.post("/api/purchase-item/create-item", json=payload)
    
    assert response.status_code == 200
    res = response.json()
    assert res["status"] == "queued"
    
    mock_post.assert_not_called()
    
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status FROM pending_masters WHERE original_name = 'W Cl Vanilla Slice 45g'")
        assert cur.fetchone()["status"] == "PENDING"
        
        cur.execute("SELECT status FROM offline_queue WHERE operation_type = 'CREATE_ITEM'")
        assert cur.fetchone()["status"] == "PENDING"

@patch('requests.post')
def test_create_supplier_queue_first(mock_post):
    payload = {
        "name": "A K Traders"
    }
    
    response = client.post("/api/purchase/create-supplier", json=payload)
    
    assert response.status_code == 200
    res = response.json()
    assert res["status"] == "queued"
    
    mock_post.assert_not_called()
    
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status FROM pending_masters WHERE original_name = 'A K Traders'")
        assert cur.fetchone()["status"] == "PENDING"
        
        cur.execute("SELECT status FROM offline_queue WHERE operation_type = 'CREATE_LEDGER'")
        assert cur.fetchone()["status"] == "PENDING"

def test_item_already_confirmed():
    with get_db() as conn:
        conn.execute("INSERT INTO uoms (name) VALUES ('NOS')")
        conn.execute("INSERT INTO stock_items (name, unit) VALUES ('Existing Item', 'NOS')")
        conn.commit()
        
    payload = {
        "name": "Existing Item",
        "parent_group": "Primary",
        "uom": "NOS"
    }
    
    response = client.post("/api/purchase-item/create-item", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as c FROM pending_masters WHERE original_name = 'Existing Item'")
        assert cur.fetchone()["c"] == 0

def test_item_already_pending():
    with get_db() as conn:
        conn.execute("INSERT INTO uoms (name) VALUES ('NOS')")
        
        cursor = conn.cursor()
        now = datetime.datetime.utcnow().isoformat()
        cursor.execute(
            "INSERT INTO offline_queue (operation_type, payload, xml_data, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ('CREATE_ITEM', json.dumps({"uom": "NOS"}), '<test/>', 'PENDING', now, now)
        )
        q_id = cursor.lastrowid
        
        cursor.execute(
            "INSERT INTO pending_masters (entity_type, original_name, normalized_name, queue_id, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ('ITEM', 'Pending Item', 'pending item', q_id, 'PENDING', now, now)
        )
        conn.commit()
        
    payload = {
        "name": "Pending Item",
        "parent_group": "Primary",
        "uom": "NOS"
    }
    
    response = client.post("/api/purchase-item/create-item", json=payload)
    assert response.status_code == 200
    # Wait, check_master_exists_locally returns True, and create_item returns "success" directly when check_master_exists_locally returns True!
    # Because my create_item code:
    # if check_master_exists_locally('ITEM', norm_item, {"uom": payload.uom}):
    #      return {"status": "success", ...}
    assert response.json()["status"] == "success"
    
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as c FROM offline_queue WHERE operation_type = 'CREATE_ITEM'")
        assert cur.fetchone()["c"] == 1 # still only 1, no duplicate

def test_ledger_already_pending():
    with get_db() as conn:
        cursor = conn.cursor()
        now = datetime.datetime.utcnow().isoformat()
        cursor.execute(
            "INSERT INTO offline_queue (operation_type, payload, xml_data, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ('CREATE_LEDGER', json.dumps({"parent": "Sundry Creditors"}), '<test/>', 'PENDING', now, now)
        )
        q_id = cursor.lastrowid
        
        cursor.execute(
            "INSERT INTO pending_masters (entity_type, original_name, normalized_name, queue_id, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ('LEDGER', 'Pending Supplier', 'pending supplier', q_id, 'PENDING', now, now)
        )
        conn.commit()
        
    payload = {
        "name": "Pending Supplier"
    }
    
    response = client.post("/api/purchase/create-supplier", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success" # same here, returns success immediately
    
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as c FROM offline_queue WHERE operation_type = 'CREATE_LEDGER'")
        assert cur.fetchone()["c"] == 1 # still only 1, no duplicate

def test_double_request():
    with get_db() as conn:
        conn.execute("INSERT INTO uoms (name) VALUES ('NOS')")
        conn.commit()
        
    payload = {
        "name": "Double Item",
        "parent_group": "Primary",
        "uom": "NOS"
    }
    
    resp1 = client.post("/api/purchase-item/create-item", json=payload)
    resp2 = client.post("/api/purchase-item/create-item", json=payload)
    
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as c FROM pending_masters WHERE original_name = 'Double Item'")
        assert cur.fetchone()["c"] == 1
        
        cur.execute("SELECT COUNT(*) as c FROM offline_queue WHERE operation_type = 'CREATE_ITEM'")
        assert cur.fetchone()["c"] == 1

@patch('backend.database.queue_master_operation')
def test_local_db_failure(mock_queue):
    mock_queue.side_effect = Exception("DB error simulated")
    
    with get_db() as conn:
        conn.execute("INSERT INTO uoms (name) VALUES ('NOS')")
        conn.commit()
        
    payload = {
        "name": "Failed Item",
        "parent_group": "Primary",
        "uom": "NOS"
    }
    
    response = client.post("/api/purchase-item/create-item", json=payload)
    assert response.status_code == 500
