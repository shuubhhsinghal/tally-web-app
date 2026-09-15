import requests
import xml.etree.ElementTree as ET

TALLY_URL = "http://100.125.198.3:9000"

def get_godown_stock(item_name: str, godown_name: str):
    # Fetch Stock Summary for a specific Godown
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
    
    response = requests.post(TALLY_URL, data=xml_data.encode('utf-8'), timeout=10)
    root = ET.fromstring(response.text)
    
    # Simple parse to find the item
    current_item = None
    for child in root.iter():
        if child.tag == 'DSPDISPNAME':
            current_item = child.text
        elif child.tag == 'DSPSTKCL' and current_item == item_name:
            qty = child.find('DSPCLQTY')
            amt = child.find('DSPCLAMTA')
            rate = child.find('DSPCLRATE')
            print(f"Found {item_name} in {godown_name}:")
            print(f"Qty: {qty.text if qty is not None else '0'}")
            print(f"Rate: {rate.text if rate is not None else '0'}")
            print(f"Amount: {amt.text if amt is not None else '0'}")
            return
            
    print(f"Item {item_name} not found in godown {godown_name}")

get_godown_stock("Beetroot Chips Bulk", "Mahagun")
