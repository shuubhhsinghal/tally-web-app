import sqlite3
import os

db_path = os.path.join("backend", "tally_sync.db")
print("DB exists:", os.path.exists(db_path))
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT id, entity_type, normalized_name, status, original_name FROM pending_masters")
    rows = cur.fetchall()
    print("Pending Masters:")
    for r in rows:
        print(r)
