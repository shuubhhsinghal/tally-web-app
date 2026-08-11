from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, UploadFile, File
import requests
import json
import os
import datetime
import traceback
from xml.sax.saxutils import escape

from backend.database import get_all_ledgers, queue_operation, get_db, update_queue_status
from backend.services.tally_response import parse_tally_response

router = APIRouter()
from backend.config import TALLY_URL

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
        
    return {
        "suppliers": sorted(list(set(suppliers)))
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
        res = queue_master_operation("LEDGER", payload.name, "CREATE_LEDGER", xml_data, {"name": payload.name, "parent": "Sundry Creditors"})
        if res.get("status") == "exists_confirmed":
            return {"status": "success", "message": f"Supplier '{payload.name}' already confirmed.", "name": payload.name}
        elif res.get("status") in ("exists_pending", "exists_pending_concurrent", "queued"):
            return {"status": "queued", "message": f"Supplier '{payload.name}' saved to offline queue.", "name": payload.name}
    except MasterConflictException as e:
        raise HTTPException(status_code=409, detail=str(e))
    except MasterFailedException as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"status": "success", "message": f"Supplier '{payload.name}' processed.", "name": payload.name}

@router.post("/extract")
def extract_invoice(file: UploadFile = File(...)):
    print(f"--- [EXTRACT] Received file: {file.filename} ({file.content_type}) ---", flush=True)
    import tempfile
    from google import genai
    from google.genai import types

    try:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not configured")

        client = genai.Client(api_key=api_key)
        
        file_bytes = file.file.read()
        print(f"--- [EXTRACT] Read {len(file_bytes)} bytes. Calling AI extraction... ---", flush=True)
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            uploaded_file = client.files.upload(file=tmp_path)
            
            today = datetime.datetime.now().strftime("%d-%m-%Y")
            year = datetime.datetime.now().year
            prompt = f"""
            The current date is {today}. Extract the following details from this purchase invoice.
            
            CRITICAL DATE RULES:
            1. Convert natural language dates ('today', 'yesterday', '1april', '31 jul') to YYYY-MM-DD.
            2. If the year is missing, assume {year}.
            3. Return only a JSON object with:
               - supplier (string)
               - invoice_number (string)
               - date (YYYY-MM-DD format)
               - amount (number)
            """
            
            response = client.models.generate_content(
                model='gemini-3.5-flash-lite',
                contents=[uploaded_file, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            
            client.files.delete(name=uploaded_file.name)
            os.unlink(tmp_path)
            
            raw_json = response.text.strip()
            data = json.loads(raw_json)
            
            results = {
                "supplier": str(data.get("supplier", "Unknown")),
                "invoice_number": str(data.get("invoice_number", "Unknown")),
                "date": str(data.get("date", datetime.datetime.now().strftime("%Y-%m-%d"))),
                "amount": float(data.get("amount", 0.0))
            }
            print(f"--- [EXTRACT] Extraction successful: {results} ---", flush=True)
            return results
            
        except Exception as inner_e:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise inner_e

    except Exception as e:
        print("\n!!! EXCEPTION IN INVOICE EXTRACTION !!!", flush=True)
        traceback.print_exc()
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n", flush=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/post")
async def post_purchase(payload: PurchaseRequest):
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
    queue_id = queue_operation("POST_VOUCHER", xml_data, payload.model_dump(), f"Purchase Invoice: {payload.invoice_number} from {payload.supplier}")

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
            
        response = requests.post(TALLY_URL, data=xml_data.encode('utf-8'), timeout=10)
        parsed = parse_tally_response(response.text, "POST_VOUCHER")
        
        if not parsed["is_success"]:
            update_queue_status(queue_id, "FAILED", parsed['error_message'])
            raise HTTPException(status_code=400, detail=f"Tally rejected the entry: {parsed['error_message']}")
            
        update_queue_status(queue_id, "SYNCED")
        return {"status": "success", "message": "Purchase entry posted successfully"}
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        # Already marked as PENDING in the DB, leave it for the background worker
        return {"status": "queued", "message": "Saved to offline queue."}
    except HTTPException:
        raise
    except Exception as e:
        update_queue_status(queue_id, "FAILED", str(e))
        raise HTTPException(status_code=500, detail=str(e))
