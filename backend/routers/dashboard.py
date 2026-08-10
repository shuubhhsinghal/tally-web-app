import os
import json
import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.database import get_db
from backend.config import TALLY_URL

router = APIRouter()

@router.get("/stats")
def get_dashboard_stats():
    queue_count = 0
    cache_count = 0
    
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
        "tally_online": tally_online
    }

@router.get("/activity")
def get_recent_activity():
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, operation_type, status, description, created_at FROM offline_queue ORDER BY id DESC LIMIT 10")
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        print(f"Error fetching activity: {e}")
        return []

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
            cursor.execute("SELECT operation_type FROM offline_queue WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if row and row['operation_type'] in ('CREATE_LEDGER', 'CREATE_ITEM', 'CREATE_UOM'):
                delete_master_queue(item_id)
                return {"message": "Deleted master queue and reservation successfully"}
            
            cursor.execute("DELETE FROM offline_queue WHERE id = ?", (item_id,))
            conn.commit()
            return {"message": "Deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/activity/{item_id}/retry")
def retry_activity(item_id: int):
    try:
        from backend.database import retry_master
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT operation_type FROM offline_queue WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if row and row['operation_type'] in ('CREATE_LEDGER', 'CREATE_ITEM', 'CREATE_UOM'):
                retry_master(item_id)
                return {"message": "Retrying master..."}
                
            cursor.execute(
                "UPDATE offline_queue SET status = 'PENDING', error_message = NULL, updated_at = datetime('now', 'localtime') WHERE id = ?",
                (item_id,)
            )
            conn.commit()
            return {"message": "Retrying..."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class RebuildPayload(BaseModel):
    payload: dict

@router.post("/activity/{item_id}/rebuild")
def rebuild_activity_xml(item_id: int, data: RebuildPayload):
    """
    Accepts an updated JSON payload dict, regenerates Tally XML from scratch,
    and updates both columns in the queue. Only works for POST_VOUCHER items
    that have a 'items' key (purchase item invoices).
    """
    import datetime
    from xml.sax.saxutils import escape as _escape

    p = data.payload

    # Validate it has the fields we need
    if "items" not in p:
        raise HTTPException(status_code=400, detail="Only purchase item vouchers with 'items' can be rebuilt.")

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

