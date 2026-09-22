import requests
import xml.etree.ElementTree as ET
import re
from backend.connector.manager import connector_manager
from backend.connector.transport import tally_transport
import logging

logger = logging.getLogger(__name__)

def is_tally_reachable(timeout: int = 3) -> bool:
    """Cheap upfront connectivity check, so a caller doing a best-effort live
    stock check across multiple items can skip the whole thing in one shot
    when Tally isn't actually reachable, instead of paying a separate timeout
    per item. `timeout` is accepted for backward compatibility with existing
    call sites but unused -- this is now a plain in-memory check."""
    return connector_manager.is_tally_reachable()

async def get_godown_stock(item_name: str, godown_name: str) -> dict:
    """
    Queries Tally for the exact closing balance and valuation of a given stock item in a specific Godown.
    Returns: {"qty": float, "rate": float, "amount": float}
    Raises Exception if the item is not found in the godown or has no stock.
    """
    
    xml_data = f"""<ENVELOPE>
      <HEADER>
        <TALLYREQUEST>Export Data</TALLYREQUEST>
      </HEADER>
      <BODY>
        <EXPORTDATA>
          <REQUESTDESC>
            <REPORTNAME>Godown Summary</REPORTNAME>
            <STATICVARIABLES>
              <GODOWNNAME>{godown_name}</GODOWNNAME>
              <EXPLODEFLAG>Yes</EXPLODEFLAG>
              <ISITEMWISE>Yes</ISITEMWISE>
            </STATICVARIABLES>
          </REQUESTDESC>
        </EXPORTDATA>
      </BODY>
    </ENVELOPE>"""
    
    try:
        response = await tally_transport.post(xml_data.encode('utf-8'), timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch Godown Summary from Tally: {e}")
        # Deliberately re-raised as the original requests exception type (not
        # wrapped into a generic Exception) so callers can tell "Tally is
        # unreachable" apart from a genuine data problem (malformed response,
        # item genuinely has no stock) and treat them differently -- e.g.
        # repack.py's stock-sufficiency check is best-effort and skips itself
        # on this specific case rather than blocking the whole operation.
        raise

    # Remove unexpected characters before <ENVELOPE> if any
    raw_xml = response.text
    start_idx = raw_xml.find('<ENVELOPE>')
    if start_idx != -1:
        raw_xml = raw_xml[start_idx:]

    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as e:
        logger.error(f"Failed to parse Godown Summary XML: {e}")
        raise Exception("Received invalid data from Tally.")

    current_item = None
    target_name_normalized = item_name.strip().casefold()
    
    for child in root.iter():
        if child.tag == 'DSPDISPNAME':
            current_item = child.text
        elif child.tag == 'DSPSTKCL' and current_item:
            if current_item.strip().casefold() == target_name_normalized:
                qty_element = child.find('DSPCLQTY')
                rate_element = child.find('DSPCLRATE')
                amt_element = child.find('DSPCLAMTA')
                
                # Parse values
                qty_str = qty_element.text if qty_element is not None else ""
                rate_str = rate_element.text if rate_element is not None else "0"
                amt_str = amt_element.text if amt_element is not None else "0"
                
                # Qty comes back with units like "100 Kgs", extract the number
                qty = 0.0
                if qty_str:
                    match = re.search(r"([\d\.\-]+)", qty_str)
                    if match:
                        qty = float(match.group(1))
                        
                rate = float(rate_str) if rate_str else 0.0
                amt = float(amt_str) if amt_str else 0.0
                
                # Tally amounts might be negative for Debit (Closing Balance) 
                # Take absolute value as we just want the positive valuation amount.
                amt = abs(amt)
                
                return {
                    "qty": qty,
                    "rate": rate,
                    "amount": amt
                }
                
    raise Exception(f"Stock Item '{item_name}' has no stock in Godown '{godown_name}'.")

