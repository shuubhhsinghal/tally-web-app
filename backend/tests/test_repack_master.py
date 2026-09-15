import pytest
from fastapi.testclient import TestClient
from backend.main import app
import json

client = TestClient(app)

def test_create_repack_product(monkeypatch):
    # Mock database interactions
    class MockCursor:
        def execute(self, query, params=None):
            pass
        def fetchone(self):
            return {"unit": "KG"}
        def fetchall(self):
            return []
        @property
        def lastrowid(self):
            return 1
            
    class MockConn:
        def cursor(self):
            return MockCursor()
        def commit(self):
            pass
            
    class MockContextManager:
        def __enter__(self):
            return MockConn()
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    import backend.routers.repack
    import backend.database
    
    monkeypatch.setattr(backend.database, "get_db", lambda: MockContextManager())
    monkeypatch.setattr(backend.database, "check_master_exists_locally", lambda type, name, params: False)
    monkeypatch.setattr(backend.database, "queue_master_operation", lambda type, name, op, xml, param: {"status": "queued"})
    monkeypatch.setattr(backend.database, "insert_product_conversion", lambda *args, **kwargs: 1)
    
    payload = {
        "source_stock_item": "Beetroot Chips",
        "new_product_name": "Beetroot Chips 250G",
        "output_unit": "PCS",
        "weight": 250,
        "weight_unit": "G",
        "conversion_factor": 0.25
    }
    
    res = client.post("/api/repack/product", json=payload)
    assert res.status_code == 200
    assert res.json()["status"] == "success"

def test_duplicate_product_prevention(monkeypatch):
    import backend.routers.repack
    import backend.database
    
    class MockCursor:
        def execute(self, query, params=None):
            pass
        def fetchone(self):
            return {"unit": "KG"}

    class MockConn:
        def cursor(self):
            return MockCursor()
        def commit(self):
            pass
            
    class MockContextManager:
        def __enter__(self):
            return MockConn()
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass
            
    monkeypatch.setattr(backend.database, "get_db", lambda: MockContextManager())
    # Return True to simulate existing master
    monkeypatch.setattr(backend.database, "check_master_exists_locally", lambda type, name, params: True if type == "ITEM" else False)
    
    payload = {
        "source_stock_item": "Beetroot Chips",
        "new_product_name": "Beetroot Chips 250G",
        "output_unit": "PCS",
        "weight": 250,
        "weight_unit": "G",
        "conversion_factor": 0.25
    }
    
    res = client.post("/api/repack/product", json=payload)
    assert res.status_code == 409
    assert "already exists" in res.json()["detail"]

def test_invalid_weight():
    payload = {
        "source_stock_item": "Beetroot Chips",
        "new_product_name": "Beetroot Chips 250G",
        "output_unit": "PCS",
        "weight": -250,
        "weight_unit": "G",
        "conversion_factor": 0.25
    }
    res = client.post("/api/repack/product", json=payload)
    assert res.status_code == 400
