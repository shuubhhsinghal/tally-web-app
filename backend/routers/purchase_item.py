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
import difflib
import sqlite3

from backend.database import (
    get_all_ledgers, get_all_stock_items, get_all_uoms, get_all_aliases,
    save_alias as db_save_alias, queue_operation, record_purchase_rate, get_purchase_rates,
    get_db, update_queue_status
)
from backend.services.tally_response import parse_tally_response
from backend.utils.math_reconciler import reconcile_full_invoice

router = APIRouter()

from backend.config import TALLY_URL
from backend.services.image_normalizer import normalize_uploaded_invoice

def normalize_item_name(name: str) -> str:
    name = (name or "").strip().casefold()
    name = re.sub(r'\s+', ' ', name)
    name = re.sub(r'\s*\(\s*', '(', name)
    name = re.sub(r'\s*\)\s*', ')', name)
    return name

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

    from backend.database import get_master_states
    master_states = get_master_states()

    return {
        "suppliers": sorted(list(set(suppliers))),
        "stock_items": sorted(stock_item_names),
        "uoms": uoms,
        "aliases": aliases,
        "master_states": master_states
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

        # Step 0.9: Debug — exact raw output from Gemini
        print("\n=== DEBUG: RAW GEMINI ITEMS ===", flush=True)
        print(json.dumps(raw_items, indent=2, default=str), flush=True)

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
                            data2 = process_invoice(file_bytes, column_mapping=saved_mapping)
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
                                    data2["gst_rate"] = float(most_common2)

                            reconciled_items2 = reconcile_full_invoice(raw_printed_items2, data2, column_mapping=saved_mapping)
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

        # Debug — exact output after math reconciliation
        print("\n=== DEBUG: RECONCILED ITEMS ===", flush=True)
        print(json.dumps(reconciled_items, indent=2, default=str), flush=True)

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

        # Step 2: Text Mapping
        stock_items = get_all_stock_items()
        aliases = get_all_aliases()
        stock_cache = {normalize_item_name(i['name']): i for i in stock_items}

        from backend.database import get_db
        pending_item_names = []
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT normalized_name, original_name FROM pending_masters WHERE entity_type = 'ITEM' AND status IN ('PENDING', 'SYNCED_WAITING_CONFIRMATION')")
            for row in cursor.fetchall():
                norm = row["normalized_name"]
                orig = row["original_name"]
                pending_item_names.append(orig)
                if norm not in stock_cache:
                    stock_cache[norm] = {"name": orig}

        mapped_items = []

        # First try exact match / alias cache
        unmapped_raw_items = []
        for item in raw_items:
            original_name = item.get('name', 'Unknown')
            norm_name = normalize_item_name(original_name)

            match_found = False
            mapped_name = None
            mapped_unit = item.get('uom', 'PCS').upper()

            # Check alias
            if norm_name in aliases:
                mapped_name = aliases[norm_name]
                match_found = True
            else:
                # Check stock cache
                for stock_key, stock_data in stock_cache.items():
                    if stock_key == norm_name or stock_data.get('name', '').lower().strip() == original_name.lower().strip():
                        mapped_name = stock_data.get('name')
                        mapped_unit = stock_data.get('unit', mapped_unit)
                        match_found = True
                        break

            item['mapped_name'] = mapped_name if match_found else ""
            item['mapped_unit'] = mapped_unit
            item['is_mapped'] = match_found

            if match_found:
                mapped_items.append(item)
            else:
                unmapped_raw_items.append(item)
                mapped_items.append(item)

        # If there are unmapped items, try Gemini text mapper
        tally_item_names = [i['name'] for i in stock_items]
        seen_tally_names = set(normalize_item_name(n) for n in tally_item_names)
        for name in pending_item_names:
            norm = normalize_item_name(name)
            if norm not in seen_tally_names:
                tally_item_names.append(name)
                seen_tally_names.add(norm)
                
        if unmapped_raw_items:
            print(f"--- [PURCHASE-ITEM EXTRACT] Calling Text Mapping for {len(unmapped_raw_items)} items... ---", flush=True)
            master_list_str = "\n".join(f"- {name}" for name in tally_item_names)

            map_prompt = f"""
            You are a data-mapping assistant. 
            I have extracted the following raw items from an invoice: {json.dumps(unmapped_raw_items)}
            
            Here is my Tally Master Stock List:
            {master_list_str}
            
            For each raw item, find the exact matching string from the Tally Master Stock List. You must account for typos, case differences, and spacing (e.g. '400g' vs '400gm').
            If a confident match is found, add a key called 'mapped_name' to the item object containing the exact Tally string. 
            If no match is found, leave 'mapped_name' empty.
            
            Return the entire updated invoice JSON using this schema:
            {{
              "items": [
                {{"name": "...", "qty": 0.0, "uom": "pcs", "rate": 0.0, "amount": 0.0, "mapped_name": "...", "mapped_unit": "..."}}
              ]
            }}
            """

            try:
                map_response = client.models.generate_content(
                    model='gemini-3.5-flash-lite',
                    contents=[map_prompt],
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                )
                map_text = map_response.text.strip()
                map_text = re.sub(r'^```json\s*', '', map_text)
                map_text = re.sub(r'\s*```$', '', map_text)
                map_data = repair_json(map_text, return_objects=True)

                if isinstance(map_data, dict) and "items" in map_data:
                    # Merge back mapped names
                    for mapped_row in map_data["items"]:
                        for out_row in mapped_items:
                            if out_row['name'] == mapped_row['name'] and mapped_row.get('mapped_name'):
                                # Ensure the mapped name actually exists in Tally before trusting the AI
                                if mapped_row['mapped_name'] in tally_item_names:
                                    out_row['mapped_name'] = mapped_row['mapped_name']
                                    out_row['is_mapped'] = True

                                    # fetch correct unit
                                    norm_mapped = normalize_item_name(out_row['mapped_name'])
                                    if norm_mapped in stock_cache:
                                        out_row['mapped_unit'] = stock_cache[norm_mapped].get('unit', out_row['mapped_unit'])
            except Exception as e:
                print(f"--- [PURCHASE-ITEM EXTRACT] Text Mapping failed: {e} ---", flush=True)

        # Try to map supplier
        supplier_name = data.get("supplier_name", "")
        ledgers = get_all_ledgers()
        cached_suppliers_list = []
        for l in ledgers:
            parent = (l.get('parent') or '').lower()
            if "creditor" in parent or "loan" in parent:
                cached_suppliers_list.append(l['name'].title())

        mapped_supplier = ""
        if supplier_name and cached_suppliers_list:
            matches = difflib.get_close_matches(
                supplier_name,
                cached_suppliers_list,
                n=1,
                cutoff=0.8
            )
            if matches:
                print(f"--- [RESOLVE] Auto-corrected Supplier from '{supplier_name}' to '{matches[0]}' ---", flush=True)
                mapped_supplier = matches[0]

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
            "supplier": mapped_supplier or (supplier_name or "Unknown Supplier").title(),
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

        # Debug — exact final payload sent to frontend
        print("\n=== DEBUG: FINAL PAYLOAD SENT TO FRONTEND ===", flush=True)
        print(json.dumps(final_response_payload, indent=2, default=str), flush=True)

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

@router.post("/post")
async def post_purchase_item(payload: PurchaseItemPostRequest):
    print(f"--- [PURCHASE-ITEM POST] Received payload ---", flush=True)
    supplier = escape(payload.supplier)
    inv_no = escape(payload.invoice_number)
    tally_date = payload.tally_date
    cc = escape(payload.cost_center)

    inventory_xml = ""
    item_subtotal = 0.0

    for item in payload.items:
        name = escape(item.mapped_name or item.name)
        qty = item.qty
        unit = item.mapped_unit or item.uom
        if not unit or unit == 'Not Applicable':
            unit = 'PCS'
        unit = escape(unit)
        rate = item.rate
        amount = item.amount

        if rate:
            record_purchase_rate(name.lower(), float(rate), payload.supplier, payload.date if hasattr(payload, 'date') else datetime.datetime.now().strftime("%Y-%m-%d"))

        item_subtotal += amount

        inventory_xml += f"""
        <INVENTORYENTRIES.LIST>
            <STOCKITEMNAME>{name}</STOCKITEMNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <RATE>{rate}/{unit}</RATE>
            <DISCOUNT>{item.discount}</DISCOUNT>
            <AMOUNT>-{amount:.2f}</AMOUNT>
            <ACTUALQTY> {qty} {unit}</ACTUALQTY>
            <BILLEDQTY> {qty} {unit}</BILLEDQTY>
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

    master_xml = """
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <LEDGER ACTION="Alter" NAME="Input CGST">
                        <NAME.LIST><NAME>Input CGST</NAME></NAME.LIST>
                        <PARENT>Duties &amp; Taxes</PARENT>
                        <TAXTYPE>GST</TAXTYPE>
                        <GSTDUTYHEAD>Central Tax</GSTDUTYHEAD>
                    </LEDGER>
                </TALLYMESSAGE>
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <LEDGER ACTION="Alter" NAME="Input SGST">
                        <NAME.LIST><NAME>Input SGST</NAME></NAME.LIST>
                        <PARENT>Duties &amp; Taxes</PARENT>
                        <TAXTYPE>GST</TAXTYPE>
                        <GSTDUTYHEAD>State Tax</GSTDUTYHEAD>
                    </LEDGER>
                </TALLYMESSAGE>
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <LEDGER ACTION="Alter" NAME="Input IGST">
                        <NAME.LIST><NAME>Input IGST</NAME></NAME.LIST>
                        <PARENT>Duties &amp; Taxes</PARENT>
                        <TAXTYPE>GST</TAXTYPE>
                        <GSTDUTYHEAD>Integrated Tax</GSTDUTYHEAD>
                    </LEDGER>
                </TALLYMESSAGE>
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

    try:
        from backend.database import get_master_dependency_state, is_master_pending_sync
        
        has_pending = False
        
        dependencies = [("LEDGER", payload.supplier, None)]
        for item in payload.items:
            uom = item.mapped_unit or item.uom
            if not uom or uom == 'Not Applicable':
                uom = 'PCS'
            dependencies.append(("ITEM", item.mapped_name or item.name, {"uom": uom}))
            dependencies.append(("UOM", uom, None))
            
        for entity_type, raw_name, definition_payload in dependencies:
            state, error_msg = get_master_dependency_state(entity_type, raw_name, definition_payload)
            if state == "FAILED":
                update_queue_status(queue_id, "FAILED", f"Cannot post invoice because {entity_type.lower()} '{raw_name}' failed to sync to Tally.")
                raise HTTPException(status_code=400, detail=f"Cannot post invoice because {entity_type.lower()} '{raw_name}' failed to sync to Tally. Resolve the master first.")
            elif state == "MISSING":
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
            
        response = requests.post(TALLY_URL, data=xml.encode('utf-8'), timeout=15)
        parsed = parse_tally_response(response.text, "POST_VOUCHER")
        
        if not parsed["is_success"]:
            update_queue_status(queue_id, "FAILED", parsed['error_message'])
            raise HTTPException(status_code=400, detail=f"Tally rejected the entry: {parsed['error_message']}")
            
        update_queue_status(queue_id, "SYNCED")
        return {"status": "success", "message": "Successfully posted to Tally."}
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        # Already marked as PENDING in the DB, leave it for the background worker
        return {"status": "queued", "message": "Tally is offline. Invoice saved to queue and will push automatically."}
    except HTTPException:
        raise
    except Exception as e:
        print("\n!!! EXCEPTION IN PURCHASE-ITEM POST !!!", flush=True)
        traceback.print_exc()
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n", flush=True)
        update_queue_status(queue_id, "FAILED", str(e))
        raise HTTPException(status_code=500, detail=str(e))
