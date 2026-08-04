import datetime
import json
import re
import os
from xml.sax.saxutils import escape
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from json_repair import repair_json

try:
    from rapidfuzz import process
except ImportError:
    process = None

MODE = "Purchase_Inventory"
TALLY_STOCK_CACHE = "tally_stock_cache.json"
TALLY_ITEM_ALIASES = "tally_item_aliases.json"
TALLY_UOM_CACHE = "tally_uom_cache.json"

def _sanitize_xml(value) -> str:
    return escape(str(value if value is not None else ""), {'"': "&quot;", "'": "&apos;"})

def normalize_item_name(name: str) -> str:
    name = name.lower()
    name = re.sub(r'[^a-z0-9\s]', '', name)
    name = re.sub(r'\s+gm\s+', 'g ', name)
    name = re.sub(r'\s+gm$', 'g', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name

def load_json(filepath):
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def save_json(filepath, data):
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=4)

def _prompt(user_text: str) -> str:
    today = datetime.datetime.now().strftime("%d-%m-%Y")
    year = datetime.datetime.now().year
    return f"""
The current date is {today}. Extract the following details from the user's invoice: supplier, inv_no, date, amount, and items.
Also extract cgst_amount, sgst_amount, and igst_amount as numerical floats. If a tax is not present, return 0.0.
For items, extract an array of objects containing name, qty, unit, rate, and amount.

CRITICAL RULES:
1. Dates must be DD-MM-YYYY. If year is missing, assume {year}.
2. MULTI-LINE ITEMS: Item names frequently wrap to a second line on printed invoices. If you see text directly below an item name that does NOT have its own distinct quantity, price, and amount (e.g., "G Detox Green Tea" on one line and "Desi Kahwa 90g" underneath it), you MUST merge them into a single item name ("G Detox Green Tea Desi Kahwa 90g"). Do NOT create a new item entry unless the line has its own financial values.
3. ROUNDING: Look at the bottom of the invoice for any text mentioning "Round", "Rounding", or "Less: Round Off". Extract this value into a JSON key named "rounding_amount" (as a float, positive or negative depending on whether it adds or subtracts from the total). If no rounding line exists, set "rounding_amount" to 0.0.
4. Return ONLY a valid JSON object matching this structure:
{{
  "supplier": "...",
  "inv_no": "...",
  "date": "...",
  "amount": 0.0,
  "rounding_amount": 0.0,
  "cgst_amount": 0.0,
  "sgst_amount": 0.0,
  "igst_amount": 0.0,
  "items": [
    {{ "name": "...", "qty": 0.0, "unit": "...", "rate": 0.0, "amount": 0.0 }}
  ]
}}

User text/image context:
{user_text}
"""

async def process_photo(update, context, photo_bytes: bytearray, generate_gemini):
    import time
    
    profiler = {
        'telegram_received': time.time()
    }
    context.user_data['profiler'] = profiler
    context.user_data['pi_photo_bytes'] = photo_bytes
    
    keyboard = [
        [
            InlineKeyboardButton("Mahagun", callback_data="set_purchase_cc|Mahagun"),
            InlineKeyboardButton("Gulshan", callback_data="set_purchase_cc|Gulshan"),
            InlineKeyboardButton("Vvip", callback_data="set_purchase_cc|Vvip")
        ]
    ]
        
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = "🏢 Please select the Cost Centre for this purchase:"
    await update.message.reply_text(msg, reply_markup=reply_markup)

async def _run_gemini_processing(update, context, photo_bytes: bytearray, generate_gemini, message):
    import time
    profiler = context.user_data.get('profiler', {})
    
    try:
        await message.edit_text("Processing item-wise invoice image with Gemini...")
    except Exception as e:
        if "Message is not modified" not in str(e):
            raise e
    from PIL import Image
    from io import BytesIO
    
    img = Image.open(BytesIO(photo_bytes))
    if img.mode != 'RGB':
        img = img.convert('RGB')
        
    contents = [_prompt("Extract details."), img]
    profiler['gemini_start'] = time.time()
    extracted_text = await generate_gemini(contents, json_mode=True, model_name="gemini-3.5-flash")
    profiler['gemini_end'] = time.time()
    
    del contents
    del img
    
    raw_text = extracted_text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[-1]
    if raw_text.endswith("```"):
        raw_text = raw_text.rsplit("```", 1)[0]
    raw_text = raw_text.strip()
    
    try:
        data = repair_json(raw_text, return_objects=True)
    except Exception as e:
        print(f"❌ JSON Parse Failure: {e}")
        print(f"📄 Raw Output was:\n{raw_text}")
        await message.edit_text(f"❌ Failed to parse JSON from AI: {e}")
        return
        
    # Initialize state
    context.user_data['pi_supplier'] = data.get('supplier', 'Unknown')
    context.user_data['pi_inv_no'] = data.get('inv_no', 'Unknown')
    context.user_data['pi_date'] = data.get('date', 'Unknown')
    context.user_data['pi_amount'] = data.get('amount', 0.0)
    taxes_dict = data.get("taxes") or {}
    cgst = float(taxes_dict.get("cgst", data.get("cgst_amount", data.get("cgst", data.get("CGST", 0.0)))))
    sgst = float(taxes_dict.get("sgst", data.get("sgst_amount", data.get("sgst", data.get("SGST", 0.0)))))
    igst = float(taxes_dict.get("igst", data.get("igst_amount", data.get("igst", data.get("IGST", 0.0)))))
    print(f"🔍 DEBUG TAXES EXTRACTED -> CGST: {cgst}, SGST: {sgst}, IGST: {igst}")
    
    context.user_data['pi_cgst'] = cgst
    context.user_data['pi_sgst'] = sgst
    context.user_data['pi_igst'] = igst
    context.user_data['pi_rounding'] = float(data.get("rounding_amount", 0.0))
    
    items = data.get('items', [])
    grand_total = float(data.get("total_amount", data.get("grand_total", data.get("amount", 0.0))))
    
    if items and grand_total > 0:
        tax_total = cgst + sgst + igst
        target_items_total = grand_total - tax_total
        current_subtotal = sum(float(item.get("amount", 0.0)) for item in items[:-1])
        
        last_item = items[-1]
        last_item_amount = round(target_items_total - current_subtotal, 2)
        last_item["amount"] = last_item_amount
        
        qty = float(last_item.get("quantity", last_item.get("qty", 1.0)))
        if qty > 0:
            last_item["rate"] = round(last_item_amount / qty, 2)
            
    context.user_data['pending_items'] = items
    context.user_data['resolved_items'] = []
    
    await message.delete()
    
    await process_next_item(update, context)

async def process_next_item(update, context):
    pending = context.user_data.get('pending_items', [])
    if not pending:
        # All items resolved! Show preview/push to tally
        await show_preview(update, context)
        return
        
    item = pending[0] # Look at the first item
    raw_name = item.get('name', 'Unknown')
    norm_name = normalize_item_name(raw_name)
    
    stock_cache = load_json(TALLY_STOCK_CACHE)
    aliases = load_json(TALLY_ITEM_ALIASES)
    
    # 1. Exact Match
    if norm_name in stock_cache:
        real_name = stock_cache[norm_name]['name']
        await resolve_item(update, context, item, real_name, stock_cache[norm_name]['unit'])
        return
        
    # 2. Alias Match
    if norm_name in aliases:
        real_name = aliases[norm_name]
        unit = item.get('unit', 'NOS')
        for k, v in stock_cache.items():
            if v['name'] == real_name:
                unit = v['unit']
                break
        await resolve_item(update, context, item, real_name, unit)
        return
        
    # 3. Fuzzy Match
    choices = [v['name'] for v in stock_cache.values()]
    if not choices:
        import json, os
        cache_file = "tally_stock_cache.json"
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    masters = json.load(f)
                    raw_items = masters.get("stock_items", []) if isinstance(masters, dict) and "stock_items" in masters else masters.values()
                    choices = []
                    for item in raw_items:
                        if isinstance(item, dict):
                            name = item.get("NAME", item.get("name", ""))
                            if name:
                                choices.append(name)
                        else:
                            choices.append(str(item))
            except Exception as e:
                print(f"⚠️ Error loading masters cache: {e}")
                
    print(f"🔍 DEBUG: Attempting to map '{norm_name}'. Tally items in cache: {len(choices)}")
    
    keyboard = []
    if choices and process:
        try:
            suggestions = process.extract(norm_name, choices, limit=3)
            print(f"🔍 DEBUG: Fuzzy suggestions found: {suggestions}")
            
            for i, m in enumerate(suggestions):
                match_str = m[0]
                score = m[1]
                context.user_data[f'fuzzy_choice_{i}'] = match_str
                keyboard.append([InlineKeyboardButton(f"🔗 Map to: {match_str} ({score:.0f}%)", callback_data=f"pi_match_{i}")])
        except Exception as e:
            print(f"❌ DEBUG: Fuzzy match crashed: {e}")
    else:
        print("⚠️ DEBUG: Cannot generate suggestions because 'choices' list is empty or process is None.")
        
    keyboard.append([InlineKeyboardButton("🔍 Search Tally", callback_data="search_tally_item")])
    keyboard.append([InlineKeyboardButton("✨ Create New Item", callback_data="pi_create_new")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    msg = f"⚠️ Unmapped Item: **{raw_name}**\nQty: {item.get('qty')} {item.get('unit')}\n\nSelect a mapping or create new in Tally."
    
    if update.message:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)

async def resolve_item(update, context, item, mapped_name, unit):
    item['mapped_name'] = mapped_name
    item['mapped_unit'] = unit
    
    # Move from pending to resolved
    pending = context.user_data.get('pending_items', [])
    resolved = context.user_data.get('resolved_items', [])
    
    if pending:
        resolved.append(pending.pop(0))
        
    context.user_data['pending_items'] = pending
    context.user_data['resolved_items'] = resolved
    
    # Process next without sending message
    await process_next_item(update, context)

async def send_uom_selection(update, context):
    pending = context.user_data.get('pending_items', [])
    if not pending:
        return
    item_name = pending[0].get('name', 'Unknown')
    
    uoms = load_json(TALLY_UOM_CACHE)
    if not uoms:
        uoms = ["NOS", "PCS", "KG", "LTR", "BOX", "MTR"] # Fallback if cache is empty
        
    keyboard = []
    row = []
    for uom in uoms:
        row.append(InlineKeyboardButton(uom, callback_data=f"pi_uom_{uom}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    keyboard.append([InlineKeyboardButton("➕ Create New Unit", callback_data="pi_create_new_uom")])
        
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = f"Select the Base Unit for **{item_name}**:"
    
    if update.message:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.edit_text(msg, parse_mode="Markdown", reply_markup=reply_markup)

async def receive_text(update, context, generate_gemini, push_to_tally=None):
    state = context.user_data.get('pi_state')
    
    if state == 'WAITING_FOR_ITEM_NAME':
        new_name = update.message.text.strip()
        pending = context.user_data.get('pending_items', [])
        if pending:
            pending[0]['name'] = new_name
        context.user_data.pop('pi_state', None)
        await send_uom_selection(update, context)
        return True
        
    elif state == 'WAITING_FOR_NEW_UOM':
        new_uom = update.message.text.strip().upper()
        context.user_data.pop('pi_state', None)
        
        if push_to_tally:
            xml_bytes = generate_unit_xml(new_uom)
            status, _ = push_to_tally(xml_bytes)
            if status == 200:
                uoms = load_json(TALLY_UOM_CACHE)
                if new_uom not in uoms:
                    uoms.append(new_uom)
                    save_json(TALLY_UOM_CACHE, uoms)
                    
                pending = context.user_data.get('pending_items', [])
                if pending:
                    item = pending[0]
                    raw_name = item.get('name', 'Unknown')
                    
                    stock_xml = generate_stock_item_xml(raw_name, new_uom)
                    s_status, _ = push_to_tally(stock_xml)
                    
                    if s_status == 200:
                        stock_cache = load_json(TALLY_STOCK_CACHE)
                        norm_name = normalize_item_name(raw_name)
                        stock_cache[norm_name] = {"name": raw_name, "unit": new_uom}
                        save_json(TALLY_STOCK_CACHE, stock_cache)
                        
                        aliases = load_json(TALLY_ITEM_ALIASES)
                        orig_extracted = context.user_data.get('pi_orig_extracted_name')
                        if orig_extracted:
                            aliases[normalize_item_name(orig_extracted)] = raw_name
                            save_json(TALLY_ITEM_ALIASES, aliases)
                            context.user_data.pop('pi_orig_extracted_name', None)
                            
                        await resolve_item(update, context, item, raw_name, new_uom)
                    else:
                        await update.message.reply_text(f"❌ Failed to create Stock Item. HTTP {s_status}")
            else:
                await update.message.reply_text(f"❌ Failed to create UOM. HTTP {status}")
        return True
        
    elif state == 'WAITING_FOR_ITEM_SEARCH':
        keyword = update.message.text.strip().lower()
        context.user_data.pop('pi_state', None)
        
        stock_cache = load_json(TALLY_STOCK_CACHE)
        
        matches = []
        for norm, info in stock_cache.items():
            if keyword in info['name'].lower():
                matches.append(info)
                if len(matches) >= 8:
                    break
                    
        keyboard = []
        for i, match in enumerate(matches):
            context.user_data[f'search_choice_{i}'] = match['name']
            keyboard.append([InlineKeyboardButton(match['name'], callback_data=f"pi_sres_{i}")])
            
        keyboard.append([InlineKeyboardButton("🔍 Search Again", callback_data="search_tally_item")])
        keyboard.append([InlineKeyboardButton("✨ Create New Item", callback_data="pi_create_new")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if matches:
            msg = f"Search results for '{keyword}':"
        else:
            msg = f"❌ No items found containing '{keyword}'."
            
        await update.message.reply_text(msg, reply_markup=reply_markup)
        return True
        
    elif state == 'WAITING_FOR_SUPPLIER':
        context.user_data['pi_supplier'] = update.message.text.strip()
        context.user_data.pop('pi_state', None)
        await show_preview(update, context)
        return True
        
    elif state == 'WAITING_FOR_DATE':
        context.user_data['pi_date'] = update.message.text.strip()
        context.user_data.pop('pi_state', None)
        await show_preview(update, context)
        return True
        
    elif state == 'WAITING_FOR_NUMBER':
        context.user_data['pi_inv_no'] = update.message.text.strip()
        context.user_data.pop('pi_state', None)
        await show_preview(update, context)
        return True
        
    elif state == 'WAITING_FOR_AMOUNT':
        try:
            context.user_data['pi_amount'] = float(update.message.text.strip())
        except ValueError:
            pass
        context.user_data.pop('pi_state', None)
        await show_preview(update, context)
        return True
        
    return False

async def handle_callback(update, context, load_db, save_db, push_to_tally, generate_gemini=None):
    query = update.callback_query
    data = query.data
    
    if data.startswith("set_purchase_cc|"):
        cc = data.split("|")[1]
        context.user_data['purchase_cost_centre'] = cc
        await query.answer()
        
        photo_bytes = context.user_data.get('pi_photo_bytes')
        if not photo_bytes or not generate_gemini:
            await query.message.edit_text("❌ Session expired or missing Gemini function. Please upload the invoice again.")
            return True
            
        await _run_gemini_processing(update, context, photo_bytes, generate_gemini, query.message)
        return True
    
    if not (data.startswith("pi_") or data == "search_tally_item"):
        return False
        
    await query.answer()
    
    if data.startswith("pi_match_"):
        idx = data.split("_")[-1]
        mapped_name = context.user_data.get(f'fuzzy_choice_{idx}')
        if not mapped_name:
            await query.message.edit_text("Session expired.")
            return True
            
        pending = context.user_data.get('pending_items', [])
        if not pending:
            return True
            
        item = pending[0]
        norm_name = normalize_item_name(item.get('name', ''))
        
        # Save to aliases
        aliases = load_json(TALLY_ITEM_ALIASES)
        aliases[norm_name] = mapped_name
        save_json(TALLY_ITEM_ALIASES, aliases)
        
        # Find unit
        stock_cache = load_json(TALLY_STOCK_CACHE)
        unit = item.get('unit', 'NOS')
        for k, v in stock_cache.items():
            if v['name'] == mapped_name:
                unit = v['unit']
                break
                
        await query.message.delete()
        await resolve_item(update, context, item, mapped_name, unit)
        return True
        
    elif data.startswith("pi_sres_"):
        idx = data.split("_")[-1]
        mapped_name = context.user_data.get(f'search_choice_{idx}')
        if not mapped_name:
            await query.message.edit_text("Session expired.")
            return True
            
        pending = context.user_data.get('pending_items', [])
        if not pending:
            return True
            
        item = pending[0]
        norm_name = normalize_item_name(item.get('name', ''))
        
        aliases = load_json(TALLY_ITEM_ALIASES)
        aliases[norm_name] = mapped_name
        save_json(TALLY_ITEM_ALIASES, aliases)
        
        stock_cache = load_json(TALLY_STOCK_CACHE)
        unit = item.get('unit', 'NOS')
        for k, v in stock_cache.items():
            if v['name'] == mapped_name:
                unit = v['unit']
                break
                
        await query.message.delete()
        await resolve_item(update, context, item, mapped_name, unit)
        return True
        
    elif data == "pi_create_new":
        pending = context.user_data.get('pending_items', [])
        if not pending:
            return True
            
        item = pending[0]
        raw_name = item.get('name', 'Unknown')
        
        context.user_data['pi_orig_extracted_name'] = raw_name
        
        keyboard = [
            [InlineKeyboardButton("✅ Proceed with this Name", callback_data="pi_name_proceed")],
            [InlineKeyboardButton("✏️ Edit Name", callback_data="pi_name_edit")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = f"Creating new item: **{raw_name}**"
        await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
        return True
        
    elif data == "pi_name_edit":
        context.user_data['pi_state'] = 'WAITING_FOR_ITEM_NAME'
        await query.message.edit_text("Please type the new name for the item:")
        return True
        
    elif data == "pi_name_proceed":
        await send_uom_selection(update, context)
        return True
        
    elif data == "search_tally_item":
        context.user_data['pi_state'] = 'WAITING_FOR_ITEM_SEARCH'
        await query.message.edit_text("Type a keyword to search in your Tally Stock (e.g., 'kurkure'):")
        return True
        
    elif data == "pi_create_new_uom":
        context.user_data['pi_state'] = 'WAITING_FOR_NEW_UOM'
        await query.message.edit_text("Type the symbol for the new unit (e.g., BTL, CRT, DOZ):")
        return True
        
    elif data.startswith("pi_uom_"):
        unit = data.split("pi_uom_")[1]
        
        pending = context.user_data.get('pending_items', [])
        if not pending:
            return True
            
        item = pending[0]
        raw_name = item.get('name', 'Unknown')
        
        # Push to Tally
        xml_bytes = generate_stock_item_xml(raw_name, unit)
        status, _ = push_to_tally(xml_bytes)
        
        if status == 200:
            # Add to stock cache
            stock_cache = load_json(TALLY_STOCK_CACHE)
            norm_name = normalize_item_name(raw_name)
            stock_cache[norm_name] = {"name": raw_name, "unit": unit}
            save_json(TALLY_STOCK_CACHE, stock_cache)
            
            aliases = load_json(TALLY_ITEM_ALIASES)
            orig_extracted = context.user_data.get('pi_orig_extracted_name')
            if orig_extracted:
                aliases[normalize_item_name(orig_extracted)] = raw_name
                save_json(TALLY_ITEM_ALIASES, aliases)
                context.user_data.pop('pi_orig_extracted_name', None)
            
            await query.message.delete()
            await resolve_item(update, context, item, raw_name, unit)
        else:
            await query.message.edit_text(f"❌ Failed to create item in Tally. HTTP {status}")
        return True
        
    elif data == "pi_edit_supplier":
        context.user_data['pi_state'] = 'WAITING_FOR_SUPPLIER'
        await query.message.edit_text("Please send the new Supplier Name:")
        return True
        
    elif data == "pi_edit_date":
        context.user_data['pi_state'] = 'WAITING_FOR_DATE'
        await query.message.edit_text("Please send the new Date (e.g. DD-MM-YYYY):")
        return True
        
    elif data == "pi_edit_number":
        context.user_data['pi_state'] = 'WAITING_FOR_NUMBER'
        await query.message.edit_text("Please send the new Invoice Number:")
        return True
        
    elif data == "pi_edit_amount":
        context.user_data['pi_state'] = 'WAITING_FOR_AMOUNT'
        await query.message.edit_text("Please send the new Total Amount:")
        return True
        
    elif data == "pi_push_tally":
        import time
        from datetime import datetime
        
        profiler = context.user_data.get('profiler', {})
        
        await query.message.edit_text("Pushing to Tally...")
        xml_bytes = build_xml(context)
        profiler['xml_generated'] = time.time()
        
        profiler['tally_req_start'] = time.time()
        status, res_text = push_to_tally(
            xml_bytes,
            user_id=update.effective_user.id,
            description=f"Purchase Inventory: Invoice {draft.get('inv_no', 'N/A')} from {draft.get('supplier', 'Unknown')}"
        )
        profiler['tally_req_end'] = time.time()
        
        if status == 200:
            await query.message.edit_text(f"✅ Item-Wise Purchase successfully exported to Tally!\nHTTP {status}")
            profiler['telegram_reply_sent'] = time.time()
        elif status == 202:
            await query.message.edit_text("⏸ Tally is offline. Saved to queue! It will push automatically when your laptop opens.")
            profiler['telegram_reply_sent'] = time.time()
            
            # --- Profiling Log ---
            with open("performance.log", "a") as f:
                f.write(f"--- Voucher Processed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                if 'telegram_received' in profiler:
                    total_time = profiler['telegram_reply_sent'] - profiler['telegram_received']
                    
                    steps = [
                        ("Gemini Processing", profiler.get('gemini_end', 0) - profiler.get('gemini_start', 0)),
                        ("XML Generation", profiler.get('xml_generated', 0) - profiler.get('gemini_end', 0)),
                        ("Tally HTTP Request", profiler.get('tally_req_end', 0) - profiler.get('tally_req_start', 0)),
                        ("Telegram Reply", profiler.get('telegram_reply_sent', 0) - profiler.get('tally_req_end', 0))
                    ]
                    
                    for step_name, duration in steps:
                        f.write(f"{step_name}: {duration:.2f}s\n")
                        if duration > 5.0:
                            f.write(f"WARNING: {step_name} took {duration:.2f} seconds (>5s)!\n")
                            
                    f.write(f"Total Execution Time (including user interaction): {total_time:.2f}s\n\n")
            
            # Clear state
            context.user_data.pop('pi_supplier', None)
            context.user_data.pop('pi_inv_no', None)
            context.user_data.pop('pi_date', None)
            context.user_data.pop('pi_amount', None)
            context.user_data.pop('pending_items', None)
            context.user_data.pop('resolved_items', None)
        else:
            await query.message.edit_text(f"❌ Failed to push to Tally.\nHTTP {status}\n{res_text}")
        return True

    return False

def generate_unit_xml(new_uom: str) -> bytes:
    name_sanitized = _sanitize_xml(new_uom)
    xml = f"""<ENVELOPE>
  <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC><REPORTNAME>All Masters</REPORTNAME></REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <UNIT ACTION="Create" NAME="{name_sanitized}">
            <NAME>{name_sanitized}</NAME>
            <ISSIMPLEUNIT>Yes</ISSIMPLEUNIT>
          </UNIT>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""
    return xml.encode('utf-8')

def generate_stock_item_xml(item_name: str, unit: str) -> bytes:
    name_sanitized = _sanitize_xml(item_name)
    unit_sanitized = _sanitize_xml(unit)
    xml = f"""<ENVELOPE>
  <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC><REPORTNAME>All Masters</REPORTNAME></REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <STOCKITEM ACTION="Create" NAME="{name_sanitized}">
            <NAME.LIST>
              <NAME>{name_sanitized}</NAME>
            </NAME.LIST>
            <BASEUNITS>{unit_sanitized}</BASEUNITS>
          </STOCKITEM>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""
    return xml.encode('utf-8')

def build_xml(context) -> bytes:
    date_raw = str(context.user_data.get('pi_date', ''))
    try:
        day, month, year = date_raw.split("-")
        date_str = f"{year}{month}{day}"
    except ValueError:
        date_str = datetime.datetime.now().strftime("%Y%m%d")

    inv_no = _sanitize_xml(context.user_data.get('pi_inv_no', ''))
    amount = context.user_data.get('pi_amount', 0)
    cgst = context.user_data.get('pi_cgst', 0.0)
    sgst = context.user_data.get('pi_sgst', 0.0)
    igst = context.user_data.get('pi_igst', 0.0)
    supplier = _sanitize_xml(context.user_data.get('pi_supplier', 'Unknown'))
    resolved_items = context.user_data.get('resolved_items', [])

    inventory_entries = []
    item_total = 0.0
    for item in resolved_items:
        iname = _sanitize_xml(item.get('mapped_name', 'Unknown'))
        iqty = item.get('qty', 0)
        iunit = _sanitize_xml(item.get('mapped_unit', 'NOS'))
        irate = item.get('rate', 0)
        iamt = float(item.get('amount', 0))
        item_total += iamt
        
        entry = f"""
        <ALLINVENTORYENTRIES.LIST>
          <STOCKITEMNAME>{iname}</STOCKITEMNAME>
          <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          <RATE>{irate}/{iunit}</RATE>
          <AMOUNT>-{iamt}</AMOUNT>
          <ACTUALQTY>{iqty} {iunit}</ACTUALQTY>
          <BILLEDQTY>{iqty} {iunit}</BILLEDQTY>
          <ACCOUNTINGALLOCATIONS.LIST>
            <LEDGERNAME>Purchase</LEDGERNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <AMOUNT>-{iamt}</AMOUNT>
          </ACCOUNTINGALLOCATIONS.LIST>
        </ALLINVENTORYENTRIES.LIST>"""
        inventory_entries.append(entry)

    inv_entries_xml = "".join(inventory_entries)

    total_debits = item_total + cgst + sgst + igst
    try:
        grand_total = float(amount)
    except ValueError:
        grand_total = 0.0
    rounding_diff = round(grand_total - total_debits, 2)
    print(f"🔍 DEBUG ROUNDING -> Grand Total: {grand_total}, Calculated Debits: {total_debits}, Difference: {rounding_diff}")

    tax_xml = ""
    if cgst > 0:
        tax_xml += f"""
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>Input CGST</LEDGERNAME>
          <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          <AMOUNT>-{cgst:.2f}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>"""
    if sgst > 0:
        tax_xml += f"""
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>Input SGST</LEDGERNAME>
          <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          <AMOUNT>-{sgst:.2f}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>"""
    if igst > 0:
        tax_xml += f"""
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>Input IGST</LEDGERNAME>
          <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          <AMOUNT>-{igst:.2f}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>"""

    rounding_xml = ""

    xml = f"""<ENVELOPE>
  <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
  <BODY><IMPORTDATA>
    <REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC>
    <REQUESTDATA><TALLYMESSAGE>
      <VOUCHER VCHTYPE="Purchase" ACTION="Create">
        <DATE>{date_str}</DATE>
        <REFERENCE>{inv_no}</REFERENCE>
        <VOUCHERNUMBER>{inv_no}</VOUCHERNUMBER>
        <NARRATION>Purchase Invoice {inv_no}</NARRATION>
        <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
        <PARTYLEDGERNAME>{supplier}</PARTYLEDGERNAME>
        <ISINVOICE>Yes</ISINVOICE>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{supplier}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          <AMOUNT>{amount}</AMOUNT>
          <COSTCENTREALLOCATIONS.LIST>
            <NAME>{context.user_data.get('purchase_cost_centre', 'Unknown')}</NAME>
            <AMOUNT>{amount}</AMOUNT>
          </COSTCENTREALLOCATIONS.LIST>
          <BILLALLOCATIONS.LIST>
            <NAME>{inv_no}</NAME><BILLTYPE>New Ref</BILLTYPE><AMOUNT>{amount}</AMOUNT>
          </BILLALLOCATIONS.LIST>
        </ALLLEDGERENTRIES.LIST>
        {tax_xml}
        {rounding_xml}
        {inv_entries_xml}
      </VOUCHER>
    </TALLYMESSAGE></REQUESTDATA>
  </IMPORTDATA></BODY>
</ENVELOPE>"""
    return xml.encode("utf-8")

async def show_preview(update, context):
    supplier = context.user_data.get('pi_supplier', 'Unknown')
    inv_no = context.user_data.get('pi_inv_no', 'Unknown')
    date_val = context.user_data.get('pi_date', 'Unknown')
    amount = context.user_data.get('pi_amount', 0.0)
    resolved = context.user_data.get('resolved_items', [])
    cc = context.user_data.get('purchase_cost_centre', 'Unknown')
    cgst = context.user_data.get('pi_cgst', 0.0)
    sgst = context.user_data.get('pi_sgst', 0.0)
    igst = context.user_data.get('pi_igst', 0.0)
    
    text = (
        f"**🛒 Item-Wise Purchase Summary**\n\n"
        f"• SUPPLIER: {supplier}\n"
        f"• COST CENTRE: {cc}\n"
        f"• DATE: {date_val}\n"
        f"• INV NO: {inv_no}\n"
        f"• TOTAL AMOUNT: {amount}\n"
        f"• CGST: {cgst}\n"
        f"• SGST: {sgst}\n"
        f"• IGST: {igst}\n\n"
        f"**Items:**\n"
    )
    for i, it in enumerate(resolved, 1):
        text += f"  {i}. {it.get('mapped_name')} - {it.get('qty')} {it.get('mapped_unit')} @ {it.get('rate')}\n"
        
    keyboard = [
        [
            InlineKeyboardButton("✏️ Edit Supplier", callback_data="pi_edit_supplier"),
            InlineKeyboardButton("✏️ Edit Date", callback_data="pi_edit_date")
        ],
        [
            InlineKeyboardButton("✏️ Edit Inv No", callback_data="pi_edit_number"),
            InlineKeyboardButton("✏️ Edit Amount", callback_data="pi_edit_amount")
        ],
        [
            InlineKeyboardButton("🚀 Push to Tally", callback_data="pi_push_tally")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
