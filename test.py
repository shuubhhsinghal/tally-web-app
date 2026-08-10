import json
from backend.database import get_db

def check_pending_master_exists_locally(entity_type: str, normalized_name: str, new_payload: dict):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM pending_masters WHERE entity_type = ? AND normalized_name = ?", (entity_type, normalized_name))
        pending_row = cursor.fetchone()
        if pending_row:
            if pending_row["status"] == "FAILED":
                return False # Let it retry
            
            cursor.execute("SELECT payload FROM offline_queue WHERE id = ?", (pending_row['queue_id'],))
            queue_row = cursor.fetchone()
            pending_payload = json.loads(queue_row['payload']) if queue_row and queue_row['payload'] else {}
            
            from backend.database import _check_definitions_conflict, MasterConflictException
            if _check_definitions_conflict(entity_type, pending_payload, new_payload):
                raise MasterConflictException("Materially conflicting master definition already pending.")
            return True
        return False
