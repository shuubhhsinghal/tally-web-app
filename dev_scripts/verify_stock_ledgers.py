import urllib.request
TALLY_URL = "http://100.107.220.58:9000"

for ledger in ["stock mahagun", "stock gulshan", "stock vvip"]:
    xml_data = f"""<ENVELOPE>
      <HEADER>
        <TALLYREQUEST>Export Data</TALLYREQUEST>
      </HEADER>
      <BODY>
        <EXPORTDATA>
          <REQUESTDESC>
            <REPORTNAME>List of Accounts</REPORTNAME>
            <STATICVARIABLES>
              <ACCOUNTTYPE>Ledgers</ACCOUNTTYPE>
              <SVFROMDATE>20260401</SVFROMDATE>
              <SVTODATE>20270331</SVTODATE>
            </STATICVARIABLES>
          </REQUESTDESC>
        </EXPORTDATA>
      </BODY>
    </ENVELOPE>"""
    try:
        req = urllib.request.Request(TALLY_URL, data=xml_data.encode('utf-8'))
        with urllib.request.urlopen(req, timeout=10) as response:
            raw_xml = response.read().decode('utf-8', errors='ignore')
            lines = raw_xml.split('\n')
            
            # Find the section for this ledger
            in_ledger = False
            for line in lines:
                if f"<NAME>{ledger}</NAME>" in line:
                    in_ledger = True
                    print(f"--- Found {ledger} ---")
                elif in_ledger and "</LEDGER>" in line:
                    in_ledger = False
                elif in_ledger and "LEDGERCLOSINGVALUES.LIST" in line:
                    print(line)
    except Exception as e:
        print(f"Error querying {ledger}:", e)
