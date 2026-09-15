import asyncio
import json
import os
import time
import requests
import xml.etree.ElementTree as ET
import re
import re
from datetime import datetime, timedelta
from backend.services.tally_response import sanitize_tally_xml, parse_tally_response

from backend.database import (
    get_pending_queue, update_queue_status,
    clear_and_bulk_insert_ledgers, clear_and_bulk_insert_stock_items, clear_and_bulk_insert_uoms, clear_and_bulk_insert_godowns
)

from backend.config import TALLY_URL



def fetch_and_cache_masters():
    # 1. Ledgers
    from datetime import datetime
    dt = datetime.now()
    if dt.month >= 4:
        fy_start = f"{dt.year}0401"
        fy_end = f"{dt.year + 1}0331"
    else:
        fy_start = f"{dt.year - 1}0401"
        fy_end = f"{dt.year}0331"

    ledger_payload = f"""<ENVELOPE>
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
                <NATIVEMETHOD>OpeningBalance</NATIVEMETHOD>
              </COLLECTION>
            </TDLMESSAGE>
          </TDL>
        </DESC>
      </BODY>
    </ENVELOPE>"""
    try:
        response = requests.post(TALLY_URL, data=ledger_payload, headers={'Content-Type': 'text/xml'}, timeout=15)
        response.raise_for_status()
        root = ET.fromstring(sanitize_tally_xml(response.text))
        ledgers = []
        for ledger_elem in root.findall('.//LEDGER'):
            name = (ledger_elem.find('NAME').text if ledger_elem.find('NAME') is not None else ledger_elem.get('NAME', '')).strip()
            if not name: continue
            parent = (ledger_elem.find('PARENT').text if ledger_elem.find('PARENT') is not None else ledger_elem.get('PARENT', '')).strip()
            cc_elem = ledger_elem.find('ISCOSTCENTRESON')
            cost_centre = cc_elem is not None and cc_elem.text and cc_elem.text.strip().lower() == 'yes'
            
            ob_elem = ledger_elem.find('OPENINGBALANCE')
            ob_val = None
            if ob_elem is not None and ob_elem.text:
                try:
                    ob_val = float(ob_elem.text.strip())
                except ValueError:
                    print(f"Warning: Invalid OPENINGBALANCE '{ob_elem.text}' for ledger '{name}'")
                    
            ledgers.append({"name": name, "parent": parent, "cost_centre": cost_centre, "opening_balance": ob_val})
        clear_and_bulk_insert_ledgers(ledgers)
    except Exception as e:
        print(f"Error syncing ledgers: {e}")

    # 1.5. Stock Ledger Closing Balances
    try:
        closing_payload = """<ENVELOPE>
          <HEADER><TALLYREQUEST>Export Data</TALLYREQUEST></HEADER>
          <BODY>
            <EXPORTDATA>
              <REQUESTDESC>
                <REPORTNAME>List of Accounts</REPORTNAME>
                <STATICVARIABLES><ACCOUNTTYPE>Ledgers</ACCOUNTTYPE></STATICVARIABLES>
              </REQUESTDESC>
            </EXPORTDATA>
          </BODY>
        </ENVELOPE>"""
        closing_resp = requests.post(TALLY_URL, data=closing_payload.encode('utf-8'), timeout=45)
        closing_resp.raise_for_status()
        c_root = ET.fromstring(sanitize_tally_xml(closing_resp.text))
        
        closing_data = []
        for ledger_elem in c_root.findall('.//LEDGER'):
            name_node = ledger_elem.find('NAME')
            if name_node is None or not name_node.text:
                continue
            lname = name_node.text.strip()
            
            # We filter for only the stock ledgers to save processing time
            # though the table can safely hold any ledger's closing balance.
            if not lname.lower().startswith('stock'):
                continue

            for cl in ledger_elem.findall('LEDGERCLOSINGVALUES.LIST'):
                date_node = cl.find('DATE')
                amount_node = cl.find('AMOUNT')
                if date_node is not None and date_node.text and amount_node is not None and amount_node.text:
                    try:
                        amt = float(amount_node.text.strip())
                        closing_data.append((lname, date_node.text.strip(), amt))
                    except ValueError:
                        pass
        
        if closing_data:
            from backend.database import get_db
            with get_db() as db:
                db.executemany('''
                    INSERT INTO reporting_ledger_closing_balances (ledger_name, date, amount)
                    VALUES (?, ?, ?)
                    ON CONFLICT(ledger_name, date) DO UPDATE SET amount = excluded.amount
                ''', closing_data)
                db.commit()
    except Exception as e:
        print(f"Error syncing closing balances: {e}")

    # 1.6 Godowns
    godown_payload = """<ENVELOPE>
      <HEADER><TALLYREQUEST>Export Data</TALLYREQUEST></HEADER>
      <BODY>
        <EXPORTDATA>
          <REQUESTDESC>
            <REPORTNAME>List of Accounts</REPORTNAME>
            <STATICVARIABLES><ACCOUNTTYPE>Godowns</ACCOUNTTYPE></STATICVARIABLES>
          </REQUESTDESC>
        </EXPORTDATA>
      </BODY>
    </ENVELOPE>"""
    try:
        response = requests.post(TALLY_URL, data=godown_payload.encode('utf-8'), timeout=15)
        response.raise_for_status()
        root = ET.fromstring(sanitize_tally_xml(response.text))
        godowns = []
        for g_elem in root.findall('.//GODOWN'):
            name = (g_elem.find('NAME').text if g_elem.find('NAME') is not None else g_elem.get('NAME', '')).strip()
            if not name: continue
            parent = (g_elem.find('PARENT').text if g_elem.find('PARENT') is not None else g_elem.get('PARENT', '')).strip()
            godowns.append({"name": name, "parent": parent})
        clear_and_bulk_insert_godowns(godowns)
    except Exception as e:
        print(f"Error syncing godowns: {e}")

    # 2. Stock Items
    item_payload = """<ENVELOPE>
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
        response = requests.post(TALLY_URL, data=item_payload, headers={'Content-Type': 'text/xml'}, timeout=15)
        response.raise_for_status()
        root = ET.fromstring(sanitize_tally_xml(response.text))
        items = []
        for item_elem in root.findall('.//STOCKITEM'):
            name = (item_elem.find('NAME').text if item_elem.find('NAME') is not None else item_elem.get('NAME', '')).strip()
            if not name: continue
            unit = (item_elem.find('BASEUNITS').text if item_elem.find('BASEUNITS') is not None else item_elem.get('BASEUNITS', '')).strip()
            items.append({"name": name, "unit": unit, "last_purchase_rate": 0.0, "last_purchase_date": "0000-00-00"})
            
        # --- NEW LOGIC: Fetch Purchase Vouchers for Last Purchase Rate ---
        # 1 year dynamic date range
        today = datetime.today()
        one_year_ago = today - timedelta(days=365)
        
        sv_from_date = one_year_ago.strftime("%Y%m%d")
        sv_to_date = today.strftime("%Y%m%d")
        
        voucher_payload = f"""<ENVELOPE>
          <HEADER>
            <VERSION>1</VERSION>
            <TALLYREQUEST>Export</TALLYREQUEST>
            <TYPE>Collection</TYPE>
            <ID>PurchaseVouchers</ID>
          </HEADER>
          <BODY>
            <DESC>
              <STATICVARIABLES>
                <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                <SVFROMDATE>{sv_from_date}</SVFROMDATE>
                <SVTODATE>{sv_to_date}</SVTODATE>
              </STATICVARIABLES>
              <TDL>
                <TDLMESSAGE>
                  <COLLECTION NAME="PurchaseVouchers">
                    <TYPE>Voucher</TYPE>
                    <FETCH>Date, InventoryEntries.*</FETCH>
                    <FILTER>IsPurchase</FILTER>
                  </COLLECTION>
                  <SYSTEM TYPE="Formulae" NAME="IsPurchase">$$IsPurchase:$VoucherTypeName</SYSTEM>
                </TDLMESSAGE>
              </TDL>
            </DESC>
          </BODY>
        </ENVELOPE>"""
        
        item_latest_rates = {}
        try:
            v_response = requests.post(TALLY_URL, data=voucher_payload, headers={'Content-Type': 'text/xml'}, timeout=15)
            if v_response.status_code == 200:
                v_root = ET.fromstring(sanitize_tally_xml(v_response.text))
                for vch in v_root.findall('.//VOUCHER'):
                    date_elem = vch.find('DATE')
                    vch_date = date_elem.text if date_elem is not None else ""
                    
                    # Tally date format is typically YYYYMMDD, convert to YYYY-MM-DD for standard DB comparison
                    if len(vch_date) == 8:
                        vch_date = f"{vch_date[:4]}-{vch_date[4:6]}-{vch_date[6:]}"
                        
                    # Tally might return INVENTORYENTRIES.LIST or ALLINVENTORYENTRIES.LIST depending on voucher configuration
                    entries = vch.findall('.//INVENTORYENTRIES.LIST') + vch.findall('.//ALLINVENTORYENTRIES.LIST')
                    for inv in entries:
                        item_name_elem = inv.find('STOCKITEMNAME')
                        rate_elem = inv.find('RATE')
                        
                        if item_name_elem is not None and rate_elem is not None:
                            i_name = item_name_elem.text.strip().lower() if item_name_elem.text else ""
                            i_rate_str = rate_elem.text.strip() if rate_elem.text else ""
                            
                            if i_name and i_rate_str:
                                # Rate often comes like "50.00/kg", so extract numbers
                                match = re.search(r"[-+]?\d*\.\d+|\d+", i_rate_str)
                                if match:
                                    rate_val = float(match.group())
                                    
                                    # If this item already has a rate, keep the one with the newest date
                                    if i_name not in item_latest_rates or vch_date > item_latest_rates[i_name]['date']:
                                        item_latest_rates[i_name] = {
                                            'rate': rate_val,
                                            'date': vch_date
                                        }
        except Exception as ve:
            print(f"Error fetching vouchers for rates: {ve}")
            
        # Merge latest rates into items list
        for item in items:
            name_lower = item['name'].lower()
            if name_lower in item_latest_rates:
                item['last_purchase_rate'] = item_latest_rates[name_lower]['rate']
                item['last_purchase_date'] = item_latest_rates[name_lower]['date']

        clear_and_bulk_insert_stock_items(items)
    except Exception as e:
        print(f"Error syncing stock items: {e}")

    # 3. UOMs
    uom_payload = """<ENVELOPE>
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
        response = requests.post(TALLY_URL, data=uom_payload, headers={'Content-Type': 'text/xml'}, timeout=15)
        response.raise_for_status()
        root = ET.fromstring(sanitize_tally_xml(response.text))
        uoms = []
        for uom_elem in root.findall('.//UNIT'):
            name = (uom_elem.find('NAME').text if uom_elem.find('NAME') is not None else uom_elem.get('NAME', '')).strip()
            if name and name not in uoms:
                uoms.append(name)
        clear_and_bulk_insert_uoms(uoms)
    except Exception as e:
        print(f"Error syncing UOMs: {e}")

    # 4. Master Refresh Reconciliation
    try:
        from backend.database import get_db, check_master_exists_locally, MasterConflictException, resolve_master_externally, mark_master_failed
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM pending_masters")
            pending_masters = cursor.fetchall()
            
            for pm in pending_masters:
                cursor.execute("SELECT payload FROM offline_queue WHERE id = ?", (pm['queue_id'],))
                q_row = cursor.fetchone()
                payload_dict = json.loads(q_row['payload']) if q_row and q_row['payload'] else {}
                try:
                    if check_master_exists_locally(pm['entity_type'], pm['normalized_name'], payload_dict):
                        resolve_master_externally(pm['queue_id'], "Resolved externally by Master Refresh")
                        print(f"Pending master {pm['entity_type']} '{pm['original_name']}' resolved externally by Master Refresh.")
                except MasterConflictException as e:
                    if pm['status'] != 'FAILED':
                        mark_master_failed(pm['queue_id'], str(e))
                        print(f"Pending master {pm['entity_type']} '{pm['original_name']}' marked FAILED due to external conflict: {e}")
    except Exception as e:
        print(f"Error during master refresh reconciliation: {e}")

def flush_offline_queue():
    queue = get_pending_queue()
    if not queue:
        return 0, 0
    
    print(f"Tally is online. Processing {len(queue)} offline queue items...")
    processed = 0
    failed = 0
    for item in queue:
        if item.get("operation_type") == "POST_VOUCHER":
            item_payload = {}
            if item.get("payload"):
                try:
                    item_payload = json.loads(item["payload"])
                except json.JSONDecodeError:
                    pass
            if item_payload.get("delivery_uncertain") is True:
                error_msg = "Delivery status is unknown from an earlier Tally submission. Verify this voucher in Tally before attempting any manual retry."
                update_queue_status(item["id"], "FAILED", error_msg)
                print(f"Queue item {item['id']} skipped and marked FAILED: {error_msg}")
                continue

            try:
                root = ET.fromstring(sanitize_tally_xml(item["xml_data"]))
                voucher_count = len(root.findall('.//VOUCHER'))
                if voucher_count > 1:
                    error_msg = "Legacy multi-voucher queue entry requires review; automatic retry is disabled to prevent partial-import duplication."
                    update_queue_status(item["id"], "FAILED", error_msg)
                    print(f"Queue item {item['id']} skipped and marked FAILED: {error_msg}")
                    continue
            except ET.ParseError:
                error_msg = "Malformed request XML. Cannot safely transmit to Tally."
                update_queue_status(item["id"], "FAILED", error_msg)
                print(f"Queue item {item['id']} skipped and marked FAILED: {error_msg}")
                continue

        if item.get("operation_type") in ("CREATE_LEDGER", "CREATE_ITEM", "CREATE_UOM"):
            # Pre-send guard
            from backend.database import is_master_confirmed_locally, normalize_master_name, MasterConflictException, resolve_master_externally, mark_master_failed
            try:
                payload_dict = json.loads(item["payload"]) if item.get("payload") else {}
                name = payload_dict.get("name") or payload_dict.get("uom") or ""
                norm_name, _ = normalize_master_name(name)
                if is_master_confirmed_locally(item["operation_type"].replace("CREATE_", ""), norm_name, payload_dict):
                    resolve_master_externally(item["id"], "Resolved externally by worker pre-send guard")
                    print(f"Queue item {item['id']} skipped and marked SYNCED: Master already exists with compatible definition.")
                    if item.get("operation_type") == "CREATE_ITEM" and "name" in payload_dict:
                        from backend.database import update_product_conversion_status
                        update_product_conversion_status(payload_dict["name"], "ACTIVE")
                    processed += 1
                    continue
            except MasterConflictException as e:
                mark_master_failed(item["id"], str(e))
                print(f"Queue item {item['id']} skipped and marked FAILED: {e}")
                failed += 1
                continue
            except Exception as e:
                print(f"Pre-send guard error on item {item['id']}: {e}")

        try:
            resp = requests.post(TALLY_URL, data=item["xml_data"], headers={'Content-Type': 'text/xml'}, timeout=10)
            
            parsed_resp = parse_tally_response(resp.text, item.get("operation_type", ""))
            
            if parsed_resp["is_success"]:
                if item.get("operation_type") in ("CREATE_LEDGER", "CREATE_ITEM", "CREATE_UOM"):
                    from backend.database import mark_master_synced, update_product_conversion_status
                    mark_master_synced(item["id"])
                    if item.get("operation_type") == "CREATE_ITEM":
                        payload_dict = json.loads(item["payload"]) if item.get("payload") else {}
                        if "name" in payload_dict:
                            update_product_conversion_status(payload_dict["name"], "ACTIVE")
                elif item.get("operation_type") == "REPACK_VOUCHER":
                    update_queue_status(item["id"], "SYNCED")
                    payload_dict = json.loads(item["payload"]) if item.get("payload") else {}
                    repack_id = payload_dict.get("repack_id")
                    if repack_id:
                        from backend.database import get_db
                        with get_db() as conn:
                            cursor = conn.cursor()
                            cursor.execute("UPDATE repack_operations SET status = 'COMPLETED' WHERE id = ?", (repack_id,))
                            conn.commit()
                else:
                    update_queue_status(item["id"], "SYNCED")
                print(f"Successfully synced queue item {item['id']}")
                processed += 1
            else:
                error_msg = parsed_resp["error_message"] or resp.text[:200]
                if item.get("operation_type") in ("CREATE_LEDGER", "CREATE_ITEM", "CREATE_UOM"):
                    from backend.database import mark_master_failed, update_product_conversion_status
                    mark_master_failed(item["id"], error_msg)
                    if item.get("operation_type") == "CREATE_ITEM":
                        payload_dict = json.loads(item["payload"]) if item.get("payload") else {}
                        if "name" in payload_dict:
                            update_product_conversion_status(payload_dict["name"], "FAILED")
                elif item.get("operation_type") == "REPACK_VOUCHER":
                    update_queue_status(item["id"], "FAILED", error_msg)
                    payload_dict = json.loads(item["payload"]) if item.get("payload") else {}
                    repack_id = payload_dict.get("repack_id")
                    if repack_id:
                        from backend.database import get_db
                        with get_db() as conn:
                            cursor = conn.cursor()
                            cursor.execute("UPDATE repack_operations SET status = 'FAILED' WHERE id = ?", (repack_id,))
                            conn.commit()
                else:
                    update_queue_status(item["id"], "FAILED", error_msg)
                print(f"Queue item {item['id']} failed validation: {error_msg}")
                failed += 1
        except requests.exceptions.ConnectionError:
            print("Tally went offline during queue processing. Stopping flush.")
            break
        except Exception as e:
            print(f"Error processing queue item {item['id']}: {e}")
            update_queue_status(item["id"], "FAILED", str(e))
            failed += 1
            
    return processed, failed


async def sync_worker_loop():
    print("Starting Tally Sync Background Worker...")
    last_master_sync = 0
    
    while True:
        try:
            # 1. Micro-Ping
            try:
                requests.get(TALLY_URL, timeout=3)
                tally_online = True
            except Exception:
                tally_online = False

            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Pinging Tally... Online: {tally_online}")

            if tally_online:
                # 2. Flush Offline Queue
                flush_offline_queue()
                            
                # 3. Master Sync (every 5 minutes)
                if time.time() - last_master_sync > 300:
                    print("Performing 5-minute master sync from Tally...")
                    await asyncio.to_thread(fetch_and_cache_masters)
                    last_master_sync = time.time()
                    print("Master sync complete.")
                    
        except Exception as e:
            print(f"Error in sync worker loop: {e}")
            
        await asyncio.sleep(60)
