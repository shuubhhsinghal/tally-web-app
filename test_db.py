import sqlite3
import json
from backend.database import get_master_dependency_state, init_db, get_db

with get_db() as conn:
    # Cyrillic C in the database
    conn.execute("INSERT OR REPLACE INTO stock_items (name, unit) VALUES (?, ?)", ("ALOO BHUJA 420G/-110", "P\u0421S"))
    conn.commit()

state, msg = get_master_dependency_state("ITEM", "ALOO BHUJA 420G/-110", {"uom": "PCS"})
print(f"STATE: {state}")
print(f"MSG: {msg}")
