import os
import json
import requests
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from backend.database import get_db
from backend.config import TALLY_URL

router = APIRouter()

@router.get("/stats")
def get_dashboard_stats():
    queue_count = 0
    cache_count = 0
    failed_count = 0
    
    # 1. Check SQLite Queue
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as count FROM offline_queue WHERE status = 'PENDING'")
            row = cursor.fetchone()
            if row:
                queue_count = row['count']

            cursor.execute("SELECT COUNT(*) as count FROM stock_items")
            row = cursor.fetchone()
            if row:
                cache_count = row['count']

            # True total, regardless of the 10-item activity feed cap or is_hidden state.
            cursor.execute("SELECT COUNT(*) as count FROM offline_queue WHERE status = 'FAILED'")
            row = cursor.fetchone()
            if row:
                failed_count = row['count']
    except Exception as e:
        print(f"Error fetching stats: {e}")

    # 3. Check Tally Server Status
    tally_online = False
    try:
        response = requests.get(TALLY_URL, timeout=2)
        if response.status_code == 200:
            tally_online = True
    except:
        pass

    return {
        "queue_count": queue_count,
        "cache_count": cache_count,
        "failed_count": failed_count,
        "tally_online": tally_online
    }

@router.get("/activity")
def get_recent_activity(
    status: Optional[str] = Query(None, description="Filter by status, e.g. FAILED"),
    include_hidden: bool = Query(False, description="Include items hidden by 'Clear Finished'"),
    limit: int = Query(10, ge=1, le=500)
):
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            conditions = []
            params = []
            if not include_hidden:
                conditions.append("is_hidden = 0")
            if status:
                conditions.append("status = ?")
                params.append(status)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            params.append(limit)
            cursor.execute(
                f"SELECT id, operation_type, status, description, created_at, is_hidden "
                f"FROM offline_queue {where} ORDER BY id DESC LIMIT ?",
                params
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        print(f"Error fetching activity: {e}")
        return []

QUEUE_PAGE_CAP = 200
QUEUE_STATUSES = ("PENDING", "FAILED", "SYNCED")

# A transaction "belongs" to a store if its payload carries a matching cost_center
# (purchase/payment/bank_statement) or from_store/to_store (stock transfer -- shows
# under BOTH stores it touches), or if it's a repack voucher whose repack_operations
# row records that store. Sales, fund transfers, and master creates never carry a
# store field at all, so they never match this and fall into "Unallocated" instead.
STORE_MATCH_SQL = """(
    json_extract(offline_queue.payload, '$.cost_center') = ?
    OR json_extract(offline_queue.payload, '$.from_store') = ?
    OR json_extract(offline_queue.payload, '$.to_store') = ?
    OR (offline_queue.operation_type = 'REPACK_VOUCHER' AND EXISTS (
        SELECT 1 FROM repack_operations ro
        WHERE ro.id = json_extract(offline_queue.payload, '$.repack_id') AND ro.store_name = ?
    ))
)"""

UNALLOCATED_MATCH_SQL = """(
    json_extract(offline_queue.payload, '$.cost_center') IS NULL
    AND json_extract(offline_queue.payload, '$.from_store') IS NULL
    AND json_extract(offline_queue.payload, '$.to_store') IS NULL
    AND NOT (offline_queue.operation_type = 'REPACK_VOUCHER' AND EXISTS (
        SELECT 1 FROM repack_operations ro
        WHERE ro.id = json_extract(offline_queue.payload, '$.repack_id') AND ro.store_name IS NOT NULL
    ))
)"""

# Almost every real voucher shares operation_type='POST_VOUCHER' (sales, purchase,
# payment, transfer, stock transfer, bank statement all use it), so transaction TYPE
# can only be told apart by the description prefix each router writes at queue time.
# These prefixes are verified mutually exclusive -- none is a string-prefix of another.
# Master-creation rows (Create Ledger/Item/UOM) are intentionally not offered here;
# they still appear under "All Types" but aren't a selectable transaction type.
TYPE_FILTERS = {
    "SALES": ("offline_queue.description LIKE ?", ["Sales:%"]),
    "PURCHASE": ("offline_queue.description LIKE ?", ["Purchase Invoice:%"]),
    "PURCHASE_ITEM": (
        "(offline_queue.description LIKE ? OR offline_queue.description LIKE ?)",
        ["Purchase Item Invoice:%", "Purchase Return (Adjustment):%"],
    ),
    "PAYMENT": ("offline_queue.description LIKE ?", ["Payment:%"]),
    "TRANSFER": ("offline_queue.description LIKE ?", ["Transfer:%"]),
    "STOCK_TRANSFER": ("offline_queue.description LIKE ?", ["Stock Transfer:%"]),
    "BANK_STATEMENT": ("offline_queue.description LIKE ?", ["Bank Stmt:%"]),
    "REPACK": ("offline_queue.operation_type = ?", ["REPACK_VOUCHER"]),
}

@router.get("/queue")
def get_queue_page(
    status: str = Query(..., description="One of PENDING, FAILED, SYNCED"),
    store: Optional[str] = Query(None, description="A store name, or 'Unallocated'; omit for all stores"),
    type: Optional[str] = Query(None, description=f"One of {', '.join(TYPE_FILTERS.keys())}; omit for all types"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
):
    if status not in QUEUE_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {', '.join(QUEUE_STATUSES)}")
    if type is not None and type not in TYPE_FILTERS:
        raise HTTPException(status_code=400, detail=f"type must be one of {', '.join(TYPE_FILTERS.keys())}")

    where = "offline_queue.status = ?"
    params = [status]
    if store == "Unallocated":
        where += f" AND {UNALLOCATED_MATCH_SQL}"
    elif store:
        where += f" AND {STORE_MATCH_SQL}"
        params += [store, store, store, store]
    if type:
        type_sql, type_params = TYPE_FILTERS[type]
        where += f" AND {type_sql}"
        params += type_params

    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) as count FROM offline_queue WHERE {where}", params)
            actual_total = cursor.fetchone()['count']
            total = min(actual_total, QUEUE_PAGE_CAP)

            offset = (page - 1) * limit
            items = []
            if offset < QUEUE_PAGE_CAP:
                # Full ledger view -- unlike /activity, is_hidden is deliberately ignored here.
                fetch_limit = min(limit, QUEUE_PAGE_CAP - offset)
                cursor.execute(
                    f"SELECT offline_queue.id, offline_queue.operation_type, offline_queue.status, "
                    f"offline_queue.description, offline_queue.created_at, offline_queue.is_hidden "
                    f"FROM offline_queue WHERE {where} ORDER BY offline_queue.id DESC LIMIT ? OFFSET ?",
                    params + [fetch_limit, offset]
                )
                items = [dict(row) for row in cursor.fetchall()]
            return {"items": items, "total": total, "page": page, "limit": limit}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error fetching queue page: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class QueueUpdatePayload(BaseModel):
    payload: str
    xml_data: str

@router.get("/activity/{item_id}")
def get_activity_detail(item_id: int):
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM offline_queue WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Item not found")
            return dict(row)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/activity/{item_id}")
def update_activity(item_id: int, data: QueueUpdatePayload):
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT operation_type FROM offline_queue WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if row and row['operation_type'] in ('CREATE_LEDGER', 'CREATE_ITEM', 'CREATE_UOM'):
                raise HTTPException(status_code=400, detail="Cannot raw-edit master identities. Delete and recreate instead.")
                
            cursor.execute(
                "UPDATE offline_queue SET payload = ?, xml_data = ?, status = 'PENDING', error_message = NULL, updated_at = datetime('now', 'localtime') WHERE id = ?",
                (data.payload, data.xml_data, item_id)
            )
            conn.commit()
            return {"message": "Updated and set to PENDING"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/activity/{item_id}")
def delete_activity(item_id: int):
    try:
        from backend.database import delete_master_queue
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT operation_type, status, payload FROM offline_queue WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Item not found")

            # Masters keep the stricter original rule: a PENDING master reservation
            # is never deletable, since it may be blocking/unblocking other queued
            # items that depend on it.
            if row['operation_type'] in ('CREATE_LEDGER', 'CREATE_ITEM', 'CREATE_UOM'):
                if row['status'] == 'PENDING':
                    raise HTTPException(status_code=400, detail="Cannot delete a master record that is still pending sync to Tally.")
                delete_master_queue(item_id)
                return {"message": "Deleted master queue and reservation successfully"}

        # Ordinary (non-master) rows: shared guards (status, delivery_uncertain,
        # compare-and-delete race guard, purchase-rate cleanup) live in
        # database.py's cancel_pending_queue_item so bank-statement transfer
        # merging (backend/routers/bank_statement.py) can reuse the same logic.
        from backend.database import cancel_pending_queue_item
        success, error_message = cancel_pending_queue_item(item_id)
        if not success:
            status_code = 409 if error_message and ("unconfirmed" in error_message or "just changed" in error_message) else 400
            raise HTTPException(status_code=status_code, detail=error_message)
        return {"message": "Deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/activity/clear")
def clear_finished_activity():
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE offline_queue SET is_hidden = 1 WHERE status IN ('SYNCED', 'FAILED')")
            conn.commit()
            return {"message": "Finished activities cleared successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/activity/{item_id}/retry")
def retry_activity(item_id: int):
    try:
        from backend.database import retry_master
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT operation_type, payload FROM offline_queue WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Item not found")

            if row['operation_type'] in ('CREATE_LEDGER', 'CREATE_ITEM', 'CREATE_UOM'):
                retry_master(item_id)
                return {"message": "Retrying master..."}

            payload_dict = json.loads(row['payload']) if row['payload'] else {}
            if payload_dict.get('delivery_uncertain') is True:
                raise HTTPException(
                    status_code=409,
                    detail="Delivery to Tally is unconfirmed for this transaction. Verify manually in Tally whether it was already recorded before retrying -- retrying now risks creating a duplicate voucher."
                )

            cursor.execute(
                "UPDATE offline_queue SET status = 'PENDING', error_message = NULL, is_hidden = 0, updated_at = datetime('now', 'localtime') WHERE id = ?",
                (item_id,)
            )
            conn.commit()
            return {"message": "Retrying..."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class RebuildPayload(BaseModel):
    payload: dict

@router.post("/activity/{item_id}/rebuild")
def rebuild_activity_xml(item_id: int, data: RebuildPayload):
    """
    Accepts an updated JSON payload dict, regenerates Tally XML from scratch,
    and updates both columns in the queue. Dispatches by payload shape: an
    'items' key means a purchase item invoice, a 'ledger'+'amount' shape means
    a sales entry.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT payload FROM offline_queue WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Item not found")
        stored_payload = json.loads(row['payload']) if row['payload'] else {}
        if stored_payload.get('delivery_uncertain') is True:
            raise HTTPException(
                status_code=409,
                detail="Delivery to Tally is unconfirmed for this transaction. Verify manually in Tally whether it was already recorded before editing -- editing and resending now risks creating a duplicate voucher."
            )

    p = data.payload
    if "items" in p:
        return _rebuild_purchase_item_voucher(item_id, p)
    elif "ledger" in p and "amount" in p:
        return _rebuild_sales_voucher(item_id, p)
    else:
        raise HTTPException(status_code=400, detail="Only purchase item vouchers and sales entries can be rebuilt.")

def _rebuild_purchase_item_voucher(item_id: int, p: dict):
    import datetime
    from xml.sax.saxutils import escape as _escape

    try:
        supplier = _escape(str(p.get("supplier", "")))
        inv_no = _escape(str(p.get("invoice_number", "")))
        tally_date = str(p.get("tally_date", ""))
        cc = _escape(str(p.get("cost_center", "")))
        cgst = float(p.get("cgst", 0))
        sgst = float(p.get("sgst", 0))
        igst = float(p.get("igst", 0))
        rounding_off = float(p.get("rounding_off", 0))

        inventory_xml = ""
        item_subtotal = 0.0

        for item in p.get("items", []):
            name = _escape(str(item.get("mapped_name") or item.get("name", "")))
            qty = float(item.get("qty", 0))
            unit = str(item.get("mapped_unit") or item.get("uom") or "PCS")
            if not unit or unit == "Not Applicable":
                unit = "PCS"
            unit = _escape(unit)
            rate = float(item.get("rate", 0))
            amount = float(item.get("amount", 0))
            discount = float(item.get("discount", 0))
            item_subtotal += amount

            inventory_xml += f"""
        <INVENTORYENTRIES.LIST>
            <STOCKITEMNAME>{name}</STOCKITEMNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <RATE>{rate}/{unit}</RATE>
            <DISCOUNT>{discount}</DISCOUNT>
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

        calculated_grand_total = item_subtotal + cgst + sgst + igst + rounding_off

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

        if cgst > 0:
            ledger_xml += f"""
        <LEDGERENTRIES.LIST>
            <LEDGERNAME>Input CGST</LEDGERNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <AMOUNT>-{cgst:.2f}</AMOUNT>
        </LEDGERENTRIES.LIST>"""

        if sgst > 0:
            ledger_xml += f"""
        <LEDGERENTRIES.LIST>
            <LEDGERNAME>Input SGST</LEDGERNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <AMOUNT>-{sgst:.2f}</AMOUNT>
        </LEDGERENTRIES.LIST>"""

        if igst > 0:
            ledger_xml += f"""
        <LEDGERENTRIES.LIST>
            <LEDGERNAME>Input IGST</LEDGERNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <AMOUNT>-{igst:.2f}</AMOUNT>
        </LEDGERENTRIES.LIST>"""

        if abs(rounding_off) >= 0.01:
            is_debit = "Yes" if rounding_off > 0 else "No"
            amount_str = f"-{abs(rounding_off):.2f}" if rounding_off > 0 else f"{abs(rounding_off):.2f}"
            ledger_xml += f"""
        <LEDGERENTRIES.LIST>
            <LEDGERNAME>Rounding Off</LEDGERNAME>
            <ISDEEMEDPOSITIVE>{is_debit}</ISDEEMEDPOSITIVE>
            <AMOUNT>{amount_str}</AMOUNT>
        </LEDGERENTRIES.LIST>"""

        guid = f"PII-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}-EDIT"

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

        new_payload_str = json.dumps(p)

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE offline_queue SET payload = ?, xml_data = ?, status = 'PENDING', error_message = NULL, updated_at = datetime('now', 'localtime') WHERE id = ? AND status = 'PENDING'",
                (new_payload_str, xml, item_id)
            )
            conn.commit()
            if cursor.rowcount == 0:
                raise HTTPException(status_code=409, detail="Item is not in PENDING state or not found. Cannot edit synced transactions.")

        return {"message": "Rebuilt and saved successfully"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def _rebuild_sales_voucher(item_id: int, p: dict):
    from xml.sax.saxutils import escape as _escape
    from backend.database import resolve_cost_center_for_ledger

    try:
        ledger = _escape(str(p.get("ledger", "")))
        amount = float(p.get("amount", 0))
        tally_date = str(p.get("tally_date", ""))
        narration = _escape(str(p.get("narration", "")))

        cost_center = resolve_cost_center_for_ledger(p.get("ledger", ""))
        allocation = ""
        if cost_center:
            allocation = f"""
              <CATEGORYALLOCATIONS.LIST>
                <CATEGORY>Primary Cost Category</CATEGORY>
                <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
                <COSTCENTREALLOCATIONS.LIST>
                  <NAME>{_escape(cost_center)}</NAME>
                  <AMOUNT>{amount}</AMOUNT>
                </COSTCENTREALLOCATIONS.LIST>
              </CATEGORYALLOCATIONS.LIST>"""

        xml = f"""<ENVELOPE>
  <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
  <BODY><IMPORTDATA>
    <REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC>
    <REQUESTDATA><TALLYMESSAGE>
      <VOUCHER VCHTYPE="Sales" ACTION="Create">
        <DATE>{tally_date}</DATE>
        <NARRATION>{narration}</NARRATION>
        <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{ledger}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          <AMOUNT>-{amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>Sales</LEDGERNAME>
          <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          <AMOUNT>{amount}</AMOUNT>{allocation}
        </ALLLEDGERENTRIES.LIST>
      </VOUCHER>
    </TALLYMESSAGE></REQUESTDATA>
  </IMPORTDATA></BODY>
</ENVELOPE>"""

        new_payload = dict(p)
        if cost_center:
            new_payload['cost_center'] = cost_center
        else:
            new_payload.pop('cost_center', None)
        new_payload_str = json.dumps(new_payload)

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE offline_queue SET payload = ?, xml_data = ?, status = 'PENDING', error_message = NULL, updated_at = datetime('now', 'localtime') WHERE id = ? AND status = 'PENDING'",
                (new_payload_str, xml, item_id)
            )
            conn.commit()
            if cursor.rowcount == 0:
                raise HTTPException(status_code=409, detail="Item is not in PENDING state or not found. Cannot edit synced transactions.")

        return {"message": "Rebuilt and saved successfully"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

