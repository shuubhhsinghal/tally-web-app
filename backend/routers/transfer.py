from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import requests
import os
from xml.sax.saxutils import escape
from backend.database import get_all_ledgers, queue_operation, update_queue_status, set_delivery_uncertain, get_master_dependency_state, is_master_pending_sync, normalize_master_name
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

def build_contra_voucher_xml(amount: float, from_account: str, to_account: str, tally_date: str, narration: str = "") -> str:
    """Builds a Tally Contra voucher XML moving `amount` from `from_account`
    (credited) to `to_account` (debited) -- the correct voucher type for a
    transfer between two of the user's own ledgers. Reused by both the
    "Move funds" feature (below) and the bank-statement inter-account
    transfer merge feature (backend/routers/bank_statement.py)."""
    from_ledger_xml = escape(str(from_account))
    to_ledger_xml = escape(str(to_account))
    narration_xml = escape(str(narration))

    return f"""<ENVELOPE>
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
            <DATE>{tally_date}</DATE>
            <VOUCHERTYPENAME>Contra</VOUCHERTYPENAME>
            <PARTYLEDGERNAME>{to_ledger_xml}</PARTYLEDGERNAME>
            <NARRATION>{narration_xml}</NARRATION>
            <PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{from_ledger_xml}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{amount}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{to_ledger_xml}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{amount}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""

@router.post("/post")
async def post_transfer(payload: TransferRequest):
    from_norm, _ = normalize_master_name(payload.from_account)
    to_norm, _ = normalize_master_name(payload.to_account)
    if from_norm == to_norm:
        raise HTTPException(status_code=400, detail="From and To accounts must be different.")

    xml_data = build_contra_voucher_xml(payload.amount, payload.from_account, payload.to_account, payload.tally_date, payload.narration)

    # Queue-First Architecture: Always save transaction to DB before attempting to send
    queue_id = queue_operation("POST_VOUCHER", xml_data, payload.model_dump(), f"Transfer: {payload.amount} from {payload.from_account}")

    try:
        for role, ledger_name in (("from", payload.from_account), ("to", payload.to_account)):
            state, error_msg = get_master_dependency_state("LEDGER", ledger_name, None)
            if state == "FAILED":
                update_queue_status(queue_id, "FAILED", f"Cannot post transfer because ledger '{ledger_name}' failed to sync to Tally.")
                raise HTTPException(status_code=400, detail=f"Cannot post transfer because ledger '{ledger_name}' failed to sync to Tally. Resolve the master first.")
            if state == "MISSING":
                update_queue_status(queue_id, "FAILED", f"Cannot post transfer because ledger '{ledger_name}' is not available in Tally or pending sync.")
                raise HTTPException(status_code=400, detail=f"Cannot post transfer because ledger '{ledger_name}' is not available in Tally or pending sync. Create or refresh the master before posting.")
            if state == "CONFLICT":
                update_queue_status(queue_id, "FAILED", f"Definition conflict for ledger '{ledger_name}': {error_msg}")
                raise HTTPException(status_code=409, detail=f"Cannot post transfer due to definition conflict for ledger '{ledger_name}': {error_msg}")
            if is_master_pending_sync(state):
                return {"status": "queued", "reason": "pending_master_dependency", "message": f"Transfer saved to offline queue because ledger '{ledger_name}' is still pending sync to Tally."}

        set_delivery_uncertain(queue_id, True)
        response = requests.post(TALLY_URL, data=xml_data.encode('utf-8'), timeout=10)
        parsed = parse_tally_response(response.text, "POST_VOUCHER")

        if not parsed["is_success"]:
            set_delivery_uncertain(queue_id, False)
            update_queue_status(queue_id, "FAILED", parsed['error_message'])
            raise HTTPException(status_code=400, detail=f"Tally rejected the entry: {parsed['error_message']}")

        set_delivery_uncertain(queue_id, False)
        update_queue_status(queue_id, "SYNCED")
        return {"status": "success", "message": "Contra entry posted successfully"}
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
