import asyncio
from backend.routers.purchase_item import get_metadata
from backend.routers.purchase import get_purchase_metadata
from backend.database import get_db, init_db

def test_metadata():
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM ledgers")
        conn.execute("DELETE FROM stock_items")
        conn.execute("DELETE FROM pending_masters")
        conn.execute("DELETE FROM offline_queue")
        
        conn.execute("INSERT INTO ledgers (name, parent) VALUES ('Confirmed Supplier', 'Sundry Creditors')")
        conn.execute("INSERT INTO pending_masters (entity_type, normalized_name, original_name, queue_id, status, error_message, created_at, updated_at) VALUES ('LEDGER', 'pending supplier', 'Pending Supplier', 1, 'PENDING', NULL, 'now', 'now')")
        conn.execute("INSERT INTO pending_masters (entity_type, normalized_name, original_name, queue_id, status, error_message, created_at, updated_at) VALUES ('LEDGER', 'failed supplier', 'Failed Supplier', 2, 'FAILED', 'Some error', 'now', 'now')")
        conn.execute("INSERT INTO pending_masters (entity_type, normalized_name, original_name, queue_id, status, error_message, created_at, updated_at) VALUES ('ITEM', 'pending item', 'Pending Item', 3, 'SYNCED_WAITING_CONFIRMATION', NULL, 'now', 'now')")
        conn.commit()
    
    print("PURCHASE ITEM METADATA:")
    import json
    print(json.dumps(asyncio.run(get_metadata()), indent=2))
    print("\nPURCHASE METADATA:")
    print(json.dumps(asyncio.run(get_purchase_metadata()), indent=2))

if __name__ == "__main__":
    test_metadata()
