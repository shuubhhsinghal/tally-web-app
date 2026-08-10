import sqlite3
import os
import json
from backend.database import queue_master_operation, get_master_dependency_state, is_master_pending_sync

db_path = os.path.join("backend", "tally_sync.db")
print("DB Path:", db_path)

res = queue_master_operation("LEDGER", "Sowmya Trading Company", "CREATE_LEDGER", "<xml></xml>", {"name": "Sowmya Trading Company", "parent": "Sundry Creditors"})
print("Queue operation result:", res)

with sqlite3.connect(db_path) as conn:
    cur = conn.cursor()
    cur.execute("UPDATE pending_masters SET status = 'SYNCED_WAITING_CONFIRMATION' WHERE normalized_name = 'sowmya trading company'")
    conn.commit()

state, err = get_master_dependency_state("LEDGER", "Sowmya Trading Company", None)
print("State from get_master_dependency_state:", state, "Error:", err)
print("is_master_pending_sync(state):", is_master_pending_sync(state))

with sqlite3.connect(db_path) as conn:
    cur = conn.cursor()
    cur.execute("DELETE FROM pending_masters WHERE normalized_name = 'sowmya trading company'")
    cur.execute("DELETE FROM offline_queue WHERE operation_type = 'CREATE_LEDGER'")
    conn.commit()
