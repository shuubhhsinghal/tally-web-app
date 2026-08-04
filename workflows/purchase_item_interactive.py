import asyncio
import datetime
import json
import re
import os
from xml.sax.saxutils import escape
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from json_repair import repair_json
import dateparser

try:
    from rapidfuzz import process as fuzz_process
except ImportError:
    fuzz_process = None

TALLY_STOCK_CACHE = "tally_stock_cache.json"
TALLY_LEDGER_CACHE = "tally_ledger_cache.json"
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
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return [] if 'uom' in filepath.lower() else {}

def save_json(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)

async def _safe_edit(message, text, **kwargs):
    """Wrapper to safely edit a Telegram message, ignoring 'Message is not modified' errors."""
    try:
        await message.edit_text(text, **kwargs)
    except Exception as e:
        err_str = str(e).lower()
        if "message is not modified" in err_str:
            return
        elif "message to edit not found" in err_str or "message can't be edited" in err_str:
            try:
                await message.reply_text(text, **kwargs)
            except Exception:
                pass
        else:
            raise

def _prompt() -> str:
    today = datetime.datetime.now().strftime("%d-%m-%Y")
    year = datetime.datetime.now().year
    return f"""
The current date is {today}. Extract the following details from this purchase invoice image.

CRITICAL RULES:
1. Dates must be DD-MM-YYYY. If year is missing, assume {year}.
2. MULTI-LINE ITEMS: Item names frequently wrap to a second line on printed invoices. If you see text directly below an item name that does NOT have its own distinct quantity, price, and amount, you MUST merge them into a single item name. Do NOT create a new item entry unless the line has its own financial values.
3. MATH RULE: Ignore MRP or List Price. Extract the "Taxable Rate" or the actual rate charged. Ensure qty * rate = amount for each item.
4. Return ONLY a valid JSON object matching this structure:
{{
  "supplier_name": "...",
  "invoice_number": "...",
  "date": "DD-MM-YYYY",
  "items": [
    {{"name": "...", "qty": 0.0, "uom": "pcs", "rate": 0.0, "amount": 0.0}}
  ],
  "cgst": 0.0,
  "sgst": 0.0,
  "igst": 0.0,
  "rounding_off": 0.0,
  "grand_total": 0.0
}}
5. `rate`: The base unit price EXCLUDING taxes. 
   *CRITICAL WARNING:* NEVER extract 'L.Price', 'List Price', or 'MRP' as the rate. Look specifically for 'Price', 'Rate', or 'Taxable Value'.
   *MATH CHECK:* `qty * rate` MUST perfectly equal the item's base `amount` (before GST).
6. `rounding_off`: Look for 'Round off' or 'Rounding' near the bottom totals. Extract the exact float value (e.g., 0.25 or -0.40). If not found, return 0.0.
8. CRITICAL INSTRUCTION: You must respond INSTANTLY. DO NOT generate any reasoning, thoughts, or <think> blocks. Output ONLY the raw JSON array and nothing else.
"""

async def process_photo(update, context, photo_bytes: bytearray, generate_gemini):
    media_group_id = update.message.media_group_id if update.message else None
    
    if media_group_id:
        if 'pii_album_buffer' not in context.chat_data:
            context.chat_data['pii_album_buffer'] = {}
        if media_group_id not in context.chat_data['pii_album_buffer']:
            context.chat_data['pii_album_buffer'][media_group_id] = []
            
        context.chat_data['pii_album_buffer'][media_group_id].append(photo_bytes)
        current_count = len(context.chat_data['pii_album_buffer'][media_group_id])
        
        await asyncio.sleep(2.0)
        
        if len(context.chat_data['pii_album_buffer'][media_group_id]) != current_count:
            return # Another handler took over, abort this one
            
        if 'pii_photo_bytes_list' not in context.user_data:
            context.user_data['pii_photo_bytes_list'] = []
        context.user_data['pii_photo_bytes_list'].extend(context.chat_data['pii_album_buffer'][media_group_id])
        del context.chat_data['pii_album_buffer'][media_group_id]
        page_count = len(context.user_data['pii_photo_bytes_list'])
    else:
        if 'pii_photo_bytes_list' not in context.user_data:
            context.user_data['pii_photo_bytes_list'] = []
        context.user_data['pii_photo_bytes_list'].append(photo_bytes)
        page_count = len(context.user_data['pii_photo_bytes_list'])
    
    keyboard = [
        [
            InlineKeyboardButton("Mahagun", callback_data="pii_cc|Mahagun"),
            InlineKeyboardButton("Gulshan", callback_data="pii_cc|Gulshan"),
            InlineKeyboardButton("Vvip", callback_data="pii_cc|Vvip")
        ],
        [InlineKeyboardButton("🗑️ Clear Pages", callback_data="pii_clear_pages")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    msg_text = f"📸 Received {page_count} page(s).\n\nSend more pages if this is a multi-page invoice. When you are done, select the Cost Centre below to begin processing:"
    
    status_msg_id = context.user_data.get('pii_status_msg_id')
    
    if status_msg_id:
        try:
            await context.bot.edit_message_text(
                text=msg_text,
                chat_id=update.effective_chat.id,
                message_id=status_msg_id,
                reply_markup=reply_markup
            )
        except Exception:
            msg = await (update.message.reply_text(msg_text, reply_markup=reply_markup) if update.message else update.callback_query.message.reply_text(msg_text, reply_markup=reply_markup))
            context.user_data['pii_status_msg_id'] = msg.message_id
    else:
        msg = await (update.message.reply_text(msg_text, reply_markup=reply_markup) if update.message else update.callback_query.message.reply_text(msg_text, reply_markup=reply_markup))
        context.user_data['pii_status_msg_id'] = msg.message_id

async def _run_gemini(update, context, generate_gemini):
    message = update.callback_query.message
    await _safe_edit(message, "Extracting invoice with Vision...")
    
    photo_list = context.user_data.get('pii_photo_bytes_list', [])
    if not photo_list:
        await _safe_edit(message, "Error: No images found in context.")
        return

    try:
        from PIL import Image
        import io
        contents = [_prompt()]
        for p_bytes in photo_list:
            img = Image.open(io.BytesIO(p_bytes)).convert("RGB")
            contents.append(img)
    except Exception as e:
        await _safe_edit(message, f"Error processing images: {e}")
        return
        
    context.user_data['pii_photo_bytes_list'] = []

    # Step 1: Heavy Vision Model
    try:
        raw_text = await generate_gemini(contents, json_mode=True, model_name="gemini-3.5-flash-lite")
    except Exception as e:
        await _safe_edit(message, f"Gemini API Error: {e}")
        return

    raw_text = re.sub(r'^```json\s*', '', raw_text)
    raw_text = re.sub(r'\s*```$', '', raw_text)
    
    try:
        data = repair_json(raw_text, return_objects=True)
    except Exception as e:
        await _safe_edit(message, f"Failed to parse Gemini response: {e}\n\nResponse was:\n{raw_text}")
        return

    if not isinstance(data, dict):
        data = {}
        
    raw_items = data.get("items", [])
    if not isinstance(raw_items, list):
        raw_items = []

    # Step 2: Lightweight Text Model for Matching
    await _safe_edit(message, "Mapping items with Text Model...")
    try:
        stock_cache = load_json(TALLY_STOCK_CACHE)
        tally_item_names = [v.get('name') for k, v in stock_cache.items() if v.get('name')]
        
        master_list_str = "\n".join(f"- {item}" for item in tally_item_names)
        
        map_prompt = f"""
You are a data-mapping assistant. 
I have extracted the following raw items from an invoice: {json.dumps(raw_items)}

Here is my Tally Master Stock List:
{master_list_str}

For each raw item, find the exact matching string from the Tally Master Stock List. You must account for typos, case differences, and spacing (e.g. '400g' vs '400gm').
If a confident match is found, add a key called 'mapped_name' to the item object containing the exact Tally string. 
If no match is found, set 'mapped_name' to the raw name.

Return the entire updated invoice JSON using this schema:
{{
  "items": [
    {{"name": "...", "qty": 0.0, "uom": "pcs", "rate": 0.0, "amount": 0.0, "mapped_name": "..."}}
  ]
}}
"""
        mapped_text = await generate_gemini([map_prompt], json_mode=True, model_name="gemini-3.5-flash-lite")
        mapped_text = re.sub(r'^```json\s*', '', mapped_text)
        mapped_text = re.sub(r'\s*```$', '', mapped_text)
        
        mapped_data = repair_json(mapped_text, return_objects=True)
        if isinstance(mapped_data, dict) and "items" in mapped_data:
            items = mapped_data["items"]
        else:
            items = raw_items
    except Exception as e:
        print(f"Text mapping failed: {e}")
        items = raw_items

    context.user_data['pii_supplier'] = data.get("supplier_name", "Unknown")
    context.user_data['pii_inv_no'] = data.get("invoice_number", "Unknown")
    context.user_data['pii_date'] = data.get("date", "Unknown")
    
    context.user_data['pii_items'] = items
    taxes_dict = data.get("taxes") or {}
    context.user_data['pii_cgst'] = float(taxes_dict.get("cgst", data.get("cgst", 0.0)))
    context.user_data['pii_sgst'] = float(taxes_dict.get("sgst", data.get("sgst", 0.0)))
    context.user_data['pii_igst'] = float(taxes_dict.get("igst", data.get("igst", 0.0)))
    context.user_data['pii_rounding_off'] = float(data.get("rounding_off", 0.0))
    context.user_data['pii_grand_total'] = float(data.get("grand_total", 0.0))
    context.user_data['pii_resolved_items'] = []
    
    await message.delete()
    await _match_party(update, context)

async def _match_party(update, context):
    supplier = context.user_data.get('pii_supplier', 'Unknown')
    cache = load_json(TALLY_LEDGER_CACHE)
    
    if not cache:
        await _process_next_item(update, context)
        return
    
    if supplier.lower() in cache:
        context.user_data['pii_supplier'] = supplier
        await _process_next_item(update, context)
        return
    
    choices = list(cache.keys())
    if fuzz_process and choices:
        matches = fuzz_process.extract(supplier.lower(), choices, limit=2)
        if matches and matches[0][1] >= 95:
            context.user_data['pii_supplier'] = supplier
            await _process_next_item(update, context)
            return
            
        keyboard = []
        for i, m in enumerate(matches):
            display_name = m[0].title()
            context.user_data[f'pii_party_{i}'] = display_name
            keyboard.append([InlineKeyboardButton(
                f"🔗 {display_name} ({m[1]:.0f}%)", 
                callback_data=f"pii_party_{i}"
            )])
        keyboard.append([InlineKeyboardButton("✏️ Type Manually", callback_data="pii_party_manual")])
        keyboard.append([InlineKeyboardButton("➕ Create New Supplier", callback_data="pii_party_create")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        msg = f"🏢 Supplier: {supplier}\n\nNo exact match found in Tally. Select the closest match:"
        if update.callback_query:
            await update.callback_query.message.reply_text(msg, reply_markup=reply_markup)
        elif update.message:
            await update.message.reply_text(msg, reply_markup=reply_markup)
    else:
        await _process_next_item(update, context)
def _get_top_item_candidates(raw_name, tally_list, top_n=2):
    def tokenize(text):
        clean = re.sub(r'[^\w\s]', ' ', text.lower())
        clean = re.sub(r'\bgm\b', 'g', clean)
        clean = re.sub(r'\bkgs\b', 'kg', clean)
        return set(clean.split())
        
    raw_tokens = tokenize(raw_name)
    scored_items = []
    
    for tally_item in tally_list:
        tally_tokens = tokenize(tally_item)
        overlap = len(raw_tokens.intersection(tally_tokens))
        scored_items.append((overlap, tally_item))
        
    scored_items.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored_items if item[0] > 0][:top_n]
async def _process_next_item(update, context):
    while True:
        items = context.user_data.get('pii_items', [])
        if not items:
            await _show_preview(update, context)
            return
            
        current_item = items[0]
        original_name = current_item.get('name', 'Unknown Item')
        norm_name = normalize_item_name(original_name)
        
        stock_cache = load_json(TALLY_STOCK_CACHE)
        aliases = load_json(TALLY_ITEM_ALIASES)
        
        match_found = False
        mapped_name = None
        mapped_unit = current_item.get('uom', 'PCS')

        if norm_name in aliases:
            mapped_name = aliases[norm_name]
            match_found = True
        else:
            for stock_key, stock_data in stock_cache.items():
                if stock_key == norm_name:
                    mapped_name = stock_data.get('name')
                    mapped_unit = stock_data.get('unit', mapped_unit)
                    match_found = True
                    break
                    
        # --- NEW: Robust Exact Match Check ---
        if not match_found:
            clean_raw = original_name.strip().lower()
            for stock_key, stock_data in stock_cache.items():
                t_item = stock_data.get('name', '')
                if t_item.strip().lower() == clean_raw:
                    mapped_name = t_item
                    mapped_unit = stock_data.get('unit', mapped_unit)
                    match_found = True
                    break
        # -------------------------------------
                    
        if match_found:
            await _resolve_item(update, context, original_name, mapped_name, mapped_unit, current_item)
            continue
            
        tally_master_list = [data.get('name') for key, data in stock_cache.items() if data.get('name')]
        candidates = _get_top_item_candidates(original_name, tally_master_list, top_n=2)
        
        keyboard = []
        for candidate in candidates:
            keyboard.append([InlineKeyboardButton(candidate, callback_data=f"map_{candidate[:40]}")])
            
        keyboard.append([InlineKeyboardButton("🔍 Manual Search", callback_data="search_item")])
        keyboard.append([InlineKeyboardButton("➕ Create New Item", callback_data="create_item")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        prompt_text = f"📦 Item: {original_name}\n\nNo exact match found in Tally. Select the closest match:"
        
        if update.callback_query:
            await update.callback_query.message.reply_text(prompt_text, reply_markup=reply_markup)
        elif update.message:
            await update.message.reply_text(prompt_text, reply_markup=reply_markup)
        return

async def _resolve_item(update, context, original_name, mapped_name, mapped_unit, item_data, save_alias=True):
    if save_alias and normalize_item_name(original_name) != normalize_item_name(mapped_name):
        aliases = load_json(TALLY_ITEM_ALIASES)
        aliases[normalize_item_name(original_name)] = mapped_name
        save_json(TALLY_ITEM_ALIASES, aliases)
        
    resolved = {
        'original_name': original_name,
        'mapped_name': mapped_name,
        'qty': float(item_data.get('qty', 0.0)),
        'mapped_unit': mapped_unit,
        'rate': float(item_data.get('rate', 0.0)),
        'amount': float(item_data.get('amount', 0.0)),
        'is_new': item_data.get('is_new', False)
    }
    
    context.user_data['pii_resolved_items'].append(resolved)
    
    items = context.user_data.get('pii_items', [])
    if items:
        items.pop(0)

async def _send_uom_selection(update, context):
    items = context.user_data.get('pii_items', [])
    if not items:
        return
        
    current_item = items[0]
    original_name = current_item.get('name', 'Unknown Item')
    uoms = load_json(TALLY_UOM_CACHE)
    
    keyboard = []
    row = []
    for u in uoms[:9]:
        row.append(InlineKeyboardButton(u, callback_data=f"pii_uom_{u}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    keyboard.append([InlineKeyboardButton("➕ Type Custom UOM", callback_data="pii_newuom")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    msg = f"Create New Item: {original_name}\n\nSelect Unit of Measure:"
    if update.callback_query:
        await _safe_edit(update.callback_query.message, msg, reply_markup=reply_markup)
    elif update.message:
        await update.message.reply_text(msg, reply_markup=reply_markup)

async def _show_preview(update, context):
    cc = context.user_data.get('pii_cc', 'Unknown')
    supplier = context.user_data.get('pii_supplier', 'Unknown')
    inv_no = context.user_data.get('pii_inv_no', 'Unknown')
    date = context.user_data.get('pii_date', 'Unknown')
    grand_total = context.user_data.get('pii_grand_total', 0.0)
    resolved_items = context.user_data.get('pii_resolved_items', [])
    
    msg = f"📋 Invoice Review ({cc})\n"
    msg += f"🏢 Supplier: {supplier}\n"
    msg += f"📅 Date: {date}\n"
    msg += f"🧾 Inv No: {inv_no}\n"
    msg += f"💰 Grand Total: {grand_total:.2f}\n\n"
    
    cgst = float(context.user_data.get('pii_cgst', 0.0))
    sgst = float(context.user_data.get('pii_sgst', 0.0))
    igst = float(context.user_data.get('pii_igst', 0.0))
    rounding_off = float(context.user_data.get('pii_rounding_off', 0.0))
    
    msg += f"⚖️ CGST: {cgst:.2f} | SGST: {sgst:.2f} | IGST: {igst:.2f}\n"
    msg += f"🔄 Rounding Off: {rounding_off:.2f}\n\n"
    
    msg += "📦 Items:\n"
    for i, item in enumerate(resolved_items):
        msg += f"{i+1}. {item['mapped_name']}\n"
        msg += f"   {item['qty']} {item['mapped_unit']} @ {item['rate']:.2f} = {item['amount']:.2f}\n"
        
    keyboard = [
        [InlineKeyboardButton("✏️ Supplier", callback_data="pii_esup"), InlineKeyboardButton("✏️ Date", callback_data="pii_edat")],
        [InlineKeyboardButton("✏️ Inv No", callback_data="pii_einv"), InlineKeyboardButton("✏️ Amount", callback_data="pii_eamt")],
        [InlineKeyboardButton("✏️ CGST", callback_data="pii_ecgst"), InlineKeyboardButton("✏️ SGST", callback_data="pii_esgst")],
        [InlineKeyboardButton("✏️ IGST", callback_data="pii_eigst"), InlineKeyboardButton("✏️ Rounding", callback_data="pii_ernd")]
    ]
    
    for i, item in enumerate(resolved_items):
        name_trunc = item['mapped_name'][:20] + "..." if len(item['mapped_name']) > 20 else item['mapped_name']
        keyboard.append([InlineKeyboardButton(f"✏️ {i+1}: {name_trunc}", callback_data=f"pii_eitem_{i}")])
        
    keyboard.append([InlineKeyboardButton("🚀 Confirm & Push to Tally", callback_data="pii_push")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await _safe_edit(update.callback_query.message, msg, reply_markup=reply_markup)
    elif update.message:
        await update.message.reply_text(msg, reply_markup=reply_markup)
import requests
from workflows.tally_buffer import add_to_queue, TALLY_CACHE_FILE as MASTERS_FILE, TALLY_URL

async def _create_supplier_in_tally(update, context, supplier_name, push_to_tally_fn=None):
    query = update.callback_query
    if query:
        await query.answer()

    # 1. Build the Tally XML for Sundry Creditor Creation
    xml_payload = f"""<ENVELOPE>
        <HEADER>
            <TALLYREQUEST>Import Data</TALLYREQUEST>
        </HEADER>
        <BODY>
            <IMPORTDATA>
                <REQUESTDESC>
                    <REPORTNAME>Vouchers</REPORTNAME>
                </REQUESTDESC>
                <REQUESTDATA>
                    <TALLYMESSAGE xmlns:UDF="TallyUDF">
                        <LEDGER ACTION="Create" NAME="{supplier_name}">
                            <NAME>{supplier_name}</NAME>
                            <PARENT>Sundry Creditors</PARENT>
                            <OPENINGBALANCE>0</OPENINGBALANCE>
                        </LEDGER>
                    </TALLYMESSAGE>
                </REQUESTDATA>
            </IMPORTDATA>
        </BODY>
    </ENVELOPE>"""

    # 2. Push or Queue based on Tally reachability
    try:
        res = requests.post(TALLY_URL, data=xml_payload, timeout=4)
        if query and query.message:
            await query.message.reply_text(f"✅ Created supplier '{supplier_name}' in Tally.")
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        add_to_queue(xml_payload, update.effective_user.id, f"Create Supplier: {supplier_name}")
        if query and query.message:
            await query.message.reply_text(f"⏸ Tally is offline. Supplier '{supplier_name}' queued for auto-creation.")

    # 3. Optimistically update local masters cache
    try:
        if os.path.exists(MASTERS_FILE):
            with open(MASTERS_FILE, "r") as f:
                data = json.load(f)
        else:
            data = {}

        if supplier_name.lower().strip() not in data:
            data[supplier_name.lower().strip()] = {"cost_centre": False, "parent": "Sundry Creditors"}
            with open(MASTERS_FILE, "w") as f:
                json.dump(data, f)
    except Exception:
        pass

    # 4. Save selected supplier to user context and proceed with workflow
    context.user_data['pii_supplier'] = supplier_name
async def _create_item_in_tally(update, context, item_name, uom, push_to_tally_fn=None):
    uom_xml = f"""<ENVELOPE>
        <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
        <BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC><REQUESTDATA>
            <TALLYMESSAGE xmlns:UDF="TallyUDF">
                <UNIT ACTION="Create" NAME="{uom}">
                    <NAME>{uom}</NAME><ISSIMPLEUNIT>Yes</ISSIMPLEUNIT>
                </UNIT>
            </TALLYMESSAGE>
        </REQUESTDATA></IMPORTDATA></BODY>
    </ENVELOPE>"""
    item_xml = f"""<ENVELOPE>
        <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
        <BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC><REQUESTDATA>
            <TALLYMESSAGE xmlns:UDF="TallyUDF">
                <STOCKITEM ACTION="Create" NAME="{item_name}">
                    <NAME.LIST><NAME>{item_name}</NAME></NAME.LIST>
                    <PARENT>Primary</PARENT><BASEUNITS>{uom}</BASEUNITS>
                </STOCKITEM>
            </TALLYMESSAGE>
        </REQUESTDATA></IMPORTDATA></BODY>
    </ENVELOPE>"""
    
    query = update.callback_query
    try:
        requests.post(TALLY_URL, data=uom_xml, timeout=4)
        requests.post(TALLY_URL, data=item_xml, timeout=4)
        if query and query.message:
            await query.message.reply_text(f"✅ Created item '{item_name}' live.")
        elif update.message:
            await update.message.reply_text(f"✅ Created item '{item_name}' live.")
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        add_to_queue(uom_xml, update.effective_user.id, f"Create UOM: {uom}")
        add_to_queue(item_xml, update.effective_user.id, f"Create Item: {item_name}")
        msg = f"⏸ Tally offline. Item '{item_name}' queued for creation."
        if query and query.message:
            await query.message.reply_text(msg)
        elif update.message:
            await update.message.reply_text(msg)
            
    try:
        stock_cache = {}
        if os.path.exists(TALLY_STOCK_CACHE):
            with open(TALLY_STOCK_CACHE, "r") as f:
                stock_cache = json.load(f)
        normalized = item_name.lower().strip()
        if normalized not in stock_cache:
            stock_cache[normalized] = {'name': item_name, 'unit': uom}
            with open(TALLY_STOCK_CACHE, "w") as f:
                json.dump(stock_cache, f)
                
        uoms = []
        if os.path.exists(TALLY_UOM_CACHE):
            with open(TALLY_UOM_CACHE, "r") as f:
                uoms = json.load(f)
        if uom not in uoms:
            uoms.append(uom)
            with open(TALLY_UOM_CACHE, "w") as f:
                json.dump(uoms, f)
    except Exception:
        pass

async def handle_callback(update, context, load_db, save_db, push_to_tally, generate_gemini=None):
    query = update.callback_query
    data = query.data
    
    if not (data.startswith("pii_") or data.startswith("pii_cc|") or data.startswith("map_") or data in ("search_item", "create_item")):
        return False
        
    await query.answer()
    
    if data.startswith("pii_cc|"):
        context.user_data.pop('pii_status_msg_id', None)
        context.user_data['pii_cc'] = data.split("|")[1]
        await _run_gemini(update, context, generate_gemini)
        return True
        
    if data.startswith("pii_party_manual"):
        context.user_data['pii_state'] = 'awaiting_supplier'
        await _safe_edit(query.message, "Type the exact supplier ledger name:")
        return True
        
    if data == "pii_party_create":
        context.user_data['is_new_supplier'] = True
        supplier_name = context.user_data.get('pii_supplier', 'Unknown')
        await _create_supplier_in_tally(update, context, supplier_name, push_to_tally)
        await _process_next_item(update, context)
        return True
        
    if data == "pii_clear_pages":
        context.user_data.pop('pii_status_msg_id', None)
        context.user_data['pii_photo_bytes_list'] = []
        await _safe_edit(query.message, "Cleared all pending invoice pages. Send a new photo to start over.")
        return True
        
    if data.startswith("pii_party_"):
        idx = data.replace("pii_party_", "")
        supplier = context.user_data.get(f'pii_party_{idx}')
        if supplier:
            context.user_data['pii_supplier'] = supplier
        await _process_next_item(update, context)
        return True
        
    if data.startswith("map_"):
        mapped_name = data.replace("map_", "")
        items = context.user_data.get('pii_items', [])
        if items and mapped_name:
            current_item = items[0]
            original_name = current_item.get('name', 'Unknown Item')
            
            stock_cache = load_json(TALLY_STOCK_CACHE)
            mapped_unit = current_item.get('uom', 'PCS')
            for k, v in stock_cache.items():
                # We do a basic startswith match since the callback might have been truncated to 40 chars
                if v.get('name') and v.get('name').startswith(mapped_name):
                    mapped_name = v.get('name')  # Restore full name
                    mapped_unit = v.get('unit', mapped_unit)
                    break
                    
            await _resolve_item(update, context, original_name, mapped_name, mapped_unit, current_item)
            await _process_next_item(update, context)
        return True
        
    if data == "search_item":
        context.user_data['pii_state'] = 'awaiting_item_search'
        await _safe_edit(query.message, "Type part of the item name to search:")
        return True
        
    if data.startswith("pii_sres_"):
        idx = data.replace("pii_sres_", "")
        mapped_name = context.user_data.get(f'pii_sres_{idx}')
        items = context.user_data.get('pii_items', [])
        if items and mapped_name:
            current_item = items[0]
            original_name = current_item.get('name', 'Unknown Item')
            stock_cache = load_json(TALLY_STOCK_CACHE)
            mapped_unit = current_item.get('uom', 'PCS')
            for k, v in stock_cache.items():
                if v.get('name') == mapped_name:
                    mapped_unit = v.get('unit', mapped_unit)
                    break
            await _resolve_item(update, context, original_name, mapped_name, mapped_unit, current_item)
            await _process_next_item(update, context)
        return True
        
    if data == "create_item":
        items = context.user_data.get('pii_items', [])
        if items:
            current_item = items[0]
            original_name = current_item.get('name', 'Unknown Item')
            current_item['is_new'] = True
            await _resolve_item(update, context, original_name, original_name, current_item.get('uom', 'PCS'), current_item)
            await _process_next_item(update, context)
        return True
        
    if data == "pii_namego":
        await _send_uom_selection(update, context)
        return True
        
    if data == "pii_nameed":
        context.user_data['pii_state'] = 'awaiting_new_item_name'
        await _safe_edit(query.message, "Type the exact name for the new stock item:")
        return True
        
    if data.startswith("pii_uom_"):
        uom = data.replace("pii_uom_", "")
        items = context.user_data.get('pii_items', [])
        if items:
            current_item = items[0]
            original_name = current_item.get('name', 'Unknown Item')
            mapped_name = context.user_data.get('pii_new_item_name', original_name)
            
            await _create_item_in_tally(update, context, mapped_name, uom, push_to_tally)
            
            await _resolve_item(update, context, original_name, mapped_name, uom, current_item)
            if 'pii_new_item_name' in context.user_data:
                del context.user_data['pii_new_item_name']
            await _process_next_item(update, context)
        return True
        
    if data == "pii_newuom":
        context.user_data['pii_state'] = 'awaiting_new_uom'
        await _safe_edit(query.message, "Type the new Unit of Measure (e.g., PCS, KG, LTR):")
        return True
        
    if data == "pii_esup":
        context.user_data['pii_state'] = 'awaiting_edit_supplier'
        await _safe_edit(query.message, "Type the new supplier name:")
        return True
        
    if data == "pii_edat":
        context.user_data['pii_state'] = 'awaiting_edit_date'
        await _safe_edit(query.message, "Type the new date (e.g. DD/MM/YYYY):")
        return True
        
    if data == "pii_einv":
        context.user_data['pii_state'] = 'awaiting_edit_inv'
        await _safe_edit(query.message, "Type the new invoice number:")
        return True
        
    if data == "pii_eamt":
        context.user_data['pii_state'] = 'awaiting_edit_amt'
        await _safe_edit(query.message, "Type the new grand total amount:")
        return True
        
    if data == "pii_ecgst":
        context.user_data['pii_state'] = 'awaiting_edit_cgst'
        await _safe_edit(query.message, "Type the new CGST amount:")
        return True
        
    if data == "pii_esgst":
        context.user_data['pii_state'] = 'awaiting_edit_sgst'
        await _safe_edit(query.message, "Type the new SGST amount:")
        return True
        
    if data == "pii_eigst":
        context.user_data['pii_state'] = 'awaiting_edit_igst'
        await _safe_edit(query.message, "Type the new IGST amount:")
        return True
        
    if data == "pii_ernd":
        context.user_data['pii_state'] = 'awaiting_edit_rnd'
        await _safe_edit(query.message, "Type the new Rounding Off amount (e.g., -0.25 or 0.50):")
        return True
        
    if data.startswith("pii_eitem_"):
        idx = int(data.replace("pii_eitem_", ""))
        context.user_data['pii_edit_idx'] = idx
        
        resolved_items = context.user_data.get('pii_resolved_items', [])
        if idx < len(resolved_items):
            item = resolved_items[idx]
            
            msg = f"Editing: {item['mapped_name']}\n"
            msg += f"Qty: {item['qty']}, Rate: {item['rate']:.2f}, Amount: {item['amount']:.2f}\n\n"
            msg += "What do you want to edit?"
            
            keyboard = [
                [InlineKeyboardButton("📝 Name", callback_data=f"pii_eitn_{idx}"), 
                 InlineKeyboardButton("🔢 Qty", callback_data=f"pii_eitq_{idx}"), 
                 InlineKeyboardButton("💰 Rate", callback_data=f"pii_eitr_{idx}")],
                [InlineKeyboardButton("🔙 Back", callback_data="pii_back")]
            ]
            
            await _safe_edit(query.message, msg, reply_markup=InlineKeyboardMarkup(keyboard))
        return True
        
    if data.startswith("pii_eitn_"):
        idx = int(data.replace("pii_eitn_", ""))
        context.user_data['pii_edit_idx'] = idx
        context.user_data['pii_state'] = 'awaiting_item_edit_name'
        await _safe_edit(query.message, "Type the new name for this item:")
        return True
        
    if data.startswith("pii_eitq_"):
        idx = int(data.replace("pii_eitq_", ""))
        context.user_data['pii_edit_idx'] = idx
        context.user_data['pii_state'] = 'awaiting_item_edit_qty'
        await _safe_edit(query.message, "Type the new quantity for this item:")
        return True
        
    if data.startswith("pii_eitr_"):
        idx = int(data.replace("pii_eitr_", ""))
        context.user_data['pii_edit_idx'] = idx
        context.user_data['pii_state'] = 'awaiting_item_edit_rate'
        await _safe_edit(query.message, "Type the new rate for this item:")
        return True
        
    if data == "pii_back":
        await _show_preview(update, context)
        return True
        
    if data == "pii_push":
        await _safe_edit(query.message, "Pushing to Tally...")
        xml_data = _build_xml(context)
        status_code, response_text = push_to_tally(
            xml_data,
            user_id=update.effective_user.id,
            description=f"Purchase Invoice: {context.user_data.get('pii_data', {}).get('invoice_number', 'N/A')} from {context.user_data.get('pii_data', {}).get('supplier_name', 'Unknown')}"
        )
        
        if status_code == 200 and "<ERRORS>0</ERRORS>" in response_text and "<EXCEPTIONS>0</EXCEPTIONS>" in response_text:
            await _safe_edit(query.message, "✅ Successfully pushed Invoice and synced Masters to Tally!")
        elif status_code == 202:
            await _safe_edit(query.message, "⏸ Tally is offline. Saved to queue! It will push automatically when your laptop opens.")
        else:
            await _safe_edit(query.message, f"❌ Failed to push to Tally.\nCode: {status_code}\n\nResponse:\n{response_text}")
            
        return True

    return False

async def receive_text(update, context, generate_gemini, push_to_tally=None):
    state = context.user_data.get('pii_state')
    if not state:
        return False
        
    text = update.message.text.strip()
    
    if state == 'awaiting_supplier':
        context.user_data['pii_supplier'] = text
        context.user_data['pii_state'] = None
        await _process_next_item(update, context)
        return True
        
    if state == 'awaiting_item_search':
        stock_cache = load_json(TALLY_STOCK_CACHE)
        choices = [data.get('name') for key, data in stock_cache.items() if data.get('name')]
        
        matches = [c for c in choices if text.lower() in c.lower()][:5]
        
        if matches:
            keyboard = []
            for i, m in enumerate(matches):
                context.user_data[f'pii_sres_{i}'] = m
                keyboard.append([InlineKeyboardButton(f"🔗 {m}", callback_data=f"pii_sres_{i}")])
            keyboard.append([InlineKeyboardButton("➕ Create New", callback_data="pii_create")])
            
            await update.message.reply_text("Search results:", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            keyboard = [[InlineKeyboardButton("➕ Create New", callback_data="pii_create")]]
            await update.message.reply_text("No matches found.", reply_markup=InlineKeyboardMarkup(keyboard))
            
        context.user_data['pii_state'] = None
        return True
        
    if state == 'awaiting_new_item_name':
        context.user_data['pii_new_item_name'] = text
        context.user_data['pii_state'] = None
        
        items = context.user_data.get('pii_items', [])
        if items:
            current_item = items[0]
            original_name = current_item.get('name', 'Unknown Item')
            
            keyboard = [[InlineKeyboardButton("✅ Proceed", callback_data="pii_namego")]]
            await update.message.reply_text(f"Original: {original_name}\nNew Name: {text}\n\nProceed?", reply_markup=InlineKeyboardMarkup(keyboard))
        return True
        
    if state == 'awaiting_new_uom':
        uom = text.upper()
        context.user_data['pii_state'] = None
        
        items = context.user_data.get('pii_items', [])
        if items:
            current_item = items[0]
            original_name = current_item.get('name', 'Unknown Item')
            mapped_name = context.user_data.get('pii_new_item_name', original_name)
            
            await _create_item_in_tally(update, context, mapped_name, uom, push_to_tally)
            stock_cache = load_json(TALLY_STOCK_CACHE)
            stock_cache[normalize_item_name(mapped_name)] = {'name': mapped_name, 'unit': uom}
            save_json(TALLY_STOCK_CACHE, stock_cache)
            
            uoms = load_json(TALLY_UOM_CACHE)
            if uom not in uoms:
                uoms.append(uom)
                save_json(TALLY_UOM_CACHE, uoms)
                
            await _resolve_item(update, context, original_name, mapped_name, uom, current_item)
            if 'pii_new_item_name' in context.user_data:
                del context.user_data['pii_new_item_name']
                
            await msg.delete()
            await _process_next_item(update, context)
        return True
        
    if state == 'awaiting_edit_supplier':
        context.user_data['pii_supplier'] = text
        context.user_data['pii_state'] = None
        await _show_preview(update, context)
        return True
        
    if state == 'awaiting_edit_date':
        parsed = dateparser.parse(text, settings={'DATE_ORDER': 'DMY', 'PREFER_DATES_FROM': 'current_period'})
        if parsed:
            context.user_data['pii_date'] = parsed.strftime("%d-%m-%Y")
        else:
            context.user_data['pii_date'] = text
        context.user_data['pii_state'] = None
        await _show_preview(update, context)
        return True
        
    if state == 'awaiting_edit_inv':
        context.user_data['pii_inv_no'] = text
        context.user_data['pii_state'] = None
        await _show_preview(update, context)
        return True
        
    if state == 'awaiting_edit_amt':
        try:
            context.user_data['pii_grand_total'] = float(text)
        except ValueError:
            pass
        context.user_data['pii_state'] = None
        await _show_preview(update, context)
        return True
        
    if state == 'awaiting_edit_cgst':
        try:
            context.user_data['pii_cgst'] = float(text)
        except ValueError:
            pass
        context.user_data['pii_state'] = None
        await _show_preview(update, context)
        return True
        
    if state == 'awaiting_edit_sgst':
        try:
            context.user_data['pii_sgst'] = float(text)
        except ValueError:
            pass
        context.user_data['pii_state'] = None
        await _show_preview(update, context)
        return True
        
    if state == 'awaiting_edit_igst':
        try:
            context.user_data['pii_igst'] = float(text)
        except ValueError:
            pass
        context.user_data['pii_state'] = None
        await _show_preview(update, context)
        return True
        
    if state == 'awaiting_edit_rnd':
        try:
            context.user_data['pii_rounding_off'] = float(text)
        except ValueError:
            pass
        context.user_data['pii_state'] = None
        await _show_preview(update, context)
        return True
        
    if state == 'awaiting_item_edit_name':
        idx = context.user_data.get('pii_edit_idx')
        if idx is not None:
            resolved_items = context.user_data.get('pii_resolved_items', [])
            if idx < len(resolved_items):
                resolved_items[idx]['mapped_name'] = text
        context.user_data['pii_state'] = None
        await _show_preview(update, context)
        return True
        
    if state == 'awaiting_item_edit_qty':
        idx = context.user_data.get('pii_edit_idx')
        if idx is not None:
            resolved_items = context.user_data.get('pii_resolved_items', [])
            if idx < len(resolved_items):
                try:
                    qty = float(text)
                    resolved_items[idx]['qty'] = qty
                    resolved_items[idx]['amount'] = qty * resolved_items[idx]['rate']
                except ValueError:
                    pass
        context.user_data['pii_state'] = None
        await _show_preview(update, context)
        return True
        
    if state == 'awaiting_item_edit_rate':
        idx = context.user_data.get('pii_edit_idx')
        if idx is not None:
            resolved_items = context.user_data.get('pii_resolved_items', [])
            if idx < len(resolved_items):
                try:
                    rate = float(text)
                    resolved_items[idx]['rate'] = rate
                    resolved_items[idx]['amount'] = resolved_items[idx]['qty'] * rate
                except ValueError:
                    pass
        context.user_data['pii_state'] = None
        await _show_preview(update, context)
        return True

    return False

def _build_xml(context) -> str:
    cc = context.user_data.get('pii_cc', 'Mahagun')
    supplier = _sanitize_xml(context.user_data.get('pii_supplier', 'Unknown'))
    inv_no = _sanitize_xml(context.user_data.get('pii_inv_no', 'Unknown'))
    
    date_str = context.user_data.get('pii_date', '')
    try:
        dt = datetime.datetime.strptime(date_str, "%d-%m-%Y")
        tally_date = dt.strftime("%Y%m%d")
    except ValueError:
        tally_date = datetime.datetime.now().strftime("%Y%m%d")
        
    extracted_grand_total = float(context.user_data.get('pii_grand_total', 0.0))
    cgst = float(context.user_data.get('pii_cgst', 0.0))
    sgst = float(context.user_data.get('pii_sgst', 0.0))
    igst = float(context.user_data.get('pii_igst', 0.0))
    extracted_rounding = float(context.user_data.get('pii_rounding_off', 0.0))
    
    resolved_items = context.user_data.get('pii_resolved_items', [])
    
    inventory_xml = ""
    item_subtotal = 0.0
    
    for item in resolved_items:
        name = _sanitize_xml(item['mapped_name'])
        qty = item['qty']
        unit = _sanitize_xml(item['mapped_unit'])
        rate = item['rate']
        amount = item['amount']
        
        item_subtotal += amount
        
        inventory_xml += f"""
        <INVENTORYENTRIES.LIST>
            <STOCKITEMNAME>{name}</STOCKITEMNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <RATE>{rate}</RATE>
            <AMOUNT>-{amount:.2f}</AMOUNT>
            <ACTUALQTY> {qty} {unit}</ACTUALQTY>
            <BILLEDQTY> {qty} {unit}</BILLEDQTY>
            <ACCOUNTINGALLOCATIONS.LIST>
                <LEDGERNAME>Purchase</LEDGERNAME>
                <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                <AMOUNT>-{amount:.2f}</AMOUNT>
                <CATEGORYALLOCATIONS.LIST>
                    <CATEGORY>Primary Cost Category</CATEGORY>
                    <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                    <COSTCENTREALLOCATIONS.LIST>
                        <NAME>{_sanitize_xml(cc)}</NAME>
                        <AMOUNT>-{amount:.2f}</AMOUNT>
                    </COSTCENTREALLOCATIONS.LIST>
                </CATEGORYALLOCATIONS.LIST>
            </ACCOUNTINGALLOCATIONS.LIST>
        </INVENTORYENTRIES.LIST>"""

    calculated_grand_total = item_subtotal + cgst + sgst + igst + extracted_rounding
    
    ledger_xml = f"""
        <LEDGERENTRIES.LIST>
            <LEDGERNAME>{supplier}</LEDGERNAME>
            <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
            <AMOUNT>{calculated_grand_total:.2f}</AMOUNT>
            <BILLALLOCATIONS.LIST>
                <NAME>{inv_no}</NAME>
                <BILLTYPE>New Ref</BILLTYPE>
                <AMOUNT>{calculated_grand_total:.2f}</AMOUNT>
            </BILLALLOCATIONS.LIST>
        </LEDGERENTRIES.LIST>"""
        
    if cgst > 0:
        ledger_xml += f"""
        <LEDGERENTRIES.LIST>
            <LEDGERNAME>Input CGST</LEDGERNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <AMOUNT>-{cgst:.2f}</AMOUNT>
        </LEDGERENTRIES.LIST>"""
        
    if sgst > 0:
        ledger_xml += f"""
        <LEDGERENTRIES.LIST>
            <LEDGERNAME>Input SGST</LEDGERNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <AMOUNT>-{sgst:.2f}</AMOUNT>
        </LEDGERENTRIES.LIST>"""
        
    if igst > 0:
        ledger_xml += f"""
        <LEDGERENTRIES.LIST>
            <LEDGERNAME>Input IGST</LEDGERNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <AMOUNT>-{igst:.2f}</AMOUNT>
        </LEDGERENTRIES.LIST>"""

    if abs(extracted_rounding) >= 0.01:
        is_debit = "Yes" if extracted_rounding > 0 else "No"
        amount_str = f"-{abs(extracted_rounding):.2f}" if extracted_rounding > 0 else f"{abs(extracted_rounding):.2f}"
        ledger_xml += f"""
        <LEDGERENTRIES.LIST>
            <LEDGERNAME>Rounding Off</LEDGERNAME>
            <ISDEEMEDPOSITIVE>{is_debit}</ISDEEMEDPOSITIVE>
            <AMOUNT>{amount_str}</AMOUNT>
        </LEDGERENTRIES.LIST>"""

    guid = f"PII-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"

    master_xml = """
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <LEDGER ACTION="Alter" NAME="Input CGST">
                        <NAME.LIST><NAME>Input CGST</NAME></NAME.LIST>
                        <PARENT>Duties &amp; Taxes</PARENT>
                        <TAXTYPE>GST</TAXTYPE>
                        <GSTDUTYHEAD>Central Tax</GSTDUTYHEAD>
                    </LEDGER>
                </TALLYMESSAGE>
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <LEDGER ACTION="Alter" NAME="Input SGST">
                        <NAME.LIST><NAME>Input SGST</NAME></NAME.LIST>
                        <PARENT>Duties &amp; Taxes</PARENT>
                        <TAXTYPE>GST</TAXTYPE>
                        <GSTDUTYHEAD>State Tax</GSTDUTYHEAD>
                    </LEDGER>
                </TALLYMESSAGE>
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <LEDGER ACTION="Alter" NAME="Input IGST">
                        <NAME.LIST><NAME>Input IGST</NAME></NAME.LIST>
                        <PARENT>Duties &amp; Taxes</PARENT>
                        <TAXTYPE>GST</TAXTYPE>
                        <GSTDUTYHEAD>Integrated Tax</GSTDUTYHEAD>
                    </LEDGER>
                </TALLYMESSAGE>
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <LEDGER ACTION="Alter" NAME="Rounding Off">
                        <NAME.LIST><NAME>Rounding Off</NAME></NAME.LIST>
                        <PARENT>Indirect Expenses</PARENT>
                        <ROUNDINGMETHOD>Normal Rounding</ROUNDINGMETHOD>
                        <ROUNDLIMIT>1</ROUNDLIMIT>
                    </LEDGER>
                </TALLYMESSAGE>"""
                
    if context.user_data.get('is_new_supplier'):
        master_xml += f"""
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <LEDGER ACTION="Alter" NAME="{supplier}">
                        <NAME.LIST><NAME>{supplier}</NAME></NAME.LIST>
                        <PARENT>Sundry Creditors</PARENT>
                    </LEDGER>
                </TALLYMESSAGE>"""
                
    for item in resolved_items:
        if item.get('is_new'):
            uom = _sanitize_xml(item.get('mapped_unit', 'PCS'))
            item_name = _sanitize_xml(item.get('mapped_name', 'Unknown Item'))
            master_xml += f"""
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <UNIT ACTION="Alter" NAME="{uom}">
                        <NAME>{uom}</NAME>
                        <ISSIMPLEUNIT>Yes</ISSIMPLEUNIT>
                    </UNIT>
                </TALLYMESSAGE>
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <STOCKITEM ACTION="Alter" NAME="{item_name}">
                        <NAME.LIST><NAME>{item_name}</NAME></NAME.LIST>
                        <PARENT>Primary</PARENT>
                        <BASEUNITS>{uom}</BASEUNITS>
                    </STOCKITEM>
                </TALLYMESSAGE>"""

    xml = f"""<ENVELOPE>
    <HEADER>
        <TALLYREQUEST>Import Data</TALLYREQUEST>
    </HEADER>
    <BODY>
        <IMPORTDATA>
            <REQUESTDESC>
                <REPORTNAME>All Masters</REPORTNAME>
                <STATICVARIABLES>
                    <SVCURRENTCOMPANY>##SVCURRENTCOMPANY</SVCURRENTCOMPANY>
                </STATICVARIABLES>
            </REQUESTDESC>
            <REQUESTDATA>{master_xml}
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <VOUCHER VCHTYPE="Purchase" ACTION="Create">
                        <DATE>{tally_date}</DATE>
                        <GUID>{guid}</GUID>
                        <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
                        <REFERENCE>{inv_no}</REFERENCE>
                        <VOUCHERNUMBER>{inv_no}</VOUCHERNUMBER>
                        <PARTYLEDGERNAME>{supplier}</PARTYLEDGERNAME>
                        <PARTYNAME>{supplier}</PARTYNAME>
                        <PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>
                        <ISINVOICE>Yes</ISINVOICE>
                        {inventory_xml}
                        {ledger_xml}
                    </VOUCHER>
                </TALLYMESSAGE>
            </REQUESTDATA>
        </IMPORTDATA>
    </BODY>
</ENVELOPE>"""
    return xml.encode('utf-8')

def _generate_unit_xml(unit_name: str) -> str:
    safe_name = _sanitize_xml(unit_name)
    return f"""<ENVELOPE>
    <HEADER>
        <TALLYREQUEST>Import Data</TALLYREQUEST>
    </HEADER>
    <BODY>
        <IMPORTDATA>
            <REQUESTDESC>
                <REPORTNAME>All Masters</REPORTNAME>
                <STATICVARIABLES>
                    <SVCURRENTCOMPANY>##SVCURRENTCOMPANY</SVCURRENTCOMPANY>
                </STATICVARIABLES>
            </REQUESTDESC>
            <REQUESTDATA>
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <UNIT NAME="{safe_name}" ACTION="Create">
                        <NAME>{safe_name}</NAME>
                        <ORIGINALNAME>{safe_name}</ORIGINALNAME>
                        <ISSIMPLEUNIT>Yes</ISSIMPLEUNIT>
                    </UNIT>
                </TALLYMESSAGE>
            </REQUESTDATA>
        </IMPORTDATA>
    </BODY>
</ENVELOPE>""".encode('utf-8')

def _generate_stock_item_xml(item_name: str, unit_name: str) -> str:
    safe_name = _sanitize_xml(item_name)
    safe_unit = _sanitize_xml(unit_name)
    return f"""<ENVELOPE>
    <HEADER>
        <TALLYREQUEST>Import Data</TALLYREQUEST>
    </HEADER>
    <BODY>
        <IMPORTDATA>
            <REQUESTDESC>
                <REPORTNAME>All Masters</REPORTNAME>
                <STATICVARIABLES>
                    <SVCURRENTCOMPANY>##SVCURRENTCOMPANY</SVCURRENTCOMPANY>
                </STATICVARIABLES>
            </REQUESTDESC>
            <REQUESTDATA>
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <STOCKITEM NAME="{safe_name}" ACTION="Create">
                        <NAME>{safe_name}</NAME>
                        <BASEUNITS>{safe_unit}</BASEUNITS>
                    </STOCKITEM>
                </TALLYMESSAGE>
            </REQUESTDATA>
        </IMPORTDATA>
    </BODY>
</ENVELOPE>""".encode('utf-8')
