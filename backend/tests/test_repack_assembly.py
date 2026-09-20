import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db
from backend.connector.manager import connector_manager

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
    # Component costs now come from get_purchase_rate (this app's own latest-
    # rate tracking), not a live Tally Godown valuation -- seed it directly.
    # Explicitly simulate Tally being unreachable (rather than relying on the
    # real dev Tally's ambient state, which is inconsistent) so the
    # stock-sufficiency check is deterministically skipped by the upfront
    # reachability ping in execute_repack.
    from backend.database import record_purchase_rate
    record_purchase_rate("bulk chips", 100.0, "Test Supplier", "2026-09-01")
    record_purchase_rate("300g box", 5.0, "Test Supplier", "2026-09-01")
    record_purchase_rate("label", 0.50, "Test Supplier", "2026-09-01")

    import requests
    def _simulate_offline(*a, **k):
        raise requests.exceptions.ConnectionError("simulated offline")
    monkeypatch.setattr(requests, "get", _simulate_offline)

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


def test_execute_repack_records_rate_history(monkeypatch):
    from backend.database import record_purchase_rate
    record_purchase_rate("bulk chips", 100.0, "Test Supplier", "2026-09-01")
    record_purchase_rate("300g box", 5.0, "Test Supplier", "2026-09-01")

    import requests
    def _simulate_offline(*a, **k):
        raise requests.exceptions.ConnectionError("simulated offline")
    monkeypatch.setattr(requests, "get", _simulate_offline)

    payload = {
        "new_product_name": "Test Rate History Item",
        "output_unit": "PCS",
        "components": [
            {"item_name": "Bulk Chips", "unit": "KGS", "quantity": 0.3},
            {"item_name": "300G Box", "unit": "PCS", "quantity": 1.0},
        ]
    }
    conv_id = client.post("/api/repack/product", json=payload).json()["conversion_id"]

    exec_payload = {
        "date": "2026-09-15",
        "store_name": "Test Store",
        "conversion_id": conv_id,
        "dest_qty": 10.0
    }
    response = client.post("/api/repack/execute", json=exec_payload)
    assert response.status_code == 200

    # 10 * 0.3 * 100 + 10 * 1 * 5 = 300 + 50 = 350; rate = 350 / 10 = 35.0
    from backend.database import get_purchase_rate
    assert get_purchase_rate("Test Rate History Item") == 35.0

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM purchase_rate_pending_entries WHERE stock_item_name = 'test rate history item'")
        row = cursor.fetchone()
        assert row is not None
        assert row["rate"] == 35.0
        assert row["source_type"] == "repack"
        assert row["qty"] == 10.0
        assert row["supplier"] is None

    hist_res = client.get("/api/purchase-item/return-item-history", params={"item_name": "Test Rate History Item"})
    assert hist_res.status_code == 200
    repack_entries = [e for e in hist_res.json()["entries"] if e["origin"] == "repack"]
    assert len(repack_entries) == 1
    assert repack_entries[0]["rate"] == 35.0


def test_execute_repack_ignores_godown_valuation_rate_when_reachable(monkeypatch):
    # Even when Tally IS reachable and its own Godown valuation would give a
    # different (e.g. weighted-average) rate, the repack's cost must be
    # computed from get_purchase_rate -- not from Tally's live valuation.
    from backend.database import record_purchase_rate
    record_purchase_rate("bulk chips", 100.0, "Test Supplier", "2026-09-01")
    record_purchase_rate("300g box", 5.0, "Test Supplier", "2026-09-01")

    monkeypatch.setattr(connector_manager, "is_tally_reachable", lambda: True)

    async def mock_get_godown_stock(item_name, godown_name):
        # Deliberately a different rate than get_purchase_rate, simulating a
        # weighted-average Godown valuation blending old and new stock.
        return {"qty": 1000.0, "rate": 999.0, "amount": 999000.0}

    import backend.services.tally_godown_stock
    monkeypatch.setattr(backend.services.tally_godown_stock, "get_godown_stock", mock_get_godown_stock)

    payload = {
        "new_product_name": "Test Ignores Valuation Item",
        "output_unit": "PCS",
        "components": [
            {"item_name": "Bulk Chips", "unit": "KGS", "quantity": 0.3},
            {"item_name": "300G Box", "unit": "PCS", "quantity": 1.0},
        ]
    }
    conv_id = client.post("/api/repack/product", json=payload).json()["conversion_id"]

    exec_payload = {
        "date": "2026-09-15",
        "store_name": "Test Store",
        "conversion_id": conv_id,
        "dest_qty": 10.0
    }
    response = client.post("/api/repack/execute", json=exec_payload)
    assert response.status_code == 200

    # 10*0.3*100 + 10*1*5 = 300 + 50 = 350 -- from get_purchase_rate, not the
    # mocked Godown "rate": 999 a weighted-average valuation would have given.
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT source_amount FROM repack_operations WHERE dest_item_name = ?", ("Test Ignores Valuation Item",))
        row = cursor.fetchone()
        assert row["source_amount"] == 350.0


def test_execute_repack_blocks_on_insufficient_stock_when_tally_reachable(monkeypatch):
    from backend.database import record_purchase_rate
    record_purchase_rate("bulk chips", 100.0, "Test Supplier", "2026-09-01")

    monkeypatch.setattr(connector_manager, "is_tally_reachable", lambda: True)

    async def mock_get_godown_stock(item_name, godown_name):
        return {"qty": 1.0, "rate": 100.0, "amount": 100.0}  # far less than required

    import backend.services.tally_godown_stock
    monkeypatch.setattr(backend.services.tally_godown_stock, "get_godown_stock", mock_get_godown_stock)

    payload = {
        "new_product_name": "Test Insufficient Stock Item",
        "output_unit": "PCS",
        "components": [
            {"item_name": "Bulk Chips", "unit": "KGS", "quantity": 0.3},
        ]
    }
    conv_id = client.post("/api/repack/product", json=payload).json()["conversion_id"]

    exec_payload = {
        "date": "2026-09-15",
        "store_name": "Test Store",
        "conversion_id": conv_id,
        "dest_qty": 100.0  # needs 30 KGS, only 1 KGS "available"
    }
    response = client.post("/api/repack/execute", json=exec_payload)
    assert response.status_code == 400
    assert "insufficient stock" in response.json()["detail"].lower()
