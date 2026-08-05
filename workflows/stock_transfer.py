import os
import re
import json
import requests
import asyncio
from datetime import datetime
from rapidfuzz import process
import xml.etree.ElementTree as ET
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ApplicationHandlerStop
from workflows.tally_buffer import TALLY_URL

TALLY_STOCK_CACHE = "tally_stock_cache.json"

async def get_latest_purchase_rate(exact_item_name):
    import requests
    import xml.etree.ElementTree as ET
    import re
    
    xml_payload = """<ENVELOPE>
        <HEADER>
            <VERSION>1</VERSION>
            <TALLYREQUEST>Export</TALLYREQUEST>
            <TYPE>Collection</TYPE>
            <ID>CustomPurchases</ID>
        </HEADER>
        <BODY>
            <DESC>
                <STATICVARIABLES>
                    <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                    <SVFROMDATE>20200401</SVFROMDATE>
                    <SVTODATE>20300331</SVTODATE>
                </STATICVARIABLES>
                <TDL>
                    <TDLMESSAGE>
                        <COLLECTION NAME="CustomPurchases">
                            <TYPE>Voucher</TYPE>
                            <FETCH>*, InventoryEntries.*, AllInventoryEntries.*</FETCH>
                            <FILTER>IsPurchase</FILTER>
                        </COLLECTION>
                        <SYSTEM NAME="IsPurchase" TYPE="Formulae">$VoucherTypeName = "Purchase"</SYSTEM>
                    </TDLMESSAGE>
                </TDL>
            </DESC>
        </BODY>
    </ENVELOPE>"""
    
    try:
        response = requests.post(TALLY_URL, data=xml_payload, headers={'Content-Type': 'text/xml'}, timeout=10)
        raw_xml = response.text
        
        # 1. Strip invalid XML entity references (e.g., &#x1D;, &#4;)
        raw_xml = re.sub(r'&#[xX]?[0-9a-fA-F]+;', '', raw_xml)
        # 2. Strip raw ASCII control characters (keeping tabs and newlines)
        raw_xml = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', raw_xml)
        
        root = ET.fromstring(raw_xml)
        
        if root.find('.//LINEERROR') is not None:
            print(f"❌ Tally XML Error: {root.find('.//LINEERROR').text}")
            
        found_rates = []
        for voucher in root.iter('VOUCHER'):
            # Ensure we only look at purchase vouchers
            vch_type = voucher.findtext('VOUCHERTYPENAME', default='').lower()
            if 'purchase' not in vch_type:
                continue
                
            for inv_list in list(voucher.iter('INVENTORYENTRIES.LIST')) + list(voucher.iter('ALLINVENTORYENTRIES.LIST')):
                item_name = inv_list.findtext('STOCKITEMNAME', default='')
                
                if item_name.strip().lower() == exact_item_name.strip().lower():
                    # 1. Try explicit Rate
                    raw_rate = inv_list.findtext('RATE', default='')
                    rate_match = re.search(r"[-+]?\d*\.\d+|\d+", raw_rate)
                    
                    if rate_match and float(rate_match.group()) > 0:
                        found_rates.append(float(rate_match.group()))
                    else:
                        # 2. Math Fallback
                        qty_str = inv_list.findtext('BILLEDQTY', default='') or inv_list.findtext('ACTUALQTY', default='')
                        amt_str = inv_list.findtext('AMOUNT', default='')
                        
                        qty_match = re.search(r"[-+]?\d*\.\d+|\d+", qty_str)
                        amt_match = re.search(r"[-+]?\d*\.\d+|\d+", amt_str)
                        
                        if qty_match and amt_match:
                            qty_val = float(qty_match.group())
                            amt_val = abs(float(amt_match.group()))
                            if qty_val > 0:
                                found_rates.append(round(amt_val / qty_val, 2))
                                
        print(f"📡 Found {len(found_rates)} purchase rates for '{exact_item_name}'.")
        
        if not found_rates:
            # Failsafe: Dump the XML to a file so the user can inspect Tally's exact output structure
            with open("tally_debug.xml", "w", encoding="utf-8") as f:
                f.write(raw_xml)
            print("⚠️ Saved raw Tally output to 'tally_debug.xml' for inspection.")
            
        return found_rates[-1] if found_rates else 0.0
        
    except Exception as e:
        print(f"Rate fetch error: {e}")
        return 0.0

async def post_journal_entry(date_str, amount, item, qty, from_store, to_store):
    from xml.sax.saxutils import escape
    safe_item = escape(str(item))
    safe_from = escape(str(from_store))
    safe_to = escape(str(to_store))
    
    xml_payload = f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
        <STATICVARIABLES>
          <SVCURRENTCOMPANY>$$CurrentCompany</SVCURRENTCOMPANY>
        </STATICVARIABLES>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Journal" ACTION="Create">
            <DATE>{date_str}</DATE>
            <VOUCHERTYPENAME>Journal</VOUCHERTYPENAME>
            <NARRATION>Inter-store transfer: {qty} pcs of {safe_item}</NARRATION>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>inter store transfer</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{amount}</AMOUNT>
              <CATEGORYALLOCATIONS.LIST>
                <CATEGORY>Primary</CATEGORY>
                <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                <COSTCENTREALLOCATIONS.LIST>
                  <NAME>{safe_to}</NAME>
                  <AMOUNT>-{amount}</AMOUNT>
                </COSTCENTREALLOCATIONS.LIST>
              </CATEGORYALLOCATIONS.LIST>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>inter store transfer</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{amount}</AMOUNT>
              <CATEGORYALLOCATIONS.LIST>
                <CATEGORY>Primary</CATEGORY>
                <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
                <COSTCENTREALLOCATIONS.LIST>
                  <NAME>{safe_from}</NAME>
                  <AMOUNT>{amount}</AMOUNT>
                </COSTCENTREALLOCATIONS.LIST>
              </CATEGORYALLOCATIONS.LIST>
            </ALLLEDGERENTRIES.LIST>
          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""
    try:
        response = requests.post(TALLY_URL, data=xml_payload, headers={'Content-Type': 'text/xml'}, timeout=10)
        return "Created" in response.text or "Updated" in response.text or "<CREATED>1</CREATED>" in response.text
    except Exception as e:
        print(f"Error posting journal: {e}")
        return False

async def send_transfer_preview(update, context):
    qty = context.user_data.get('st_qty')
    item = context.user_data.get('st_item')
    rate = context.user_data.get('st_rate', 0.0)
    amount = context.user_data.get('st_amount', 0.0)
    from_store = context.user_data.get('st_from_store', '')
    to_store = context.user_data.get('st_to_store', '')
    date_str = context.user_data.get('st_date', '')
    try:
        display_date = datetime.strptime(date_str, '%Y%m%d').strftime('%d-%b-%Y')
    except ValueError:
        display_date = date_str
    
    text = (f"📝 *Stock Transfer Preview*\n\n"
            f"📅 Date: {display_date}\n"
            f"📦 Item: {qty} x {item}\n"
            f"💵 Rate: ₹{rate:.2f}\n"
            f"💰 Total Amount: ₹{amount:.2f}\n\n"
            f"📤 From Store: {from_store}\n"
            f"📥 To Store: {to_store}")
            
    keyboard = [
        [InlineKeyboardButton("📝 Edit Date", callback_data="st_edit:date"),
         InlineKeyboardButton("💵 Edit Amount", callback_data="st_edit:amount")],
        [InlineKeyboardButton("🔄 Edit Accounts", callback_data="st_edit:accounts")],
        [InlineKeyboardButton("✅ Confirm & Post", callback_data="st_post")],
        [InlineKeyboardButton("❌ Cancel", callback_data="st_cancel")]
    ]
    
    target = update.message if update.message else update.callback_query.message
    sent_msg = await target.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    context.user_data['st_msg_id'] = sent_msg.message_id

def parse_flexible_date(text):
    text = text.strip().lower()
    # Strip ordinal suffixes (1st -> 1, 2nd -> 2, etc.)
    text = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', text)
    text = re.sub(r'\s+', ' ', text)
    
    current_year = datetime.now().year
    has_year = bool(re.search(r'\d{4}', text))
    
    formats = [
        '%d %B %Y', '%d%B %Y', '%d %b %Y', '%d%b %Y',
        '%d/%m/%Y', '%d-%m-%Y', '%Y%m%d', '%d %B', '%d %b',
        '%d/%m/%y', '%d-%m-%y'
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(text, fmt)
            if dt.year == 1900 and not has_year:
                dt = dt.replace(year=current_year)
            return dt.strftime('%Y%m%d')
        except ValueError:
            continue
            
    # Fallback regex for "1 april" or "1st apr"
    months = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6, 
              'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}
    match = re.search(r'(\d{1,2})\s*([a-z]+)', text)
    if match:
        day = int(match.group(1))
        m_str = match.group(2)[:3]
        if m_str in months:
            month = months[m_str]
            year_match = re.search(r'\d{4}', text)
            year = int(year_match.group()) if year_match else current_year
            return datetime(year, month, day).strftime('%Y%m%d')
            
    return datetime.now().strftime('%Y%m%d')

async def handle_pending_text(update, context: ContextTypes.DEFAULT_TYPE):
    waiting = context.user_data.get('waiting_for')
    text = update.message.text.strip()
    
    try:
        if waiting == 'st_date':
            parsed_date = parse_flexible_date(text)
            context.user_data['st_date'] = parsed_date
        elif waiting == 'st_amount':
            amt_match = re.search(r'[\d.]+', text)
            if amt_match:
                amt = float(amt_match.group())
                context.user_data['st_amount'] = amt
            else:
                await update.message.reply_text("❌ Could not read amount. Please type a number:")
                raise ApplicationHandlerStop()
        elif waiting == 'st_accounts':
            parts = [p.strip() for p in text.split(',')]
            if len(parts) == 2:
                context.user_data['st_from_store'] = parts[0]
                context.user_data['st_to_store'] = parts[1]
            else:
                await update.message.reply_text("❌ Please format as: sender store, receiver store")
                raise ApplicationHandlerStop()
                
        # Clear state
        context.user_data.pop('waiting_for', None)
        
        # Redraw preview as a new message
        await send_transfer_preview(update, context)
            
    except Exception as e:
        if not isinstance(e, ApplicationHandlerStop):
            print(f"Error handling pending text: {e}")
            await update.message.reply_text("❌ Error updating value. Please try again.")
    finally:
        raise ApplicationHandlerStop()

async def handle_transfer_message(update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text:
        return
        
    pattern = r"(\d+(?:\.\d+)?)\s*(?:pcs|pieces)?\s*of\s+(.*?)\s+from\s+(.+?)\s+to\s+(.+)"
    match = re.search(pattern, text, re.IGNORECASE)
    
    if not match:
        return
        
    try:
        qty = float(match.group(1))
        item = match.group(2).strip()
        from_store = match.group(3).strip()
        to_store = match.group(4).strip()
        
        print(f"📦 [STOCK TRANSFER] Extracted locally: Qty={qty}, Item='{item}', From='{from_store}', To='{to_store}'")
    
        tally_items = []
        try:
            with open(TALLY_STOCK_CACHE, "r", encoding="utf-8") as f:
                stock_cache = json.load(f)
                tally_items = [data.get('name') for key, data in stock_cache.items() if type(data) is dict and data.get('name')]
        except Exception:
            pass
            
        if len(tally_items) == 0:
            await update.message.reply_text("❌ Error: Tally item cache is empty or could not be loaded.")
            raise ApplicationHandlerStop()
                
        clean_raw = item.strip().lower()
        valid_items = []
        for tally_item in tally_items:
            name = tally_item.get("name", str(tally_item)) if isinstance(tally_item, dict) else str(tally_item)
            valid_items.append(name.strip())
    
        exact_match = next((i for i in valid_items if i.strip().lower() == clean_raw), None)
        
        if exact_match:
            await process_item_selection(update, context, exact_match, qty, from_store, to_store)
            raise ApplicationHandlerStop()
    
        matches = [valid_item for valid_item in valid_items if clean_raw in valid_item.lower()]
        if not matches:
            import difflib
            matches = difflib.get_close_matches(clean_raw, valid_items, n=4, cutoff=0.2)
    
        matches = matches[:4]
    
        if not matches:
            await update.message.reply_text(f"❌ Item '{item}' not found in Tally.")
            raise ApplicationHandlerStop()
    
        context.user_data['st_qty'] = qty
        context.user_data['st_from_store'] = from_store
        context.user_data['st_to_store'] = to_store
        context.user_data['st_matches'] = matches
        
        keyboard = [[InlineKeyboardButton(m, callback_data=f"st_idx:{i}")] for i, m in enumerate(matches)]
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="st_cancel")])
        
        await update.message.reply_text(
            f"Item '{item}' not found exactly. Did you mean:", 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        if not isinstance(e, ApplicationHandlerStop):
            import traceback
            traceback.print_exc()
            await update.message.reply_text("❌ An error occurred while processing the transfer.")
    finally:
        raise ApplicationHandlerStop()

async def process_item_selection(update, context, exact_item, qty, from_store, to_store, query=None):
    msg = query.message if query else update.message
    edit_func = query.edit_message_text if query else msg.reply_text
    
    await edit_func(f"⏳ Fetching latest rate for: {exact_item}...")
    
    rate = await get_latest_purchase_rate(exact_item)
    amount = qty * rate
    
    context.user_data['st_item'] = exact_item
    context.user_data['st_rate'] = rate
    context.user_data['st_amount'] = amount
    context.user_data['st_qty'] = qty
    context.user_data['st_from_store'] = from_store
    context.user_data['st_to_store'] = to_store
    context.user_data['st_date'] = datetime.now().strftime('%Y%m%d')
    try:
        display_date = datetime.strptime(context.user_data['st_date'], '%Y%m%d').strftime('%d-%b-%Y')
    except ValueError:
        display_date = context.user_data['st_date']
    
    # Render Preview
    text = (f"📝 *Stock Transfer Preview*\n\n"
            f"📅 Date: {display_date}\n"
            f"📦 Item: {qty} x {exact_item}\n"
            f"💵 Rate: ₹{rate:.2f}\n"
            f"💰 Total Amount: ₹{amount:.2f}\n\n"
            f"📤 From Store: {from_store}\n"
            f"📥 To Store: {to_store}")
            
    keyboard = [
        [InlineKeyboardButton("📝 Edit Date", callback_data="st_edit:date"),
         InlineKeyboardButton("💵 Edit Amount", callback_data="st_edit:amount")],
        [InlineKeyboardButton("🔄 Edit Accounts", callback_data="st_edit:accounts")],
        [InlineKeyboardButton("✅ Confirm & Post", callback_data="st_post")],
        [InlineKeyboardButton("❌ Cancel", callback_data="st_cancel")]
    ]
    
    if query:
        sent_msg = await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        context.user_data['st_msg_id'] = sent_msg.message_id
    else:
        sent_msg = await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        context.user_data['st_msg_id'] = sent_msg.message_id

async def handle_transfer_callback(update, context: ContextTypes.DEFAULT_TYPE):
    from telegram.ext import ApplicationHandlerStop
    query = update.callback_query
    await query.answer()

    try:
        if query.data == "st_cancel":
            context.user_data.pop('waiting_for', None)
            await query.edit_message_text("❌ Transfer calculation cancelled.")
            
        elif query.data.startswith("st_idx:"):
            idx = int(query.data.split(":")[1])
            matches = context.user_data.get('st_matches', [])
            qty = context.user_data.get('st_qty', 0)
            from_store = context.user_data.get('st_from_store', '')
            to_store = context.user_data.get('st_to_store', '')

            if idx < len(matches):
                exact_item = matches[idx]
                await process_item_selection(update, context, exact_item, qty, from_store, to_store, query)
            else:
                await query.edit_message_text("❌ Session expired. Please type your transfer again.")
                
        elif query.data.startswith("st_edit:"):
            field = query.data.split(":")[1]
            if field == 'date':
                context.user_data['waiting_for'] = 'st_date'
                await context.bot.send_message(chat_id=update.effective_chat.id, text="📅 Type the new date (YYYYMMDD):")
            elif field == 'amount':
                context.user_data['waiting_for'] = 'st_amount'
                await context.bot.send_message(chat_id=update.effective_chat.id, text="💵 Type the new total amount:")
            elif field == 'accounts':
                context.user_data['waiting_for'] = 'st_accounts'
                await context.bot.send_message(chat_id=update.effective_chat.id, text="🔄 Type new accounts as: From Store to To Store\n(e.g., mahagun to vvip)")
                
        elif query.data == "st_post":
            await query.edit_message_text("⏳ Posting Journal Voucher to Tally...")
            success = await post_journal_entry(
                context.user_data.get('st_date'),
                context.user_data.get('st_amount'),
                context.user_data.get('st_item'),
                context.user_data.get('st_qty'),
                context.user_data.get('st_from_store'),
                context.user_data.get('st_to_store')
            )
            if success:
                await query.edit_message_text("✅ Successfully posted Inter-Store Transfer to Tally!")
                context.user_data.pop('waiting_for', None)
            else:
                await query.edit_message_text("❌ Failed to post voucher to Tally. Check logs.")
                
    except Exception as e:
        print(f"❌ Callback error: {e}")
    finally:
        raise ApplicationHandlerStop()

async def stock_transfer_router(update, context: ContextTypes.DEFAULT_TYPE):
    """Multiplexes text inputs to either pending edit handler or the main regex handler"""
    if context.user_data.get('waiting_for'):
        await handle_pending_text(update, context)
        return
        
    text = update.message.text
    if text and re.search(r"transfer", text, re.IGNORECASE):
        await handle_transfer_message(update, context)
        return
