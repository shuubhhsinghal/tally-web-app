import os
from dotenv import load_dotenv

load_dotenv()
import json
import difflib
import asyncio
from io import BytesIO
import openpyxl
import datetime
from PIL import Image
import xml.etree.ElementTree as ET
import urllib.parse

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from google import genai
from google.genai import types
import re
import requests
from workflows import purchase as purchase_workflow
from workflows import sales as sales_workflow
from workflows import payment as payment_workflow
from workflows import transfer as transfer_workflow
from workflows import purchase_inventory as purchase_inventory_workflow
from workflows import purchase_item_interactive as pii_workflow
from workflows.bank_statement import bank_statement_handlers, BANK_STATEMENT_LEDGERS, handle_rule_ledger_selection, handle_viewrules_account
from workflows.tally_buffer import add_to_queue, get_queue, clear_queue, fetch_and_cache_masters, TALLY_CACHE_FILE
from workflows.stock_transfer import handle_transfer_message, handle_transfer_callback, stock_transfer_router


TALLY_URL = os.getenv("TALLY_URL", "http://100.125.198.3:9000")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

import pandas as pd
from xml.sax.saxutils import escape

# A single async Gemini client is shared by all Telegram handlers.  The old
# `google-generativeai` SDK is legacy; this uses Google's supported `google-genai` SDK.
gemini_client = None


def configure_gemini(api_key):
    """Configure Gemini once during application startup."""
    global gemini_client
    gemini_client = genai.Client(api_key=api_key)


async def generate_gemini(contents, *, json_mode=False, model_name=None):
    """Generate a Gemini response without blocking Telegram's event loop."""
    import time
    if gemini_client is None:
        raise RuntimeError("Gemini is not configured. Set GEMINI_API_KEY and restart the bot.")

    config = types.GenerateContentConfig(
        response_mime_type="application/json" if json_mode else None,
        temperature=0.0 if json_mode else None
    )

    model_to_use = model_name if model_name else GEMINI_MODEL

    # Detailed Instrumentation
    prompt_str = str(contents)
    prompt_len = len(prompt_str)
    
    try:
        token_resp = await gemini_client.aio.models.count_tokens(model=model_to_use, contents=contents)
        est_tokens = token_resp.total_tokens
    except Exception as e:
        est_tokens = f"Error counting tokens: {e}"

    print(f"\n{'='*50}\n--- GEMINI API REQUEST DETAILS ---")
    print(f"1. Exact model ID: {model_to_use}")
    print(f"2. Prompt size: {prompt_len} characters")
    print(f"3. Estimated input tokens: {est_tokens}")
    print(f"4. Thinking config: None (default API behavior)")
    print(f"5. Tool config: None (default API behavior)")
    print(f"Complete Prompt Context Sent:\n{prompt_str}\n{'-'*50}")
    
    start_time = time.time()
    print(f"6. Request start time: {start_time}")
    
    first_byte_time = None
    full_text = ""
    
    try:
        response_stream = await gemini_client.aio.models.generate_content_stream(
            model=model_to_use,
            contents=contents,
            config=config,
        )
        async for chunk in response_stream:
            if first_byte_time is None:
                first_byte_time = time.time()
                print(f"7. First byte received at: {first_byte_time} (TTFB: {first_byte_time - start_time:.2f}s)")
            
            try:
                chunk_text = chunk.text if chunk.text else ""
            except ValueError:
                chunk_text = ""
                print(f"⚠️ Gemini response blocked or empty. Safety ratings: {chunk.prompt_feedback}")
                
            full_text += f"{chunk_text}"
            
        end_time = time.time()
        print(f"8. Response completion time: {end_time}")
        print(f"9. Total latency: {end_time - start_time:.2f}s")
        print(f"{'='*50}\n")
        
        if not full_text:
            raise ValueError("Gemini returned an empty response.")
        return full_text
        
    except Exception as e:
        end_time = time.time()
        print(f"!!! GEMINI API EXCEPTION at {end_time} (Total latency before failure: {end_time - start_time:.2f}s)")
        print(f"Exception: {e}")
        print(f"{'='*50}\n")
        raise

def sanitize_xml(text):
    if text is None:
        return ""
    return escape(str(text), {'"': "&quot;", "'": "&apos;"})

# Bank Mapping Rules Configuration
BANK_SUSPENSE_LEDGER = "Bank Suspense Account"

LEDGERS_FILE = 'ledgers.json'

def load_ledgers_config():
    if not os.path.exists(LEDGERS_FILE):
        return {
            "TALLY_LEDGERS": [],
            "COST_CENTER_LEDGERS": [],
            "CONTRA_LEDGERS": []
        }
    with open(LEDGERS_FILE, 'r') as f:
        return json.load(f)

def save_ledgers_config(config):
    with open(LEDGERS_FILE, 'w') as f:
        json.dump(config, f, indent=4)

_ledgers_config = load_ledgers_config()
TALLY_LEDGERS = _ledgers_config.get("TALLY_LEDGERS", [])
CONTRA_LEDGERS = _ledgers_config.get("CONTRA_LEDGERS", [])



# ---------------------------------------------------------
# State Management
# ---------------------------------------------------------
MAIN_MENU, TRANSACTIONS_MENU, WAIT_FOR_INVOICE = range(3)

# Persistent DB for queues
def get_db_file(mode):
    if mode == "Purchase": return "purchases_db.json"
    if mode == "Sales": return "sales_db.json"
    return "default_db.json"

def load_db(mode):
    db_file = get_db_file(mode)
    if not os.path.exists(db_file):
        return {}
    with open(db_file, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_db(data, mode):
    db_file = get_db_file(mode)
    with open(db_file, "w") as f:
        json.dump(data, f, indent=4)

RULES_FILE = "rules.json"




def sanitize_tally_xml(raw_xml: str) -> str:
    # 1. Remove DECIMAL invalid XML entities (e.g., &#29;, &#11;, &#29)
    clean_xml = re.sub(r'&#0*([0-8]|1[1-2]|1[4-9]|2[0-9]|3[0-1]);?', '', raw_xml)
    
    # 2. Remove HEX invalid XML entities (e.g., &#x1D;, &#x0B;)
    clean_xml = re.sub(r'&#x0*([0-8b-ce-f]|1[0-9a-f]);?', '', clean_xml, flags=re.IGNORECASE)
    
    # 3. Remove literal unescaped control characters (keeping \t, \n, \r)
    clean_xml = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', clean_xml)
    
    return clean_xml

async def sync_tally_ledgers(tally_url=TALLY_URL):
    import os
    try:
        await asyncio.to_thread(fetch_and_cache_masters, tally_url)
        if os.path.exists(TALLY_CACHE_FILE):
            with open(TALLY_CACHE_FILE, 'r', encoding="utf-8") as f:
                return len(json.load(f)), None
        return 0, None
    except Exception as e:
        return 0, str(e)

def requires_cost_centre(ledger_name: str) -> bool:
    if not ledger_name:
        return False
    try:
        with open(TALLY_CACHE_FILE, 'r') as f:
            cache = json.load(f)
            val = cache.get(ledger_name.strip().lower(), False)
            if isinstance(val, dict):
                return val.get("cost_centre", False)
            return bool(val)
    except (FileNotFoundError, json.JSONDecodeError):
        return False

TALLY_STOCK_CACHE_FILE = "tally_stock_cache.json"

async def sync_tally_stock_items(tally_url=TALLY_URL):
    payload = """<ENVELOPE>
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
        response = await asyncio.to_thread(requests.post, tally_url, data=payload, headers={'Content-Type': 'text/xml'}, timeout=15)
        response.raise_for_status()
        
        safe_xml_text = sanitize_tally_xml(response.text)
        root = ET.fromstring(safe_xml_text)
        stock_cache = {}
        
        for item_elem in root.findall('.//STOCKITEM'):
            name = None
            name_elem = item_elem.find('NAME')
            if name_elem is not None and name_elem.text:
                name = name_elem.text.strip()
            elif item_elem.get('NAME'):
                name = item_elem.get('NAME').strip()
                
            if not name:
                continue
                
            unit = None
            unit_elem = item_elem.find('BASEUNITS')
            if unit_elem is not None and unit_elem.text:
                unit = unit_elem.text.strip()
            elif item_elem.get('BASEUNITS'):
                unit = item_elem.get('BASEUNITS').strip()
                
            stock_cache[name.lower()] = {"name": name, "unit": unit}
            
        with open(TALLY_STOCK_CACHE_FILE, 'w', encoding="utf-8") as f:
            json.dump(stock_cache, f, indent=4)
            
        return len(stock_cache), None
    except Exception as e:
        return 0, str(e)

TALLY_UOM_CACHE_FILE = "tally_uom_cache.json"

async def sync_tally_uom(tally_url=TALLY_URL):
    payload = """<ENVELOPE>
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
        response = await asyncio.to_thread(requests.post, tally_url, data=payload, headers={'Content-Type': 'text/xml'}, timeout=15)
        response.raise_for_status()
        
        safe_xml_text = sanitize_tally_xml(response.text)
        root = ET.fromstring(safe_xml_text)
        uom_list = []
        
        for item_elem in root.findall('.//UNIT'):
            name = None
            name_elem = item_elem.find('NAME')
            if name_elem is not None and name_elem.text:
                name = name_elem.text.strip()
            elif item_elem.get('NAME'):
                name = item_elem.get('NAME').strip()
                
            if name and name not in uom_list:
                uom_list.append(name)
                
        with open(TALLY_UOM_CACHE_FILE, 'w', encoding="utf-8") as f:
            json.dump(uom_list, f, indent=4)
            
        return len(uom_list), None
    except Exception as e:
        return 0, str(e)
async def perform_master_sync():
    """Silent background sync for Tally Masters"""
    try:
        await sync_tally_ledgers()
        await sync_tally_stock_items()
        await sync_tally_uom()
    except Exception as e:
        print(f"Background master sync failed: {e}")

async def sync_masters_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔄 Syncing masters from Tally via Tailscale...")
    ledgers_count, ledgers_err = await sync_tally_ledgers()
    stock_count, stock_err = await sync_tally_stock_items()
    uom_count, uom_err = await sync_tally_uom()
    
    err_msgs = []
    if ledgers_err:
        err_msgs.append(f"Ledgers: {ledgers_err}")
    if stock_err:
        err_msgs.append(f"Stock Items: {stock_err}")
    if uom_err:
        err_msgs.append(f"UOMs: {uom_err}")
        
    if err_msgs:
        await msg.edit_text(f"❌ Failed to sync some masters:\n" + "\n".join(err_msgs))
    else:
        await msg.edit_text(f"✅ Synced {ledgers_count} ledgers, {stock_count} stock items, and {uom_count} UOMs from Tally!")

# user_current: { user_id: invoice_dict }
user_current = {}
# user_edit_state: { user_id: "amount" | "inv_no" | "supplier" | "narration" }
user_edit_state = {}
# user_mode: { user_id: "Purchase" | "Sales" | "Payment" }
user_mode = {}
# user_payment_type: { user_id: "Expense" | "Others" }
user_payment_type = {}


# ---------------------------------------------------------
# OpenAI Integration
# ---------------------------------------------------------
async def call_gemini(contents, mode="Purchase"):
    if mode == "Sales":
        system_prompt = (
            "You are an expert OCR extractor. Extract data to JSON format: {\"records\": [...]}\n"
            "Keys per record:\n"
            "- 'date' (string): DD-MM-YYYY. Default to today.\n"
            "- 'amount' (float): Total amount.\n"
            "- 'cost_center' (string): 'Mahagun', 'Vvip', or 'Gulshan'. Default 'None'.\n"
            "- 'payment_type' (string): 'cash' or 'online'. Default 'cash'.\n\n"
            "Rules:\n"
            "If Cost Center is mentioned once, apply it to ALL records in the message.\n"
            "Pair payment type with adjacent amounts (e.g. 'gulshan cash 4000 online 12000' -> 2 records, both Gulshan).\n"
            f"Current year: {datetime.datetime.now().year}, today: {datetime.datetime.now().strftime('%d-%m-%Y')}.\n"
            "Return ONLY valid JSON."
        )
    elif mode == "Payment":
        system_prompt = (
            "Extract details for a Tally Payment voucher: amount, date, account, paid_from, cost_center, narration.\n\n"
            "RULES:\n"
            "1. AMOUNT & ACCOUNT: If the amount is below 200, strictly set 'account' to \"Petty Expense\". Otherwise, extract the logical expense/supplier ledger name.\n"
            "2. DATE: Convert natural language to 'DD-MM-YYYY'. Default to today's date if missing.\n"
            "3. PAID_FROM: Must match one of these exactly: \"Cash Mahagun\", \"Cash Vvip\", \"Cash Gulshan\", \"Union Bank Mahagun 133\", \"Union Bank Vvip 2170\", \"Federal Bank Gulshan\". If unclear, return null.\n"
            "4. COST_CENTER: Map to \"Mahagun\", \"Vvip\", or \"Gulshan\". If unclear, return null.\n"
            "5. NARRATION: Return the exact raw text the user typed.\n\n"
            "Return ONLY a valid JSON object with keys: 'amount', 'date', 'account', 'paid_from', 'cost_center', 'narration'. Do not include markdown formatting."
        )
    else:
        system_prompt = (
            "You are an expert OCR extractor. Extract data to JSON object:\n"
            "- 'supplier' (string): Vendor name.\n"
            "- 'date' (string): DD-MM-YYYY.\n"
            "- 'inv_no' (string): Invoice number.\n"
            "- 'amount' (float): Total amount.\n\n"
            "Rules:\n"
            "1. Plain numbers: larger=amount, smaller=inv_no.\n"
            "2. Avoid 0.0 for amount if valid candidate exists.\n"
            "3. Add current year to day/month dates.\n"
            f"Current year: {datetime.datetime.now().year}.\n"
            "Missing strings='Unknown', missing amount=0.0.\n"
            "Return ONLY valid JSON."
        )

    full_contents = [system_prompt] + contents

    text = await generate_gemini(full_contents, json_mode=True)
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^```\s*', '', text, flags=re.MULTILINE)
    
    return json.loads(text.strip())


# ---------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------
def get_sales_account(payment_type, cost_center):
    pt = payment_type.lower()
    cc = cost_center.lower()
    
    if pt == "cash":
        if cc == "mahagun": return "cash mahagun"
        elif cc == "vvip": return "cash vvip"
        elif cc == "gulshan": return "cash gulshan"
        else: return "Unknown Cash"
    else:
        # online/bank/card/gpay
        if cc == "vvip" and ("gpay" in pt or "google pay" in pt):
            return "Gpay Vvip"
        if cc == "mahagun": return "Paytm Mahagun"
        elif cc == "vvip": return "Paytm Vvip"
        elif cc == "gulshan": return "Paytm Gulshan"
        else: return "Unknown Paytm"


def get_next_invoice_number():
    counter_file = "counter.txt"
    if not os.path.exists(counter_file):
        count = 1
    else:
        with open(counter_file, "r") as f:
            try:
                count = int(f.read().strip()) + 1
            except ValueError:
                count = 1
    with open(counter_file, "w") as f:
        f.write(str(count))
    return count

async def send_preview(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    if user_mode.get(user_id) == "Purchase":
        await purchase_workflow.show_preview(update, context)
        return
    if user_mode.get(user_id) == "Sales":
        await sales_workflow.show_preview(update, context)
        return
    if user_mode.get(user_id) == "Payment":
        await payment_workflow.show_preview(update, context)
        return

    data = user_current.get(user_id)
    if not data:
        return
        
    mode = user_mode.get(user_id, "Purchase")
    if mode == "Sales":
        emoji = "📈"
    elif mode == "Payment":
        emoji = "💳"
    else:
        emoji = "🛒"
    mode_text = f"Active Mode: {emoji} {mode}"
    
    if mode == "Sales":
        # Recalculate account just in case cost_center or payment_type changed
        pt = data.get('payment_type', 'cash')
        cc = data.get('cost_center', 'None')
        account = get_sales_account(pt, cc)
        data['account'] = account
        
        text = (
            f"**{mode_text}**\n\n"
            "📄 *Extracted Sales Data*:\n"
            f"• ACCOUNT: {account}\n"
            f"• PAYMENT TYPE: {pt.title()}\n"
            f"• AMOUNT: {data.get('amount', 0.0)}\n"
            f"• DATE: {data.get('date', 'Unknown')}\n"
            f"• COST CENTER: {cc}"
        )
        
        keyboard = [
            [
                InlineKeyboardButton("Mahagun", callback_data="cc_Mahagun"),
                InlineKeyboardButton("Vvip", callback_data="cc_Vvip"),
                InlineKeyboardButton("Gulshan", callback_data="cc_Gulshan")
            ],
            [
                InlineKeyboardButton("✏️ Edit Amount", callback_data="edit_amount"),
                InlineKeyboardButton("✏️ Edit Date", callback_data="edit_date")
            ],
            [
                InlineKeyboardButton("🚀 Push to Tally", callback_data="push_sales_tally")
            ]
        ]

    else:
        text = (
            f"**{mode_text}**\n\n"
            "📄 *Extracted Invoice Data*:\n"
            f"• SUPPLIER: {data.get('supplier', 'Unknown')}\n"
            f"• DATE: {data.get('date', 'Unknown')}\n"
            f"• INV NO: {data.get('inv_no', 'Unknown')}\n"
            f"• AMOUNT: {data.get('amount', 0.0)}\n"
            f"• COST CENTER: {data.get('cost_center', 'None')}"
        )
        
        keyboard = [
            [
                InlineKeyboardButton("Mahagun", callback_data="cc_Mahagun"),
                InlineKeyboardButton("Vvip", callback_data="cc_Vvip"),
                InlineKeyboardButton("Gulshan", callback_data="cc_Gulshan")
            ],
            [
                InlineKeyboardButton("✏️ Edit Amount", callback_data="edit_amount"),
                InlineKeyboardButton("✏️ Edit Inv No", callback_data="edit_inv_no")
            ],
            [
                InlineKeyboardButton("✏️ Edit Supplier", callback_data="edit_supplier"),
                InlineKeyboardButton("✏️ Edit Date", callback_data="edit_date")
            ],
            [
                InlineKeyboardButton("➕ Save & Add Next", callback_data="save_next"),
                InlineKeyboardButton("🚀 Push to Tally", callback_data="push_purchase_tally")
            ]
        ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")


async def process_invoice_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    user_id = update.effective_user.id
    mode = user_mode.get(user_id, "Purchase")

    if mode == "Purchase":
        await purchase_workflow.process_text(update, context, generate_gemini)
        return
    if mode == "Sales":
        await sales_workflow.process_text(update, context, generate_gemini)
        return
    if mode == "Payment":
        await payment_workflow.process_text(update, context, generate_gemini)
        return
    if mode == "Transfer":
        await transfer_workflow.process_text(update, context, generate_gemini)
        return

    
        processing_msg = await update.message.reply_text("Processing invoice text with Gemini...")
        try:
            contents = [text]
            extracted_data = await call_gemini(contents, mode=mode)
        except Exception as e:
            await processing_msg.edit_text(f"Error processing text: {str(e)}")
            return
            
    try:
        
        if mode == "Sales":
            records = extracted_data.get("records", [])
            if not records:
                # Fallback if AI didn't format as list
                records = [extracted_data]
                
            if len(records) > 1:
                db = load_db(mode)
                str_uid = str(user_id)
                if str_uid not in db:
                    db[str_uid] = []
                    
                summary_lines = []
                for i, rec in enumerate(records, 1):
                    pt = str(rec.get("payment_type", "cash")).lower()
                    cc = str(rec.get("cost_center", "None")).title()
                    account = get_sales_account(pt, cc)
                    amount = float(rec.get("amount", 0.0) if rec.get("amount") is not None else 0.0)
                    
                    record_dict = {
                        "date": str(rec.get("date", "Unknown")),
                        "amount": amount,
                        "cost_center": cc,
                        "payment_type": pt,
                        "account": account
                    }
                    db[str_uid].append(record_dict)
                    summary_lines.append(f"{i}. {amount} - {account}")
                    
                save_db(db, mode)
                await processing_msg.delete()
                
                keyboard = [[InlineKeyboardButton("📊 Generate Excel File", callback_data="generate_excel")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(
                    f"✅ Added {len(records)} Sales Entries to queue:\n" + "\n".join(summary_lines),
                    reply_markup=reply_markup
                )
            else:
                rec = records[0]
                user_current[user_id] = {
                    "date": str(rec.get("date", "Unknown")),
                    "amount": float(rec.get("amount", 0.0) if rec.get("amount") is not None else 0.0),
                    "cost_center": str(rec.get("cost_center", "None")).title(),
                    "payment_type": str(rec.get("payment_type", "cash")).lower()
                }
                await processing_msg.delete()
                await send_preview(update, context, user_id)
        else:
            user_current[user_id] = {
                "supplier": str(extracted_data.get("supplier", "Unknown")),
                "date": str(extracted_data.get("date", "Unknown")),
                "inv_no": str(extracted_data.get("inv_no", "Unknown")),
                "amount": float(extracted_data.get("amount", 0.0) if extracted_data.get("amount") is not None else 0.0),
                "cost_center": "None"
            }
            await processing_msg.delete()
            await send_preview(update, context, user_id)
    except Exception as e:
        await processing_msg.edit_text(f"Error processing text: {str(e)}")


async def process_invoice_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, photo_bytes: bytearray):
    user_id = update.effective_user.id
    mode = user_mode.get(user_id, "Purchase")
    if mode == "Purchase":
        await purchase_workflow.process_photo(update, context, photo_bytes, generate_gemini)
        return
    if mode == "Purchase_Inventory":
        await purchase_inventory_workflow.process_photo(update, context, photo_bytes, generate_gemini)
        return
    if mode == "Purchase_Item_Interactive":
        await pii_workflow.process_photo(update, context, photo_bytes, generate_gemini)
        return
    if mode == "Sales":
        await sales_workflow.process_photo(update, context, photo_bytes, generate_gemini)
        return
    if mode == "Payment":
        await payment_workflow.process_photo(update, context, photo_bytes, generate_gemini)
        return

    processing_msg = await update.message.reply_text("Processing invoice image with Gemini...")
    try:
        # Open image with Pillow
        img = Image.open(BytesIO(photo_bytes))
        if img.mode != 'RGB':
            img = img.convert('RGB')
            
        contents = ["Extract the details from this invoice.", img]
        extracted_data = await call_gemini(contents, mode=mode)
        
        # Explicit cleanup to prevent memory leaks
        del contents
        del img
        
        if mode == "Sales":
            records = extracted_data.get("records", [])
            if not records:
                # Fallback if AI didn't format as list
                records = [extracted_data]
                
            if len(records) > 1:
                db = load_db(mode)
                str_uid = str(user_id)
                if str_uid not in db:
                    db[str_uid] = []
                    
                summary_lines = []
                for i, rec in enumerate(records, 1):
                    pt = str(rec.get("payment_type", "cash")).lower()
                    cc = str(rec.get("cost_center", "None")).title()
                    account = get_sales_account(pt, cc)
                    amount = float(rec.get("amount", 0.0) if rec.get("amount") is not None else 0.0)
                    
                    record_dict = {
                        "date": str(rec.get("date", "Unknown")),
                        "amount": amount,
                        "cost_center": cc,
                        "payment_type": pt,
                        "account": account
                    }
                    db[str_uid].append(record_dict)
                    summary_lines.append(f"{i}. {amount} - {account}")
                    
                save_db(db, mode)
                await processing_msg.delete()
                
                keyboard = [[InlineKeyboardButton("📊 Generate Excel File", callback_data="generate_excel")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(
                    f"✅ Added {len(records)} Sales Entries to queue:\n" + "\n".join(summary_lines),
                    reply_markup=reply_markup
                )
            else:
                rec = records[0]
                user_current[user_id] = {
                    "date": str(rec.get("date", "Unknown")),
                    "amount": float(rec.get("amount", 0.0) if rec.get("amount") is not None else 0.0),
                    "cost_center": str(rec.get("cost_center", "None")).title(),
                }
                await processing_msg.delete()
                await send_preview(update, context, user_id)
        else:
            user_current[user_id] = {
                "supplier": str(extracted_data.get("supplier", "Unknown")),
                "date": str(extracted_data.get("date", "Unknown")),
                "inv_no": str(extracted_data.get("inv_no", "Unknown")),
                "amount": float(extracted_data.get("amount", 0.0) if extracted_data.get("amount") is not None else 0.0),
                "cost_center": "None"
            }
            await processing_msg.delete()
            await send_preview(update, context, user_id)
    except Exception as e:
        await processing_msg.edit_text(f"Error processing photo: {str(e)}")


# ---------------------------------------------------------
# Message Handlers
# ---------------------------------------------------------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    if user_mode.get(user_id) == "Purchase" and await purchase_workflow.receive_pending_field(update, context, generate_gemini):
        return WAIT_FOR_INVOICE
    if user_mode.get(user_id) == "Purchase_Inventory" and await purchase_inventory_workflow.receive_text(update, context, generate_gemini, push_to_tally=push_to_tally):
        return WAIT_FOR_INVOICE
    if user_mode.get(user_id) == "Purchase_Item_Interactive" and await pii_workflow.receive_text(update, context, generate_gemini, push_to_tally=push_to_tally):
        return WAIT_FOR_INVOICE
    if user_mode.get(user_id) == "Sales" and await sales_workflow.receive_edit(update, context, generate_gemini):
        return WAIT_FOR_INVOICE
    if user_mode.get(user_id) == "Payment" and await payment_workflow.receive_edit(update, context, generate_gemini):
        return WAIT_FOR_INVOICE
    
    # Check if user is currently editing a field
    if user_id in user_edit_state:
        field = user_edit_state[user_id]
        if field == "amount":
            try:
                user_current[user_id][field] = float(text)
            except ValueError:
                await update.message.reply_text("Please enter a valid number for amount.")
                return
        else:
            if field.lower() == 'date':
                import datetime as dt
                current_date = dt.datetime.now().strftime("%d %B %Y")
                current_year = dt.datetime.now().year
                prompt = f"The current date is {current_date}. The user typed a date: '{text}'. Convert this into a strict 'DD-MM-YYYY' format. If the year is missing, assume {current_year}. Return ONLY the 'DD-MM-YYYY' string, nothing else."
                try:
                    formatted_date = (await generate_gemini(prompt)).strip()
                    user_current[user_id][field] = formatted_date
                except Exception as e:
                    user_current[user_id][field] = text.strip()
            else:
                user_current[user_id][field] = text.strip()
        # Clear edit state and refresh preview
        del user_edit_state[user_id]
        await send_preview(update, context, user_id)
    else:
        # Process as a new invoice text
        await process_invoice_text(update, context, text)
        
    return WAIT_FOR_INVOICE


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # If a new photo is sent, cancel any ongoing edit state
    if user_id in user_edit_state:
        del user_edit_state[user_id]
        
    photo_file = await update.message.photo[-1].get_file()
    photo_bytes = await photo_file.download_as_bytearray()
    
    await process_invoice_photo(update, context, photo_bytes)
    return WAIT_FOR_INVOICE


# ---------------------------------------------------------
# Callback Handler
# ---------------------------------------------------------
async def invoice_edit_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "main_menu":
        await start(update, context)
        return MAIN_MENU

    if user_mode.get(user_id) == "Purchase":
        handled = await purchase_workflow.handle_callback(
            update,
            context,
            load_db=load_db,
            save_db=save_db,
            push_to_tally=push_to_tally,
        )
        if handled:
            return WAIT_FOR_INVOICE
    if user_mode.get(user_id) == "Purchase_Inventory":
        handled = await purchase_inventory_workflow.handle_callback(
            update,
            context,
            load_db=load_db,
            save_db=save_db,
            push_to_tally=push_to_tally,
            generate_gemini=generate_gemini
        )
        if handled:
            return WAIT_FOR_INVOICE
    if user_mode.get(user_id) == "Purchase_Item_Interactive":
        handled = await pii_workflow.handle_callback(
            update,
            context,
            load_db=load_db,
            save_db=save_db,
            push_to_tally=push_to_tally,
            generate_gemini=generate_gemini
        )
        if handled:
            return WAIT_FOR_INVOICE
    if user_mode.get(user_id) == "Sales":
        handled = await sales_workflow.handle_callback(update, context, push_to_tally=push_to_tally)
        if handled:
            return WAIT_FOR_INVOICE
    if user_mode.get(user_id) == "Payment":
        handled = await payment_workflow.handle_callback(update, context, push_to_tally=push_to_tally)
        if handled:
            return WAIT_FOR_INVOICE
    if data.startswith("transfer_"):
        handled = await transfer_workflow.handle_callback(update, context, push_to_tally=push_to_tally)
        if handled:
            return WAIT_FOR_INVOICE
        
    # Validate session
    if user_id not in user_current and data != "generate_excel":
        await query.message.reply_text("No active invoice session found. Please send an image or text first.")
        return WAIT_FOR_INVOICE
        
    # Cost Center Update
    elif data.startswith("cc_"):
        cc = data.split("_", 1)[1]
        if user_id in user_current:
            user_current[user_id]["cost_center"] = cc
            await send_preview(update, context, user_id)
        
    # Edit Action
    elif data.startswith("edit_"):
        field = data.split("_", 1)[1]
        user_edit_state[user_id] = field
        await query.message.reply_text(f"Please type the new value for {field.replace('_', ' ').title()}:")
        
    # Save & Add Next
    elif data == "save_next":
        mode = user_mode.get(user_id, "Purchase")
        db = load_db(mode)
        str_uid = str(user_id)
        if str_uid not in db:
            db[str_uid] = []
        db[str_uid].append(user_current[user_id])
        save_db(db, mode)
        
        del user_current[user_id]
        
        total = len(db[str_uid])
        if mode == "Sales":
            success_msg = (
                f"Sales entry added to queue (Total: {total}). Type the next entry.\n\n"
                "Format example: [Amount] [Cash/Online] [Cost Center]\n"
                "Example: 3000 cash mahagun"
            )
        else:
            success_msg = (
                f"Invoice added to queue (Total: {total}). Send the next photo or text.\n\n"
                "If sending text, please use this format:\n"
                "Supplier: [Name]\n"
                "Date: [DD-MM-YYYY]\n"
                "Inv No: [Number]\n"
                "Amount: [Value]\n"
                "Cost Center: [Name]"
            )
        keyboard = [[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(success_msg, reply_markup=reply_markup)
        
    # Generate Excel File
    elif data == "generate_excel":
        await do_export(context, user_id)
        
    # Push Purchase to Tally
    elif data == "push_purchase_tally":
        if user_id in user_current:
            invoice_data = user_current[user_id]
            xml_data = generate_session_purchase_xml(invoice_data)
            status_code, response_text = push_to_tally(xml_data)
            if status_code == 200:
                inv = invoice_data.get('inv_no', 'N/A')
                sup = invoice_data.get('supplier', 'Unknown')
                await query.message.edit_text(f"✅ Success! Invoice {inv} from {sup} pushed to Tally.")
                del user_current[user_id]
            elif status_code == 500:
                kb = [
                    [InlineKeyboardButton("📥 Download XML", callback_data="download_xml_purchase")],
                    [InlineKeyboardButton("🚀 Retry Push to Tally", callback_data="push_purchase_tally")]
                ]
                await query.message.edit_text(
                    "⚠️ Shop PC is offline or Tally is closed.\nYour transaction data has been saved locally!",
                    reply_markup=InlineKeyboardMarkup(kb)
                )
            else:
                await query.message.edit_text(f"⚠️ Failed to push to Tally (HTTP {status_code}). Please fix the supplier name or details.\nResponse: {response_text[:200]}")
                
    # Push Sales to Tally
    elif data == "push_sales_tally":
        if user_id in user_current:
            sales_data = user_current[user_id]
            xml_data = generate_session_sales_xml(sales_data)
            status_code, response_text = push_to_tally(xml_data)
            if status_code == 200:
                amt = sales_data.get('amount', 0)
                cc = sales_data.get('cost_center', 'None')
                await query.message.edit_text(f"✅ Success! Sales voucher of {amt} for {cc} pushed to Tally.")
                del user_current[user_id]
            elif status_code == 500:
                kb = [
                    [InlineKeyboardButton("📥 Download XML", callback_data="download_xml_sales")],
                    [InlineKeyboardButton("🚀 Retry Push to Tally", callback_data="push_sales_tally")]
                ]
                await query.message.edit_text(
                    "⚠️ Shop PC is offline or Tally is closed.\nYour transaction data has been saved locally!",
                    reply_markup=InlineKeyboardMarkup(kb)
                )
            else:
                await query.message.edit_text(f"⚠️ Failed to push to Tally (HTTP {status_code}).\nResponse: {response_text[:200]}")
                


    elif data == "download_xml_purchase":
        if user_id in user_current:
            xml_data = generate_session_purchase_xml(user_current[user_id])
            await context.bot.send_document(
                chat_id=user_id,
                document=BytesIO(xml_data),
                filename="purchase_voucher.xml",
                caption="Here is your offline XML file."
            )
            
    elif data == "download_xml_sales":
        if user_id in user_current:
            xml_data = generate_session_sales_xml(user_current[user_id])
            await context.bot.send_document(
                chat_id=user_id,
                document=BytesIO(xml_data),
                filename="sales_voucher.xml",
                caption="Here is your offline XML file."
            )
            

    return WAIT_FOR_INVOICE




def get_tally_transaction_xml(vch_type, debit_ledger, credit_ledger, amount, narration):
    date_str = datetime.datetime.now().strftime("%Y%m%d")
    escaped_narration = sanitize_xml(str(narration)) if narration else ""
    escaped_debit = sanitize_xml(str(debit_ledger))
    escaped_credit = sanitize_xml(str(credit_ledger))
    
    xml = f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE>
          <VOUCHER VCHTYPE="{vch_type}" ACTION="Create">
            <DATE>{date_str}</DATE>
            <NARRATION>{escaped_narration}</NARRATION>
            <VOUCHERTYPENAME>{vch_type}</VOUCHERTYPENAME>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{escaped_debit}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{amount}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{escaped_credit}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{amount}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""
    return xml.encode('utf-8')

def generate_purchase_xml(intent):
    party = intent.get('party_ledger', 'Unknown Party')
    amount = intent.get('amount', 0)
    narration = intent.get('narration', '')
    return get_tally_transaction_xml("Purchase", "Purchases", party, amount, narration)

def generate_sales_xml(intent):
    party = intent.get('party_ledger', 'Unknown Party')
    amount = intent.get('amount', 0)
    narration = intent.get('narration', '')
    return get_tally_transaction_xml("Sales", party, "Sales", amount, narration)


    return xml.encode('utf-8')


def generate_session_purchase_xml(invoice_data):
    """Compatibility wrapper; Purchase XML now belongs to its workflow module."""
    return purchase_workflow.build_xml(invoice_data)

def generate_session_sales_xml(sales_data):
    """Compatibility wrapper; Sales XML now belongs to its workflow module."""
    return sales_workflow.build_xml(sales_data)








def push_to_tally(xml_bytes, user_id=None, description="Offline Entry"):
    try:
        response = requests.post(TALLY_URL, data=xml_bytes, headers={'Content-Type': 'text/xml'}, timeout=15)
        
        resp_text = response.text
        if "<CREATED>0</CREATED>" in resp_text:
            error_msg = "Unknown Tally Error"
            import re
            match = re.search(r'<LINEERROR>(.*?)</LINEERROR>', resp_text)
            if match:
                error_msg = match.group(1)
            else:
                match = re.search(r'<ERROR>(.*?)</ERROR>', resp_text)
                if match:
                    error_msg = match.group(1)
            return 400, f"Tally Rejected: {error_msg}"
            
        return response.status_code, response.text
    except requests.exceptions.ConnectionError:
        if user_id:
            add_to_queue(xml_bytes, user_id, description)
            return 202, "QUEUED" # HTTP 202 Accepted
        return 503, "Service Unavailable (Offline)"
    except Exception as e:
        return 500, str(e)







# ---------------------------------------------------------
# Bot Initialization
# ---------------------------------------------------------


async def do_export(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    mode = user_mode.get(user_id, "Purchase")
    if mode == "Purchase":
        await purchase_workflow.export_queue(context, user_id, load_db, save_db, get_next_invoice_number)
        return
    if mode == "Sales":
        await sales_workflow.export_queue(context, user_id, load_db, save_db, get_next_invoice_number)
        return
    db = load_db(mode)
    str_uid = str(user_id)
    
    if str_uid not in db:
        db[str_uid] = []
        
    # Append current working invoice to queue if not already added
    if user_id in user_current:
        db[str_uid].append(user_current[user_id])
        save_db(db, mode)
        del user_current[user_id]
        
    if not db[str_uid]:
        await context.bot.send_message(chat_id=user_id, text=f"No invoices to generate Excel for {mode}. Please add an invoice first.")
        return
        
    # Create Excel File in memory
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = mode
    
    # Write Headers
    if mode == "Sales":
        headers = [
            "DATE", "ACCOUNT", "SALES", "COST CENTER", "VCH TYPE", "AMOUNT", "accounting mode"
        ]
    else:
        headers = [
            "SUPPLIER", "DATE", "INV NO", "AMOUNT",
            "PURCHASE LEDGER", "COST CENTER", "ACCOUNTING MODE", "INV TYPE"
        ]
    ws.append(headers)
    
    # Write Rows
    for inv in db[str_uid]:
        if mode == "Sales":
            pt = inv.get("payment_type", "cash")
            cc = inv.get("cost_center", "None")
            account = get_sales_account(pt, cc)
            row = [
                inv.get("date", "Unknown"),
                account,
                "SALES",
                cc,
                "SALES",
                inv.get("amount", 0.0),
                "Accounting Invoice"
            ]
        else:
            row = [
                inv.get("supplier", "Unknown"),
                inv.get("date", "Unknown"),
                inv.get("inv_no", "Unknown"),
                inv.get("amount", 0.0),
                "Purchase",
                inv.get("cost_center", "None"),
                "As Voucher",
                "Purchase"
            ]
        ws.append(row)
        
    excel_file = BytesIO()
    wb.save(excel_file)
    excel_file.seek(0)
    
    # Generate dynamic filename
    count = get_next_invoice_number()
    dynamic_filename = f"{mode}_{count}.xlsx"
    
    # Send Document
    keyboard = [[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_document(
        chat_id=user_id,
        document=excel_file,
        filename=dynamic_filename,
        caption=f"Here is your generated {mode} Excel file: {dynamic_filename}",
        reply_markup=reply_markup
    )
    
    # Clear queues
    db[str_uid] = []
    save_db(db, mode)

async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await do_export(context, user_id)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_edit_state.pop(user_id, None)
    welcome_text = (
        "👋 Welcome to the Invoice Scanner Bot!\n\n"
        "(Your Tally masters are being synced in the background!)\n\n"
        "Please select an option below:"
    )
    keyboard = [
        [
            InlineKeyboardButton("📊 Bank Statement", callback_data="menu_bank_statement"),
            InlineKeyboardButton("💸 Transactions", callback_data="menu_transactions")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(welcome_text, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=reply_markup)
        
    return MAIN_MENU

async def main_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    user_edit_state.pop(user_id, None)
    
    if data == "menu_transactions":
        keyboard = [
            [
                InlineKeyboardButton("🛒 Purchases", callback_data="submenu_purchases")
            ],
            [
                InlineKeyboardButton("📈 Sales", callback_data="setmode_Sales"),
                InlineKeyboardButton("💳 Payment", callback_data="setmode_Payment")
            ],
            [
                InlineKeyboardButton("🔄 Fund Transfer", callback_data="setmode_Transfer")
            ],
            [
                InlineKeyboardButton("📦 Stock Transfer", callback_data="setmode_StockTransfer")
            ],
            [
                InlineKeyboardButton("🔙 Back", callback_data="main_menu")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("Select a transaction type:", reply_markup=reply_markup)
        return TRANSACTIONS_MENU
        
    elif data == "menu_bank_statement":
        keyboard = []
        for b in BANK_STATEMENT_LEDGERS:
            keyboard.append([InlineKeyboardButton(b, callback_data=f"bank_{b}")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("Select the Bank Ledger for this statement:", reply_markup=reply_markup)
        return ConversationHandler.END
        
    return MAIN_MENU

async def transactions_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    
    if data == "main_menu":
        return await start(update, context)
        
    if data == "menu_transactions":
        keyboard = [
            [
                InlineKeyboardButton("🛒 Purchases", callback_data="submenu_purchases")
            ],
            [
                InlineKeyboardButton("📈 Sales", callback_data="setmode_Sales"),
                InlineKeyboardButton("💳 Payment", callback_data="setmode_Payment")
            ],
            [
                InlineKeyboardButton("📦 Stock Transfer", callback_data="setmode_StockTransfer")
            ],
            [
                InlineKeyboardButton("🔙 Back", callback_data="main_menu")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("Select a transaction type:", reply_markup=reply_markup)
        return TRANSACTIONS_MENU
        
    if data == "submenu_purchases":
        keyboard = [
            [
                # [InlineKeyboardButton("🛒 Item-Wise", callback_data="setmode_Purchase_Inventory")],
                InlineKeyboardButton("🛒 Accounting", callback_data="setmode_Purchase")
            ],
            [
                InlineKeyboardButton("📸 Item-Wise", callback_data="setmode_Purchase_Item_Interactive")
            ],
            [
                InlineKeyboardButton("🔙 Back to Transactions", callback_data="menu_transactions")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("Select Purchase mode:", reply_markup=reply_markup)
        return TRANSACTIONS_MENU
        
    if data.startswith("setmode_"):
        user_edit_state.pop(user_id, None)
        mode = data.split("_", 1)[1]
        user_mode[user_id] = mode
        if mode == "Purchase":
            msg = (
                "🛒 *Purchase mode ready.* Send an image of the invoice, or if you prefer to send text, please use the following comma-separated format:\n\n"
                "• Supplier Name\n"
                "• Invoice Number\n"
                "• Date\n"
                "• Amount\n\n"
                "*Example Text Format:*\n"
                "`ABC Traders, Inv 101, 27-07-2026, 15000`"
            )
        elif mode == "Payment":
            msg = "💸 *Payment mode ready.* What type of payment is this?"
            keyboard = [
                [InlineKeyboardButton("📉 Expense", callback_data="ptype_Expense"), InlineKeyboardButton("🔄 Others", callback_data="ptype_Others")],
                [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
            return TRANSACTIONS_MENU
        elif mode == "Sales":
            msg = (
                "📈 *Sales mode ready.* Send your sales as a text message.\n\n"
                "*Examples:*\n"
                "`cash sales mahagun 4000`\n"
                "`cash sale gulshan 4000 yesterday`\n\n"
                "Please send one sales entry per message."
            )
        elif mode == "Purchase_Inventory":
            msg = (
                "🛒 *Item-Wise Purchase Mode*\n\n"
                "Please upload the purchase invoice (PDF/Image) or type the details to begin extraction."
            )
        elif mode == "Purchase_Item_Interactive":
            msg = (
                "📸 *Scan Invoice Mode*\n\n"
                "Upload a purchase invoice photo. I'll extract items, match them with your Tally masters, "
                "and let you review everything before pushing."
            )
        elif mode == "Transfer":
            msg = (
                "🔄 *Fund Transfer mode ready.*\n\n"
                "Type your transfer instruction. Example:\n"
                "`2000 from cash mahagun to cash vvip`"
            )
        elif mode == "StockTransfer":
            msg = (
                "📦 *Stock Transfer mode ready.*\n\n"
                "Type your stock transfer instruction. Example:\n"
                "`I transferred 7 pcs of item a from mahagun to vvip`"
            )
        else:
            msg = f"**{mode}** mode ready."
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
        return WAIT_FOR_INVOICE
        
    elif data.startswith("ptype_"):
        user_edit_state.pop(user_id, None)
        ptype = data.split("_", 1)[1]
        payment_workflow.set_payment_type(context, ptype)
        msg = f"💸 *{ptype} Payment mode ready.* Send an image of the receipt, or type the details naturally."
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
        return WAIT_FOR_INVOICE
        
    return TRANSACTIONS_MENU










async def addledger_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /addledger <Ledger Name>")
        return
        
    ledger = " ".join(args).strip()
    
    if ledger not in TALLY_LEDGERS:
        TALLY_LEDGERS.append(ledger)
        _ledgers_config["TALLY_LEDGERS"] = TALLY_LEDGERS
        save_ledgers_config(_ledgers_config)
        await update.message.reply_text(f"✅ Added '{ledger}' to your master ledgers.")
    else:
        await update.message.reply_text(f"⚠️ '{ledger}' is already in your master ledgers.")

async def addexpense_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cost Centres are now dynamically synced from Tally! Please run /sync_masters to update your ledgers instead.")

async def removeledger_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /removeledger <Ledger Name>")
        return
        
    ledger = " ".join(args).strip()
    
    global _ledgers_config, TALLY_LEDGERS, CONTRA_LEDGERS
    _ledgers_config = load_ledgers_config()
    TALLY_LEDGERS = _ledgers_config.get("TALLY_LEDGERS", [])
    CONTRA_LEDGERS = _ledgers_config.get("CONTRA_LEDGERS", [])
    
    removed = False
    
    if ledger in TALLY_LEDGERS:
        TALLY_LEDGERS.remove(ledger)
        _ledgers_config["TALLY_LEDGERS"] = TALLY_LEDGERS
        removed = True
        
    if ledger in CONTRA_LEDGERS:
        CONTRA_LEDGERS.remove(ledger)
        _ledgers_config["CONTRA_LEDGERS"] = CONTRA_LEDGERS
        removed = True
        
    if removed:
        save_ledgers_config(_ledgers_config)
        await update.message.reply_text(f"✅ '{ledger}' has been removed from your ledger lists.")
    else:
        await update.message.reply_text(f"⚠️ '{ledger}' was not found in your ledgers.")

async def parse_intent(user_text: str) -> dict:
    prompt = f"""You are a strict JSON intent parser for a Tally ERP bot. 
Extract the user's intent into a JSON object with the following keys:
- 'action': (must be 'add_rule', 'remove_rule', 'add_ledger', 'remove_ledger', 'view_rules', 'view_ledgers', 'record_purchase', or 'record_sales')
- 'keyword': (the target keyword, lowercase)
- 'ledger': (the target Tally ledger)
- 'cost_center': (the target cost center, or null if none)
- 'is_expense': (boolean, true if adding an expense/cost center ledger)
- 'amount': (the numerical value of the transaction)
- 'party_ledger': (the name of the customer, supplier, or payee)
- 'narration': (a brief description of the transaction)

If the user is recording a transaction, set the action to 'record_purchase', or 'record_sales'. Extract the 'amount' as a number, 'party_ledger' as a string, and 'narration' as a string. If the user doesn't specify a party, try to infer it from the narration.

Do not return any text outside of the JSON object.

User Text: {user_text}"""
    response_text = await generate_gemini(prompt, json_mode=True)
    return json.loads(response_text.strip())

async def process_natural_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _ledgers_config, TALLY_LEDGERS, CONTRA_LEDGERS
    
    orig_text = update.message.text
    try:
        intent = await parse_intent(orig_text)
    except Exception as e:
        print(f"Intent Parse Error: {e}")
        await update.message.reply_text(f"⚠️ System Error: {str(e)}")
        return
        
    action = intent.get("action")
    
    _ledgers_config = load_ledgers_config()
    TALLY_LEDGERS = _ledgers_config.get("TALLY_LEDGERS", [])
    CONTRA_LEDGERS = _ledgers_config.get("CONTRA_LEDGERS", [])
    
    if action == "add_rule":
        keyword = intent.get("keyword", "").strip().lower()
        ledger_str = intent.get("ledger", "").strip()
        cost_center_str = intent.get("cost_center")
        if isinstance(cost_center_str, str):
            cost_center_str = cost_center_str.strip()
            
        await validate_and_prompt_rule(update, context, "add", keyword, ledger_str, cost_center_str)
        
    elif action == "remove_rule":
        keyword = intent.get("keyword", "").strip().lower()
        await validate_and_prompt_rule(update, context, "delete", keyword)
            
    elif action == "add_ledger":
        ledger = intent.get("ledger", "").strip()
        is_expense = intent.get("is_expense", False)
        
        if not ledger:
            return
            
        changed = False
        if ledger not in TALLY_LEDGERS:
            TALLY_LEDGERS.append(ledger)
            changed = True
            
        if changed:
            save_ledgers_config(_ledgers_config)
            
        if is_expense:
            await update.message.reply_text(f"✅ Understood. I added '{ledger}' to your master ledgers. Note: Cost centre status is now dynamically managed via Tally.")
        else:
            await update.message.reply_text(f"✅ Understood. I added '{ledger}' to your master ledgers.")
            
    elif action == "remove_ledger":
        ledger = intent.get("ledger", "").strip()
        
        removed = False
        if ledger in TALLY_LEDGERS:
            TALLY_LEDGERS.remove(ledger)
            removed = True
        if ledger in CONTRA_LEDGERS:
            CONTRA_LEDGERS.remove(ledger)
            removed = True
            
        if removed:
            save_ledgers_config(_ledgers_config)
            await update.message.reply_text(f"✅ Understood. I have removed '{ledger}' from your ledger lists.")
        else:
            await update.message.reply_text(f"⚠️ '{ledger}' was not found in your ledgers.")
    elif action == "view_rules":
        await viewrules_command(update, context)
        
    elif action == "view_ledgers":
        lines = ["**Master Ledgers:**"]
        for l in TALLY_LEDGERS:
            lines.append(f"- {l}")
            
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        
    elif action in ["record_purchase", "record_sales"]:
        if action == "record_purchase":
            xml_bytes = generate_purchase_xml(intent)
        elif action == "record_sales":
            xml_bytes = generate_sales_xml(intent)
            
        status_code, response_text = push_to_tally(xml_bytes)
        
        party = intent.get('party_ledger', 'Unknown Party')
        amt = intent.get('amount', 0)
        await update.message.reply_text(f"✅ Transaction processed: {action} with {party} for {amt}.\n📡 Tally Sync: HTTP {status_code}")
        
    else:
        await update.message.reply_text("⚠️ Sorry, I didn't understand that command.")


async def post_init(application: Application):
    asyncio.create_task(perform_master_sync())
    await application.bot.set_my_commands([
        BotCommand("start", "Open main menu"),
        BotCommand("addrule", "Add a bank mapping rule"),
        BotCommand("delrule", "Delete a bank mapping rule"),
        BotCommand("viewrules", "View all mapping rules")
    ])

import time
async def tally_heartbeat_worker(context: ContextTypes.DEFAULT_TYPE):
    current_time = time.strftime('%H:%M:%S')
    
    print(f"[{current_time}] 💓 Pinging Tally server...") # Logs every 60 seconds
    
    # 1. Micro-Ping
    try:
        requests.get(TALLY_URL, timeout=3)
        tally_online = True
    except Exception:
        tally_online = False

    if not tally_online:
        print(f"[{current_time}] 🔴 Tally is offline. Going back to sleep.")
        return # Stop executing the rest of the function since Tally is offline

    if tally_online:
        # 2. Flush Offline Queue
        queue = get_queue()
        if queue:
            print(f"[{current_time}] 🔄 Tally is online. Found {len(queue)} offline entries. Pushing now...")
            success_count = 0
            user_to_notify = None
            
            for item in list(queue): # Iterate over copy since we might break
                if not user_to_notify:
                    user_to_notify = item.get("user_id")
                try:
                    resp = requests.post(TALLY_URL, data=item["xml"], headers={'Content-Type': 'text/xml'}, timeout=5)
                    if "<CREATED>1</CREATED>" in resp.text or "<CREATED>" in resp.text:
                        success_count += 1
                except Exception:
                    break # Tally died mid-queue
            
            if success_count > 0:
                clear_queue()
                print(f"[{current_time}] ✅ Successfully pushed {success_count} entries from queue to Tally!")
                if user_to_notify:
                    await context.bot.send_message(
                        chat_id=user_to_notify, 
                        text=f"🔄 Tally is back online! Successfully pushed {success_count} offline entries."
                    )
        
        # 3. Gentle Auto-Sync (Every 5 minutes)
        last_sync = 0
        if os.path.exists(TALLY_CACHE_FILE):
            last_sync = os.path.getmtime(TALLY_CACHE_FILE)
                
        # 300 seconds = 5 minutes
        if time.time() - last_sync > 300:
            print(f"[{current_time}] ⏳ 5 minutes passed. Syncing latest masters from Tally...")
            await asyncio.to_thread(fetch_and_cache_masters, TALLY_URL)
            print(f"[{current_time}] ✅ Tally masters synced and saved to local cache!")

def main():
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    gemini_key = os.environ.get("GEMINI_API_KEY")
    
    if not bot_token:
        print("Please set TELEGRAM_BOT_TOKEN environment variable.")
        return
    if not gemini_key:
        print("Please set GEMINI_API_KEY environment variable.")
        return
        
    configure_gemini(gemini_key)
    application = Application.builder().token(bot_token).post_init(post_init).build()
    
    if application.job_queue:
        application.job_queue.run_repeating(tally_heartbeat_worker, interval=60, first=10)

    master_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [CallbackQueryHandler(main_menu_cb)],
            TRANSACTIONS_MENU: [CallbackQueryHandler(transactions_menu_cb)],
            WAIT_FOR_INVOICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
                MessageHandler(filters.PHOTO, handle_photo),
                CallbackQueryHandler(invoice_edit_cb)
            ],
        },
        fallbacks=[CommandHandler("start", start)]
    )
    
    application.bot_data['push_to_tally'] = push_to_tally
    
    application.add_handler(master_conv_handler)
    for h in bank_statement_handlers:
        application.add_handler(h)
        
    application.add_handler(CallbackQueryHandler(handle_rule_ledger_selection, pattern="^rule_ledger:"), group=1)
    application.add_handler(CallbackQueryHandler(handle_viewrules_account, pattern="^viewrules_acct:"), group=1)
    application.add_handler(CallbackQueryHandler(handle_transfer_callback, pattern="^st_"), group=-1)
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(start, pattern="^main_menu$"))
    application.add_handler(CommandHandler("addledger", addledger_command))
    application.add_handler(CommandHandler("addexpense", addexpense_command))
    application.add_handler(CommandHandler("removeledger", removeledger_command))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, stock_transfer_router), group=-1)
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_natural_language))

    print("Bot is polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
