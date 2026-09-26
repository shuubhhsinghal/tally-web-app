from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
import requests
import json
from xml.sax.saxutils import escape
from backend.database import get_all_ledgers, queue_master_operation, queue_operation, get_db, check_master_exists_locally, normalize_master_name, MasterConflictException, MasterFailedException, update_queue_status, set_delivery_uncertain, get_master_dependency_state, is_master_pending_sync
from backend.services.tally_response import parse_tally_response
from backend.connector.transport import tally_transport

router = APIRouter()

class JournalRequest(BaseModel):
    debit_ledger: str  # Where the value is going to
    credit_ledger: str  # Where the value is coming from
    amount: float
    tally_date: str  # YYYYMMDD
    narration: str

class CreateLedgerRequest(BaseModel):
    name: str
    parent: str

def is_bank_or_cash(parent: str) -> bool:
    p = parent.lower()
    return "bank account" in p or "cash" in p

@router.get("/metadata")
async def get_journal_metadata():
    # A journal entry can legitimately debit or credit ANY ledger -- unlike
    # Payment/Receipt, there's no group to exclude here (Tally accepts a
    # Journal voucher touching a bank/cash or purchase/sales ledger just
    # fine, confirmed live against the real company).
    ledgers_out = []
    valid_groups = set()

    try:
        ledgers = get_all_ledgers()
        for l in ledgers:
            parent = l.get('parent') or ''
            name = l['name'].title()
            ledgers_out.append({"name": name, "is_pending": False})
            # New-account creation still steers away from bank/cash groups --
            # those are set up once via Settings/Tally directly, not casually
            # created mid-entry.
            if parent and not is_bank_or_cash(parent):
                valid_groups.add(parent)

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT display_name, payload FROM pending_masters WHERE entity_type = 'LEDGER'")
            for row in cursor.fetchall():
                name = row['display_name'].title()
                ledgers_out.append({"name": name, "is_pending": True})

    except Exception as e:
        print(f"Error loading ledgers: {e}")

    unique = {}
    for item in ledgers_out:
        n = item["name"].lower()
        if n not in unique or not unique[n]["is_pending"]:
            unique[n] = item

    return {
        "ledgers": sorted(list(unique.values()), key=lambda x: x["name"]),
        "groups": sorted(list(valid_groups)),
    }

@router.post("/create-ledger")
async def create_journal_ledger(payload: CreateLedgerRequest):
    xml_data = f"""<ENVELOPE>
      <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
      <BODY>
        <IMPORTDATA>
          <REQUESTDESC><REPORTNAME>All Masters</REPORTNAME></REQUESTDESC>
          <REQUESTDATA>
            <TALLYMESSAGE xmlns:UDF="TallyUDF">
              <LEDGER ACTION="Create" NAME="{escape(payload.name)}">
                <NAME.LIST><NAME>{escape(payload.name)}</NAME></NAME.LIST>
                <PARENT>{escape(payload.parent)}</PARENT>
              </LEDGER>
            </TALLYMESSAGE>
          </REQUESTDATA>
        </IMPORTDATA>
      </BODY>
    </ENVELOPE>"""

    try:
        norm, _ = normalize_master_name(payload.name)
        if check_master_exists_locally('LEDGER', norm, {"parent": payload.parent}):
            return {"status": "success", "message": f"Ledger '{payload.name}' already exists (idempotent).", "name": payload.name}
    except MasterConflictException as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        queue_payload = {"name": payload.name, "parent": payload.parent}
        result = await run_in_threadpool(queue_master_operation, "LEDGER", payload.name, "CREATE_LEDGER", xml_data, queue_payload)

        status = result.get("status")
        if status == "exists_confirmed":
            return {"status": "success", "message": "Ledger already exists in Tally", "name": payload.name}
        if status in ("exists_pending", "exists_pending_concurrent"):
            return {"status": "queued", "message": f"Ledger '{payload.name}' is already waiting to sync.", "name": payload.name}

        return {"status": "queued", "message": f"Ledger '{payload.name}' queued successfully.", "name": payload.name}
    except MasterConflictException as e:
        raise HTTPException(status_code=409, detail=str(e))
    except MasterFailedException as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")

@router.post("/post")
async def post_journal(payload: JournalRequest, request: Request):
    current_user = request.state.user

    safe_debit = escape(str(payload.debit_ledger))
    safe_credit = escape(str(payload.credit_ledger))
    safe_nar = escape(str(payload.narration))

    xml_data = f"""<ENVELOPE>
  <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
  <BODY><IMPORTDATA>
    <REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC>
    <REQUESTDATA><TALLYMESSAGE>
      <VOUCHER VCHTYPE="Journal" ACTION="Create">
        <DATE>{payload.tally_date}</DATE>
        <VOUCHERTYPENAME>Journal</VOUCHERTYPENAME>
        <NARRATION>{safe_nar}</NARRATION>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{safe_debit}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          <AMOUNT>-{payload.amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{safe_credit}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          <AMOUNT>{payload.amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
      </VOUCHER>
    </TALLYMESSAGE></REQUESTDATA>
  </IMPORTDATA></BODY>
</ENVELOPE>"""

    # Queue-First Architecture: Always save transaction to DB before attempting to send
    queue_payload = payload.model_dump()
    queue_payload['created_by'] = current_user['name']
    queue_id = queue_operation("POST_VOUCHER", xml_data, queue_payload, f"Journal: {payload.amount} ({payload.debit_ledger} / {payload.credit_ledger})")

    try:
        for role, ledger_name in (("debit", payload.debit_ledger), ("credit", payload.credit_ledger)):
            state, error_msg = get_master_dependency_state("LEDGER", ledger_name, None)
            if state == "FAILED":
                update_queue_status(queue_id, "FAILED", f"Cannot post because ledger '{ledger_name}' failed to sync to Tally.")
                raise HTTPException(status_code=400, detail=f"Cannot post because ledger '{ledger_name}' failed to sync to Tally. Resolve the master first.")
            if state == "MISSING":
                update_queue_status(queue_id, "FAILED", f"Cannot post because ledger '{ledger_name}' is not available in Tally or pending sync.")
                raise HTTPException(status_code=400, detail=f"Cannot post because ledger '{ledger_name}' is not available in Tally or pending sync. Create or refresh the master before posting.")
            if state == "CONFLICT":
                update_queue_status(queue_id, "FAILED", f"Definition conflict for ledger '{ledger_name}': {error_msg}")
                raise HTTPException(status_code=409, detail=f"Cannot post due to definition conflict for ledger '{ledger_name}': {error_msg}")
            if is_master_pending_sync(state):
                return {"status": "queued", "reason": "pending_master_dependency", "message": f"Journal entry saved to offline queue because ledger '{ledger_name}' is still pending sync to Tally."}

        set_delivery_uncertain(queue_id, True)
        response = await tally_transport.post(xml_data.encode('utf-8'), timeout=10)
        parsed = parse_tally_response(response.text, "POST_VOUCHER")

        if not parsed["is_success"]:
            set_delivery_uncertain(queue_id, False)
            update_queue_status(queue_id, "FAILED", parsed['error_message'])
            raise HTTPException(status_code=400, detail=f"Tally rejected the entry: {parsed['error_message']}")

        set_delivery_uncertain(queue_id, False)
        update_queue_status(queue_id, "SYNCED")
        return {"status": "success", "message": "Journal entry posted successfully"}
    except requests.exceptions.ConnectTimeout:
        set_delivery_uncertain(queue_id, False)
        return {"status": "queued", "message": "Saved to offline queue."}
    except requests.exceptions.Timeout:
        # Ambiguous: connection was established and the request was sent, but no
        # response came back in time -- leave delivery_uncertain set (already
        # persisted above) so a manual retry is blocked until verified in Tally.
        return {"status": "queued", "message": "Saved to offline queue."}
    except requests.exceptions.ConnectionError:
        set_delivery_uncertain(queue_id, False)
        return {"status": "queued", "message": "Saved to offline queue."}
    except HTTPException:
        raise
    except Exception as e:
        update_queue_status(queue_id, "FAILED", str(e))
        raise HTTPException(status_code=500, detail=str(e))
