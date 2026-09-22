import datetime
import traceback
from xml.sax.saxutils import escape
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Form, Query, Request
from pydantic import BaseModel
import requests

from backend.database import (
    get_all_ledgers, get_all_stock_items, get_all_uoms, get_all_aliases,
    save_alias as db_save_alias, queue_operation, record_purchase_rate, get_purchase_rates,
    get_db, update_queue_status, set_delivery_uncertain,
    record_pending_purchase_rate_entry, get_local_purchase_rate_history
)
from backend.services.tally_response import parse_tally_response
from backend.services.auth_helpers import enforce_store_access

router = APIRouter()

from backend.connector.transport import tally_transport
from backend.services.item_mapping import normalize_item_name

# Models
class NewSupplierRequest(BaseModel):
    name: str

class NewItemRequest(BaseModel):
    name: str
    uom: str
    parent_group: str = "Primary"

class SaveAliasRequest(BaseModel):
    original_name: str
    mapped_name: str

class PurchaseItemRow(BaseModel):
    name: str
    qty: float
    uom: str = "PCS"
    rate: float
    discount: float = 0.0
    amount: float
    mapped_name: Optional[str] = None
    mapped_unit: Optional[str] = None
    is_mapped: bool = False

class PurchaseReturnItem(BaseModel):
    name: str
    uom: str = "PCS"
    qty: float
    rate: float = 0.0
    amount: float = 0.0

class PurchaseAdjustment(BaseModel):
    store: str
    reason: Optional[str] = None
    notes: Optional[str] = None
    items: List[PurchaseReturnItem] = []

class ReturnRatePreviewRequest(BaseModel):
    item_name: str
    qty: float

class PurchaseItemPostRequest(BaseModel):
    supplier: str
    invoice_number: str
    tally_date: str # YYYYMMDD
    cost_center: str
    cgst: float = 0.0
    sgst: float = 0.0
    igst: float = 0.0
    rounding_off: float = 0.0
    items: List[PurchaseItemRow]
    adjustment: Optional[PurchaseAdjustment] = None

@router.get("/items")
def get_item_cache():
    return get_purchase_rates()

@router.get("/metadata")
async def get_metadata():
    from backend.database import get_master_states, get_active_stores

    # Share one connection across these reads instead of each helper
    # opening/closing its own -- this endpoint is called on essentially
    # every purchase-item page load, so 6 separate connections per request
    # added up to real, avoidable overhead.
    with get_db() as conn:
        ledgers = get_all_ledgers(conn)
        stock_items = get_all_stock_items(conn)
        uoms = get_all_uoms(conn)
        aliases = get_all_aliases(conn)
        master_states = get_master_states(conn)
        active_stores = get_active_stores(conn)

    suppliers = []
    for l in ledgers:
        parent = (l.get('parent') or '').lower()
        if "creditor" in parent or "loan" in parent:
            suppliers.append(l['name'].title())

    stock_item_names = [i['name'] for i in stock_items]
    stock_item_units = {i['name']: (i.get('unit') or 'PCS') for i in stock_items}

    store_names = [s['store_name'] for s in active_stores]

    return {
        "suppliers": sorted(list(set(suppliers))),
        "stock_items": sorted(stock_item_names),
        "stock_item_units": stock_item_units,
        "uoms": uoms,
        "aliases": aliases,
        "master_states": master_states,
        "stores": store_names
    }

@router.post("/return-item-rate")
async def preview_return_item_rate(payload: ReturnRatePreviewRequest):
    from backend.database import get_purchase_rate
    rate = get_purchase_rate(payload.item_name)
    if rate <= 0:
        raise HTTPException(status_code=400, detail=f"Could not find a valid purchase rate for '{payload.item_name}'.")
    return {
        "item_name": payload.item_name,
        "qty": payload.qty,
        "rate": rate,
        "amount": round(payload.qty * rate, 2)
    }

@router.get("/return-item-history")
async def get_return_item_history(item_name: str = Query(...)):
    """Last-2-years purchase history for one item, for the return-item
    rate-history picker. Tries Tally live first (the complete, authoritative
    source); falls back to the local cache (reporting_vouchers/
    reporting_inventory_entries + purchase_rate_pending_entries) only on a
    connectivity failure -- a malformed Tally response is a real data problem
    and is allowed to surface as a 502 instead of being silently hidden."""
    from datetime import datetime, timedelta
    from backend.services.tally_reporting_sync import fetch_live_purchase_history_from_tally
    import xml.etree.ElementTree as ET

    since_date_iso = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
    since_date_compact = since_date_iso.replace('-', '')

    unit = None
    for si in get_all_stock_items():
        if si['name'].lower() == item_name.strip().lower():
            unit = si.get('unit')
            break

    try:
        entries = await fetch_live_purchase_history_from_tally(item_name, since_date_compact)
        for e in entries:
            e['unit'] = unit
        # Merge in any not-yet-Tally-confirmed local purchases, plus repack
        # costs -- neither will ever appear in Tally's own Purchase-voucher
        # history (a pending purchase hasn't synced yet; a repack posts a
        # Stock Journal, not a Purchase, so it never will).
        local_entries = get_local_purchase_rate_history(item_name, since_date_iso)
        pending_only = [e for e in local_entries if e['origin'] in ('app_post', 'repack')]
        from backend.database import dedup_pending_against_tally
        entries = entries + dedup_pending_against_tally(entries, pending_only)
        entries.sort(key=lambda e: e['date'], reverse=True)
        source = "tally_live"
    except (requests.exceptions.ConnectTimeout, requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        entries = get_local_purchase_rate_history(item_name, since_date_iso)
        for e in entries:
            if not e.get('unit'):
                e['unit'] = unit
        source = "local_cache"
    except ET.ParseError:
        raise HTTPException(status_code=502, detail="Received a malformed response from Tally while fetching purchase history.")

    return {"source": source, "entries": entries}

@router.post("/create-supplier")
async def create_supplier(payload: NewSupplierRequest):
    xml_data = f"""<ENVELOPE>
        <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
        <BODY>
            <IMPORTDATA>
                <REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC>
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

    try:
        response = await tally_transport.post(xml_data.encode('utf-8'), timeout=4)
        parsed = parse_tally_response(response.text, "CREATE_LEDGER")
        if not parsed["is_success"]:
            raise HTTPException(status_code=400, detail=f"Tally rejected the creation: {parsed['error_message']}")
            
        from backend.services.tally_response import is_already_exists_success
        from backend.services.tally_verification import verify_tally_master_definition, VerificationResult
        
        insert_name = payload.name
        if is_already_exists_success(parsed, "CREATE_LEDGER"):
            ver_res, canonical = await verify_tally_master_definition("LEDGER", payload.name, {"parent": "Sundry Creditors"})
            if ver_res == VerificationResult.UNVERIFIABLE:
                raise HTTPException(status_code=400, detail="Master already exists in Tally, but its definition could not be verified. Refresh masters and try again.")
            elif ver_res == VerificationResult.CONFLICT:
                raise HTTPException(status_code=409, detail="Master already exists in Tally with a conflicting definition.")
            insert_name = canonical['name']
        
        # Optimistically update local database
        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO ledgers (name, parent, cost_centre) VALUES (?, ?, ?)",
                    (insert_name, "Sundry Creditors", False)
                )
                conn.commit()
        except Exception as e:
            print(f"Failed to optimistic insert ledger: {e}")
            
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        from backend.database import queue_master_operation, MasterFailedException
        try:
            res = queue_master_operation("LEDGER", payload.name, "CREATE_LEDGER", xml_data, {"name": payload.name, "parent": "Sundry Creditors"})
            if res.get("status") == "exists_confirmed":
                return {"status": "success", "message": f"Supplier '{payload.name}' already confirmed.", "name": payload.name}
            elif res.get("status") in ("exists_pending", "exists_pending_concurrent"):
                return {"status": "success", "message": f"Supplier '{payload.name}' already pending.", "name": payload.name}
        except MasterConflictException as e:
            raise HTTPException(status_code=409, detail=str(e))
        except MasterFailedException as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            print(f"Unexpected error: {e}")
            raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")
            
        print(f"--- [PURCHASE-ITEM] Tally offline. Queued supplier: {payload.name} ---", flush=True)
        return {"status": "queued", "message": f"Supplier '{payload.name}' saved to offline queue.", "name": payload.name}
    except HTTPException:
        raise

    return {"status": "success", "message": f"Supplier '{payload.name}' processed.", "name": payload.name}

@router.post("/create-item")
async def create_item(payload: NewItemRequest):
    uom_xml = f"""<ENVELOPE>
        <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
        <BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC><REQUESTDATA>
            <TALLYMESSAGE xmlns:UDF="TallyUDF">
                <UNIT ACTION="Create" NAME="{escape(payload.uom)}">
                    <NAME>{escape(payload.uom)}</NAME><ISSIMPLEUNIT>Yes</ISSIMPLEUNIT>
                </UNIT>
            </TALLYMESSAGE>
        </REQUESTDATA></IMPORTDATA></BODY>
    </ENVELOPE>"""
    item_xml = f"""<ENVELOPE>
        <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
        <BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC><REQUESTDATA>
            <TALLYMESSAGE xmlns:UDF="TallyUDF">
                <STOCKITEM ACTION="Create" NAME="{escape(payload.name)}">
                    <NAME.LIST><NAME>{escape(payload.name)}</NAME></NAME.LIST>
                    <PARENT>{escape(payload.parent_group)}</PARENT><BASEUNITS>{escape(payload.uom)}</BASEUNITS>
                </STOCKITEM>
            </TALLYMESSAGE>
        </REQUESTDATA></IMPORTDATA></BODY>
    </ENVELOPE>"""

    from backend.database import queue_master_operation, MasterConflictException, MasterFailedException, check_master_exists_locally, normalize_master_name, get_db

    # --- UOM PHASE ---
    uom_state = "NEW" # CONFIRMED, SYNCED_WAITING, PENDING, NEW
    try:
        norm_uom, _ = normalize_master_name(payload.uom)
        if check_master_exists_locally('UOM', norm_uom, {}):
            uom_state = "CONFIRMED"
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise HTTPException(status_code=400, detail="Something went wrong. Please try again.")
        
    if uom_state == "NEW":
        try:
            res = queue_master_operation("UOM", payload.uom, "CREATE_UOM", uom_xml, {"uom": payload.uom})
            if res.get("status") == "exists_confirmed":
                uom_state = "CONFIRMED"
            else:
                uom_state = "PENDING"
        except MasterFailedException:
            raise HTTPException(status_code=400, detail="UOM creation previously failed. Resolution required in Dashboard.")
        except Exception as e:
            print(f"Unexpected error: {e}")
            raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")

    # --- ITEM PHASE ---
    try:
        norm_item, _ = normalize_master_name(payload.name)
        if check_master_exists_locally('ITEM', norm_item, {"uom": payload.uom}):
            return {"status": "success", "message": f"Item '{payload.name}' already exists.", "name": payload.name, "uom": payload.uom}
    except MasterConflictException as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise HTTPException(status_code=400, detail="Something went wrong. Please try again.")

    try:
        res = queue_master_operation("ITEM", payload.name, "CREATE_ITEM", item_xml, payload.model_dump())
        if res.get("status") == "exists_confirmed":
            return {"status": "success", "message": f"Item '{payload.name}' already confirmed.", "name": payload.name, "uom": payload.uom}
        elif res.get("status") in ("exists_pending", "exists_pending_concurrent", "queued"):
            return {"status": "queued", "message": "Item saved to offline queue.", "name": payload.name, "uom": payload.uom}
    except MasterConflictException as e:
        raise HTTPException(status_code=409, detail=str(e))
    except MasterFailedException as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")

@router.post("/save-alias")
async def save_alias_endpoint(payload: SaveAliasRequest):
    try:
        normalized_orig = normalize_item_name(payload.original_name)
        if normalized_orig != normalize_item_name(payload.mapped_name):
            db_save_alias(normalized_orig, payload.mapped_name)
        return {"status": "success"}
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")

@router.post("/post")
async def post_purchase_item(payload: PurchaseItemPostRequest, request: Request):
    print(f"--- [PURCHASE-ITEM POST] Received payload ---", flush=True)
    current_user = request.state.user
    enforce_store_access(current_user, payload.cost_center, "post purchases for")
    if payload.adjustment:
        enforce_store_access(current_user, payload.adjustment.store, "post returns for")

    supplier = escape(payload.supplier)
    inv_no = escape(payload.invoice_number)
    
    # Robustly parse tally_date to YYYYMMDD
    raw_date = payload.tally_date
    if '/' in raw_date:
        parts = raw_date.split('/')
        if len(parts) == 3:
            # Assume DD/MM/YYYY
            tally_date = f"{parts[2]}{parts[1].zfill(2)}{parts[0].zfill(2)}"
        else:
            tally_date = raw_date.replace('/', '')
    elif '-' in raw_date:
        parts = raw_date.split('-')
        if len(parts) == 3 and len(parts[0]) == 4:
            # Assume YYYY-MM-DD
            tally_date = f"{parts[0]}{parts[1].zfill(2)}{parts[2].zfill(2)}"
        else:
            tally_date = raw_date.replace('-', '')
    else:
        tally_date = raw_date

    # Reformat YYYYMMDD -> YYYY-MM-DD to match the format tally_sync_worker.py
    # writes into stock_items.last_purchase_date, so get_purchase_rate's plain
    # string date comparison ("whichever source is newer wins") stays apples-
    # to-apples instead of always comparing against wall-clock "now".
    purchase_date = f"{tally_date[0:4]}-{tally_date[4:6]}-{tally_date[6:8]}"

    from backend.database import get_store_mapping
    store_mapping = get_store_mapping(payload.cost_center)
    if not store_mapping or not store_mapping.get('godown_name'):
        raise HTTPException(status_code=400, detail=f"Store '{payload.cost_center}' does not have a mapped Cost Centre or Godown. Please configure it.")
        
    cc = escape(store_mapping['cost_center_name'])
    godown = escape(store_mapping['godown_name'])

    inventory_xml = ""
    item_subtotal = 0.0

    for item in payload.items:
        name = escape(item.mapped_name or item.name)
        qty = item.qty
        unit = item.mapped_unit or item.uom
        if unit:
            unit = unit.replace('.', '').strip()
        if not unit or unit == 'Not Applicable':
            unit = 'PCS'
        unit = escape(unit)
        rate = item.rate
        amount = item.amount

        item_subtotal += amount

        # Check if batch allocation is needed
        # Since batches are not active, we omit <BATCHNAME> or use "Primary Batch" if Tally strictly requires it. 
        # Usually, when "Maintain Multiple Godowns" is on, but batches are off, Tally only requires <GODOWNNAME> and <BATCHNAME>Primary Batch</BATCHNAME>.
        
        inventory_xml += f"""
        <INVENTORYENTRIES.LIST>
            <STOCKITEMNAME>{name}</STOCKITEMNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <RATE>{rate}/{unit}</RATE>
            <DISCOUNT>{item.discount}</DISCOUNT>
            <AMOUNT>-{amount:.2f}</AMOUNT>
            <ACTUALQTY> {qty} {unit}</ACTUALQTY>
            <BILLEDQTY> {qty} {unit}</BILLEDQTY>
            <BATCHALLOCATIONS.LIST>
                <GODOWNNAME>{godown}</GODOWNNAME>
                <BATCHNAME>Primary Batch</BATCHNAME>
                <AMOUNT>-{amount:.2f}</AMOUNT>
                <ACTUALQTY> {qty} {unit}</ACTUALQTY>
                <BILLEDQTY> {qty} {unit}</BILLEDQTY>
            </BATCHALLOCATIONS.LIST>
            <ACCOUNTINGALLOCATIONS.LIST>
                <LEDGERNAME>Purchase</LEDGERNAME>
                <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                <AMOUNT>-{amount:.2f}</AMOUNT>
                <CATEGORYALLOCATIONS.LIST>
                    <CATEGORY>Primary Cost Category</CATEGORY>
                    <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                    <COSTCENTREALLOCATIONS.LIST>
                        <NAME>{cc}</NAME>
                        <AMOUNT>-{amount:.2f}</AMOUNT>
                    </COSTCENTREALLOCATIONS.LIST>
                </CATEGORYALLOCATIONS.LIST>
            </ACCOUNTINGALLOCATIONS.LIST>
        </INVENTORYENTRIES.LIST>"""

    calculated_grand_total = item_subtotal + payload.cgst + payload.sgst + payload.igst + payload.rounding_off

    ledger_xml = f"""
        <LEDGERENTRIES.LIST>
            <LEDGERNAME>{supplier}</LEDGERNAME>
            <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
            <AMOUNT>{calculated_grand_total:.2f}</AMOUNT>
            <BILLALLOCATIONS.LIST>
                <NAME>{inv_no}</NAME>
                <BILLTYPE>New Ref</BILLTYPE>
                <AMOUNT>{calculated_grand_total:.2f}</AMOUNT>
            </BILLALLOCATIONS.LIST>
        </LEDGERENTRIES.LIST>"""

    if payload.cgst > 0:
        ledger_xml += f"""
        <LEDGERENTRIES.LIST>
            <LEDGERNAME>Input CGST</LEDGERNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <AMOUNT>-{payload.cgst:.2f}</AMOUNT>
        </LEDGERENTRIES.LIST>"""

    if payload.sgst > 0:
        ledger_xml += f"""
        <LEDGERENTRIES.LIST>
            <LEDGERNAME>Input SGST</LEDGERNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <AMOUNT>-{payload.sgst:.2f}</AMOUNT>
        </LEDGERENTRIES.LIST>"""

    if payload.igst > 0:
        ledger_xml += f"""
        <LEDGERENTRIES.LIST>
            <LEDGERNAME>Input IGST</LEDGERNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <AMOUNT>-{payload.igst:.2f}</AMOUNT>
        </LEDGERENTRIES.LIST>"""

    if abs(payload.rounding_off) >= 0.01:
        is_debit = "Yes" if payload.rounding_off > 0 else "No"
        amount_str = f"-{abs(payload.rounding_off):.2f}" if payload.rounding_off > 0 else f"{abs(payload.rounding_off):.2f}"
        ledger_xml += f"""
        <LEDGERENTRIES.LIST>
            <LEDGERNAME>Rounding Off</LEDGERNAME>
            <ISDEEMEDPOSITIVE>{is_debit}</ISDEEMEDPOSITIVE>
            <AMOUNT>{amount_str}</AMOUNT>
        </LEDGERENTRIES.LIST>"""

    guid = f"PII-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"

    # Only force-correct the GST/rounding classification of a ledger this
    # voucher actually references -- altering ones it doesn't touch just
    # risks overwriting a user's own Tally-side customization for no reason.
    master_xml = ""
    if payload.cgst > 0:
        master_xml += """
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <LEDGER ACTION="Alter" NAME="Input CGST">
                        <NAME.LIST><NAME>Input CGST</NAME></NAME.LIST>
                        <PARENT>Duties &amp; Taxes</PARENT>
                        <TAXTYPE>GST</TAXTYPE>
                        <GSTDUTYHEAD>Central Tax</GSTDUTYHEAD>
                    </LEDGER>
                </TALLYMESSAGE>"""
    if payload.sgst > 0:
        master_xml += """
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <LEDGER ACTION="Alter" NAME="Input SGST">
                        <NAME.LIST><NAME>Input SGST</NAME></NAME.LIST>
                        <PARENT>Duties &amp; Taxes</PARENT>
                        <TAXTYPE>GST</TAXTYPE>
                        <GSTDUTYHEAD>State Tax</GSTDUTYHEAD>
                    </LEDGER>
                </TALLYMESSAGE>"""
    if payload.igst > 0:
        master_xml += """
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <LEDGER ACTION="Alter" NAME="Input IGST">
                        <NAME.LIST><NAME>Input IGST</NAME></NAME.LIST>
                        <PARENT>Duties &amp; Taxes</PARENT>
                        <TAXTYPE>GST</TAXTYPE>
                        <GSTDUTYHEAD>Integrated Tax</GSTDUTYHEAD>
                    </LEDGER>
                </TALLYMESSAGE>"""
    if abs(payload.rounding_off) >= 0.01:
        master_xml += """
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <LEDGER ACTION="Alter" NAME="Rounding Off">
                        <NAME.LIST><NAME>Rounding Off</NAME></NAME.LIST>
                        <PARENT>Indirect Expenses</PARENT>
                        <ROUNDINGMETHOD>Normal Rounding</ROUNDINGMETHOD>
                        <ROUNDLIMIT>1</ROUNDLIMIT>
                    </LEDGER>
                </TALLYMESSAGE>"""

    xml = f"""<ENVELOPE>
    <HEADER>
        <TALLYREQUEST>Import Data</TALLYREQUEST>
    </HEADER>
    <BODY>
        <IMPORTDATA>
            <REQUESTDESC>
                <REPORTNAME>All Masters</REPORTNAME>
                <STATICVARIABLES>
                    <SVCURRENTCOMPANY>##SVCURRENTCOMPANY</SVCURRENTCOMPANY>
                </STATICVARIABLES>
            </REQUESTDESC>
            <REQUESTDATA>{master_xml}
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <VOUCHER VCHTYPE="Purchase" ACTION="Create">
                        <DATE>{tally_date}</DATE>
                        <GUID>{guid}</GUID>
                        <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
                        <REFERENCE>{inv_no}</REFERENCE>
                        <VOUCHERNUMBER>{inv_no}</VOUCHERNUMBER>
                        <PARTYLEDGERNAME>{supplier}</PARTYLEDGERNAME>
                        <PARTYNAME>{supplier}</PARTYNAME>
                        <PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>
                        <ISINVOICE>Yes</ISINVOICE>
                        {inventory_xml}
                        {ledger_xml}
                    </VOUCHER>
                </TALLYMESSAGE>
            </REQUESTDATA>
        </IMPORTDATA>
    </BODY>
</ENVELOPE>"""

    print("\n--- FINAL TALLY XML PAYLOAD ---", flush=True)
    print(xml, flush=True)
    print("-------------------------------\n", flush=True)

    # Build the Adjustment (Item-wise Purchase Return via Debit Note) XML, if present,
    # BEFORE queuing anything -- so a validation failure here (bad qty, unmapped store,
    # no known rate) never leaves an orphaned/duplicate main-invoice queue entry behind.
    debit_note_xml = None
    if payload.adjustment and payload.adjustment.items:
        adjustment = payload.adjustment
        from backend.database import get_purchase_rate

        for ritem in adjustment.items:
            if ritem.qty <= 0 or not ritem.name.strip():
                raise HTTPException(status_code=400, detail="Return items must have a name and a quantity greater than zero.")

        adj_store_mapping = get_store_mapping(adjustment.store)
        if not adj_store_mapping or not adj_store_mapping.get('godown_name'):
            raise HTTPException(status_code=400, detail=f"Return store '{adjustment.store}' does not have a mapped Cost Centre or Godown. Please configure it.")
        adj_cc = escape(adj_store_mapping['cost_center_name'])
        adj_godown = escape(adj_store_mapping['godown_name'])

        adj_guid = f"PRJ-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"

        return_inventory_xml = ""
        return_subtotal = 0.0
        for ritem in adjustment.items:
            # Trust a rate the client already resolved -- either the auto-filled
            # latest rate from /return-item-rate, or one explicitly picked from
            # the 2-year purchase history picker (which may deliberately NOT be
            # the latest rate). Only re-resolve server-side when the client
            # genuinely didn't supply one (e.g. an old draft).
            resolved_rate = ritem.rate if ritem.rate and ritem.rate > 0 else get_purchase_rate(ritem.name)
            if resolved_rate <= 0:
                raise HTTPException(status_code=400, detail=f"Could not resolve a purchase rate for return item '{ritem.name}'. Refresh masters or record a purchase for it first.")

            r_name = escape(ritem.name)
            r_unit = escape((ritem.uom or "PCS").replace('.', '').strip() or "PCS")
            r_qty = ritem.qty
            r_amount = round(r_qty * resolved_rate, 2)
            return_subtotal += r_amount

            return_inventory_xml += f"""
        <INVENTORYENTRIES.LIST>
            <STOCKITEMNAME>{r_name}</STOCKITEMNAME>
            <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
            <RATE>{resolved_rate}/{r_unit}</RATE>
            <DISCOUNT>0</DISCOUNT>
            <AMOUNT>{r_amount:.2f}</AMOUNT>
            <ACTUALQTY>-{r_qty} {r_unit}</ACTUALQTY>
            <BILLEDQTY>-{r_qty} {r_unit}</BILLEDQTY>
            <BATCHALLOCATIONS.LIST>
                <GODOWNNAME>{adj_godown}</GODOWNNAME>
                <BATCHNAME>Primary Batch</BATCHNAME>
                <AMOUNT>{r_amount:.2f}</AMOUNT>
                <ACTUALQTY>-{r_qty} {r_unit}</ACTUALQTY>
                <BILLEDQTY>-{r_qty} {r_unit}</BILLEDQTY>
            </BATCHALLOCATIONS.LIST>
            <ACCOUNTINGALLOCATIONS.LIST>
                <LEDGERNAME>Purchase Return</LEDGERNAME>
                <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
                <AMOUNT>{r_amount:.2f}</AMOUNT>
                <CATEGORYALLOCATIONS.LIST>
                    <CATEGORY>Primary Cost Category</CATEGORY>
                    <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
                    <COSTCENTREALLOCATIONS.LIST>
                        <NAME>{adj_cc}</NAME>
                        <AMOUNT>{r_amount:.2f}</AMOUNT>
                    </COSTCENTREALLOCATIONS.LIST>
                </CATEGORYALLOCATIONS.LIST>
            </ACCOUNTINGALLOCATIONS.LIST>
        </INVENTORYENTRIES.LIST>"""

        return_total = round(return_subtotal, 2)

        narration_parts = [p for p in [adjustment.reason, adjustment.notes] if p and p.strip()]
        narration_xml = f"<NARRATION>{escape(' -- '.join(narration_parts))}</NARRATION>" if narration_parts else ""

        debit_note_xml = f"""<ENVELOPE>
    <HEADER>
        <TALLYREQUEST>Import Data</TALLYREQUEST>
    </HEADER>
    <BODY>
        <IMPORTDATA>
            <REQUESTDESC>
                <REPORTNAME>All Masters</REPORTNAME>
                <STATICVARIABLES>
                    <SVCURRENTCOMPANY>##SVCURRENTCOMPANY</SVCURRENTCOMPANY>
                </STATICVARIABLES>
            </REQUESTDESC>
            <REQUESTDATA>
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <LEDGER ACTION="Alter" NAME="Purchase Return">
                        <NAME.LIST><NAME>Purchase Return</NAME></NAME.LIST>
                        <PARENT>Purchase Accounts</PARENT>
                    </LEDGER>
                </TALLYMESSAGE>
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <VOUCHER VCHTYPE="Debit Note" ACTION="Create">
                        <DATE>{tally_date}</DATE>
                        <GUID>{adj_guid}</GUID>
                        <VOUCHERTYPENAME>Debit Note</VOUCHERTYPENAME>
                        <REFERENCE>{inv_no}</REFERENCE>
                        <PARTYLEDGERNAME>{supplier}</PARTYLEDGERNAME>
                        <PARTYNAME>{supplier}</PARTYNAME>
                        <PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>
                        <ISINVOICE>Yes</ISINVOICE>
                        {narration_xml}
                        {return_inventory_xml}
                        <LEDGERENTRIES.LIST>
                            <LEDGERNAME>{supplier}</LEDGERNAME>
                            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                            <AMOUNT>-{return_total:.2f}</AMOUNT>
                            <BILLALLOCATIONS.LIST>
                                <NAME>{inv_no}</NAME>
                                <BILLTYPE>Agst Ref</BILLTYPE>
                                <AMOUNT>-{return_total:.2f}</AMOUNT>
                            </BILLALLOCATIONS.LIST>
                        </LEDGERENTRIES.LIST>
                    </VOUCHER>
                </TALLYMESSAGE>
            </REQUESTDATA>
        </IMPORTDATA>
    </BODY>
</ENVELOPE>"""

    # Queue-First Architecture: Always save transaction(s) to DB before attempting to send.
    queue_payload = payload.model_dump()
    queue_payload['created_by'] = current_user['name']
    queue_id = queue_operation("POST_VOUCHER", xml, queue_payload, f"Purchase Item Invoice: {inv_no} from {supplier}")
    adjustment_queue_id = None
    if debit_note_xml:
        adjustment_queue_id = queue_operation("POST_VOUCHER", debit_note_xml, queue_payload, f"Purchase Return (Debit Note): {inv_no} from {supplier}")

    try:
        from backend.database import get_master_dependency_state, is_master_pending_sync
        
        has_pending = False
        
        dependencies = [("LEDGER", payload.supplier, None)]
        for item in payload.items:
            uom = item.mapped_unit or item.uom
            if uom:
                uom = uom.replace('.', '').strip()
            if not uom or uom == 'Not Applicable':
                uom = 'PCS'
            if item.is_mapped:
                dependencies.append(("ITEM", item.mapped_name, None))
            else:
                dependencies.append(("ITEM", item.name, {"uom": uom}))
            dependencies.append(("UOM", uom, None))

        if payload.adjustment and payload.adjustment.items:
            for ritem in payload.adjustment.items:
                r_uom = (ritem.uom or "PCS").replace('.', '').strip() or "PCS"
                dependencies.append(("ITEM", ritem.name, None))
                dependencies.append(("UOM", r_uom, None))

        for entity_type, raw_name, definition_payload in dependencies:
            state, error_msg = get_master_dependency_state(entity_type, raw_name, definition_payload)
            if state == "FAILED":
                update_queue_status(queue_id, "FAILED", f"Cannot post invoice because {entity_type.lower()} '{raw_name}' failed to sync to Tally.")
                raise HTTPException(status_code=400, detail=f"Cannot post invoice because {entity_type.lower()} '{raw_name}' failed to sync to Tally. Resolve the master first.")
            elif state == "MISSING":
                if entity_type == "UOM":
                    uom_xml = f"""<ENVELOPE>
        <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
        <BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC><REQUESTDATA>
            <TALLYMESSAGE xmlns:UDF="TallyUDF">
                <UNIT ACTION="Create" NAME="{escape(raw_name)}">
                    <NAME>{escape(raw_name)}</NAME><ISSIMPLEUNIT>Yes</ISSIMPLEUNIT>
                </UNIT>
            </TALLYMESSAGE>
        </REQUESTDATA></IMPORTDATA></BODY>
    </ENVELOPE>"""
                    from backend.database import queue_master_operation
                    queue_master_operation("UOM", raw_name, "CREATE_UOM", uom_xml, {"uom": raw_name})
                    has_pending = True
                else:
                    update_queue_status(queue_id, "FAILED", f"Cannot post invoice because {entity_type.lower()} '{raw_name}' is not available in Tally or pending sync.")
                    raise HTTPException(status_code=400, detail=f"Cannot post invoice because {entity_type.lower()} '{raw_name}' is not available in Tally or pending sync. Create or refresh the master before posting.")
            elif state == "CONFLICT":
                update_queue_status(queue_id, "FAILED", f"Definition conflict for {entity_type.lower()} '{raw_name}': {error_msg}")
                raise HTTPException(status_code=409, detail=f"Cannot post invoice due to definition conflict for {entity_type.lower()} '{raw_name}': {error_msg}")
            elif is_master_pending_sync(state):
                has_pending = True

        # Record each item's observed rate now that the dependency loop above
        # has confirmed nothing is FAILED/CONFLICT (either already raised).
        # Runs before the has_pending early-return: a purchase merely waiting
        # on a master to sync is still legitimate data -- this IS the "if not
        # yet synced by Tally, use the web app's own voucher entry" source.
        # Uses the raw (unescaped) name and the invoice's real date, and does
        # NOT apply to payload.adjustment.items (returns aren't new purchases).
        for item in payload.items:
            if item.rate:
                raw_name = (item.mapped_name or item.name).lower()
                record_purchase_rate(raw_name, float(item.rate), payload.supplier, purchase_date, source_queue_id=queue_id)
                record_pending_purchase_rate_entry(
                    raw_name, float(item.rate), purchase_date, payload.supplier,
                    voucher_number=payload.invoice_number, qty=item.qty,
                    unit=item.mapped_unit or item.uom, source_queue_id=queue_id
                )

        if has_pending:
            # Leave as PENDING
            return {"status": "queued", "reason": "pending_master_dependency", "message": "Invoice saved to offline queue because a required master is still pending sync to Tally."}
            
        # Post Purchase Voucher
        set_delivery_uncertain(queue_id, True)
        response = await tally_transport.post(xml.encode('utf-8'), timeout=15)
        parsed = parse_tally_response(response.text, "POST_VOUCHER")

        if not parsed["is_success"]:
            set_delivery_uncertain(queue_id, False)
            update_queue_status(queue_id, "FAILED", parsed['error_message'])
            raise HTTPException(status_code=400, detail=f"Tally rejected the purchase entry: {parsed['error_message']}")

        set_delivery_uncertain(queue_id, False)
        update_queue_status(queue_id, "SYNCED")

        # Post Adjustment Voucher (Debit Note) if present
        adjustment_result = {"status": "not_applicable"}
        if adjustment_queue_id:
            adjustment_result = {"status": "queued"}
            try:
                set_delivery_uncertain(adjustment_queue_id, True)
                adj_response = await tally_transport.post(debit_note_xml.encode('utf-8'), timeout=15)
                adj_parsed = parse_tally_response(adj_response.text, "POST_VOUCHER")
                if not adj_parsed["is_success"]:
                    set_delivery_uncertain(adjustment_queue_id, False)
                    update_queue_status(adjustment_queue_id, "FAILED", adj_parsed['error_message'])
                    # We do not fail the request entirely since the purchase posted successfully.
                    # The UI will surface the return's failure separately.
                    adjustment_result = {"status": "failed", "message": adj_parsed['error_message']}
                else:
                    set_delivery_uncertain(adjustment_queue_id, False)
                    update_queue_status(adjustment_queue_id, "SYNCED")
                    adjustment_result = {"status": "success"}
            except requests.exceptions.ConnectTimeout:
                # Connection never established -- as safe as ConnectionError.
                set_delivery_uncertain(adjustment_queue_id, False)
                adjustment_result = {"status": "queued", "message": "Tally is offline. Return saved to queue and will push automatically."}
            except requests.exceptions.Timeout:
                # Ambiguous read timeout -- leave delivery_uncertain set, blocking manual retry.
                adjustment_result = {"status": "queued", "message": "Delivery uncertain -- verify the return in Tally before retrying."}
            except requests.exceptions.ConnectionError:
                set_delivery_uncertain(adjustment_queue_id, False)
                adjustment_result = {"status": "queued", "message": "Tally is offline. Return saved to queue and will push automatically."}

        return {"status": "success", "message": "Successfully posted to Tally.", "adjustment": adjustment_result}
    except requests.exceptions.ConnectTimeout:
        # The connection itself never established (Tally's address is unreachable)
        # -- as safe as ConnectionError, nothing was ever sent.
        set_delivery_uncertain(queue_id, False)
        return {"status": "queued", "message": "Tally is offline. Invoice saved to queue and will push automatically."}
    except requests.exceptions.Timeout:
        # Ambiguous: connection was established and the request was sent, but no
        # response came back in time -- Tally may have processed it before the
        # response was lost. Leave delivery_uncertain set (already persisted
        # above) so a manual retry is blocked until someone verifies in Tally.
        return {"status": "queued", "message": "Tally is offline. Invoice saved to queue and will push automatically."}
    except requests.exceptions.ConnectionError:
        # Request never reached Tally at all -- safe to clear and leave PENDING.
        set_delivery_uncertain(queue_id, False)
        return {"status": "queued", "message": "Tally is offline. Invoice saved to queue and will push automatically."}
    except HTTPException:
        raise
    except Exception as e:
        print("\n!!! EXCEPTION IN PURCHASE-ITEM POST !!!", flush=True)
        traceback.print_exc()
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n", flush=True)
        update_queue_status(queue_id, "FAILED", str(e))
        if adjustment_queue_id:
            update_queue_status(adjustment_queue_id, "FAILED", str(e))
        raise HTTPException(status_code=500, detail=str(e))
