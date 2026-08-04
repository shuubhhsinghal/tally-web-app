import os
import json
import base64
from io import BytesIO
import openpyxl
import datetime
from PIL import Image

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
import google.generativeai as genai
import re

import pandas as pd
from xml.sax.saxutils import escape

# Bank Mapping Rules Configuration
BANK_LEDGER_NAME = "Main Bank Account"
BANK_SUSPENSE_LEDGER = "Bank Suspense Account"

TALLY_LEDGERS = [
    "Drawings 3024",
    "Drawings 7002",
    "Loan Account",
    "Petty Expense",
    "Electricity and Maintenance",
    "Salary",
    "Bank Suspense",
]

# ---------------------------------------------------------
# State Management
# ---------------------------------------------------------
# Persistent DB for queues
def get_db_file(mode):
    if mode == "Purchase": return "purchases_db.json"
    if mode == "Sales": return "sales_db.json"
    if mode == "Payment": return "payments_db.json"
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

def load_rules():
    if not os.path.exists(RULES_FILE):
        default_rules = {
            "SHUBH SINGHAL": "Drawings",
            "PAYTM": "Loan Account"
        }
        save_rules(default_rules)
        return default_rules
    with open(RULES_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_rules(rules):
    with open(RULES_FILE, "w") as f:
        json.dump(rules, f, indent=4)

# user_current: { user_id: invoice_dict }
user_current = {}
# user_edit_state: { user_id: "amount" | "inv_no" | "supplier" | "narration" }
user_edit_state = {}
# user_mode: { user_id: "Purchase" | "Sales" | "Payment" }
user_mode = {}


# ---------------------------------------------------------
# OpenAI Integration
# ---------------------------------------------------------
async def call_gemini(contents, mode="Purchase"):
    if mode == "Payment":
        system_prompt = (
            "You are an expert OCR extractor. Extract data to JSON object:\n"
            "- 'date' (string): DD-MM-YYYY. Default to today if not provided.\n"
            "- 'amount' (float): Total numerical amount of the payment.\n"
            "- 'payment_type' (string): 'cash', 'online', 'paytm', 'upi', or 'bank'.\n"
            "- 'cost_center' (string): 'Mahagun', 'Vvip', or 'Gulshan'.\n"
            "- 'supplier' (string): Payment AC based on the rules below.\n"
            "- 'narration' (string): Narration or description. See rules below.\n\n"
            "Rules for 'supplier' (Payment AC):\n"
            "1. Petty Expenses: If text contains 'petty' or 'petty expenses', strictly set 'supplier' to 'petty expense'.\n"
            "2. Drawings: If text contains '3024', set 'supplier' to 'Drawings 3024'. If text contains '7002', set 'supplier' to 'Drawings 7002'.\n"
            "3. Electricity/Maintenance: If text contains 'electricity' or 'maintenance', set 'supplier' to 'Electricity and Maintenance'.\n"
            "4. Default: Extract the supplier name or general expense category (e.g. ABC Ltd, Salary).\n\n"
            "Rules for 'narration':\n"
            "1. For petty expenses: Extract the underlying expense item as the narration (e.g. 'paytm 3000 guard petty mahagun' -> narration = 'guard').\n"
            "2. General: Extract explicit narration if provided. If no narration is found, leave empty \"\".\n\n"
            f"Current year: {datetime.datetime.now().year}, today: {datetime.datetime.now().strftime('%d-%m-%Y')}.\n"
            "Return ONLY valid JSON."
        )
    elif mode == "Sales":
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

    model = genai.GenerativeModel('gemini-3.6-flash')
    response = model.generate_content(full_contents)
    
    text = response.text
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
        # online/bank/card
        if cc == "gulshan": return "federal bank gulshan"
        elif cc == "mahagun": return "union bank mahagun"
        elif cc == "vvip": return "union bank vvip"
        else: return "Unknown Bank"

def get_payment_account(payment_type, cost_center):
    pt = payment_type.lower() if payment_type else "cash"
    cc = cost_center.lower() if cost_center else "none"
    is_cash = "cash" in pt
    
    if cc == "gulshan":
        return "Cash Gulshan" if is_cash else "Federal Bank Gulshan"
    elif cc == "mahagun":
        return "Cash Mahagun" if is_cash else "Union Bank Mahagun"
    elif cc == "vvip":
        return "Cash VVIP" if is_cash else "Union Bank VVIP"
    else:
        return f"Unknown {'Cash' if is_cash else 'Bank'}"

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
                InlineKeyboardButton("🔄 Toggle Payment Type", callback_data="toggle_payment")
            ],
            [
                InlineKeyboardButton("➕ Save & Add Next", callback_data="save_next"),
                InlineKeyboardButton("📊 Generate Excel File", callback_data="generate_excel")
            ]
        ]
    elif mode == "Payment":
        pt = data.get('payment_type', 'cash')
        cc = data.get('cost_center', 'None')
        account = get_payment_account(pt, cc)
        data['account'] = account
        data['inv_no'] = "1"
        data['inv_type'] = "Payment"
        
        text = (
            f"**{mode_text}**\n\n"
            "📄 *Extracted Payment Data*:\n"
            f"• ACCOUNT (Col A): {account}\n"
            f"• DATE (Col B): {data.get('date', 'Unknown')}\n"
            f"• INV NO (Col C): {data.get('inv_no')}\n"
            f"• AMOUNT (Col D): {data.get('amount', 0.0)}\n"
            f"• PAYMENT AC (Col E): {data.get('supplier', 'Unknown')}\n"
            f"• INV TYPE (Col F): {data.get('inv_type')}\n"
            f"• NARRATION (Col G): {data.get('narration', '')}"
        )
        if not data.get('narration'):
            text += "\n\n❓ *Do you want to add a narration?*"
            
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
                InlineKeyboardButton("✏️ Edit Payment AC", callback_data="edit_supplier"),
                InlineKeyboardButton("✏️ Edit Narration", callback_data="edit_narration")
            ],
            [
                InlineKeyboardButton("🔄 Toggle Payment Type", callback_data="toggle_payment")
            ],
            [
                InlineKeyboardButton("➕ Save & Add Next", callback_data="save_next"),
                InlineKeyboardButton("📊 Generate Excel File", callback_data="generate_excel")
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
                InlineKeyboardButton("📊 Generate Excel File", callback_data="generate_excel")
            ]
        ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")


async def process_invoice_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    user_id = update.effective_user.id
    processing_msg = await update.message.reply_text("Processing invoice text with Gemini...")
    try:
        mode = user_mode.get(user_id, "Purchase")
        contents = [text]
        extracted_data = await call_gemini(contents, mode=mode)
        
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
        elif mode == "Payment":
            user_current[user_id] = {
                "supplier": str(extracted_data.get("supplier", "Unknown")),
                "date": str(extracted_data.get("date", "Unknown")),
                "amount": float(extracted_data.get("amount", 0.0) if extracted_data.get("amount") is not None else 0.0),
                "cost_center": str(extracted_data.get("cost_center", "None")).title(),
                "payment_type": str(extracted_data.get("payment_type", "cash")).lower(),
                "narration": str(extracted_data.get("narration", ""))
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
    processing_msg = await update.message.reply_text("Processing invoice image with Gemini...")
    try:
        mode = user_mode.get(user_id, "Purchase")
        
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
                    "payment_type": str(rec.get("payment_type", "cash")).lower()
                }
                await processing_msg.delete()
                await send_preview(update, context, user_id)
        elif mode == "Payment":
            user_current[user_id] = {
                "supplier": str(extracted_data.get("supplier", "Unknown")),
                "date": str(extracted_data.get("date", "Unknown")),
                "amount": float(extracted_data.get("amount", 0.0) if extracted_data.get("amount") is not None else 0.0),
                "cost_center": str(extracted_data.get("cost_center", "None")).title(),
                "payment_type": str(extracted_data.get("payment_type", "cash")).lower(),
                "narration": str(extracted_data.get("narration", ""))
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
            user_current[user_id][field] = text
            
        # Clear edit state and refresh preview
        del user_edit_state[user_id]
        await send_preview(update, context, user_id)
    else:
        # Process as a new invoice text
        await process_invoice_text(update, context, text)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # If a new photo is sent, cancel any ongoing edit state
    if user_id in user_edit_state:
        del user_edit_state[user_id]
        
    photo_file = await update.message.photo[-1].get_file()
    photo_bytes = await photo_file.download_as_bytearray()
    
    await process_invoice_photo(update, context, photo_bytes)


# ---------------------------------------------------------
# Callback Handler
# ---------------------------------------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    # Main Menu routing
    if data == "main_menu":
        await start(update, context)
        return
        
    elif data == "menu_transactions":
        keyboard = [
            [
                InlineKeyboardButton("🛒 Purchase", callback_data="setmode_Purchase"),
                InlineKeyboardButton("📈 Sales", callback_data="setmode_Sales"),
                InlineKeyboardButton("💳 Payment", callback_data="setmode_Payment")
            ],
            [
                InlineKeyboardButton("🔙 Back", callback_data="main_menu")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("Select a transaction type:", reply_markup=reply_markup)
        return
        
    elif data == "menu_bank_statement":
        user_mode[user_id] = "Bank Statement"
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            "Bank Statement mode ready. Please upload your Excel (.xlsx) file.",
            reply_markup=reply_markup
        )
        return
        
    # Mode Update
    elif data.startswith("setmode_"):
        mode = data.split("_", 1)[1]
        user_mode[user_id] = mode
        msg = f"**{mode}** mode ready. Send text or image."
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
        return
        
    # Validate session
    elif user_id not in user_current and data != "generate_excel":
        await query.message.reply_text("No active invoice session found. Please send an image or text first.")
        return
        
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
        
    # Toggle Payment Action
    elif data == "toggle_payment":
        if user_id in user_current:
            pt = user_current[user_id].get("payment_type", "cash")
            user_current[user_id]["payment_type"] = "online" if pt == "cash" else "cash"
            await send_preview(update, context, user_id)
            
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
        
    # Main Menu
    elif data == "main_menu":
        await start(update, context)


def parse_excel(excel_bytes, rules):
    df = pd.read_excel(BytesIO(excel_bytes))
    cols = {str(c).lower(): c for c in df.columns}
    
    date_col = next((c for k, c in cols.items() if 'date' in k), None)
    narr_col = next((c for k, c in cols.items() if 'narration' in k or 'description' in k or 'particulars' in k), None)
    withdraw_col = next((c for k, c in cols.items() if 'withdrawal' in k or 'debit' in k), None)
    deposit_col = next((c for k, c in cols.items() if 'deposit' in k or 'credit' in k), None)
    
    if not (date_col and narr_col and withdraw_col and deposit_col):
        raise ValueError("Could not identify all required columns (Date, Narration, Withdrawal, Deposit).")
    
    transactions = []
    for _, row in df.iterrows():
        raw_date = row[date_col]
        if pd.isna(raw_date): continue
        dt = pd.to_datetime(raw_date)
        tally_date = dt.strftime('%Y%m%d')
        
        raw_narration = str(row[narr_col]) if not pd.isna(row[narr_col]) else ""
        clean_narration = escape(raw_narration)
        narr_upper = raw_narration.upper()
        
        withdraw = 0.0 if pd.isna(row[withdraw_col]) else abs(float(str(row[withdraw_col]).replace(',', '')))
        deposit = 0.0 if pd.isna(row[deposit_col]) else abs(float(str(row[deposit_col]).replace(',', '')))
        
        if withdraw == 0 and deposit == 0: continue
            
        ledger = BANK_SUSPENSE_LEDGER
        for keyword, mapped_ledger in rules.items():
            if keyword.upper() in narr_upper:
                ledger = mapped_ledger
                break
                
        is_unmapped = (ledger == BANK_SUSPENSE_LEDGER)
        
        transactions.append({
            'date': tally_date,
            'raw_narration': raw_narration,
            'clean_narration': clean_narration,
            'withdraw': withdraw,
            'deposit': deposit,
            'ledger': ledger,
            'unmapped': is_unmapped
        })
        
    return transactions

def build_tally_xml(transactions):
    xml_lines = [
        '<ENVELOPE>',
        '  <HEADER>',
        '    <TALLYREQUEST>Import Data</TALLYREQUEST>',
        '  </HEADER>',
        '  <BODY>',
        '    <IMPORTDATA>',
        '      <REQUESTDESC>',
        '        <REPORTNAME>Vouchers</REPORTNAME>',
        '      </REQUESTDESC>',
        '      <REQUESTDATA>',
        '        <TALLYMESSAGE xmlns:UDF="TallyUDF">'
    ]
    
    for t in transactions:
        withdraw = t['withdraw']
        deposit = t['deposit']
        ledger = t['ledger']
        
        if withdraw > 0:
            vch_type = "Payment"
            ledgers_xml = f"""
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{escape(ledger)}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{withdraw}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{escape(BANK_LEDGER_NAME)}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{withdraw}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""
        else:
            vch_type = "Receipt"
            ledgers_xml = f"""
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{escape(ledger)}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{deposit}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{escape(BANK_LEDGER_NAME)}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{deposit}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""
        
        voucher = f"""
       <VOUCHER VCHTYPE="{vch_type}" ACTION="Create">
          <DATE>{t['date']}</DATE>
          <NARRATION>{t['clean_narration']}</NARRATION>
          <VOUCHERTYPENAME>{vch_type}</VOUCHERTYPENAME>{ledgers_xml}
       </VOUCHER>"""
        xml_lines.append(voucher)
        
    xml_lines.extend([
        '        </TALLYMESSAGE>',
        '      </REQUESTDATA>',
        '    </IMPORTDATA>',
        '  </BODY>',
        '</ENVELOPE>'
    ])
    return "\n".join(xml_lines).encode('utf-8')

REVIEW_UNMAPPED, SEARCH_LEDGER, SELECT_LEDGER, SAVE_RULE, ASK_KEYWORD = range(5)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    mode = user_mode.get(user_id)
    
    if mode != "Bank Statement":
        await update.message.reply_text("Please select 'Bank Statement' from the main menu before uploading an Excel file.")
        return ConversationHandler.END
        
    processing_msg = await update.message.reply_text("Downloading and parsing Excel file...")
    try:
        doc_file = await update.message.document.get_file()
        file_bytes = await doc_file.download_as_bytearray()
        
        rules = load_rules()
        transactions = parse_excel(file_bytes, rules)
        
        context.user_data['transactions'] = transactions
        context.user_data['unmapped_indices'] = [i for i, t in enumerate(transactions) if t['unmapped']]
        
        await processing_msg.delete()
        
        if not context.user_data['unmapped_indices']:
            return await finish_xml_generation(update, context)
            
        context.user_data['current_unmapped_idx'] = 0
        return await prompt_next_unmapped(update, context)
    except Exception as e:
        await processing_msg.edit_text(f"Error processing Excel document: {str(e)}")
        return ConversationHandler.END

async def prompt_next_unmapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
    unmapped_indices = context.user_data['unmapped_indices']
    current_idx = context.user_data['current_unmapped_idx']
    
    if current_idx >= len(unmapped_indices):
        return await finish_xml_generation(update, context)
        
    tx_idx = unmapped_indices[current_idx]
    tx = context.user_data['transactions'][tx_idx]
    amt = tx['withdraw'] if tx['withdraw'] > 0 else tx['deposit']
    
    msg = (
        f"⚠️ **Unmapped Transaction ({current_idx + 1}/{len(unmapped_indices)}):**\n\n"
        f"Narration: `{tx['raw_narration']}`\n"
        f"Amount: ₹{amt}\n\n"
        "Type the first few letters of the ledger name to search, or type `Skip` to use Suspense Account."
    )
    
    if update.callback_query:
        await update.callback_query.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, parse_mode="Markdown")
    return SEARCH_LEDGER

async def search_ledger_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if text.lower() == "skip":
        context.user_data['current_unmapped_idx'] += 1
        return await prompt_next_unmapped(update, context)
        
    if text.lower().startswith("force:"):
        ledger = text[6:].strip()
        return await assign_ledger_and_ask_save(update, context, ledger)
        
    # Search TALLY_LEDGERS
    matches = [l for l in TALLY_LEDGERS if text.lower() in l.lower()]
    
    if not matches:
        await update.message.reply_text("No matches found. Try another search term, or type `Force: <LedgerName>` to use a custom ledger not in the list.", parse_mode="Markdown")
        return SEARCH_LEDGER
        
    # Build keyboard with matches
    keyboard = []
    for match in matches:
        idx = TALLY_LEDGERS.index(match)
        keyboard.append([InlineKeyboardButton(match, callback_data=f"sel_{idx}")])
        
    keyboard.append([InlineKeyboardButton("❌ Cancel / Search Again", callback_data="cancel_search")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text("Select a matching ledger:", reply_markup=reply_markup)
    return SELECT_LEDGER

async def select_ledger_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel_search":
        await query.message.reply_text("Type the first few letters of the ledger name to search, or type `Skip` to use Suspense Account.", parse_mode="Markdown")
        return SEARCH_LEDGER
        
    idx_str = query.data.split("_")[1]
    ledger = TALLY_LEDGERS[int(idx_str)]
    return await assign_ledger_and_ask_save(update, context, ledger)

async def assign_ledger_and_ask_save(update: Update, context: ContextTypes.DEFAULT_TYPE, ledger: str):
    current_idx = context.user_data['current_unmapped_idx']
    tx_idx = context.user_data['unmapped_indices'][current_idx]
    tx = context.user_data['transactions'][tx_idx]
    
    tx['ledger'] = ledger
    tx['unmapped'] = False
    
    keyboard = [
        [InlineKeyboardButton("✅ Yes", callback_data="rule_permanent")],
        [InlineKeyboardButton("❌ No", callback_data="rule_temporary")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    msg = f"Do you want to save `{tx['raw_narration']}` = `{ledger}` as a permanent rule?"
    
    if update.callback_query:
        await update.callback_query.message.reply_text(msg, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode="Markdown")
    return SAVE_RULE

async def save_rule_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "rule_temporary":
        context.user_data['current_unmapped_idx'] += 1
        return await prompt_next_unmapped(update, context)
        
    elif query.data == "rule_permanent":
        await query.message.reply_text("Please reply with the exact **keyword** from the narration that should trigger this rule in the future.")
        return ASK_KEYWORD

async def ask_keyword_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyword = update.message.text.strip()
    current_idx = context.user_data['current_unmapped_idx']
    tx_idx = context.user_data['unmapped_indices'][current_idx]
    ledger = context.user_data['transactions'][tx_idx]['ledger']
    
    rules = load_rules()
    rules[keyword] = ledger
    save_rules(rules)
    
    await update.message.reply_text(f"✅ Saved rule: `{keyword}` ➡️ `{ledger}`", parse_mode="Markdown")
    
    context.user_data['current_unmapped_idx'] += 1
    return await prompt_next_unmapped(update, context)

async def finish_xml_generation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    transactions = context.user_data['transactions']
    xml_data = build_tally_xml(transactions)
    
    total = len(transactions)
    still_unmapped = sum(1 for t in transactions if t['unmapped'])
    
    summary = f"🎉 **Bank Statement Complete!**\nTotal Transactions: {total}\n"
    if still_unmapped > 0:
        summary += f"⚠️ Left in Suspense: {still_unmapped}\n"
        
    xml_file = BytesIO(xml_data)
    user_id = update.effective_user.id
    
    if update.callback_query:
        msg_obj = update.callback_query.message
    else:
        msg_obj = update.message
        
    await context.bot.send_document(
        chat_id=user_id,
        document=xml_file,
        filename="bank_import.xml",
        caption=summary,
        parse_mode="Markdown"
    )
    
    user_mode[user_id] = None
    # Reset context
    context.user_data.pop('transactions', None)
    context.user_data.pop('unmapped_indices', None)
    context.user_data.pop('current_unmapped_idx', None)
    
    await start(update, context)
    return ConversationHandler.END

# ---------------------------------------------------------
# Bot Initialization
# ---------------------------------------------------------


async def do_export(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    mode = user_mode.get(user_id, "Purchase")
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
    elif mode == "Payment":
        headers = [
            "ACCOUNT", "DATE", "INV NO", "AMOUNT", "PAYMENT AC", "INV TYPE", "NARRATION"
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
        elif mode == "Payment":
            pt = inv.get("payment_type", "cash")
            cc = inv.get("cost_center", "None")
            account = get_payment_account(pt, cc)
            row = [
                account,
                inv.get("date", "Unknown"),
                "1",
                inv.get("amount", 0.0),
                inv.get("supplier", "Unknown"),
                "Payment",
                inv.get("narration", "")
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
    welcome_text = (
        "👋 Welcome to the Invoice Scanner Bot!\n\n"
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
        await update.callback_query.message.edit_text(welcome_text, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=reply_markup)

async def addrule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /addrule KEYWORD = LEDGER_NAME")
        return
        
    full_text = " ".join(args)
    if "=" not in full_text:
        await update.message.reply_text("Usage: /addrule KEYWORD = LEDGER_NAME\nMake sure to include the '=' sign.")
        return
        
    parts = full_text.split("=", 1)
    keyword = parts[0].strip()
    ledger = parts[1].strip()
    
    if not keyword or not ledger:
        await update.message.reply_text("Both Keyword and Ledger Name must be provided.")
        return
        
    rules = load_rules()
    rules[keyword] = ledger
    save_rules(rules)
    
    await update.message.reply_text(f"✅ Rule added successfully:\n\nKeyword: `{keyword}`\nMaps to Ledger: `{ledger}`", parse_mode="Markdown")

async def viewrules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rules = load_rules()
    if not rules:
        await update.message.reply_text("No bank mapping rules found. Use /addrule to create one.")
        return
        
    lines = ["**Current Bank Mapping Rules:**\n"]
    for idx, (kw, ledger) in enumerate(rules.items(), 1):
        lines.append(f"{idx}. `{kw}` ➡️ `{ledger}`")
        
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def post_init(application: Application):
    await application.bot.set_my_commands([
        BotCommand("start", "Open main menu"),
        BotCommand("export", "Generate Excel file"),
        BotCommand("addrule", "Add a bank mapping rule"),
        BotCommand("viewrules", "View all mapping rules")
    ])

def main():
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    gemini_key = os.environ.get("GEMINI_API_KEY")
    
    if not bot_token:
        print("Please set TELEGRAM_BOT_TOKEN environment variable.")
        return
    if not gemini_key:
        print("Please set GEMINI_API_KEY environment variable.")
        return
        
    genai.configure(api_key=gemini_key)
    application = Application.builder().token(bot_token).post_init(post_init).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("export", export_command))
    application.add_handler(CommandHandler("addrule", addrule_command))
    application.add_handler(CommandHandler("viewrules", viewrules_command))
    
    bank_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Document.FileExtension("xlsx") | filters.Document.FileExtension("xls"), handle_document)],
        states={
            SEARCH_LEDGER: [MessageHandler(filters.TEXT & ~filters.COMMAND, search_ledger_handler)],
            SELECT_LEDGER: [CallbackQueryHandler(select_ledger_handler, pattern="^(sel_|cancel_search)")],
            SAVE_RULE: [CallbackQueryHandler(save_rule_handler, pattern="^rule_")],
            ASK_KEYWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_keyword_handler)],
        },
        fallbacks=[CommandHandler("start", start)]
    )
    application.add_handler(bank_conv_handler)
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(CallbackQueryHandler(button_handler))

    print("Bot is polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
