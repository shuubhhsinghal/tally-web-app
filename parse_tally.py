import urllib.request
import xml.etree.ElementTree as ET

TALLY_URL = "http://100.107.220.58:9000"

xml_data = """<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Export Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <EXPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Stock Summary</REPORTNAME>
        <STATICVARIABLES>
          <SVFROMDATE>20260401</SVFROMDATE>
          <SVTODATE>20270331</SVTODATE>
          <EXPLODEFLAG>Yes</EXPLODEFLAG>
          <ISITEMWISE>Yes</ISITEMWISE>
        </STATICVARIABLES>
      </REQUESTDESC>
    </EXPORTDATA>
  </BODY>
</ENVELOPE>"""

req = urllib.request.Request(TALLY_URL, data=xml_data.encode('utf-8'))
with urllib.request.urlopen(req, timeout=30) as response:
    raw_xml = response.read().decode('utf-16', errors='ignore')

# Strip any weird characters before <ENVELOPE>
start_idx = raw_xml.find('<ENVELOPE>')
if start_idx != -1:
    raw_xml = raw_xml[start_idx:]

root = ET.fromstring(raw_xml)

# Find DSPACCINFO elements
for dsp in root.findall('.//DSPACCINFO'):
    name = dsp.findtext('DSPDISPNAME', default='')
    op_val = dsp.findtext('DSPOPVALA', default='')
    cl_val = dsp.findtext('DSPCLVALA', default='')
    
    # Try finding inside DSPSTKINFO
    if not op_val or not cl_val:
        op_val = dsp.findtext('.//DSPOPVALA', default='')
        cl_val = dsp.findtext('.//DSPCLVALA', default='')
        
    if name:
        print(f"Item: {name} | Opening: {op_val} | Closing: {cl_val}")
