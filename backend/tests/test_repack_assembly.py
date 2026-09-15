import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db

@pytest.fixture(autouse=True)
def setup_db():
    # Setup test DB tables
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Insert a store
        cursor.execute("INSERT OR IGNORE INTO stores (store_name, cost_center_name, godown_name) VALUES ('Test Store', 'Test Cost Center', 'Test Godown')")
        
        # Insert stock items
        cursor.execute("INSERT OR IGNORE INTO stock_items (name, unit) VALUES ('Bulk Chips', 'Kgs')")
        cursor.execute("INSERT OR IGNORE INTO stock_items (name, unit) VALUES ('300G Box', 'Pcs')")
        cursor.execute("INSERT OR IGNORE INTO stock_items (name, unit) VALUES ('Label', 'Pcs')")
        
        conn.commit()

client = TestClient(app)

def test_create_recipe():
    payload = {
        "new_product_name": "Test Beetroot Chips 300G",
        "output_unit": "PCS",
        "components": [
            {"item_name": "Bulk Chips", "unit": "KGS", "quantity": 0.3},
            {"item_name": "300G Box", "unit": "PCS", "quantity": 1.0},
            {"item_name": "Label", "unit": "PCS", "quantity": 1.0}
        ]
    }
    
    response = client.post("/api/repack/product", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    
    conv_id = data["conversion_id"]
    
    # Verify in DB
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM repack_recipe_components WHERE conversion_id = ?", (conv_id,))
        comps = cursor.fetchall()
        assert len(comps) == 3

def test_execute_repack(monkeypatch):
    # Mock tally_godown_stock to return static values
    def mock_get_godown_stock(item_name, godown_name):
        if item_name == "Bulk Chips":
            return {"qty": 100.0, "rate": 100.0, "amount": 10000.0}
        elif item_name == "300G Box":
            return {"qty": 1000.0, "rate": 5.0, "amount": 5000.0}
        elif item_name == "Label":
            return {"qty": 1000.0, "rate": 0.50, "amount": 500.0}
        return {"qty": 0.0, "rate": 0.0, "amount": 0.0}
        
    import backend.services.tally_godown_stock
    monkeypatch.setattr(backend.services.tally_godown_stock, "get_godown_stock", mock_get_godown_stock)
    
    # First create recipe
    payload = {
        "new_product_name": "Test Assembly Item",
        "output_unit": "PCS",
        "components": [
            {"item_name": "Bulk Chips", "unit": "KGS", "quantity": 0.3},
            {"item_name": "300G Box", "unit": "PCS", "quantity": 1.0},
            {"item_name": "Label", "unit": "PCS", "quantity": 1.0}
        ]
    }
    create_res = client.post("/api/repack/product", json=payload).json()
    conv_id = create_res["conversion_id"]
    
    # Execute repack (produce 100 PCS)
    exec_payload = {
        "date": "2026-09-15",
        "store_name": "Test Store",
        "conversion_id": conv_id,
        "dest_qty": 100.0
    }
    
    response = client.post("/api/repack/execute", json=exec_payload)
    assert response.status_code == 200
    data = response.json()
    
    # Total cost should be:
    # 100 * 0.3 = 30 KGS * 100 = 3000
    # 100 * 1 = 100 Boxes * 5 = 500
    # 100 * 1 = 100 Labels * 0.5 = 50
    # Total = 3550
    
    assert "₹3550.00" in data["message"]
    
    repack_id = data["repack_id"]
    
    # Check operations snapshot
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM repack_operations WHERE id = ?", (repack_id,))
        op = cursor.fetchone()
        assert op["source_amount"] == 3550.0
        
        cursor.execute("SELECT * FROM repack_operation_components WHERE operation_id = ?", (repack_id,))
        comps = cursor.fetchall()
        assert len(comps) == 3
