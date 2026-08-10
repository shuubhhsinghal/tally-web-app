import requests
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET
from enum import Enum
import os

from backend.database import normalize_master_name, _check_definitions_conflict

TALLY_URL = os.environ.get("TALLY_URL", "http://localhost:9000")

class VerificationResult(Enum):
    COMPATIBLE = "COMPATIBLE"
    CONFLICT = "CONFLICT"
    UNVERIFIABLE = "UNVERIFIABLE"

def verify_tally_master_definition(entity_type: str, requested_name: str, requested_payload: dict):
    """
    Looks up a specific master by name in Tally and verifies if its definition
    is compatible with the requested payload.
    Returns (VerificationResult, tally_dict_if_compatible_else_None)
    """
    norm_req, _ = normalize_master_name(requested_name)
    
    # Map entity type to Tally Subtype
    if entity_type == "LEDGER":
        subtype = "Ledger"
    elif entity_type == "ITEM":
        subtype = "Stock Item"
    elif entity_type == "UOM":
        subtype = "Unit"
    else:
        return VerificationResult.UNVERIFIABLE, None

    # Construct single-object export XML
    xml_request = f"""<ENVELOPE>
      <HEADER>
        <VERSION>1</VERSION>
        <TALLYREQUEST>Export</TALLYREQUEST>
        <TYPE>Object</TYPE>
        <SUBTYPE>{subtype}</SUBTYPE>
        <ID TYPE="Name">{escape(requested_name)}</ID>
      </HEADER>
      <BODY>
        <DESC>
          <STATICVARIABLES>
            <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
          </STATICVARIABLES>
          <FETCHLIST>
            <FETCH>Name</FETCH>
            <FETCH>Parent</FETCH>
            <FETCH>BaseUnits</FETCH>
          </FETCHLIST>
        </DESC>
      </BODY>
    </ENVELOPE>"""
    
    try:
        resp = requests.post(TALLY_URL, data=xml_request.encode('utf-8'), timeout=4)
        resp.raise_for_status()
    except Exception:
        return VerificationResult.UNVERIFIABLE, None
        
    try:
        from backend.services.tally_response import sanitize_tally_xml
        clean_xml = sanitize_tally_xml(resp.text)
        root = ET.fromstring(clean_xml)
    except Exception:
        return VerificationResult.UNVERIFIABLE, None
        
    if entity_type == "LEDGER":
        obj_elem = root.find('.//LEDGER')
    elif entity_type == "ITEM":
        obj_elem = root.find('.//STOCKITEM')
    elif entity_type == "UOM":
        obj_elem = root.find('.//UNIT')
        
    if obj_elem is None:
        return VerificationResult.UNVERIFIABLE, None
        
    # Get actual name
    name_elem = obj_elem.attrib.get('NAME') or (obj_elem.find('NAME.LIST/NAME').text if obj_elem.find('NAME.LIST/NAME') is not None else None)
    if not name_elem:
        # Fallback to direct NAME tag
        direct_name = obj_elem.find('NAME')
        name_elem = direct_name.text if direct_name is not None else None
        
    if not name_elem:
        return VerificationResult.UNVERIFIABLE, None
        
    try:
        norm_actual, _ = normalize_master_name(name_elem)
    except Exception:
        return VerificationResult.UNVERIFIABLE, None
        
    if norm_req != norm_actual:
        return VerificationResult.UNVERIFIABLE, None
        
    # Extract fields
    actual_payload = {}
    if entity_type == "LEDGER":
        parent_elem = obj_elem.find('PARENT')
        if parent_elem is None or not parent_elem.text:
            return VerificationResult.UNVERIFIABLE, None
        actual_payload['parent'] = parent_elem.text
    elif entity_type == "ITEM":
        uom_elem = obj_elem.find('BASEUNITS')
        if uom_elem is None or not uom_elem.text:
            return VerificationResult.UNVERIFIABLE, None
        actual_payload['uom'] = uom_elem.text
            
    # Check definition conflict
    if _check_definitions_conflict(entity_type, requested_payload, actual_payload):
        return VerificationResult.CONFLICT, None
        
    # Compatible! We return the canonical actual payload to be cached
    canonical = {'name': name_elem}
    if entity_type == "LEDGER":
        canonical['parent'] = actual_payload.get('parent')
    elif entity_type == "ITEM":
        canonical['unit'] = actual_payload.get('uom')
        
    return VerificationResult.COMPATIBLE, canonical
