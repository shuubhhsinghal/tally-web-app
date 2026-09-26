from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import requests
import json
import os
from xml.sax.saxutils import escape
from backend.database import get_all_ledgers, queue_operation, update_queue_status, set_delivery_uncertain, get_store_mapping, get_active_stores
from backend.services.tally_response import parse_tally_response
from backend.connector.transport import tally_transport

router = APIRouter()

class SalesRequest(BaseModel):
    ledger: str
    amount: float
    tally_date: str
    narration: str
    store: str

@router.get("/metadata")
async def get_sales_metadata():
    customers = []
    try:
        ledgers = get_all_ledgers()
        for l in ledgers:
            parent = (l.get('parent') or '').lower()
            name = l['name'].title()
            # Deliberately includes Cash-in-Hand and Sundry Debtors -- many
            # shops record a sale straight into a cash/UPI/wallet ledger they
            # created as a stand-in "customer" (money received via that
            # channel, pending settlement), not just formal named debtors.
            # Excludes real bank ledgers (Bank Accounts/Bank OD A/c) -- a
            # sale should never be recorded directly against the bank
            # itself, only reconciled into it later via a bank statement.
            if "debtor" in parent or "cash" in parent:
                customers.append(name)
    except Exception:
        pass

    store_names = [s['store_name'] for s in get_active_stores()]

    return {"customers": sorted(list(set(customers))), "stores": store_names}

@router.post("/preview")
async def preview_sales(payload: SalesRequest):
    return {
        "ledger": payload.ledger,
        "amount": payload.amount,
        "tally_date": payload.tally_date,
        "narration": payload.narration,
        "store": payload.store,
        "status": "success"
    }

@router.post("/post")
async def post_sales(payload: SalesRequest, request: Request):
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero.")

    safe_ledger = escape(str(payload.ledger))
    safe_nar = escape(str(payload.narration))

    # A staff account is scoped to one store -- even if they tamper with the
    # request, they can't post a sale into a different store's figures. The
    # owner account (store_name is None) can post to any store.
    current_user = request.state.user
    if not current_user['is_owner'] and payload.store != current_user['store_name']:
        raise HTTPException(status_code=403, detail=f"Your account can only post sales for {current_user['store_name']}.")

    # Store is picked explicitly on the form now (not guessed from the ledger
    # name), so the allocation always uses the exact Tally cost-centre casing
    # regardless of which ledger was chosen.
    store_mapping = get_store_mapping(payload.store)
    if not store_mapping:
        raise HTTPException(status_code=400, detail=f"Store '{payload.store}' does not have a mapped Cost Centre. Please configure it.")
    cost_center = store_mapping['cost_center_name']

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
    queue_payload = payload.model_dump()
    queue_payload['cost_center'] = cost_center
    queue_payload['created_by'] = current_user['name']
    queue_id = queue_operation("POST_VOUCHER", xml_data, queue_payload, f"Sales: {payload.amount} from {payload.ledger}")

    try:
        set_delivery_uncertain(queue_id, True)
        response = await tally_transport.post(xml_data.encode('utf-8'), timeout=10)
        parsed = parse_tally_response(response.text, "POST_VOUCHER")

        if not parsed["is_success"]:
            set_delivery_uncertain(queue_id, False)
            update_queue_status(queue_id, "FAILED", parsed['error_message'])
            raise HTTPException(status_code=400, detail=f"Tally rejected the entry: {parsed['error_message']}")

        set_delivery_uncertain(queue_id, False)
        update_queue_status(queue_id, "SYNCED")
        return {"status": "success", "message": "Sales entry posted successfully"}
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
