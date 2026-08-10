import os
import json
import asyncio
import requests
import re
import xml.etree.ElementTree as ET

from backend.config import TALLY_URL
TALLY_CACHE_FILE = "tally_ledger_cache.json"
TALLY_STOCK_CACHE_FILE = "tally_stock_cache.json"
TALLY_UOM_CACHE_FILE = "tally_uom_cache.json"

def sanitize_tally_xml(raw_xml: str) -> str:
    clean_xml = re.sub(r'&#0*([0-8]|1[1-2]|1[4-9]|2[0-9]|3[0-1]);?', '', raw_xml)
    clean_xml = re.sub(r'&#x0*([0-8b-ce-f]|1[0-9a-f]);?', '', clean_xml, flags=re.IGNORECASE)
    clean_xml = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', clean_xml)
    return clean_xml

def fetch_and_cache_masters(tally_url=TALLY_URL):
    payload = """<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>LedgerCollection</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="LedgerCollection">
            <TYPE>Ledger</TYPE>
            <NATIVEMETHOD>Name</NATIVEMETHOD>
            <NATIVEMETHOD>Parent</NATIVEMETHOD>
            <NATIVEMETHOD>IsCostCentresOn</NATIVEMETHOD>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""
    try:
        response = requests.post(tally_url, data=payload, headers={'Content-Type': 'text/xml'}, timeout=15)
        response.raise_for_status()
        
        safe_xml_text = sanitize_tally_xml(response.text)
        root = ET.fromstring(safe_xml_text)
        ledger_cache = {}
        
        for ledger_elem in root.findall('.//LEDGER'):
            name = None
            name_elem = ledger_elem.find('NAME')
            if name_elem is not None and name_elem.text:
                name = name_elem.text.strip()
            elif ledger_elem.get('NAME'):
                name = ledger_elem.get('NAME').strip()
                
            if not name:
                continue
                
            parent = None
            parent_elem = ledger_elem.find('PARENT')
            if parent_elem is not None and parent_elem.text:
                parent = parent_elem.text.strip()
            elif ledger_elem.get('PARENT'):
                parent = ledger_elem.get('PARENT').strip()
                
            cc_elem = ledger_elem.find('ISCOSTCENTRESON')
            cost_centre = False
            if cc_elem is not None and cc_elem.text:
                cost_centre = cc_elem.text.strip().lower() == 'yes'
                
            ledger_cache[name.lower()] = {
                "name": name,
                "parent": parent,
                "cost_centre": cost_centre
            }
            
        with open(TALLY_CACHE_FILE, 'w', encoding="utf-8") as f:
            json.dump(ledger_cache, f, indent=4)
            
    except Exception as e:
        print(f"Error fetching masters: {e}")

async def sync_tally_ledgers(tally_url=TALLY_URL):
    try:
        await asyncio.to_thread(fetch_and_cache_masters, tally_url)
        if os.path.exists(TALLY_CACHE_FILE):
            with open(TALLY_CACHE_FILE, 'r', encoding="utf-8") as f:
                return len(json.load(f)), None
        return 0, None
    except Exception as e:
        return 0, str(e)

async def sync_tally_stock_items(tally_url=TALLY_URL):
    payload = """<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>StockItemCollection</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="StockItemCollection">
            <TYPE>Stock Item</TYPE>
            <NATIVEMETHOD>Name</NATIVEMETHOD>
            <NATIVEMETHOD>BaseUnits</NATIVEMETHOD>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""
    try:
        response = await asyncio.to_thread(requests.post, tally_url, data=payload, headers={'Content-Type': 'text/xml'}, timeout=15)
        response.raise_for_status()
        
        safe_xml_text = sanitize_tally_xml(response.text)
        root = ET.fromstring(safe_xml_text)
        stock_cache = {}
        
        for item_elem in root.findall('.//STOCKITEM'):
            name = None
            name_elem = item_elem.find('NAME')
            if name_elem is not None and name_elem.text:
                name = name_elem.text.strip()
            elif item_elem.get('NAME'):
                name = item_elem.get('NAME').strip()
                
            if not name:
                continue
                
            unit = None
            unit_elem = item_elem.find('BASEUNITS')
            if unit_elem is not None and unit_elem.text:
                unit = unit_elem.text.strip()
            elif item_elem.get('BASEUNITS'):
                unit = item_elem.get('BASEUNITS').strip()
                
            stock_cache[name.lower()] = {"name": name, "unit": unit}
            
        with open(TALLY_STOCK_CACHE_FILE, 'w', encoding="utf-8") as f:
            json.dump(stock_cache, f, indent=4)
            
        return len(stock_cache), None
    except Exception as e:
        return 0, str(e)

async def sync_tally_uom(tally_url=TALLY_URL):
    payload = """<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>UnitCollection</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="UnitCollection">
            <TYPE>Unit</TYPE>
            <NATIVEMETHOD>Name</NATIVEMETHOD>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""
    try:
        response = await asyncio.to_thread(requests.post, tally_url, data=payload, headers={'Content-Type': 'text/xml'}, timeout=15)
        response.raise_for_status()
        
        safe_xml_text = sanitize_tally_xml(response.text)
        root = ET.fromstring(safe_xml_text)
        uom_list = []
        
        for item_elem in root.findall('.//UNIT'):
            name = None
            name_elem = item_elem.find('NAME')
            if name_elem is not None and name_elem.text:
                name = name_elem.text.strip()
            elif item_elem.get('NAME'):
                name = item_elem.get('NAME').strip()
                
            if name and name not in uom_list:
                uom_list.append(name)
                
        with open(TALLY_UOM_CACHE_FILE, 'w', encoding="utf-8") as f:
            json.dump(uom_list, f, indent=4)
            
        return len(uom_list), None
    except Exception as e:
        return 0, str(e)

async def perform_master_sync():
    """Silent background sync for Tally Masters"""
    try:
        await sync_tally_ledgers()
        await sync_tally_stock_items()
        await sync_tally_uom()
    except Exception as e:
        print(f"Background master sync failed: {e}")
