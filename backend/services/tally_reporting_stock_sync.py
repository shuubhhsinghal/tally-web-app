import asyncio
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
import calendar
import logging
from backend.database import get_db
from backend.config import TALLY_URL
from backend.services.tally_sync_service import sanitize_tally_xml

logger = logging.getLogger(__name__)

def _get_months_in_range(start_date_str: str, end_date_str: str):
    start_dt = datetime.strptime(start_date_str, "%Y%m%d")
    end_dt = datetime.strptime(end_date_str, "%Y%m%d")
    
    current_dt = start_dt.replace(day=1)
    months = []
    
    while current_dt <= end_dt:
        year = current_dt.year
        month = current_dt.month
        
        last_day = calendar.monthrange(year, month)[1]
        
        first_day_str = f"{year}{month:02d}01"
        last_day_str = f"{year}{month:02d}{last_day:02d}"
        ym_str = f"{year}-{month:02d}"
        
        months.append((ym_str, first_day_str, last_day_str))
        
        if month == 12:
            current_dt = current_dt.replace(year=year+1, month=1, day=1)
        else:
            current_dt = current_dt.replace(month=month+1, day=1)
            
    return months

def _safe_float(val: str) -> float:
    if not val: return 0.0
    val = val.strip()
    if not val: return 0.0
    try:
        return float(val)
    except ValueError:
        return 0.0

def fetch_stock_for_month_sync(ym_str: str, first_day: str, last_day: str, tally_url: str):
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
          <SVFROMDATE>{first_day}</SVFROMDATE>
          <SVTODATE>{last_day}</SVTODATE>
          <DSPSHOWOPENING>Yes</DSPSHOWOPENING>
          <DSPSHOWTRANS>Yes</DSPSHOWTRANS>
          <DSPSHOWCLOSING>Yes</DSPSHOWCLOSING>
        </STATICVARIABLES>
      </REQUESTDESC>
    </EXPORTDATA>
  </BODY>
</ENVELOPE>"""

    try:
        response = requests.post(tally_url, data=payload.strip(), headers={'Content-Type': 'text/xml'}, timeout=30)
        response.raise_for_status()
        xml_text = response.text
        
        safe_xml_text = sanitize_tally_xml(xml_text)
        root = ET.fromstring(safe_xml_text)
        
        parsed_data = {}
        for i in range(len(root)):
            elem = root[i]
            if elem.tag == 'DSPACCNAME':
                name_elem = elem.find('DSPDISPNAME')
                if name_elem is not None and name_elem.text:
                    lname = name_elem.text.strip().lower()
                    if i + 1 < len(root) and root[i+1].tag == 'DSPACCINFO':
                        info_elem = root[i+1]
                        op = _safe_float(info_elem.findtext('DSPOPAMT/DSPOPAMTA', default='0'))
                        dr = _safe_float(info_elem.findtext('DSPDRAMT/DSPDRAMTA', default='0'))
                        cr = _safe_float(info_elem.findtext('DSPCRAMT/DSPCRAMTA', default='0'))
                        cl = op + dr + cr
                        
                        parsed_data[lname] = {
                            "opening_balance": op,
                            "debit_movement": dr,
                            "credit_movement": cr,
                            "closing_balance": cl
                        }
        
        required_ledgers = {"stock mahagun", "stock gulshan", "stock vvip"}
        found_ledgers = set(parsed_data.keys())
        
        if not required_ledgers.issubset(found_ledgers):
            missing = required_ledgers - found_ledgers
            return {"status": "error", "ym": ym_str, "error": f"Missing required ledgers: {missing}"}
            
        return {"status": "success", "ym": ym_str, "data": parsed_data}
        
    except Exception as e:
        return {"status": "error", "ym": ym_str, "error": str(e)}

async def async_sync_monthly_stock(start_date: str, end_date: str, tally_url: str):
    months = _get_months_in_range(start_date, end_date)
    semaphore = asyncio.Semaphore(5)
    
    async def bound_fetch(month_tuple):
        ym_str, first_day, last_day = month_tuple
        async with semaphore:
            return await asyncio.to_thread(fetch_stock_for_month_sync, ym_str, first_day, last_day, tally_url)
                
    tasks = [bound_fetch(m) for m in months]
    batch_results = await asyncio.gather(*tasks)
    
    failed_months = []
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        try:
            for res in batch_results:
                ym_str = res['ym']
                if res['status'] == 'error':
                    failed_months.append(f"{ym_str}: {res['error']}")
                    continue
                    
                data = res['data']
                required_ledgers = ["stock mahagun", "stock gulshan", "stock vvip"]
                
                for ledger_name in required_ledgers:
                    vals = data[ledger_name]
                    cursor.execute("""
                        INSERT INTO reporting_monthly_stock 
                        (year_month, ledger_name, opening_balance, debit_movement, credit_movement, closing_balance)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(year_month, ledger_name) DO UPDATE SET
                        opening_balance = excluded.opening_balance,
                        debit_movement = excluded.debit_movement,
                        credit_movement = excluded.credit_movement,
                        closing_balance = excluded.closing_balance,
                        synced_at = CURRENT_TIMESTAMP
                    """, (ym_str, ledger_name, vals['opening_balance'], vals['debit_movement'], vals['credit_movement'], vals['closing_balance']))
            
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
            
    return failed_months
