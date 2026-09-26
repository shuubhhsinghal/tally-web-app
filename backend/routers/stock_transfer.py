from fastapi import APIRouter, Request
from pydantic import BaseModel
import json
import os
from fastapi import HTTPException
from backend.database import get_all_stock_items, queue_operation, update_queue_status, set_delivery_uncertain, get_active_stores, get_store_mapping
import requests
from backend.services.tally_response import parse_tally_response
from backend.connector.transport import tally_transport
from backend.services.auth_helpers import enforce_store_access_either

router = APIRouter()

@router.get("/items")
def get_item_cache():
    items = get_all_stock_items()
    result = {}
    for i in items:
        result[i['name'].lower()] = {"name": i['name'], "unit": i['unit']}
    return result

class PreviewRequest(BaseModel):
    item_name: str
    qty: float
    from_store: str
    to_store: str

@router.get("/metadata")
async def get_metadata():
    items = []
    try:
        stock_items = get_all_stock_items()
        items = [i['name'] for i in stock_items]
    except Exception as e:
        print(f"Error loading stock items: {e}")

    # Stores now come from this app's own local `stores` table (the same one
    # repack/purchase already use) instead of a live Tally Cost Centre query,
    # so each store's godown_name is available for the physical stock
    # movement below -- Tally's live Cost Centre list has no godown mapping.
    stores = [s['store_name'] for s in get_active_stores()]

    return {"items": sorted(items), "stores": stores}

async def get_latest_purchase_rate(exact_item_name: str) -> float:
    from backend.database import get_purchase_rate
    try:
        return get_purchase_rate(exact_item_name)
    except Exception as e:
        print(f"Rate fetch error from local DB: {e}")
        return 0.0

@router.post("/preview")
async def preview_transfer(payload: PreviewRequest):
    if payload.qty <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero.")
    if payload.from_store.strip().lower() == payload.to_store.strip().lower():
        raise HTTPException(status_code=400, detail="From and To stores must be different.")

    fetched_rate = await get_latest_purchase_rate(payload.item_name)
    
    if fetched_rate <= 0:
        raise HTTPException(status_code=400, detail=f"Could not find a valid purchase rate for '{payload.item_name}' in Tally.")

    return {
        "item_name": payload.item_name,
        "qty": payload.qty,
        "rate": fetched_rate,
        "total_amount": round(payload.qty * fetched_rate, 2),
        "from_store": payload.from_store,
        "to_store": payload.to_store,
        "status": "success"
    }

class PostRequest(BaseModel):
    item_name: str
    qty: float
    rate: float
    total_amount: float
    from_store: str
    to_store: str
    tally_date: str # YYYYMMDD

async def _attempt_post_voucher(queue_id: int, xml_payload: str) -> dict:
    """Attempts an immediate live POST for one already-queued voucher and
    reports its own outcome, independent of any other voucher in the same
    request -- mirrors the queue-first + best-effort-immediate-send pattern
    used for the main invoice/adjustment pair in purchase_item.py."""
    try:
        set_delivery_uncertain(queue_id, True)
        response = await tally_transport.post(xml_payload.encode('utf-8'), timeout=10)
        parsed = parse_tally_response(response.text, "POST_VOUCHER")

        if not parsed["is_success"]:
            set_delivery_uncertain(queue_id, False)
            update_queue_status(queue_id, "FAILED", parsed['error_message'])
            return {"status": "failed", "message": parsed['error_message']}

        set_delivery_uncertain(queue_id, False)
        update_queue_status(queue_id, "SYNCED")
        return {"status": "success"}
    except requests.exceptions.ConnectTimeout:
        # The connection itself never established (Tally's address is unreachable)
        # -- as safe as ConnectionError, nothing was ever sent.
        set_delivery_uncertain(queue_id, False)
        return {"status": "queued", "message": "Tally is offline. Saved to queue."}
    except requests.exceptions.Timeout:
        # Ambiguous: connection was established and the request was sent, but no
        # response came back in time -- Tally may have processed it before the
        # response was lost. Leave delivery_uncertain set (already persisted
        # above) so a manual retry is blocked until someone verifies in Tally.
        return {"status": "queued", "message": "Delivery uncertain -- verify in Tally before retrying."}
    except requests.exceptions.ConnectionError:
        # Request never reached Tally at all -- safe to clear and leave PENDING.
        set_delivery_uncertain(queue_id, False)
        return {"status": "queued", "message": "Tally is offline. Saved to queue."}
    except Exception as e:
        update_queue_status(queue_id, "FAILED", str(e))
        return {"status": "failed", "message": str(e)}

@router.post("/post")
async def post_transfer(payload: PostRequest, request: Request):
    from xml.sax.saxutils import escape
    from backend.services.tally_godown_stock import get_godown_stock, is_tally_reachable
    import uuid

    if payload.qty <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero.")
    if payload.from_store.strip().lower() == payload.to_store.strip().lower():
        raise HTTPException(status_code=400, detail="From and To stores must be different.")

    current_user = request.state.user
    enforce_store_access_either(current_user, payload.from_store, payload.to_store, "move stock for")

    safe_item = escape(str(payload.item_name))
    safe_from = escape(str(payload.from_store))
    safe_to = escape(str(payload.to_store))

    # The physical (godown-level) movement needs both stores' godown mapping
    # -- hard-require it rather than silently skipping the physical leg, so a
    # missing store config is never a silent gap in your stock counts.
    from_mapping = get_store_mapping(payload.from_store)
    to_mapping = get_store_mapping(payload.to_store)
    if not from_mapping or not from_mapping.get('godown_name'):
        raise HTTPException(status_code=400, detail=f"Store '{payload.from_store}' does not have a mapped Godown. Please configure it.")
    if not to_mapping or not to_mapping.get('godown_name'):
        raise HTTPException(status_code=400, detail=f"Store '{payload.to_store}' does not have a mapped Godown. Please configure it.")
    from_godown = escape(from_mapping['godown_name'])
    to_godown = escape(to_mapping['godown_name'])

    # Best-effort stock-sufficiency check at the source godown -- same
    # philosophy as repack.py: a single upfront reachability ping so an
    # offline Tally skips this in one shot rather than a connection timeout,
    # but a genuine insufficient-stock result (while Tally IS reachable)
    # still hard-blocks the transfer.
    if is_tally_reachable():
        try:
            stock = await get_godown_stock(payload.item_name, from_mapping['godown_name'])
            if stock["qty"] < payload.qty:
                raise HTTPException(status_code=400, detail=f"Insufficient stock for '{payload.item_name}' in Godown '{from_mapping['godown_name']}'. Required: {payload.qty}, Available: {stock['qty']}")
        except HTTPException:
            raise
        except requests.exceptions.RequestException:
            pass  # Tally became unreachable between the ping and this call -- proceed without the check.
        except Exception as e:
            print(f"Unexpected error: {e}")
            raise HTTPException(status_code=400, detail="Something went wrong. Please try again.")

    amount_str = ""
    amount_str_pos = ""
    if payload.total_amount > 0:
        amount_str = f"<AMOUNT>-{payload.total_amount}</AMOUNT>"
        amount_str_pos = f"<AMOUNT>{payload.total_amount}</AMOUNT>"

    accounting_xml = f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
        <STATICVARIABLES>
          <SVCURRENTCOMPANY>$$CurrentCompany</SVCURRENTCOMPANY>
        </STATICVARIABLES>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Journal" ACTION="Create">
            <DATE>{payload.tally_date}</DATE>
            <VOUCHERTYPENAME>Journal</VOUCHERTYPENAME>
            <NARRATION>Inter-store transfer: {payload.qty} pcs of {safe_item}</NARRATION>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>inter store transfer</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              {amount_str}
              <CATEGORYALLOCATIONS.LIST>
                <CATEGORY>Primary</CATEGORY>
                <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                <COSTCENTREALLOCATIONS.LIST>
                  <NAME>{safe_to}</NAME>
                  {amount_str}
                </COSTCENTREALLOCATIONS.LIST>
              </CATEGORYALLOCATIONS.LIST>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>inter store transfer</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              {amount_str_pos}
              <CATEGORYALLOCATIONS.LIST>
                <CATEGORY>Primary</CATEGORY>
                <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
                <COSTCENTREALLOCATIONS.LIST>
                  <NAME>{safe_from}</NAME>
                  {amount_str_pos}
                </COSTCENTREALLOCATIONS.LIST>
              </CATEGORYALLOCATIONS.LIST>
            </ALLLEDGERENTRIES.LIST>
          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""

    # The physical movement: a Stock Journal relocating the SAME item/qty/rate
    # from one godown to the other -- net valuation change is zero, unlike
    # repack.py's version of this XML shape which combines different items
    # into a new one. Sign convention (ISDEEMEDPOSITIVE/AMOUNT) mirrors
    # repack.py's already Tally-verified OUT/IN pattern exactly.
    physical_guid = f"TRANSFER-{uuid.uuid4()}"
    physical_voucher_number = f"ST-{physical_guid[-8:]}"
    amount_val = round(payload.qty * payload.rate, 2)

    physical_xml = f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
        <STATICVARIABLES>
          <SVCURRENTCOMPANY>$$CurrentCompany</SVCURRENTCOMPANY>
        </STATICVARIABLES>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Stock Journal" ACTION="Create">
            <DATE>{payload.tally_date}</DATE>
            <GUID>{physical_guid}</GUID>
            <VOUCHERTYPENAME>Stock Journal</VOUCHERTYPENAME>
            <VOUCHERNUMBER>{physical_voucher_number}</VOUCHERNUMBER>
            <NARRATION>Stock Transfer: {payload.qty} of {safe_item} from {safe_from} to {safe_to}</NARRATION>
            <ISINVOICE>No</ISINVOICE>
            <INVENTORYENTRIESOUT.LIST>
              <STOCKITEMNAME>{safe_item}</STOCKITEMNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <RATE>{payload.rate}</RATE>
              <AMOUNT>{amount_val:.2f}</AMOUNT>
              <ACTUALQTY> {payload.qty:.4f}</ACTUALQTY>
              <BILLEDQTY> {payload.qty:.4f}</BILLEDQTY>
              <BATCHALLOCATIONS.LIST>
                <GODOWNNAME>{from_godown}</GODOWNNAME>
                <BATCHNAME>Primary Batch</BATCHNAME>
                <AMOUNT>{amount_val:.2f}</AMOUNT>
                <ACTUALQTY> {payload.qty:.4f}</ACTUALQTY>
                <BILLEDQTY> {payload.qty:.4f}</BILLEDQTY>
              </BATCHALLOCATIONS.LIST>
            </INVENTORYENTRIESOUT.LIST>
            <INVENTORYENTRIESIN.LIST>
              <STOCKITEMNAME>{safe_item}</STOCKITEMNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <RATE>{payload.rate}</RATE>
              <AMOUNT>-{amount_val:.2f}</AMOUNT>
              <ACTUALQTY> {payload.qty:.4f}</ACTUALQTY>
              <BILLEDQTY> {payload.qty:.4f}</BILLEDQTY>
              <BATCHALLOCATIONS.LIST>
                <GODOWNNAME>{to_godown}</GODOWNNAME>
                <BATCHNAME>Primary Batch</BATCHNAME>
                <AMOUNT>-{amount_val:.2f}</AMOUNT>
                <ACTUALQTY> {payload.qty:.4f}</ACTUALQTY>
                <BILLEDQTY> {payload.qty:.4f}</BILLEDQTY>
              </BATCHALLOCATIONS.LIST>
            </INVENTORYENTRIESIN.LIST>
          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""

    # Queue-First Architecture: both vouchers are durably saved before either
    # is ever attempted against Tally.
    queue_payload = payload.model_dump()
    queue_payload['created_by'] = current_user['name']
    accounting_queue_id = queue_operation("POST_VOUCHER", accounting_xml, queue_payload, f"Stock Transfer: {payload.qty} pcs of {safe_item} (Accounting)")
    physical_queue_id = queue_operation("POST_VOUCHER", physical_xml, queue_payload, f"Stock Transfer: {payload.qty} pcs of {safe_item} (Physical)")

    accounting_result = await _attempt_post_voucher(accounting_queue_id, accounting_xml)
    physical_result = await _attempt_post_voucher(physical_queue_id, physical_xml)

    statuses = {accounting_result["status"], physical_result["status"]}
    if statuses == {"success"}:
        overall_status, overall_message = "success", "Successfully posted to Tally"
    elif "failed" in statuses:
        # Tally actively rejected at least one of the two vouchers -- this is
        # a real error the user needs to see and act on (e.g. correct a
        # ledger/godown name), not a quiet queued-for-later state.
        overall_status, overall_message = "failed", "Tally rejected one or both entries. Check details below."
    else:
        overall_status, overall_message = "queued", "Saved to offline queue."

    return {
        "status": overall_status,
        "message": overall_message,
        "accounting": accounting_result,
        "physical": physical_result,
    }
