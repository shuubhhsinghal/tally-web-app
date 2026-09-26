import json
import re
import requests
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from backend.database import get_db, get_all_item_aliases_full, update_item_alias, delete_item_alias, get_app_setting
from backend.services.tally_response import parse_tally_response
from backend.connector.transport import tally_transport
from backend.routers.bank_statement import sanitize_xml

router = APIRouter(prefix="/api/masters", tags=["masters"])

COMPANY_NAME_SETTING_KEY = "company_name"
DEFAULT_COMPANY_NAME = "Mom's Pride"

def _current_financial_year() -> str:
    # Indian FY: 1 April - 31 March.
    now = datetime.now()
    start_year = now.year if now.month >= 4 else now.year - 1
    return f"{start_year}–{str(start_year + 1)[-2:]}"

class UpdateAliasRequest(BaseModel):
    mapped_name: str

class CreateLedgerRequest(BaseModel):
    name: str
    parent: str
    cost_center: bool = False

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
    cost_centres = []

    counts = {
        "ledgers": {"total": 0, "in_tally": 0, "in_queue": 0, "syncing": 0, "failed": 0},
        "items": {"total": 0, "in_tally": 0, "in_queue": 0, "syncing": 0, "failed": 0},
        "cost_centres": {"total": 0}
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

        # Cost centres -- read-only list, no pending/queue concept for these
        # (unlike ledgers/items, they're not created via this app's own
        # posted vouchers, so there's nothing to merge in from the queue).
        cursor.execute("SELECT name, parent FROM cost_centres ORDER BY name")
        for row in cursor.fetchall():
            cost_centres.append({"name": row["name"], "parent": row["parent"]})
        counts["cost_centres"]["total"] = len(cost_centres)

    return {
        "ledgers": ledgers,
        "items": items,
        "cost_centres": cost_centres,
        "counts": counts,
        "company_name": get_app_setting(COMPANY_NAME_SETTING_KEY, DEFAULT_COMPANY_NAME),
        "financial_year": _current_financial_year(),
    }

@router.post("/create-ledger")
async def create_ledger_endpoint(payload: CreateLedgerRequest):
    """Creates a ledger directly in Tally (falling back to the offline queue
    if it's unreachable), then mirrors it into the local `ledgers` cache with
    the cost-centre flag the caller asked for -- this is the same
    create-then-cache-or-queue pattern used for suppliers/customers elsewhere,
    just callable standalone from the Masters screen instead of only inline
    in another voucher flow."""
    name = payload.name.strip()
    parent = payload.parent.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Ledger name is required.")
    if not parent:
        raise HTTPException(status_code=400, detail="Select a group.")
    cc_flag = "Yes" if payload.cost_center else "No"

    xml_data = f"""<ENVELOPE>
      <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
      <BODY>
        <IMPORTDATA>
          <REQUESTDESC><REPORTNAME>All Masters</REPORTNAME></REQUESTDESC>
          <REQUESTDATA>
            <TALLYMESSAGE xmlns:UDF="TallyUDF">
              <LEDGER ACTION="Create" NAME="{sanitize_xml(name)}">
                <NAME>{sanitize_xml(name)}</NAME>
                <PARENT>{sanitize_xml(parent)}</PARENT>
                <ISCOSTCAPP>{cc_flag}</ISCOSTCAPP>
              </LEDGER>
            </TALLYMESSAGE>
          </REQUESTDATA>
        </IMPORTDATA>
      </BODY>
    </ENVELOPE>"""

    xml_bytes = xml_data.encode('utf-8')

    # 1. Pre-send cache check
    from backend.database import check_master_exists_locally, normalize_master_name, MasterConflictException
    try:
        norm, _ = normalize_master_name(name)
        if check_master_exists_locally('LEDGER', norm, {"parent": parent}):
            return {"status": "success", "message": f"Ledger '{name}' already exists (idempotent)."}
    except MasterConflictException as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        response = await tally_transport.post(xml_bytes, timeout=4)
        parsed = parse_tally_response(response.text, "CREATE_LEDGER")
        if not parsed["is_success"]:
            raise HTTPException(status_code=400, detail=f"Tally rejected the ledger creation: {parsed['error_message']}")

        from backend.services.tally_response import is_already_exists_success
        from backend.services.tally_verification import verify_tally_master_definition, VerificationResult

        insert_name = name
        if is_already_exists_success(parsed, "CREATE_LEDGER"):
            ver_res, canonical = await verify_tally_master_definition("LEDGER", name, {"parent": parent})
            if ver_res == VerificationResult.UNVERIFIABLE:
                raise HTTPException(status_code=400, detail="Master already exists in Tally, but its definition could not be verified. Refresh masters and try again.")
            elif ver_res == VerificationResult.CONFLICT:
                raise HTTPException(status_code=409, detail="Master already exists in Tally with a conflicting definition.")
            insert_name = canonical['name']

        # LOCAL INJECTION: update the cache only after confirmation
        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO ledgers (name, parent, cost_centre) VALUES (?, ?, ?)",
                    (insert_name, parent, payload.cost_center)
                )
                conn.commit()
        except Exception as e:
            print(f"Local ledger insert failed: {e}")

        return {"status": "success", "message": f"Ledger {insert_name} created in Tally."}
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        from backend.database import queue_master_operation, MasterFailedException
        try:
            res = await run_in_threadpool(queue_master_operation, "LEDGER", name, "CREATE_LEDGER", xml_data, payload.model_dump())
            if res.get("status") == "exists_confirmed":
                return {"status": "success", "message": f"Ledger '{name}' already confirmed."}
            elif res.get("status") in ("exists_pending", "exists_pending_concurrent"):
                return {"status": "success", "message": f"Ledger '{name}' already pending."}
        except MasterConflictException as e:
            raise HTTPException(status_code=409, detail=str(e))
        except MasterFailedException as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            print(f"Unexpected error: {e}")
            raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")

        return {"status": "queued", "message": f"Ledger {name} saved to offline queue."}
    except HTTPException:
        raise

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
