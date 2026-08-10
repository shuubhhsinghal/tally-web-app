from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import requests
import os
from xml.sax.saxutils import escape
from backend.database import get_all_ledgers, queue_operation
from backend.services.tally_response import parse_tally_response

router = APIRouter()
from backend.config import TALLY_URL

class TransferRequest(BaseModel):
    amount: float
    from_account: str  # Credit
    to_account: str    # Debit
    tally_date: str    # YYYYMMDD
    narration: str = ""

@router.get("/metadata")
async def get_transfer_metadata():
    accounts = []
    try:
        ledgers = get_all_ledgers()
        for l in ledgers:
            parent = (l.get('parent') or '').lower()
            name = l['name'].title()
            if "bank" in parent or "cash" in parent:
                accounts.append(name)
    except Exception as e:
        print(f"Error loading ledgers: {e}")
    
    return {"accounts": sorted(list(set(accounts)))}

@router.post("/preview")
async def preview_transfer(payload: TransferRequest):
    return {
        "amount": payload.amount,
        "from_account": payload.from_account,
        "to_account": payload.to_account,
        "tally_date": payload.tally_date,
        "narration": payload.narration,
        "status": "success"
    }

@router.post("/post")
async def post_transfer(payload: TransferRequest):
    from_ledger_xml = escape(str(payload.from_account))
    to_ledger_xml = escape(str(payload.to_account))
    narration_xml = escape(str(payload.narration))
    
    xml_data = f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER ACTION="Create" VCHTYPE="Contra">
            <DATE>{payload.tally_date}</DATE>
            <VOUCHERTYPENAME>Contra</VOUCHERTYPENAME>
            <PARTYLEDGERNAME>{to_ledger_xml}</PARTYLEDGERNAME>
            <NARRATION>{narration_xml}</NARRATION>
            <PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{from_ledger_xml}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{payload.amount}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{to_ledger_xml}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{payload.amount}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""

    try:
        response = requests.post(TALLY_URL, data=xml_data.encode('utf-8'), timeout=10)
        parsed = parse_tally_response(response.text, "POST_VOUCHER")
        if not parsed["is_success"]:
            raise HTTPException(status_code=400, detail=f"Tally rejected the entry: {parsed['error_message']}")
        return {"status": "success", "message": "Contra entry posted successfully"}
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        queue_operation("POST_VOUCHER", xml_data, payload.model_dump(), f"Transfer: {payload.amount} from {payload.from_account}")
        return {"status": "queued", "message": "Saved to offline queue."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
