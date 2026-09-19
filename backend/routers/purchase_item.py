import os
import json
import re
import datetime
import traceback
import tempfile
from xml.sax.saxutils import escape
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, File, UploadFile, HTTPException, Form
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
import requests

from json_repair import repair_json
from google import genai
from google.genai import types
import sqlite3

from backend.database import (
    get_all_ledgers, get_all_stock_items, get_all_uoms, get_all_aliases,
    save_alias as db_save_alias, queue_operation, record_purchase_rate, get_purchase_rates,
    get_db, update_queue_status, set_delivery_uncertain
)
from backend.services.tally_response import parse_tally_response
from backend.utils.math_reconciler import reconcile_full_invoice

router = APIRouter()

from backend.config import TALLY_URL
from backend.services.image_normalizer import normalize_uploaded_invoice
from backend.services.item_mapping import normalize_item_name, map_items_to_tally, map_supplier_to_tally

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

class PurchaseAdjustment(BaseModel):
    amount: float
    store: str
    reason: Optional[str] = None
    notes: Optional[str] = None

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
    ledgers = get_all_ledgers()
    stock_items = get_all_stock_items()
    uoms = get_all_uoms()
    aliases = get_all_aliases()

    suppliers = []
    for l in ledgers:
        parent = (l.get('parent') or '').lower()
        if "creditor" in parent or "loan" in parent:
            suppliers.append(l['name'].title())

    stock_item_names = [i['name'] for i in stock_items]

    from backend.database import get_master_states, get_active_stores
    master_states = get_master_states()
    
    active_stores = get_active_stores()
    store_names = [s['store_name'] for s in active_stores]

    return {
        "suppliers": sorted(list(set(suppliers))),
        "stock_items": sorted(stock_item_names),
        "uoms": uoms,
        "aliases": aliases,
        "master_states": master_states,
        "stores": store_names
    }

@router.post("/extract")
async def extract_invoice(
    files: List[UploadFile] = File(...),
    column_mapping: Optional[str] = Form(None)
):
    print(f"--- [PURCHASE-ITEM EXTRACT] Received {len(files)} files ---", flush=True)
    try:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not configured")

        client = genai.Client(api_key=api_key)

        file_bytes_list = []
        for file in files:
            f_bytes = await file.read()
            f_bytes = normalize_uploaded_invoice(f_bytes, file.filename, file.content_type)
            file_bytes_list.append(f_bytes)
            
        print(f"--- [PURCHASE-ITEM EXTRACT] Read {len(file_bytes_list)} files. Calling Hybrid V2 Engine... ---", flush=True)

        parsed_mapping = None
        if column_mapping:
            try:
                parsed_mapping = json.loads(column_mapping)
                print(f"--- [PURCHASE-ITEM EXTRACT] Using user-provided column mapping ---", flush=True)
            except json.JSONDecodeError:
                pass

        from backend.services.extraction_engine import process_invoice
        data = await run_in_threadpool(process_invoice, file_bytes_list, parsed_mapping)

        if not isinstance(data, dict):
            data = {}

        raw_items = data.get("items", [])
        if not isinstance(raw_items, list):
            raw_items = []
        detected_headers = data.get("detected_headers", [])
        supplier_name = data.get("supplier_name", "")

        print(f"--- [PURCHASE-ITEM EXTRACT] Vision extraction complete. Got {len(raw_items)} items. ---", flush=True)

        # Step 1.5: Algebraic Math Reconciliation
        gst_rate = float(data.get("gst_rate") or 0.0)
        # Build raw printed_* versions of the items for the reconciler
        raw_printed_items = []
        for it in raw_items:
            raw_printed_items.append({
                "name": it.get("name", ""),
                "reasoning": it.get("reasoning", ""),
                "printed_qty": it.get("printed_qty", it.get("qty")),
                "printed_uom": it.get("printed_uom", it.get("uom")),
                "printed_rate": it.get("printed_rate", it.get("rate")),
                "printed_discount_pct": it.get("printed_discount_pct", it.get("discount")),
                "printed_gst_pct": it.get("printed_gst_pct"),
                "printed_amount": it.get("printed_amount", it.get("amount")),
            })

        if gst_rate == 0.0:
            gst_rates = [float(it.get("printed_gst_pct")) for it in raw_printed_items if it.get("printed_gst_pct") is not None and float(it.get("printed_gst_pct")) > 0]
            if gst_rates:
                from collections import Counter
                most_common = Counter(gst_rates).most_common(1)[0][0]
                gst_rate = float(most_common)
                
        if gst_rate in [2.5, 6.0, 9.0, 14.0, 20.0]:
            gst_rate *= 2.0
            
        data["gst_rate"] = gst_rate

        reconciled_items = reconcile_full_invoice(raw_printed_items, data, column_mapping=parsed_mapping)

        # Validation Logic
        def calculate_grand_total(reconciled, inv_data):
            calc_sub = sum(r["amount"] for r in reconciled)
            tx = inv_data.get("taxes") or {}
            c = float(tx.get("cgst", inv_data.get("cgst", 0.0)))
            s = float(tx.get("sgst", inv_data.get("sgst", 0.0)))
            i = float(tx.get("igst", inv_data.get("igst", 0.0)))
            r = float(inv_data.get("rounding_off", 0.0))
            return calc_sub + c + s + i + r

        calc_sub = sum(r["amount"] for r in reconciled_items)
        if data.get("cgst", 0.0) == 0.0 and data.get("sgst", 0.0) == 0.0 and data.get("igst", 0.0) == 0.0 and gst_rate > 0.0:
            total_tax = calc_sub * (gst_rate / 100.0)
            data["cgst"] = round(total_tax / 2, 2)
            data["sgst"] = round(total_tax / 2, 2)

        calculated_grand_total = calculate_grand_total(reconciled_items, data)
        printed_grand_total = data.get("printed_grand_total")
        total_difference = 0.0
        
        validation_status = "unverified"
        mapping_source = None
        mapping_attempt_failed = False

        if printed_grand_total is not None:
            printed_grand_total = float(printed_grand_total)
            total_difference = abs(calculated_grand_total - printed_grand_total)
            is_match = total_difference <= 1.00
            
            if is_match:
                validation_status = "matched"
                if parsed_mapping:
                    mapping_source = "user"
                    from backend.database import save_supplier_column_mapping
                    save_supplier_column_mapping(supplier_name, parsed_mapping)
                else:
                    mapping_source = "normal"
            else:
                if parsed_mapping:
                    validation_status = "needs_mapping"
                    mapping_attempt_failed = True
                else:
                    # Look up saved mapping
                    from backend.database import get_supplier_column_mapping
                    saved_mapping = get_supplier_column_mapping(supplier_name)
                    
                    if saved_mapping:
                        def normalize_header(h):
                            return re.sub(r'[^a-z0-9]', '', (h or "").lower())
                            
                        norm_detected = [normalize_header(h) for h in detected_headers]
                        
                        req_headers = []
                        for key in ["qty_header", "rate_header", "amount_header"]:
                            if saved_mapping.get(key):
                                req_headers.append(saved_mapping[key])
                                
                        all_headers_exist = True
                        for h in req_headers:
                            if normalize_header(h) not in norm_detected:
                                all_headers_exist = False
                                break
                                
                        if all_headers_exist:
                            print(f"--- [PURCHASE-ITEM EXTRACT] Retrying with saved mapping... ---", flush=True)
                            data2 = await run_in_threadpool(process_invoice, file_bytes_list, saved_mapping)
                            raw_items2 = data2.get("items", [])
                            
                            raw_printed_items2 = []
                            for it in raw_items2:
                                raw_printed_items2.append({
                                    "name": it.get("name", ""),
                                    "reasoning": it.get("reasoning", ""),
                                    "printed_qty": it.get("printed_qty", it.get("qty")),
                                    "printed_uom": it.get("printed_uom", it.get("uom")),
                                    "printed_rate": it.get("printed_rate", it.get("rate")),
                                    "printed_discount_pct": it.get("printed_discount_pct", it.get("discount")),
                                    "printed_gst_pct": it.get("printed_gst_pct"),
                                    "printed_amount": it.get("printed_amount", it.get("amount")),
                                })
                            
                            gst_rate2 = float(data2.get("gst_rate") or 0.0)
                            if gst_rate2 == 0.0:
                                gst_rates2 = [float(it.get("printed_gst_pct")) for it in raw_printed_items2 if it.get("printed_gst_pct") is not None and float(it.get("printed_gst_pct")) > 0]
                                if gst_rates2:
                                    from collections import Counter
                                    most_common2 = Counter(gst_rates2).most_common(1)[0][0]
                                    gst_rate2 = float(most_common2)

                            if gst_rate2 in [2.5, 6.0, 9.0, 14.0, 20.0]:
                                gst_rate2 *= 2.0
                                
                            data2["gst_rate"] = gst_rate2

                            reconciled_items2 = reconcile_full_invoice(raw_printed_items2, data2, column_mapping=saved_mapping)
                            
                            calc_sub2 = sum(r["amount"] for r in reconciled_items2)
                            if data2.get("cgst", 0.0) == 0.0 and data2.get("sgst", 0.0) == 0.0 and data2.get("igst", 0.0) == 0.0 and gst_rate2 > 0.0:
                                total_tax2 = calc_sub2 * (gst_rate2 / 100.0)
                                data2["cgst"] = round(total_tax2 / 2, 2)
                                data2["sgst"] = round(total_tax2 / 2, 2)
                                
                            calc_grand2 = calculate_grand_total(reconciled_items2, data2)
                            diff2 = abs(calc_grand2 - printed_grand_total)
                            
                            if diff2 <= 1.00:
                                validation_status = "matched"
                                mapping_source = "saved"
                                data = data2
                                raw_items = raw_items2
                                reconciled_items = reconciled_items2
                                calculated_grand_total = calc_grand2
                                total_difference = diff2
                            else:
                                validation_status = "needs_mapping"
                        else:
                            validation_status = "needs_mapping"
                    else:
                        validation_status = "needs_mapping"

        # Merge the sanitized math results back into the original item rows,
        # preserving the extracted name and mapping fields.
        for idx, item in enumerate(raw_items):
            if idx < len(reconciled_items):
                r = reconciled_items[idx]
                item["qty"] = r["qty"]
                item["rate"] = r["rate"]
                item["discount"] = r["discount"]
                item["amount"] = r["amount"]
                item["uom"] = r["uom"]
            else:
                item["uom"] = str(item.get("printed_uom") or item.get("uom") or "PCS").strip().upper()

        print(f"--- [PURCHASE-ITEM EXTRACT] Math reconciliation complete. "
              f"{len(reconciled_items)} items reconciled (gst_rate={gst_rate}). ---", flush=True)

        # Step 2: Text Mapping (shared with the V4 engine — backend/services/item_mapping.py)
        print(f"--- [PURCHASE-ITEM EXTRACT] Mapping {len(raw_items)} items... ---", flush=True)
        mapped_items = map_items_to_tally(raw_items)

        # Map supplier
        supplier_name = data.get("supplier_name", "")
        mapped_supplier = map_supplier_to_tally(supplier_name)

        taxes_dict = data.get("taxes") or {}
        cgst = float(taxes_dict.get("cgst", data.get("cgst", 0.0)))
        sgst = float(taxes_dict.get("sgst", data.get("sgst", 0.0)))
        igst = float(taxes_dict.get("igst", data.get("igst", 0.0)))

        final_response_payload = {
            "validation_status": validation_status,
            "mapping_source": mapping_source,
            "mapping_attempt_failed": mapping_attempt_failed,
            "detected_headers": detected_headers,
            "printed_grand_total": printed_grand_total,
            "calculated_grand_total": calculated_grand_total,
            "total_difference": total_difference,
            "supplier": mapped_supplier,
            "invoice_number": str(data.get("invoice_number", "")),
            "date": str(data.get("date", datetime.datetime.now().strftime("%Y-%m-%d"))),
            "cgst": cgst,
            "sgst": sgst,
            "igst": igst,
            "rounding_off": float(data.get("rounding_off", 0.0)),
            "gst_rate": int(data.get("gst_rate", 0)),
            "tax_type": str(data.get("tax_type", "local")),
            "items": mapped_items
        }

        print(f"--- [PURCHASE-ITEM EXTRACT] Finished successfully. ---", flush=True)
        return final_response_payload

    except Exception as e:
        print("\n!!! EXCEPTION IN PURCHASE-ITEM EXTRACTION !!!", flush=True)
        traceback.print_exc()
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n", flush=True)
        raise HTTPException(status_code=500, detail=str(e))

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
        response = requests.post(TALLY_URL, data=xml_data.encode('utf-8'), timeout=4)
        parsed = parse_tally_response(response.text, "CREATE_LEDGER")
        if not parsed["is_success"]:
            raise HTTPException(status_code=400, detail=f"Tally rejected the creation: {parsed['error_message']}")
            
        from backend.services.tally_response import is_already_exists_success
        from backend.services.tally_verification import verify_tally_master_definition, VerificationResult
        
        insert_name = payload.name
        if is_already_exists_success(parsed, "CREATE_LEDGER"):
            ver_res, canonical = verify_tally_master_definition("LEDGER", payload.name, {"parent": "Sundry Creditors"})
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
            raise HTTPException(status_code=500, detail=str(e))
            
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
        raise HTTPException(status_code=400, detail=str(e))
        
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
            raise HTTPException(status_code=500, detail=str(e))

    # --- ITEM PHASE ---
    try:
        norm_item, _ = normalize_master_name(payload.name)
        if check_master_exists_locally('ITEM', norm_item, {"uom": payload.uom}):
            return {"status": "success", "message": f"Item '{payload.name}' already exists.", "name": payload.name, "uom": payload.uom}
    except MasterConflictException as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

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
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/save-alias")
async def save_alias_endpoint(payload: SaveAliasRequest):
    try:
        normalized_orig = normalize_item_name(payload.original_name)
        if normalized_orig != normalize_item_name(payload.mapped_name):
            db_save_alias(normalized_orig, payload.mapped_name)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/detect-corners")
async def detect_corners_endpoint(file: UploadFile = File(...)):
    """Best-effort starting point for the manual crop UI -- never a hard
    dependency, so this always returns 200 even when detection fails."""
    from backend.services.corner_detection import detect_document_corners
    file_bytes = await file.read()
    file_bytes = normalize_uploaded_invoice(file_bytes, file.filename, file.content_type)
    corners = await run_in_threadpool(detect_document_corners, file_bytes)
    return {"corners": corners}

@router.post("/post")
async def post_purchase_item(payload: PurchaseItemPostRequest):
    print(f"--- [PURCHASE-ITEM POST] Received payload ---", flush=True)
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

        if rate:
            record_purchase_rate(name.lower(), float(rate), payload.supplier, payload.date if hasattr(payload, 'date') else datetime.datetime.now().strftime("%Y-%m-%d"))

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
    
    # Queue-First Architecture: Always save transaction to DB before attempting to send
    queue_id = queue_operation("POST_VOUCHER", xml, payload.model_dump(), f"Purchase Item Invoice: {inv_no} from {supplier}")
    
    # Handle Adjustment (Purchase Return via Journal)
    adjustment_queue_id = None
    if payload.adjustment and payload.adjustment.amount > 0:
        adjustment = payload.adjustment
        adj_amount = f"{adjustment.amount:.2f}"
        adj_guid = f"PRJ-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        journal_xml = f"""<ENVELOPE>
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
                    <VOUCHER VCHTYPE="Journal" ACTION="Create">
                        <DATE>{tally_date}</DATE>
                        <GUID>{adj_guid}</GUID>
                        <VOUCHERTYPENAME>Journal</VOUCHERTYPENAME>
                        <REFERENCE>{inv_no}</REFERENCE>
                        <PARTYLEDGERNAME>{supplier}</PARTYLEDGERNAME>
                        <PARTYNAME>{supplier}</PARTYNAME>
                        <PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>
                        <ISINVOICE>No</ISINVOICE>
                        <LEDGERENTRIES.LIST>
                            <LEDGERNAME>{supplier}</LEDGERNAME>
                            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                            <AMOUNT>-{adj_amount}</AMOUNT>
                        </LEDGERENTRIES.LIST>
                        <LEDGERENTRIES.LIST>
                            <LEDGERNAME>Purchase Return</LEDGERNAME>
                            <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
                            <AMOUNT>{adj_amount}</AMOUNT>
                            <CATEGORYALLOCATIONS.LIST>
                                <CATEGORY>Primary Cost Category</CATEGORY>
                                <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
                                <COSTCENTREALLOCATIONS.LIST>
                                    <NAME>{escape(get_store_mapping(adjustment.store)['cost_center_name']) if get_store_mapping(adjustment.store) else escape(adjustment.store)}</NAME>
                                    <AMOUNT>{adj_amount}</AMOUNT>
                                </COSTCENTREALLOCATIONS.LIST>
                            </CATEGORYALLOCATIONS.LIST>
                        </LEDGERENTRIES.LIST>
                    </VOUCHER>
                </TALLYMESSAGE>
            </REQUESTDATA>
        </IMPORTDATA>
    </BODY>
</ENVELOPE>"""
        adjustment_queue_id = queue_operation("POST_VOUCHER", journal_xml, payload.model_dump(), f"Purchase Return (Adjustment): {inv_no} from {supplier}")

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
                
        if has_pending:
            # Leave as PENDING
            return {"status": "queued", "reason": "pending_master_dependency", "message": "Invoice saved to offline queue because a required master is still pending sync to Tally."}
            
        # Post Purchase Voucher
        set_delivery_uncertain(queue_id, True)
        response = requests.post(TALLY_URL, data=xml.encode('utf-8'), timeout=15)
        parsed = parse_tally_response(response.text, "POST_VOUCHER")

        if not parsed["is_success"]:
            set_delivery_uncertain(queue_id, False)
            update_queue_status(queue_id, "FAILED", parsed['error_message'])
            raise HTTPException(status_code=400, detail=f"Tally rejected the purchase entry: {parsed['error_message']}")

        set_delivery_uncertain(queue_id, False)
        update_queue_status(queue_id, "SYNCED")

        # Post Adjustment Voucher if present
        if adjustment_queue_id:
            try:
                set_delivery_uncertain(adjustment_queue_id, True)
                adj_response = requests.post(TALLY_URL, data=journal_xml.encode('utf-8'), timeout=15)
                adj_parsed = parse_tally_response(adj_response.text, "POST_VOUCHER")
                if not adj_parsed["is_success"]:
                    set_delivery_uncertain(adjustment_queue_id, False)
                    update_queue_status(adjustment_queue_id, "FAILED", adj_parsed['error_message'])
                    # We do not fail the request entirely since the purchase posted successfully.
                    # The UI will just tell them the adjustment failed and is in queue.
                else:
                    set_delivery_uncertain(adjustment_queue_id, False)
                    update_queue_status(adjustment_queue_id, "SYNCED")
            except requests.exceptions.ConnectTimeout:
                # Connection never established -- as safe as ConnectionError.
                set_delivery_uncertain(adjustment_queue_id, False)
            except requests.exceptions.Timeout:
                pass # Ambiguous read timeout -- leave delivery_uncertain set, blocking manual retry.
            except requests.exceptions.ConnectionError:
                set_delivery_uncertain(adjustment_queue_id, False)

        return {"status": "success", "message": "Successfully posted to Tally."}
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
