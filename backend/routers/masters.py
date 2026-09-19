import json
import re
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.database import get_db, get_all_item_aliases_full, update_item_alias, delete_item_alias

router = APIRouter(prefix="/api/masters", tags=["masters"])

class UpdateAliasRequest(BaseModel):
    mapped_name: str

def normalize_name(name: str) -> str:
    if not name:
        return ""
    return re.sub(r'\s+', ' ', name).strip().lower()

def map_status(status: str) -> str:
    s = (status or "").upper()
    if s == "PENDING":
        return "in_queue"
    if s == "SYNCED_WAITING_CONFIRMATION":
        return "syncing"
    if s == "FAILED":
        return "failed"
    return "in_queue"

@router.get("")
def get_masters():
    ledgers = []
    items = []
    
    counts = {
        "ledgers": {"total": 0, "in_tally": 0, "in_queue": 0, "syncing": 0, "failed": 0},
        "items": {"total": 0, "in_tally": 0, "in_queue": 0, "syncing": 0, "failed": 0}
    }
    
    confirmed_ledgers_map = {}
    confirmed_items_map = {}
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Confirmed Ledgers
        cursor.execute("SELECT name, parent, cost_centre FROM ledgers")
        for row in cursor.fetchall():
            name = row["name"]
            norm = normalize_name(name)
            confirmed_ledgers_map[norm] = True
            
            ledgers.append({
                "name": name,
                "parent": row["parent"] or "Other / Unknown",
                "cost_centre": bool(row["cost_centre"]),
                "status": "in_tally",
                "error": None
            })
            counts["ledgers"]["in_tally"] += 1
            counts["ledgers"]["total"] += 1

        # Confirmed Items
        cursor.execute("SELECT name, unit FROM stock_items")
        for row in cursor.fetchall():
            name = row["name"]
            norm = normalize_name(name)
            confirmed_items_map[norm] = True
            
            items.append({
                "name": name,
                "unit": row["unit"] or "-",
                "status": "in_tally",
                "error": None
            })
            counts["items"]["in_tally"] += 1
            counts["items"]["total"] += 1
            
        # Pending Ledgers
        cursor.execute("""
            SELECT pm.normalized_name, pm.original_name, pm.status, pm.error_message, oq.payload 
            FROM pending_masters pm 
            LEFT JOIN offline_queue oq ON pm.queue_id = oq.id 
            WHERE pm.entity_type = 'LEDGER'
        """)
        for row in cursor.fetchall():
            norm = row["normalized_name"]
            if norm in confirmed_ledgers_map:
                continue
                
            payload_str = row["payload"]
            parent = "Other / Unknown"
            if payload_str:
                try:
                    payload = json.loads(payload_str)
                    if isinstance(payload, dict) and payload.get("parent"):
                        parent = payload["parent"]
                except:
                    pass
            
            mapped_stat = map_status(row["status"])
            ledgers.append({
                "name": row["original_name"] or norm,
                "parent": parent,
                "cost_centre": False,
                "status": mapped_stat,
                "error": row["error_message"]
            })
            counts["ledgers"][mapped_stat] = counts["ledgers"].get(mapped_stat, 0) + 1
            counts["ledgers"]["total"] += 1
            
        # Pending Items
        cursor.execute("""
            SELECT pm.normalized_name, pm.original_name, pm.status, pm.error_message, oq.payload 
            FROM pending_masters pm 
            LEFT JOIN offline_queue oq ON pm.queue_id = oq.id 
            WHERE pm.entity_type = 'ITEM'
        """)
        for row in cursor.fetchall():
            norm = row["normalized_name"]
            if norm in confirmed_items_map:
                continue
                
            payload_str = row["payload"]
            unit = "-"
            if payload_str:
                try:
                    payload = json.loads(payload_str)
                    if isinstance(payload, dict) and payload.get("uom"):
                        unit = payload["uom"]
                except:
                    pass
            
            mapped_stat = map_status(row["status"])
            items.append({
                "name": row["original_name"] or norm,
                "unit": unit,
                "status": mapped_stat,
                "error": row["error_message"]
            })
            counts["items"][mapped_stat] = counts["items"].get(mapped_stat, 0) + 1
            counts["items"]["total"] += 1
            
    return {
        "ledgers": ledgers,
        "items": items,
        "counts": counts
    }

@router.get("/aliases")
def list_item_aliases():
    return {"aliases": get_all_item_aliases_full()}

@router.put("/aliases/{alias_id}")
def update_item_alias_endpoint(alias_id: int, payload: UpdateAliasRequest):
    mapped_name = payload.mapped_name.strip()
    if not mapped_name:
        raise HTTPException(status_code=400, detail="mapped_name cannot be empty")
    if not update_item_alias(alias_id, mapped_name):
        raise HTTPException(status_code=404, detail="Alias not found")
    return {"status": "success"}

@router.delete("/aliases/{alias_id}")
def delete_item_alias_endpoint(alias_id: int):
    if not delete_item_alias(alias_id):
        raise HTTPException(status_code=404, detail="Alias not found")
    return {"status": "success"}
