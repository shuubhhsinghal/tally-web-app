from fastapi import APIRouter
from pydantic import BaseModel
import json
import os
from fastapi import HTTPException
from backend.database import get_all_stock_items, queue_operation
import requests
from backend.services.tally_response import parse_tally_response

from backend.config import TALLY_URL

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

async def get_tally_cost_centers():
    import xml.etree.ElementTree as ET
    import re
    
    xml_payload = """<ENVELOPE>
      <HEADER>
        <TALLYREQUEST>Export Data</TALLYREQUEST>
      </HEADER>
      <BODY>
        <EXPORTDATA>
          <REQUESTDESC>
            <REPORTNAME>List of Accounts</REPORTNAME>
            <STATICVARIABLES>
              <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
              <ACCOUNTTYPE>Cost Centres</ACCOUNTTYPE>
            </STATICVARIABLES>
          </REQUESTDESC>
        </EXPORTDATA>
      </BODY>
    </ENVELOPE>"""
    try:
        response = requests.post(TALLY_URL, data=xml_payload, headers={'Content-Type': 'text/xml'}, timeout=10)
        raw_xml = response.text
        raw_xml = re.sub(r'&#[xX]?[0-9a-fA-F]+;', '', raw_xml)
        raw_xml = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', raw_xml)
        root = ET.fromstring(raw_xml)
        stores = []
        for node in root.findall('.//NAME'):
            name = node.text
            if name and name.lower() not in ["primary", "main location"]:
                stores.append(name)
        return sorted(list(set(stores)))
    except Exception as e:
        print(f"Error fetching cost centers (Offline Mode Active): {e}")
        return ["Mahagun", "Vvip", "Gulshan"]

@router.get("/metadata")
async def get_metadata():
    items = []
    try:
        stock_items = get_all_stock_items()
        items = [i['name'] for i in stock_items]
    except Exception as e:
        print(f"Error loading stock items: {e}")
        
    stores = await get_tally_cost_centers()
        
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

@router.post("/post")
async def post_transfer(payload: PostRequest):
    from fastapi import HTTPException
    from xml.sax.saxutils import escape

    safe_item = escape(str(payload.item_name))
    safe_from = escape(str(payload.from_store))
    safe_to = escape(str(payload.to_store))
    
    amount_str = ""
    amount_str_pos = ""
    if payload.total_amount > 0:
        amount_str = f"<AMOUNT>-{payload.total_amount}</AMOUNT>"
        amount_str_pos = f"<AMOUNT>{payload.total_amount}</AMOUNT>"

    xml_payload = f"""<ENVELOPE>
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

    try:
        response = requests.post(TALLY_URL, data=xml_payload.encode('utf-8'), timeout=10)
        parsed = parse_tally_response(response.text, "POST_VOUCHER")
        if not parsed["is_success"]:
            raise HTTPException(status_code=400, detail=f"Tally rejected the entry: {parsed['error_message']}")
        return {"status": "success", "message": "Successfully posted to Tally"}
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        queue_operation("POST_VOUCHER", xml_payload, payload.model_dump(), f"Stock Transfer: {payload.qty} pcs of {safe_item}")
        return {"status": "queued", "message": "Saved to offline queue."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
