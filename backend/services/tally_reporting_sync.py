import asyncio
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
import re

from backend.config import TALLY_URL
from backend.services.tally_sync_service import sanitize_tally_xml
from backend.database import get_db

def _get_current_fy_start() -> str:
    now = datetime.now()
    if now.month >= 4:
        return f"{now.year}0401"
    else:
        return f"{now.year - 1}0401"

def _get_current_fy_end() -> str:
    now = datetime.now()
    if now.month >= 4:
        return f"{now.year + 1}0331"
    else:
        return f"{now.year}0331"

def _get_default_sync_end_date() -> str:
    """Default end date for an automatic/no-args voucher sync: the earlier of
    today and the fiscal year end, so we never request months that haven't
    happened yet (which always fail Tally's stock-summary fetch)."""
    today_str = datetime.now().strftime("%Y%m%d")
    fy_end_str = _get_current_fy_end()
    return min(today_str, fy_end_str)

def fetch_and_store_cost_centres(tally_url=TALLY_URL):
    payload = """<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>CostCentreCollection</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="CostCentreCollection">
            <TYPE>Cost Centre</TYPE>
            <NATIVEMETHOD>Name</NATIVEMETHOD>
            <NATIVEMETHOD>Parent</NATIVEMETHOD>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""
    try:
        response = requests.post(tally_url, data=payload, headers={'Content-Type': 'text/xml'}, timeout=30)
        response.raise_for_status()
        
        safe_xml_text = sanitize_tally_xml(response.text)
        root = ET.fromstring(safe_xml_text)
        
        cost_centres = []
        for cc_elem in root.findall('.//COSTCENTRE'):
            name_elem = cc_elem.find('NAME')
            name = name_elem.text.strip() if name_elem is not None and name_elem.text else cc_elem.get('NAME', '').strip()
            if not name:
                continue
                
            parent_elem = cc_elem.find('PARENT')
            parent = parent_elem.text.strip() if parent_elem is not None and parent_elem.text else cc_elem.get('PARENT', '').strip()
            
            cost_centres.append((name, parent))
            
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                for name, parent in cost_centres:
                    cursor.execute("""
                        INSERT INTO cost_centres (name, parent)
                        VALUES (?, ?)
                        ON CONFLICT(name) DO UPDATE SET parent=excluded.parent
                    """, (name, parent))
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
                
        return len(cost_centres)
    except Exception as e:
        print(f"Error fetching cost centres: {e}")
        raise e

def _parse_amount(amt_str: str) -> float:
    if not amt_str:
        return 0.0
    try:
        return float(amt_str)
    except ValueError:
        return 0.0

def fetch_and_store_vouchers(start_date: str, end_date: str, tally_url=TALLY_URL):
    payload = f"""<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>VoucherCollection</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        <SVFROMDATE>{start_date}</SVFROMDATE>
        <SVTODATE>{end_date}</SVTODATE>
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="VoucherCollection">
            <TYPE>Voucher</TYPE>
            <FETCH>GUID, Date, VoucherTypeName, VoucherNumber, PartyLedgerName, Narration, Reference, ReferenceDate, EffectiveDate, IsOptional, IsCancelled</FETCH>
            <FETCH>AllLedgerEntries.*, AllInventoryEntries.*, AllLedgerEntries.BankAllocations.*</FETCH>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""
    try:
        response = requests.post(tally_url, data=payload, headers={'Content-Type': 'text/xml'}, timeout=120)
        response.raise_for_status()
        
        safe_xml_text = sanitize_tally_xml(response.text)
        root = ET.fromstring(safe_xml_text)
        
        vouchers_count = 0
        valid_tally_guids = set()
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                for vch_elem in root.findall('.//VOUCHER'):
                    guid = vch_elem.findtext('GUID', default='').strip()
                    if not guid:
                        guid = vch_elem.get('VCHKEY', '').strip()
                        if not guid:
                            continue
                            
                    # 1. State Filtering (Optional/Cancelled)
                    is_optional = vch_elem.findtext('ISOPTIONAL', default='').strip().lower() == 'yes'
                    is_cancelled = vch_elem.findtext('ISCANCELLED', default='').strip().lower() == 'yes'
                    
                    if is_optional or is_cancelled:
                        # Skip processing this voucher completely.
                        # Since it's not added to valid_tally_guids, it will be deleted by ghost cleanup.
                        continue
                        
                    valid_tally_guids.add(guid)
                            
                    date_val = vch_elem.findtext('DATE', default='').strip()
                    vch_type = vch_elem.findtext('VOUCHERTYPENAME', default='').strip()
                    vch_num = vch_elem.findtext('VOUCHERNUMBER', default='').strip()
                    party_ledger = vch_elem.findtext('PARTYLEDGERNAME', default='').strip()
                    narration = vch_elem.findtext('NARRATION', default='').strip()
                    ref = vch_elem.findtext('REFERENCE', default='').strip()
                    ref_date = vch_elem.findtext('REFERENCEDATE', default='').strip()
                    eff_date = vch_elem.findtext('EFFECTIVEDATE', default='').strip()
                    
                    cheque_num = ""
                    cheque_date = ""
                    bank_name = ""
                    for ledg_elem in vch_elem.findall('.//ALLLEDGERENTRIES.LIST'):
                        for bank_alloc in ledg_elem.findall('.//BANKALLOCATIONS.LIST'):
                            c_num = bank_alloc.findtext('INSTRUMENTNUMBER', default='').strip()
                            c_date = bank_alloc.findtext('INSTRUMENTDATE', default='').strip()
                            b_name = bank_alloc.findtext('BANKNAME', default='').strip()
                            if c_num: cheque_num = c_num
                            if c_date: cheque_date = c_date
                            if b_name: bank_name = b_name
                    
                    # 1. Upsert Voucher
                    cursor.execute("SELECT id FROM reporting_vouchers WHERE tally_guid = ?", (guid,))
                    row = cursor.fetchone()
                    if row:
                        vch_id = row['id']
                        cursor.execute("""
                            UPDATE reporting_vouchers 
                            SET date=?, voucher_number=?, voucher_type=?, party_ledger_name=?, narration=?,
                                reference=?, reference_date=?, effective_date=?, cheque_number=?, cheque_date=?, bank_name=?
                            WHERE id=?
                        """, (date_val, vch_num, vch_type, party_ledger, narration, ref, ref_date, eff_date, cheque_num, cheque_date, bank_name, vch_id))
                        cursor.execute("DELETE FROM reporting_ledger_entries WHERE voucher_id=?", (vch_id,))
                        cursor.execute("DELETE FROM reporting_inventory_entries WHERE voucher_id=?", (vch_id,))
                    else:
                        cursor.execute("""
                            INSERT INTO reporting_vouchers (tally_guid, date, voucher_number, voucher_type, party_ledger_name, narration, reference, reference_date, effective_date, cheque_number, cheque_date, bank_name)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (guid, date_val, vch_num, vch_type, party_ledger, narration, ref, ref_date, eff_date, cheque_num, cheque_date, bank_name))
                        vch_id = cursor.lastrowid
                        
                    # 2. Ledger Entries & Allocations
                    for ledg_elem in vch_elem.findall('.//ALLLEDGERENTRIES.LIST'):
                        lname = ledg_elem.findtext('LEDGERNAME', default='').strip()
                        if not lname:
                            continue
                            
                        amt = _parse_amount(ledg_elem.findtext('AMOUNT', default='0'))
                        is_deemed = ledg_elem.findtext('ISDEEMEDPOSITIVE', default='').strip().lower() == 'yes'
                        
                        cursor.execute("""
                            INSERT INTO reporting_ledger_entries (voucher_id, ledger_name, amount, is_deemed_positive)
                            VALUES (?, ?, ?, ?)
                        """, (vch_id, lname, amt, is_deemed))
                        ledg_id = cursor.lastrowid
                        
                        for cat_elem in ledg_elem.findall('.//CATEGORYALLOCATIONS.LIST'):
                            for cc_alloc in cat_elem.findall('.//COSTCENTREALLOCATIONS.LIST'):
                                cc_name = cc_alloc.findtext('NAME', default='').strip()
                                cc_amt = _parse_amount(cc_alloc.findtext('AMOUNT', default='0'))
                                if cc_name:
                                    cursor.execute("""
                                        INSERT INTO reporting_cost_centre_allocations (ledger_entry_id, cost_centre_name, amount)
                                        VALUES (?, ?, ?)
                                    """, (ledg_id, cc_name, cc_amt))
                    
                    # 3. Inventory Entries
                    for inv_elem in vch_elem.findall('.//ALLINVENTORYENTRIES.LIST'):
                        item_name = inv_elem.findtext('STOCKITEMNAME', default='').strip()
                        if not item_name:
                            continue
                            
                        b_qty_str = inv_elem.findtext('BILLEDQTY', default='').strip()
                        qty = 0.0
                        if b_qty_str:
                            match = re.search(r'[-+]?\d*\.\d+|\d+', b_qty_str)
                            if match:
                                qty = float(match.group())
                                
                        amt = _parse_amount(inv_elem.findtext('AMOUNT', default='0'))
                        rate = _parse_amount(inv_elem.findtext('RATE', default='0'))
                        
                        godown_name = ""
                        for batch in inv_elem.findall('.//BATCHALLOCATIONS.LIST'):
                            gname = batch.findtext('GODOWNNAME', default='').strip()
                            if gname:
                                godown_name = gname
                                break
                                
                        cursor.execute("""
                            INSERT INTO reporting_inventory_entries (voucher_id, stock_item_name, godown_name, billed_qty, amount, rate)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (vch_id, item_name, godown_name, qty, amt, rate))
                    
                    vouchers_count += 1
                
                # Ghost Voucher Deletion
                cursor.execute("""
                    SELECT id, tally_guid FROM reporting_vouchers 
                    WHERE date >= ? AND date <= ?
                """, (start_date, end_date))
                
                local_vouchers = cursor.fetchall()
                deleted_count = 0
                for lv in local_vouchers:
                    if lv['tally_guid'] not in valid_tally_guids:
                        cursor.execute("DELETE FROM reporting_vouchers WHERE id = ?", (lv['id'],))
                        deleted_count += 1
                
                if deleted_count > 0:
                    print(f"Ghost cleanup: Removed {deleted_count} orphaned/cancelled/optional vouchers from SQLite.")
                
                # Insert sync history
                now_str = datetime.now().isoformat()
                cursor.execute("""
                    INSERT INTO reporting_sync_history (start_date, end_date, synced_at)
                    VALUES (?, ?, ?)
                """, (start_date, end_date, now_str))

                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
                
        return vouchers_count
    except Exception as e:
        print(f"Error fetching reporting vouchers: {e}")
        raise e

_live_purchase_history_cache = {}
_LIVE_HISTORY_CACHE_TTL_SECONDS = 60

def fetch_live_purchase_history_from_tally(item_name: str, since_date: str, tally_url=TALLY_URL) -> list:
    """On-demand, per-item purchase history straight from Tally (not the local
    reporting_vouchers cache) -- used as the primary source for the return-item
    rate-history picker when Tally is reachable. `since_date` is YYYYMMDD.

    Reuses the exact same VoucherCollection FETCH shape as fetch_and_store_vouchers
    (deliberately -- that shape is already parser-tested) but fetches the whole
    company's purchase vouchers in the window and filters to one item in Python,
    matching the same approach tally_sync_worker.py's master sync already uses
    for its own per-item rate scan. Never writes to the local DB.

    Raises requests.exceptions.ConnectTimeout/Timeout/ConnectionError on
    connectivity failure (caller decides whether to fall back to local data),
    and xml.etree.ElementTree.ParseError on a malformed response (a real data
    problem, not a connectivity one -- callers should NOT treat this as a
    reason to silently fall back).
    """
    cache_key = item_name.strip().lower()
    cached = _live_purchase_history_cache.get(cache_key)
    if cached and (datetime.now().timestamp() - cached['fetched_at']) < _LIVE_HISTORY_CACHE_TTL_SECONDS:
        return cached['entries']

    today_str = datetime.now().strftime("%Y%m%d")
    payload = f"""<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>VoucherCollection</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        <SVFROMDATE>{since_date}</SVFROMDATE>
        <SVTODATE>{today_str}</SVTODATE>
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="VoucherCollection">
            <TYPE>Voucher</TYPE>
            <FETCH>GUID, Date, VoucherTypeName, VoucherNumber, PartyLedgerName, Narration, Reference, ReferenceDate, EffectiveDate, IsOptional, IsCancelled</FETCH>
            <FETCH>AllLedgerEntries.*, AllInventoryEntries.*, AllLedgerEntries.BankAllocations.*</FETCH>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""

    response = requests.post(tally_url, data=payload, headers={'Content-Type': 'text/xml'}, timeout=10)
    response.raise_for_status()

    # A malformed/garbled response is a real data problem -- let ET.ParseError
    # propagate uncaught rather than being mistaken for a connectivity failure.
    root = ET.fromstring(sanitize_tally_xml(response.text))

    item_name_lower = item_name.strip().lower()
    entries = []
    for vch_elem in root.findall('.//VOUCHER'):
        if vch_elem.findtext('ISOPTIONAL', default='').strip().lower() == 'yes':
            continue
        if vch_elem.findtext('ISCANCELLED', default='').strip().lower() == 'yes':
            continue
        if vch_elem.findtext('VOUCHERTYPENAME', default='').strip() != 'Purchase':
            continue

        date_val = vch_elem.findtext('DATE', default='').strip()
        vch_num = vch_elem.findtext('VOUCHERNUMBER', default='').strip()
        party_ledger = vch_elem.findtext('PARTYLEDGERNAME', default='').strip()

        for inv_elem in vch_elem.findall('.//ALLINVENTORYENTRIES.LIST'):
            stock_item = inv_elem.findtext('STOCKITEMNAME', default='').strip()
            if stock_item.lower() != item_name_lower:
                continue

            b_qty_str = inv_elem.findtext('BILLEDQTY', default='').strip()
            qty = None
            if b_qty_str:
                match = re.search(r'[-+]?\d*\.\d+|\d+', b_qty_str)
                if match:
                    qty = float(match.group())

            rate = _parse_amount(inv_elem.findtext('RATE', default='0'))
            if not rate and qty:
                # Tally doesn't always emit a parseable per-line RATE even when
                # AMOUNT/BILLEDQTY are present (confirmed against real synced
                # data) -- derive it rather than showing a misleading ₹0.
                amount = _parse_amount(inv_elem.findtext('AMOUNT', default='0'))
                rate = round(abs(amount) / qty, 2)

            entries.append({
                "date": _tally_date_to_iso_str(date_val),
                "rate": rate,
                "supplier": party_ledger or None,
                "voucher_number": vch_num or None,
                "qty": qty,
                "unit": None,  # filled in by the caller from stock_items, same as the local-cache path
                "origin": "tally"
            })

    entries.sort(key=lambda e: e['date'], reverse=True)
    _live_purchase_history_cache[cache_key] = {"entries": entries, "fetched_at": datetime.now().timestamp()}
    return entries

def _tally_date_to_iso_str(raw_date: str) -> str:
    if raw_date and len(raw_date) == 8 and raw_date.isdigit():
        return f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
    return raw_date or ""

async def async_sync_cost_centres(tally_url=TALLY_URL):
    return await asyncio.to_thread(fetch_and_store_cost_centres, tally_url)

from backend.services.tally_reporting_stock_sync import async_sync_monthly_stock

async def async_sync_vouchers(start_date: str = None, end_date: str = None, tally_url=TALLY_URL):
    if not start_date:
        start_date = _get_current_fy_start()
    if not end_date:
        end_date = _get_default_sync_end_date()
        
    vouchers_count = await asyncio.to_thread(fetch_and_store_vouchers, start_date, end_date, tally_url)
    
    # Run the monthly stock sync
    failed_months = await async_sync_monthly_stock(start_date, end_date, tally_url)
    
    return {
        "vouchers_count": vouchers_count,
        "failed_months": failed_months
    }

# TODO: investigate post-dated accounting treatment
# Currently ISPOSTDATED=Yes vouchers are imported as normal, which might inflate P&L/Daybook 
# if Tally natively excludes them. We need to verify if Tally includes post-dated vouchers 
# in its Daybook/P&L before implementing skip/delete logic.
