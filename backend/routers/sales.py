from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import requests
import json
import os
from xml.sax.saxutils import escape
from backend.database import get_all_ledgers, queue_operation, update_queue_status
from backend.services.tally_response import parse_tally_response

router = APIRouter()
from backend.config import TALLY_URL

class SalesRequest(BaseModel):
    ledger: str
    amount: float
    tally_date: str
    narration: str

@router.get("/metadata")
async def get_sales_metadata():
    customers = []
    try:
        ledgers = get_all_ledgers()
        for l in ledgers:
            parent = (l.get('parent') or '').lower()
            name = l['name'].title()
            if "debtor" in parent or "cash" in parent or "bank" in parent or "paytm" in parent or "gpay" in parent:
                customers.append(name)
    except Exception:
        pass

    return {"customers": sorted(list(set(customers)))}

@router.post("/preview")
async def preview_sales(payload: SalesRequest):
    return {
        "ledger": payload.ledger,
        "amount": payload.amount,
        "tally_date": payload.tally_date,
        "narration": payload.narration,
        "status": "success"
    }

@router.post("/post")
async def post_sales(payload: SalesRequest):
    safe_ledger = escape(str(payload.ledger))
    safe_nar = escape(str(payload.narration))
    
    # Auto-derive cost center
    cost_center = None
    ledger_lower = payload.ledger.lower()
    if "mahagun" in ledger_lower:
        cost_center = "Mahagun"
    elif "vvip" in ledger_lower:
        cost_center = "Vvip"
    elif "gulshan" in ledger_lower:
        cost_center = "Gulshan"
        
    allocation = ""
    if cost_center:
        allocation = f"""
              <CATEGORYALLOCATIONS.LIST>
                <CATEGORY>Primary Cost Category</CATEGORY>
                <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
                <COSTCENTREALLOCATIONS.LIST>
                  <NAME>{escape(cost_center)}</NAME>
                  <AMOUNT>{payload.amount}</AMOUNT>
                </COSTCENTREALLOCATIONS.LIST>
              </CATEGORYALLOCATIONS.LIST>"""
              
    xml_data = f"""<ENVELOPE>
  <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
  <BODY><IMPORTDATA>
    <REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC>
    <REQUESTDATA><TALLYMESSAGE>
      <VOUCHER VCHTYPE="Sales" ACTION="Create">
        <DATE>{payload.tally_date}</DATE>
        <NARRATION>{safe_nar}</NARRATION>
        <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{safe_ledger}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          <AMOUNT>-{payload.amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>Sales</LEDGERNAME>
          <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          <AMOUNT>{payload.amount}</AMOUNT>{allocation}
        </ALLLEDGERENTRIES.LIST>
      </VOUCHER>
    </TALLYMESSAGE></REQUESTDATA>
  </IMPORTDATA></BODY>
</ENVELOPE>"""

    # Queue-First Architecture: Always save transaction to DB before attempting to send
    queue_id = queue_operation("POST_VOUCHER", xml_data, payload.model_dump(), f"Sales: {payload.amount} from {payload.ledger}")

    try:
        response = requests.post(TALLY_URL, data=xml_data.encode('utf-8'), timeout=10)
        parsed = parse_tally_response(response.text, "POST_VOUCHER")
        
        if not parsed["is_success"]:
            update_queue_status(queue_id, "FAILED", parsed['error_message'])
            raise HTTPException(status_code=400, detail=f"Tally rejected the entry: {parsed['error_message']}")
            
        update_queue_status(queue_id, "SYNCED")
        return {"status": "success", "message": "Sales entry posted successfully"}
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        # Already marked as PENDING in the DB, leave it for the background worker
        return {"status": "queued", "message": "Saved to offline queue."}
    except HTTPException:
        raise
    except Exception as e:
        update_queue_status(queue_id, "FAILED", str(e))
        raise HTTPException(status_code=500, detail=str(e))
