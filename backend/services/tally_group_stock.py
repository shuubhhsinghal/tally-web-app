import requests
import xml.etree.ElementTree as ET
from datetime import date
from typing import Dict, Optional, Tuple
from backend.config import TALLY_URL
import re

def fetch_stock_balances_from_tally(from_date: date, to_date: date) -> Dict[str, Dict[str, float]]:
    """
    Fetches the stock balances for the specified period natively from Tally using
    the Group Summary report for Stock-in-Hand.
    
    Returns a dictionary mapping store name (e.g., 'mahagun', 'gulshan', 'vvip')
    to their opening and closing presentation stock values.
    Returns empty dict if Tally is unreachable.
    """
    from_date_str = from_date.strftime("%Y%m%d")
    to_date_str = to_date.strftime("%Y%m%d")
    
    payload = f"""<ENVELOPE>
      <HEADER>
        <TALLYREQUEST>Export Data</TALLYREQUEST>
      </HEADER>
      <BODY>
        <EXPORTDATA>
          <REQUESTDESC>
            <REPORTNAME>Group Summary</REPORTNAME>
            <STATICVARIABLES>
              <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
              <GROUPNAME>Stock-in-Hand</GROUPNAME>
              <EXPLODEALLLEVELS>Yes</EXPLODEALLLEVELS>
              <SVFROMDATE>{from_date_str}</SVFROMDATE>
              <SVTODATE>{to_date_str}</SVTODATE>
              <DSPSHOWOPENING>Yes</DSPSHOWOPENING>
              <DSPSHOWTRANS>Yes</DSPSHOWTRANS>
              <DSPSHOWCLOSING>Yes</DSPSHOWCLOSING>
            </STATICVARIABLES>
          </REQUESTDESC>
        </EXPORTDATA>
      </BODY>
    </ENVELOPE>"""
    
    try:
        response = requests.post(TALLY_URL, data=payload.strip(), headers={'Content-Type': 'text/xml'}, timeout=30)
        
        safe_xml = response.text
        start_idx = safe_xml.find('<ENVELOPE>')
        if start_idx != -1:
            safe_xml = safe_xml[start_idx:]
        safe_xml = re.sub(r'&#[0-9]+;', '', safe_xml)
        
        tree = ET.fromstring(safe_xml)
        
        bals = {}
        current_name = None
        for child in tree.iter():
            if child.tag == 'DSPACCNAME':
                name_node = child.find('DSPDISPNAME')
                if name_node is not None and name_node.text:
                    current_name = name_node.text.strip().lower()
            elif child.tag == 'DSPACCINFO' and current_name:
                if current_name.startswith('stock '):
                    store_key = current_name.replace('stock ', '').strip()
                    op = child.find('.//DSPOPAMT/DSPOPAMTA')
                    dr = child.find('.//DSPDRAMT/DSPDRAMTA')
                    cr = child.find('.//DSPCRAMT/DSPCRAMTA')
                    
                    op_val = float(op.text) if op is not None and op.text else 0.0
                    dr_val = float(dr.text) if dr is not None and dr.text else 0.0
                    cr_val = float(cr.text) if cr is not None and cr.text else 0.0
                    
                    computed_closing = op_val + dr_val + cr_val
                    
                    # Normalize signs: presentation_stock = -signed_tally_amount
                    bals[store_key] = {
                        'opening': -op_val,
                        'closing': -computed_closing
                    }
                current_name = None
        return bals
    except Exception as e:
        print(f"Failed to fetch stock from Tally: {e}")
        return {}

def fetch_stock_balances_from_db(start_month: str, end_month: str) -> Dict[str, Dict[str, float]]:
    """
    Fetches the monthly stock snapshot from SQLite.
    start_month and end_month should be in 'YYYY-MM' format.
    Raises Exception if data is missing or incomplete for either boundary.
    """
    from backend.database import get_db
    bals = {}
    
    required_ledgers = {"stock mahagun", "stock gulshan", "stock vvip"}
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Check completeness for start_month
        cursor.execute("SELECT ledger_name, opening_balance FROM reporting_monthly_stock WHERE year_month = ?", (start_month,))
        start_rows = cursor.fetchall()
        start_data = {r['ledger_name']: r['opening_balance'] for r in start_rows}
        
        if not required_ledgers.issubset(set(start_data.keys())):
            missing = required_ledgers - set(start_data.keys())
            raise Exception(f"Stock data for the requested months is incomplete. Missing ledgers {missing} in {start_month}. Please run Reporting Sync for this period.")
            
        # Check completeness for end_month
        cursor.execute("SELECT ledger_name, closing_balance FROM reporting_monthly_stock WHERE year_month = ?", (end_month,))
        end_rows = cursor.fetchall()
        end_data = {r['ledger_name']: r['closing_balance'] for r in end_rows}
        
        if not required_ledgers.issubset(set(end_data.keys())):
            missing = required_ledgers - set(end_data.keys())
            raise Exception(f"Stock data for the requested months is incomplete. Missing ledgers {missing} in {end_month}. Please run Reporting Sync for this period.")
            
        for ledger in required_ledgers:
            store_key = ledger.replace("stock ", "").strip()
            # Normalize signs just like Tally (if they were stored natively from Tally, they might already be negative)
            # Actually, the sync script stores the raw values from Tally XML (which are negative).
            # We must apply the same normalization: presentation_stock = -signed_tally_amount
            op_val = start_data[ledger]
            cl_val = end_data[ledger]
            
            bals[store_key] = {
                'opening': -op_val,
                'closing': -cl_val
            }
            
    return bals
