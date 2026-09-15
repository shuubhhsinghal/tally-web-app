import os
import sqlite3
import pytest
from fastapi.testclient import TestClient

# Set up test environment variables
os.environ["TESTING"] = "true"

from backend.main import app
from backend.database import init_db, get_db

client = TestClient(app)

def test_repack_stock_items_api_returns_confirmed_items():
    # 1. Initialize the test database
    init_db()
    
    # 2. Insert a confirmed stock item directly into the database
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM stock_items") # Clear existing test data
        cursor.execute(
            "INSERT INTO stock_items (name, unit, last_purchase_rate, last_purchase_date) VALUES (?, ?, ?, ?)",
            ("Test Beetroot Chips", "KG", 150.0, "2026-09-15")
        )
        conn.commit()

    # 3. Call the API
    response = client.get("/api/repack/stock-items")
    assert response.status_code == 200
    
    data = response.json()
    
    # 4. Verify the API returned the item with the correct shape {name, base_unit}
    assert isinstance(data, list)
    assert len(data) >= 1
    
    found = False
    for item in data:
        if item["name"] == "Test Beetroot Chips":
            assert item["base_unit"] == "KG"
            found = True
            break
            
    assert found, "Test stock item was not returned by the API"
    print("Regression test passed! The API returned:", [i for i in data if i["name"] == "Test Beetroot Chips"])

if __name__ == "__main__":
    test_repack_stock_items_api_returns_confirmed_items()
