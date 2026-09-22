import pytest
from fastapi.testclient import TestClient
import json

from backend.main import app
from backend.database import get_db, init_db
from backend.routers.purchase_item import normalize_item_name
from unittest.mock import patch

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_teardown():
    # Setup
    with get_db() as conn:
        cursor = conn.cursor()
        # clear for clean state
        cursor.execute("DELETE FROM stock_items")
        cursor.execute("DELETE FROM pending_masters")
        cursor.execute("DELETE FROM offline_queue")
        
        # Insert Confirmed Item
        cursor.execute("INSERT INTO stock_items (name, unit) VALUES (?, ?)", ("Confirmed Item", "PCS"))
        
        # Insert Pending Item
        queue_id_pending = 1
        cursor.execute("INSERT INTO offline_queue (id, operation_type, payload, xml_data, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (queue_id_pending, "CREATE_ITEM", json.dumps({"name": "JALAPEÑO & CHESSE CASHEWS (180Gm)", "uom": "PKT"}), "<xml/>", "PENDING", "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z"))
        cursor.execute("INSERT INTO pending_masters (queue_id, entity_type, normalized_name, original_name, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (queue_id_pending, "ITEM", normalize_item_name("JALAPEÑO & CHESSE CASHEWS (180Gm)"), "JALAPEÑO & CHESSE CASHEWS (180Gm)", "PENDING", "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z"))
                       
        # Insert Syncing Item
        queue_id_syncing = 2
        cursor.execute("INSERT INTO offline_queue (id, operation_type, payload, xml_data, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (queue_id_syncing, "CREATE_ITEM", json.dumps({"name": "Syncing Item", "uom": "PCS"}), "<xml/>", "PROCESSING", "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z"))
        cursor.execute("INSERT INTO pending_masters (queue_id, entity_type, normalized_name, original_name, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (queue_id_syncing, "ITEM", normalize_item_name("Syncing Item"), "Syncing Item", "SYNCED_WAITING_CONFIRMATION", "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z"))
                       
        # Insert Failed Item
        queue_id_failed = 3
        cursor.execute("INSERT INTO offline_queue (id, operation_type, payload, xml_data, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (queue_id_failed, "CREATE_ITEM", json.dumps({"name": "Failed Item", "uom": "PCS"}), "<xml/>", "FAILED", "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z"))
        cursor.execute("INSERT INTO pending_masters (queue_id, entity_type, normalized_name, original_name, status, error_message, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (queue_id_failed, "ITEM", normalize_item_name("Failed Item"), "Failed Item", "FAILED", "Some error", "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z"))

        # Insert Duplicate (Confirmed + Pending)
        cursor.execute("INSERT INTO stock_items (name, unit) VALUES (?, ?)", ("Duplicate Item", "PCS"))
        queue_id_dup = 4
        cursor.execute("INSERT INTO offline_queue (id, operation_type, payload, xml_data, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (queue_id_dup, "CREATE_ITEM", json.dumps({"name": "Duplicate Item", "uom": "PCS"}), "<xml/>", "PENDING", "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z"))
        cursor.execute("INSERT INTO pending_masters (queue_id, entity_type, normalized_name, original_name, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (queue_id_dup, "ITEM", normalize_item_name("Duplicate Item"), "Duplicate Item", "PENDING", "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z"))
        
        conn.commit()
    
    yield
    
    # Teardown
    with get_db() as conn:
        conn.execute("DELETE FROM stock_items")
        conn.execute("DELETE FROM pending_masters")
        conn.execute("DELETE FROM offline_queue")
        conn.commit()

def test_purchase_item_metadata_includes_pending():
    response = client.get("/api/purchase-item/metadata")
    assert response.status_code == 200
    data = response.json()
    
    stock_items = data.get("stock_items", [])
    master_states = data.get("master_states", {}).get("stock_items", [])
    
    # confirmed items should be in stock_items
    assert "Confirmed Item" in stock_items
    assert "Duplicate Item" in stock_items
    
    # Pending items should NOT be directly in stock_items (MasterAutocomplete handles them via master_states)
    assert "JALAPEÑO & CHESSE CASHEWS (180Gm)" not in stock_items
    
    # Check master_states contains pending items
    pending_names = [m["name"] for m in master_states]
    assert "JALAPEÑO & CHESSE CASHEWS (180Gm)" in pending_names
    assert "Syncing Item" in pending_names
    assert "Failed Item" in pending_names
