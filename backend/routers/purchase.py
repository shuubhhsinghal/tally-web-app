from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
import requests
from xml.sax.saxutils import escape

from backend.database import get_all_ledgers, queue_operation, get_db, update_queue_status, set_delivery_uncertain
from backend.services.tally_response import parse_tally_response
from backend.connector.transport import tally_transport
from backend.services.auth_helpers import enforce_store_access

router = APIRouter()

class PurchaseRequest(BaseModel):
    supplier: str
    invoice_number: str
    amount: float
    tally_date: str # YYYYMMDD
    cost_center: str
    narration: str

class NewSupplierRequest(BaseModel):
    name: str

@router.get("/metadata")
async def get_purchase_metadata():
    suppliers = []
    try:
        ledgers = get_all_ledgers()
        for l in ledgers:
            parent = (l.get('parent') or '').lower()
            name = l['name'].title()
            if "creditor" in parent:
                suppliers.append(name)
    except Exception as e:
        print(f"Error loading ledgers: {e}")
        
    from backend.database import get_active_stores
    active_stores = get_active_stores()
    store_names = [s['store_name'] for s in active_stores]

    return {
        "suppliers": sorted(list(set(suppliers))),
        "stores": store_names
    }

@router.post("/create-supplier")
async def create_supplier(payload: NewSupplierRequest):
    xml_data = f"""<ENVELOPE>
      <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
      <BODY>
        <IMPORTDATA>
          <REQUESTDESC><REPORTNAME>All Masters</REPORTNAME></REQUESTDESC>
          <REQUESTDATA>
            <TALLYMESSAGE xmlns:UDF="TallyUDF">
              <LEDGER ACTION="Create" NAME="{escape(payload.name)}">
                <NAME.LIST><NAME>{escape(payload.name)}</NAME></NAME.LIST>
                <PARENT>Sundry Creditors</PARENT>
              </LEDGER>
            </TALLYMESSAGE>
          </REQUESTDATA>
        </IMPORTDATA>
      </BODY>
    </ENVELOPE>"""
    
    # 1. Pre-send cache check
    from backend.database import check_master_exists_locally, normalize_master_name, MasterConflictException
    try:
        norm, _ = normalize_master_name(payload.name)
        if check_master_exists_locally('LEDGER', norm, {"parent": "Sundry Creditors"}):
            return {"status": "success", "message": f"Supplier '{payload.name}' already exists (idempotent).", "name": payload.name}
    except MasterConflictException as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    from backend.database import queue_master_operation, MasterFailedException
    try:
        res = await run_in_threadpool(queue_master_operation, "LEDGER", payload.name, "CREATE_LEDGER", xml_data, {"name": payload.name, "parent": "Sundry Creditors"})
        if res.get("status") == "exists_confirmed":
            return {"status": "success", "message": f"Supplier '{payload.name}' already confirmed.", "name": payload.name}
        elif res.get("status") in ("exists_pending", "exists_pending_concurrent", "queued"):
            return {"status": "queued", "message": f"Supplier '{payload.name}' saved to offline queue.", "name": payload.name}
    except MasterConflictException as e:
        raise HTTPException(status_code=409, detail=str(e))
    except MasterFailedException as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")

    return {"status": "success", "message": f"Supplier '{payload.name}' processed.", "name": payload.name}

@router.post("/post")
async def post_purchase(payload: PurchaseRequest, request: Request):
    current_user = request.state.user
    # An "unallocated" (no cost centre) purchase can't be attributed to a
    # staff account's store, so it's owner-only; a staff account posting
    # with a cost centre must match their own store.
    if not payload.cost_center or payload.cost_center.lower() == "none":
        if not current_user['is_owner']:
            raise HTTPException(status_code=403, detail="Your account must select a store for this purchase.")
    else:
        enforce_store_access(current_user, payload.cost_center, "post purchases for")

    safe_supplier = escape(str(payload.supplier))
    safe_inv = escape(str(payload.invoice_number))
    safe_nar = escape(str(payload.narration))
    
    cost_center_xml = ""
    if payload.cost_center and payload.cost_center.lower() != "none":
        safe_cc = escape(str(payload.cost_center))
        cost_center_xml = f"""
              <CATEGORYALLOCATIONS.LIST>
                <CATEGORY>Primary Cost Category</CATEGORY>
                <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                <COSTCENTREALLOCATIONS.LIST>
                  <NAME>{safe_cc}</NAME>
                  <AMOUNT>-{payload.amount}</AMOUNT>
                </COSTCENTREALLOCATIONS.LIST>
              </CATEGORYALLOCATIONS.LIST>"""
              
    xml_data = f"""<ENVELOPE>
  <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
  <BODY><IMPORTDATA>
    <REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC>
    <REQUESTDATA><TALLYMESSAGE>
      <VOUCHER VCHTYPE="Purchase" ACTION="Create">
        <DATE>{payload.tally_date}</DATE>
        <REFERENCE>{safe_inv}</REFERENCE>
        <VOUCHERNUMBER>{safe_inv}</VOUCHERNUMBER>
        <NARRATION>{safe_nar}</NARRATION>
        <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{safe_supplier}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          <AMOUNT>{payload.amount}</AMOUNT>
          <BILLALLOCATIONS.LIST>
            <NAME>{safe_inv}</NAME><BILLTYPE>New Ref</BILLTYPE><AMOUNT>{payload.amount}</AMOUNT>
          </BILLALLOCATIONS.LIST>
        </ALLLEDGERENTRIES.LIST>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>Purchases</LEDGERNAME>
          <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          <AMOUNT>-{payload.amount}</AMOUNT>{cost_center_xml}
        </ALLLEDGERENTRIES.LIST>
      </VOUCHER>
    </TALLYMESSAGE></REQUESTDATA>
  </IMPORTDATA></BODY>
</ENVELOPE>"""

    # Queue-First Architecture: Always save transaction to DB before attempting to send
    queue_payload = payload.model_dump()
    queue_payload['created_by'] = current_user['name']
    queue_id = queue_operation("POST_VOUCHER", xml_data, queue_payload, f"Purchase Invoice: {payload.invoice_number} from {payload.supplier}")

    try:
        from backend.database import get_master_dependency_state, is_master_pending_sync
        state, error_msg = get_master_dependency_state("LEDGER", payload.supplier, None)
        if state == "FAILED":
            update_queue_status(queue_id, "FAILED", f"Cannot post purchase because ledger '{payload.supplier}' failed to sync to Tally.")
            raise HTTPException(status_code=400, detail=f"Cannot post purchase because ledger '{payload.supplier}' failed to sync to Tally. Resolve the master first.")
        elif state == "MISSING":
            update_queue_status(queue_id, "FAILED", f"Cannot post purchase because ledger '{payload.supplier}' is not available in Tally or pending sync.")
            raise HTTPException(status_code=400, detail=f"Cannot post purchase because ledger '{payload.supplier}' is not available in Tally or pending sync. Create or refresh the master before posting.")
        elif state == "CONFLICT":
            update_queue_status(queue_id, "FAILED", f"Definition conflict for ledger '{payload.supplier}': {error_msg}")
            raise HTTPException(status_code=409, detail=f"Cannot post purchase due to definition conflict for ledger '{payload.supplier}': {error_msg}")
        elif is_master_pending_sync(state):
            # Leave as PENDING
            return {"status": "queued", "reason": "pending_master_dependency", "message": "Purchase saved to offline queue because a required master is still pending sync to Tally."}
            
        set_delivery_uncertain(queue_id, True)
        response = await tally_transport.post(xml_data.encode('utf-8'), timeout=10)
        parsed = parse_tally_response(response.text, "POST_VOUCHER")

        if not parsed["is_success"]:
            set_delivery_uncertain(queue_id, False)
            update_queue_status(queue_id, "FAILED", parsed['error_message'])
            raise HTTPException(status_code=400, detail=f"Tally rejected the entry: {parsed['error_message']}")

        set_delivery_uncertain(queue_id, False)
        update_queue_status(queue_id, "SYNCED")
        return {"status": "success", "message": "Purchase entry posted successfully"}
    except requests.exceptions.ConnectTimeout:
        # The connection itself never established (Tally's address is unreachable)
        # -- as safe as ConnectionError, nothing was ever sent.
        set_delivery_uncertain(queue_id, False)
        return {"status": "queued", "message": "Saved to offline queue."}
    except requests.exceptions.Timeout:
        # Ambiguous: connection was established and the request was sent, but no
        # response came back in time -- Tally may have processed it before the
        # response was lost. Leave delivery_uncertain set (already persisted
        # above) so a manual retry is blocked until someone verifies in Tally.
        return {"status": "queued", "message": "Saved to offline queue."}
    except requests.exceptions.ConnectionError:
        # Request never reached Tally at all -- safe to clear and leave PENDING.
        set_delivery_uncertain(queue_id, False)
        return {"status": "queued", "message": "Saved to offline queue."}
    except HTTPException:
        raise
    except Exception as e:
        update_queue_status(queue_id, "FAILED", str(e))
        raise HTTPException(status_code=500, detail=str(e))
