import json
import os
import time
import requests
import re
import xml.etree.ElementTree as ET

QUEUE_FILE = "offline_queue.json"
TALLY_CACHE_FILE = "tally_ledger_cache.json"
TALLY_URL = os.environ.get("TALLY_URL", "http://100.90.163.23:9000")

# --- 1. OFFLINE QUEUE LOGIC ---
def add_to_queue(xml_data, user_id, description):
    queue = []
    if os.path.exists(QUEUE_FILE):
        try:
            with open(QUEUE_FILE, "r", encoding="utf-8") as f:
                queue = json.load(f)
        except json.JSONDecodeError:
            pass
            
    # Need to store xml_data as string if it's bytes
    if isinstance(xml_data, bytes):
        xml_data = xml_data.decode("utf-8")
        
    queue.append({"xml": xml_data, "user_id": user_id, "desc": description})
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=4)

def get_queue():
    if os.path.exists(QUEUE_FILE):
        try:
            with open(QUEUE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return []
    return []

def clear_queue():
    if os.path.exists(QUEUE_FILE):
        os.remove(QUEUE_FILE)

# --- 2. MASTER CACHE LOGIC ---
def get_cached_ledgers():
    """Workflows MUST use this to get ledgers instantly without hitting Tally."""
    if os.path.exists(TALLY_CACHE_FILE):
        try:
            with open(TALLY_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}

def sanitize_tally_xml(raw_xml: str) -> str:
    clean_xml = re.sub(r'&#0*([0-8]|1[1-2]|1[4-9]|2[0-9]|3[0-1]);?', '', raw_xml)
    clean_xml = re.sub(r'&#x0*([0-8b-ce-f]|1[0-9a-f]);?', '', clean_xml, flags=re.IGNORECASE)
    clean_xml = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', clean_xml)
    return clean_xml

def fetch_and_cache_masters(tally_url=TALLY_URL):
    """Quietly fetches ledgers from Tally and updates the cloud memory."""
    payload = """<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>LedgerCostCentreStatus</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="LedgerCostCentreStatus">
            <TYPE>Ledger</TYPE>
            <NATIVEMETHOD>Name</NATIVEMETHOD>
            <NATIVEMETHOD>IsCostCentresOn</NATIVEMETHOD>
            <NATIVEMETHOD>Parent</NATIVEMETHOD>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""
    try:
        response = requests.post(tally_url, data=payload, headers={'Content-Type': 'text/xml'}, timeout=10)
        if response.status_code == 200:
            safe_xml_text = sanitize_tally_xml(response.text)
            root = ET.fromstring(safe_xml_text)
            ledger_cache = {}
            
            for ledger_elem in root.findall('.//LEDGER'):
                name_elem = ledger_elem.find('NAME')
                if name_elem is None or not name_elem.text:
                    name_elem = ledger_elem.find('NAME.LIST/NAME')
                    if name_elem is None or not name_elem.text:
                        if ledger_elem.get('NAME'):
                            name = ledger_elem.get('NAME').strip()
                        else:
                            continue
                    else:
                        name = name_elem.text.strip()
                else:
                    name = name_elem.text.strip()
                    
                name = name.lower()
                
                is_cc_on = ledger_elem.find('ISCOSTCENTRESON')
                is_cc_app = ledger_elem.find('ISCOSTCENTRESAPPLICABLE')
                
                cc_required = False
                if is_cc_on is not None and is_cc_on.text and is_cc_on.text.strip().lower() == 'yes':
                    cc_required = True
                elif ledger_elem.get('ISCOSTCENTRESON') and ledger_elem.get('ISCOSTCENTRESON').strip().lower() == 'yes':
                    cc_required = True
                elif is_cc_app is not None and is_cc_app.text and is_cc_app.text.strip().lower() == 'yes':
                    cc_required = True
                elif ledger_elem.get('ISCOSTCENTRESAPPLICABLE') and ledger_elem.get('ISCOSTCENTRESAPPLICABLE').strip().lower() == 'yes':
                    cc_required = True
                    
                parent_name = ""
                parent_elem = ledger_elem.find('PARENT')
                if parent_elem is not None and parent_elem.text:
                    parent_name = parent_elem.text.strip()
                elif ledger_elem.get('PARENT'):
                    parent_name = ledger_elem.get('PARENT').strip()
                    
                ledger_cache[name] = {
                    "cost_centre": cc_required,
                    "parent": parent_name
                }
                    
            if ledger_cache:
                with open(TALLY_CACHE_FILE, 'w', encoding="utf-8") as f:
                    json.dump(ledger_cache, f, indent=4)
    except Exception:
        pass # Fail silently if Tally is offline
