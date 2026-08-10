from typing import Optional, List
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
import requests
import os
import json
from xml.sax.saxutils import escape
from backend.database import get_all_ledgers, queue_master_operation, queue_operation, get_db, check_master_exists_locally, normalize_master_name, MasterConflictException
from backend.services.tally_response import parse_tally_response

router = APIRouter()
from backend.config import TALLY_URL

class PaymentRequest(BaseModel):
    mode: Optional[str] = None # kept for backwards compatibility
    debit_ledger: str # The expense/party account
    credit_ledger: str # Bank/Cash account
    amount: float
    tally_date: str # YYYYMMDD
    narration: str
    cost_center: Optional[str] = None

class CreateLedgerRequest(BaseModel):
    name: str
    parent: str

def is_bank_or_cash(parent: str) -> bool:
    p = parent.lower()
    return "bank account" in p or "cash" in p

def is_purchase_or_sales(parent: str) -> bool:
    p = parent.lower()
    return "purchase" in p or "sales" in p

@router.get("/metadata")
async def get_payment_metadata():
    expense_paid_to = []
    party_paid_to = []
    paid_from = []
    valid_groups = set()
    
    try:
        ledgers = get_all_ledgers()
        
        for l in ledgers:
            parent = l.get('parent') or ''
            name = l['name'].title()
            has_cc = bool(l.get("cost_centre"))
            
            # Group collection for new accounts
            if parent and not is_bank_or_cash(parent) and not is_purchase_or_sales(parent):
                valid_groups.add(parent)
            
            is_bc = is_bank_or_cash(parent)
            is_ps = is_purchase_or_sales(parent)
            
            # EXPENSE PAID TO
            if has_cc and not is_ps and not is_bc:
                expense_paid_to.append({"name": name, "is_pending": False})
                
            # PARTY / OTHER PAID TO
            if not is_ps and not is_bc:
                party_paid_to.append({"name": name, "is_pending": False})
                
            # PAID FROM
            if is_bc:
                paid_from.append({"name": name, "is_pending": False})
                
        # Also fetch pending masters
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT display_name, payload FROM pending_masters WHERE entity_type = 'LEDGER'")
            for row in cursor.fetchall():
                name = row['display_name'].title()
                try:
                    payload = json.loads(row['payload'])
                except:
                    payload = {}
                parent = payload.get("parent", "")
                has_cc = bool(payload.get("cost_center"))
                
                is_bc = is_bank_or_cash(parent)
                is_ps = is_purchase_or_sales(parent)
                
                if has_cc and not is_ps and not is_bc:
                    expense_paid_to.append({"name": name, "is_pending": True})
                    
                if not is_ps and not is_bc:
                    party_paid_to.append({"name": name, "is_pending": True})
                
    except Exception as e:
        print(f"Error loading ledgers: {e}")
        
    def sort_ledgers(lst):
        # Unique by name, preserve is_pending
        unique = {}
        for item in lst:
            n = item["name"].lower()
            if n not in unique or not unique[n]["is_pending"]:
                unique[n] = item
        return sorted(list(unique.values()), key=lambda x: x["name"])

    return {
        "expense_paid_to": sort_ledgers(expense_paid_to),
        "party_paid_to": sort_ledgers(party_paid_to),
        "paid_from": sort_ledgers(paid_from),
        "groups": sorted(list(valid_groups))
    }

@router.post("/create-ledger")
async def create_payment_ledger(payload: CreateLedgerRequest):
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
        result = queue_master_operation("LEDGER", payload.name, "CREATE_LEDGER", xml_data, queue_payload)
        
        if result["status"] == "exists_confirmed":
            return {"status": "success", "message": "Ledger already exists in Tally", "name": payload.name}
            
        return {"status": "queued", "message": f"Ledger '{payload.name}' queued successfully.", "name": payload.name}
    except MasterConflictException as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/post")
async def post_payment(payload: PaymentRequest):
    safe_debit = escape(str(payload.debit_ledger))
    safe_credit = escape(str(payload.credit_ledger))
    safe_nar = escape(str(payload.narration))
    
    allocation = ""
    # If expense mode, cost_center is required, otherwise optional. 
    # But we just pass it if it exists and mode is expenses.
    if payload.mode == "expenses" and payload.cost_center:
        safe_cc = escape(str(payload.cost_center))
        allocation = f"""
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
      <VOUCHER VCHTYPE="Payment" ACTION="Create">
        <DATE>{payload.tally_date}</DATE>
        <VOUCHERTYPENAME>Payment</VOUCHERTYPENAME>
        <NARRATION>{safe_nar}</NARRATION>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{safe_debit}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          <AMOUNT>-{payload.amount}</AMOUNT>{allocation}
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

    try:
        response = requests.post(TALLY_URL, data=xml_data.encode('utf-8'), timeout=10)
        parsed = parse_tally_response(response.text, "POST_VOUCHER")
        if not parsed["is_success"]:
            raise HTTPException(status_code=400, detail=f"Tally rejected the entry: {parsed['error_message']}")
        return {"status": "success", "message": "Payment entry posted successfully"}
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        queue_operation("POST_VOUCHER", xml_data, payload.model_dump(), f"Payment: {payload.amount} to {payload.debit_ledger}")
        return {"status": "queued", "message": "Saved to offline queue."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
