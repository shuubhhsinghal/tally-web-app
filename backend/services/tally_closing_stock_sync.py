import requests
import xml.etree.ElementTree as ET
from backend.config import TALLY_URL
from backend.database import get_db

import re

def sanitize_tally_xml(xml_string):
    start_idx = xml_string.find('<ENVELOPE>')
    if start_idx != -1:
        xml_string = xml_string[start_idx:]
    # Strip invalid XML characters like &#1; &#21; etc.
    return re.sub(r'&#[0-9]+;', '', xml_string)

def sync_tally_closing_balances(tally_url=TALLY_URL):
    payload = """<ENVELOPE>
      <HEADER>
        <TALLYREQUEST>Export Data</TALLYREQUEST>
      </HEADER>
      <BODY>
        <EXPORTDATA>
          <REQUESTDESC>
            <REPORTNAME>List of Accounts</REPORTNAME>
            <STATICVARIABLES>
              <ACCOUNTTYPE>Ledgers</ACCOUNTTYPE>
            </STATICVARIABLES>
          </REQUESTDESC>
        </EXPORTDATA>
      </BODY>
    </ENVELOPE>"""

    response = requests.post(tally_url, data=payload.encode('utf-8'), timeout=30)
    response.raise_for_status()

    safe_xml = sanitize_tally_xml(response.text)
    root = ET.fromstring(safe_xml)
    
    ledgers = root.findall('.//LEDGER')
    
    with get_db() as db:
        for ledger in ledgers:
            name_node = ledger.find('NAME')
            if name_node is None or not name_node.text:
                continue
            name = name_node.text
            
            # We only care about stock ledgers, but we can sync all just in case
            closing_lists = ledger.findall('LEDGERCLOSINGVALUES.LIST')
            for cl in closing_lists:
                date_node = cl.find('DATE')
                amount_node = cl.find('AMOUNT')
                
                if date_node is not None and date_node.text and amount_node is not None and amount_node.text:
                    date = date_node.text
                    # Convert to float. Keep sign (usually negative for Debit in closing balance, or negative for Credit? Tally amounts are float)
                    try:
                        amount = float(amount_node.text)
                    except ValueError:
                        amount = 0.0
                    
                    db.execute('''
                        INSERT INTO reporting_ledger_closing_balances (ledger_name, date, amount)
                        VALUES (?, ?, ?)
                        ON CONFLICT(ledger_name, date) DO UPDATE SET amount = excluded.amount
                    ''', (name, date, amount))
        
        db.commit()

if __name__ == '__main__':
    sync_tally_closing_balances()
    print("Closing balances synced successfully.")
