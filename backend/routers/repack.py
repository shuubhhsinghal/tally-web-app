from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from xml.sax.saxutils import escape

router = APIRouter()

from typing import List

class RepackComponent(BaseModel):
    item_name: str
    unit: str
    quantity: float

class RepackProductRequest(BaseModel):
    new_product_name: str
    components: List[RepackComponent]
    output_unit: str

@router.post("/product")
async def create_repack_product(payload: RepackProductRequest):
    if not payload.components:
        raise HTTPException(status_code=400, detail="At least one component is required.")
        
    for c in payload.components:
        if c.quantity <= 0:
            raise HTTPException(status_code=400, detail=f"Component {c.item_name} must have a positive quantity.")

    from backend.database import get_db, queue_master_operation, MasterConflictException, MasterFailedException, check_master_exists_locally, normalize_master_name, insert_product_conversion
    
    parent_group = ""
    # Optional: inherit parent group from first component if possible

        
    import json
    import os
    master_states_path = os.path.join(os.path.dirname(__file__), "..", "tally_ledger_cache.json")
    if os.path.exists(master_states_path):
        with open(master_states_path, "r") as f:
            try:
                data = json.load(f)
                items = data.get("stock_items", [])
                for item in items:
                    if item.get("name") == payload.components[0].item_name:
                        parent_group = item.get("parent", "")
                        break
            except Exception:
                pass

    uom_xml = f"""<ENVELOPE>
        <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
        <BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC><REQUESTDATA>
            <TALLYMESSAGE xmlns:UDF="TallyUDF">
                <UNIT ACTION="Create" NAME="{escape(payload.output_unit)}">
                    <NAME>{escape(payload.output_unit)}</NAME><ISSIMPLEUNIT>Yes</ISSIMPLEUNIT>
                </UNIT>
            </TALLYMESSAGE>
        </REQUESTDATA></IMPORTDATA></BODY>
    </ENVELOPE>"""
    
    parent_xml = f"<PARENT>{escape(parent_group)}</PARENT>" if parent_group else ""
    item_xml = f"""<ENVELOPE>
        <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
        <BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC><REQUESTDATA>
            <TALLYMESSAGE xmlns:UDF="TallyUDF">
                <STOCKITEM ACTION="Create" NAME="{escape(payload.new_product_name)}">
                    <NAME.LIST><NAME>{escape(payload.new_product_name)}</NAME></NAME.LIST>
                    {parent_xml}<BASEUNITS>{escape(payload.output_unit)}</BASEUNITS>
                </STOCKITEM>
            </TALLYMESSAGE>
        </REQUESTDATA></IMPORTDATA></BODY>
    </ENVELOPE>"""

    # --- UOM PHASE ---
    uom_state = "NEW"
    try:
        norm_uom, _ = normalize_master_name(payload.output_unit)
        if check_master_exists_locally('UOM', norm_uom, {}):
            uom_state = "CONFIRMED"
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    if uom_state == "NEW":
        try:
            res = queue_master_operation("UOM", payload.output_unit, "CREATE_UOM", uom_xml, {"uom": payload.output_unit})
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
        norm_item, _ = normalize_master_name(payload.new_product_name)
        if check_master_exists_locally('ITEM', norm_item, {"uom": payload.output_unit}):
            raise MasterConflictException(f"Item '{payload.new_product_name}' already exists.")
    except MasterConflictException as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        res = queue_master_operation("ITEM", payload.new_product_name, "CREATE_ITEM", item_xml, {"name": payload.new_product_name, "uom": payload.output_unit, "parent": parent_group})
    except MasterConflictException as e:
        raise HTTPException(status_code=409, detail=str(e))
    except MasterFailedException as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    new_id = insert_product_conversion(
        finished_stock_item=payload.new_product_name,
        output_unit=payload.output_unit,
        components=[c.model_dump() for c in payload.components]
    )

    return {"status": "success", "message": f"Product '{payload.new_product_name}' queued for creation.", "name": payload.new_product_name, "conversion_id": new_id}

@router.get("/products")
async def get_products():
    from backend.database import get_product_conversions
    return get_product_conversions()

@router.get("/stores")
async def get_stores():
    from backend.database import get_db
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, store_name, cost_center_name, godown_name, active FROM stores WHERE active = 1")
        return [dict(row) for row in cursor.fetchall()]

@router.get("/stock-items")
async def get_repack_stock_items():
    from backend.database import get_db
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name, unit as base_unit FROM stock_items ORDER BY name ASC")
        return [dict(row) for row in cursor.fetchall()]

class RepackExecuteRequest(BaseModel):
    date: str = None
    store_name: str
    conversion_id: int
    dest_qty: float

@router.post("/execute")
async def execute_repack(payload: RepackExecuteRequest):
    if payload.dest_qty <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be positive.")

    from backend.database import get_db, queue_operation, get_purchase_rate
    from backend.services.tally_godown_stock import get_godown_stock, is_tally_reachable
    import requests
    import uuid
    import json

    # 1 & 2. Get Conversion and Store mappings
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM product_conversions WHERE id = ?", (payload.conversion_id,))
        conv = cursor.fetchone()
        if not conv:
            raise HTTPException(status_code=404, detail="Product conversion not found.")
            
        dest_item = conv["finished_stock_item"]
        
        cursor.execute("SELECT * FROM stores WHERE store_name = ?", (payload.store_name,))
        store = cursor.fetchone()
        if not store:
            raise HTTPException(status_code=404, detail="Store not found.")
            
        godown_name = store["godown_name"]
        if not godown_name:
            raise HTTPException(status_code=400, detail="Selected store does not have a Godown mapping.")

        cursor.execute("SELECT * FROM repack_recipe_components WHERE conversion_id = ?", (payload.conversion_id,))
        components = [dict(row) for row in cursor.fetchall()]

    if not components:
        raise HTTPException(status_code=400, detail="Product recipe has no components.")

    # A single quick reachability check up front, so an offline Tally skips
    # the stock-sufficiency check for every component in one shot rather than
    # paying a separate connection timeout per component.
    tally_reachable = is_tally_reachable()

    # 3. Calculate requirements, using each component's latest purchase rate
    total_source_amount = 0.0
    inventory_out_xml = ""
    component_snapshots = []

    from xml.sax.saxutils import escape
    import datetime

    for comp in components:
        comp_name = comp["component_item_name"]
        qty_required = payload.dest_qty * comp["quantity_per_finished_unit"]

        # Cost each component at its latest purchase rate (Tally-confirmed,
        # else this app's own local record) -- the same source used
        # everywhere else in the app (returns, transfers) -- rather than
        # Tally's live Godown valuation, which reflects whatever costing
        # method the item is configured with (Average Cost by default) and
        # so blends old and new stock instead of reflecting the latest price.
        comp_rate = get_purchase_rate(comp_name)
        if comp_rate <= 0:
            raise HTTPException(status_code=400, detail=f"Could not resolve a purchase rate for '{comp_name}'. Refresh masters or record a purchase for it first.")

        # Stock-sufficiency is still worth checking live against Tally when
        # possible, but it's best-effort: unlike rate, there's no local cache
        # of physical stock quantity to fall back on, so if Tally is simply
        # unreachable we skip this check and trust the recipe rather than
        # blocking the whole (otherwise fully offline-capable) operation.
        # Gated on the single upfront reachability ping above so an offline
        # Tally skips this for every component at once, not one timeout each.
        try:
            if not tally_reachable:
                raise requests.exceptions.ConnectionError("Tally is offline (skipped by upfront reachability check).")
            stock = get_godown_stock(comp_name, godown_name)
            if stock["qty"] < qty_required:
                raise HTTPException(status_code=400, detail=f"Insufficient stock for '{comp_name}' in Godown '{godown_name}'. Required: {qty_required}, Available: {stock['qty']}")
        except HTTPException:
            raise
        except requests.exceptions.RequestException:
            pass  # Tally unreachable -- proceed without the stock-sufficiency check.
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

        comp_amount = qty_required * comp_rate
        total_source_amount += comp_amount

        component_snapshots.append({
            "name": comp_name,
            "qty": qty_required,
            "rate": comp_rate,
            "amount": comp_amount,
            "godown": godown_name
        })

        inventory_out_xml += f"""
                <INVENTORYENTRIESOUT.LIST>
                  <STOCKITEMNAME>{escape(comp_name)}</STOCKITEMNAME>
                  <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
                  <RATE>{comp_rate}</RATE>
                  <AMOUNT>{comp_amount:.2f}</AMOUNT>
                  <ACTUALQTY> {qty_required:.4f}</ACTUALQTY>
                  <BILLEDQTY> {qty_required:.4f}</BILLEDQTY>
                  <BATCHALLOCATIONS.LIST>
                    <GODOWNNAME>{escape(godown_name)}</GODOWNNAME>
                    <AMOUNT>{comp_amount:.2f}</AMOUNT>
                    <ACTUALQTY> {qty_required:.4f}</ACTUALQTY>
                    <BILLEDQTY> {qty_required:.4f}</BILLEDQTY>
                  </BATCHALLOCATIONS.LIST>
                </INVENTORYENTRIESOUT.LIST>"""

    # 6. Build Stock Journal XML
    
    repack_id = str(uuid.uuid4())
    guid = f"REPACK-{repack_id}"
    voucher_number = f"RP-{repack_id[:8]}"
    dest_rate = round(total_source_amount / payload.dest_qty, 2)

    if payload.date:
        current_date_tally = payload.date.replace("-", "")
        purchase_date_iso = payload.date
    else:
        current_date_tally = datetime.datetime.now().strftime('%Y%m%d')
        purchase_date_iso = datetime.datetime.now().strftime('%Y-%m-%d')

    xml_data = f"""<ENVELOPE>
      <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
      <BODY>
        <IMPORTDATA>
          <REQUESTDESC>
            <REPORTNAME>Vouchers</REPORTNAME>
            <STATICVARIABLES>
              <SVCURRENTCOMPANY>##SVCURRENTCOMPANY</SVCURRENTCOMPANY>
            </STATICVARIABLES>
          </REQUESTDESC>
          <REQUESTDATA>
            <TALLYMESSAGE xmlns:UDF="TallyUDF">
              <VOUCHER VCHTYPE="Stock Journal" ACTION="Create">
                <DATE>{current_date_tally}</DATE>
                <GUID>{guid}</GUID>
                <VOUCHERTYPENAME>Stock Journal</VOUCHERTYPENAME>
                <VOUCHERNUMBER>{voucher_number}</VOUCHERNUMBER>
                <NARRATION>Repack: {payload.dest_qty} {escape(conv['output_unit'])} of {escape(dest_item)} at {escape(payload.store_name)}</NARRATION>
                <ISINVOICE>No</ISINVOICE>
                {inventory_out_xml}
                <INVENTORYENTRIESIN.LIST>
                  <STOCKITEMNAME>{escape(dest_item)}</STOCKITEMNAME>
                  <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                  <RATE>{dest_rate:.2f}</RATE>
                  <AMOUNT>-{total_source_amount:.2f}</AMOUNT>
                  <ACTUALQTY> {payload.dest_qty:.4f}</ACTUALQTY>
                  <BILLEDQTY> {payload.dest_qty:.4f}</BILLEDQTY>
                  <BATCHALLOCATIONS.LIST>
                    <GODOWNNAME>{escape(godown_name)}</GODOWNNAME>
                    <AMOUNT>-{total_source_amount:.2f}</AMOUNT>
                    <ACTUALQTY> {payload.dest_qty:.4f}</ACTUALQTY>
                    <BILLEDQTY> {payload.dest_qty:.4f}</BILLEDQTY>
                  </BATCHALLOCATIONS.LIST>
                </INVENTORYENTRIESIN.LIST>
              </VOUCHER>
            </TALLYMESSAGE>
          </REQUESTDATA>
        </IMPORTDATA>
      </BODY>
    </ENVELOPE>"""

    # 7. Open DB again and Insert repack_operations
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO repack_operations (
                id, store_name, source_item_name, dest_item_name, 
                source_qty, source_rate, source_amount, dest_qty, conversion_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            repack_id, payload.store_name, "MULTI_COMPONENT", dest_item,
            0, 0, total_source_amount, payload.dest_qty, payload.conversion_id
        ))

        for snap in component_snapshots:
            cursor.execute("""
                INSERT INTO repack_operation_components (
                    operation_id, component_item_name, quantity, rate, amount, godown_name
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                repack_id, snap["name"], snap["qty"], snap["rate"], snap["amount"], snap["godown"]
            ))

        conn.commit()
        
    # 8. Queue voucher operation (has its own DB connection)
    queue_payload = {"repack_id": repack_id}
    queue_id = queue_operation(
        operation_type="REPACK_VOUCHER",
        xml_data=xml_data,
        payload=queue_payload,
        description=f"Repack: {payload.dest_qty} {conv['output_unit']} of {dest_item} at {payload.store_name}"
    )

    # 9. Feed the just-computed cost into the same rate-tracking system real
    # purchases use, so returning/transferring this item later has a real
    # rate to work from instead of "no rate found" -- and so this cost shows
    # up in the item's rate-history picker. Tied to queue_id like every other
    # rate record, so deleting this queued voucher (dashboard.py's
    # delete_activity) automatically un-records it too.
    from backend.database import record_purchase_rate, record_pending_purchase_rate_entry
    record_purchase_rate(dest_item.lower(), dest_rate, "Repack", purchase_date_iso, source_queue_id=queue_id)
    record_pending_purchase_rate_entry(
        dest_item.lower(), rate=dest_rate, purchase_date=purchase_date_iso, supplier=None,
        voucher_number=guid, qty=payload.dest_qty, unit=conv['output_unit'],
        source_queue_id=queue_id, source_type='repack'
    )

    return {"status": "success", "repack_id": repack_id, "message": f"Repack transaction queued. Value: ₹{total_source_amount:.2f}"}
