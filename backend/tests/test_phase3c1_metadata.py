import pytest
import sqlite3
from unittest.mock import patch, MagicMock
from backend.routers.purchase_item import get_metadata
from backend.database import get_master_states

def test_get_master_states_mapping(monkeypatch):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    
    # Mock data inside pending_masters
    mock_cursor.fetchall.return_value = [
        {"entity_type": "LEDGER", "normalized_name": "pending supplier", "original_name": "Pending Supplier", "status": "PENDING", "error_message": None},
        {"entity_type": "ITEM", "normalized_name": "syncing item", "original_name": "Syncing Item", "status": "SYNCED_WAITING_CONFIRMATION", "error_message": None},
        {"entity_type": "UOM", "normalized_name": "failed uom", "original_name": "Failed UOM", "status": "FAILED", "error_message": "Network error"},
    ]
    
    # Mock context manager
    mock_db_context = MagicMock()
    mock_db_context.__enter__.return_value = mock_conn
    monkeypatch.setattr("backend.database.get_db", lambda: mock_db_context)
    
    states = get_master_states()
    
    # 7. Expected entity grouping
    assert "ledgers" in states
    assert "stock_items" in states
    assert "uoms" in states
    
    # 1, 2, 3: Mapping tests
    assert states["ledgers"][0]["state"] == "pending"
    assert states["stock_items"][0]["state"] == "syncing"
    assert states["uoms"][0]["state"] == "failed"
    
    # 6. No internal fields leaked
    for group in states.values():
        for item in group:
            keys = set(item.keys())
            assert keys == {"name", "normalized_name", "state", "error"}

@pytest.mark.anyio
@patch("backend.routers.purchase_item.get_all_ledgers")
@patch("backend.routers.purchase_item.get_all_stock_items")
@patch("backend.routers.purchase_item.get_all_uoms")
@patch("backend.database.get_master_states")
async def test_purchase_item_metadata_contract(mock_get_master_states, mock_get_uoms, mock_get_stock, mock_get_ledgers):
    # Mock legacy arrays
    mock_get_ledgers.return_value = [
        {"name": "Confirmed Supplier", "parent": "Sundry Creditors"}
    ]
    mock_get_stock.return_value = [
        {"name": "Confirmed Item", "unit": "PCS"}
    ]
    mock_get_uoms.return_value = [
        {"name": "PCS", "formal_name": "Pieces"}
    ]
    
    mock_get_master_states.return_value = {
        "ledgers": [{"name": "Pending Supplier", "normalized_name": "pending supplier", "state": "pending", "error": None}],
        "stock_items": [],
        "uoms": []
    }
    
    result = await get_metadata()
    
    # 4 & 5: Legacy confirmed arrays remain confirmed-only
    assert result["suppliers"] == ["Confirmed Supplier"]
    assert "Pending Supplier" not in result["suppliers"]
    
    assert "Confirmed Item" in result["stock_items"]
    assert {"name": "PCS", "formal_name": "Pieces"} in result["uoms"]
    
    # 8. Existing fields are backwards compatible
    assert "suppliers" in result
    assert "stock_items" in result
    assert "uoms" in result
    assert "aliases" in result
    
    # New state section
    assert "master_states" in result
    assert result["master_states"]["ledgers"][0]["name"] == "Pending Supplier"
