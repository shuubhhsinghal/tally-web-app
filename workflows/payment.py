"""Payment workflow, isolated from Purchase, Sales, and bank-statement logic."""

from __future__ import annotations

import datetime
import json
import time
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from PIL import Image
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from rapidfuzz import process, fuzz


MODE = "Payment"
DRAFT_KEY = "payment_draft"
TYPE_KEY = "payment_type"
EDIT_KEY = "payment_edit_field"


def _sanitize_xml(value: Any) -> str:
    return escape(str(value if value is not None else ""), {'"': "&quot;", "'": "&apos;"})

def payment_type(context) -> str:
    return context.user_data.get(TYPE_KEY, "Others")

def set_payment_type(context, value: str) -> None:
    context.user_data[TYPE_KEY] = value
    context.user_data.pop(EDIT_KEY, None)

def clear(context) -> None:
    context.user_data.pop(DRAFT_KEY, None)
    context.user_data.pop(EDIT_KEY, None)
    context.user_data.pop("fuzzy_payee_choices", None)
    context.user_data.pop("source_choices", None)
    context.user_data.pop("payment_state", None)

def build_xml(payment_data: dict[str, Any], payment_kind: str) -> bytes:
    try:
        day, month, year = str(payment_data.get("date", "")).split("-")
        date_str = f"{year}{month}{day}"
    except ValueError:
        date_str = datetime.datetime.now().strftime("%Y%m%d")
    amount = payment_data.get("amount", 0)
    account = _sanitize_xml(payment_data.get("account", "Unknown"))
    paid_from = _sanitize_xml(payment_data.get("paid_from", "Unknown"))
    narration = _sanitize_xml(payment_data.get("narration", ""))
    cost_center = payment_data.get("cost_center")
    if cost_center in ("None", "null", None, ""):
        cost_center = None
    allocation = ""
    if cost_center and payment_kind == "Expense":
        allocation = f"""
              <CATEGORYALLOCATIONS.LIST>
                <CATEGORY>Primary Cost Category</CATEGORY>
                <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                <COSTCENTREALLOCATIONS.LIST>
                  <NAME>{_sanitize_xml(cost_center)}</NAME>
                  <AMOUNT>-{amount}</AMOUNT>
                </COSTCENTREALLOCATIONS.LIST>
              </CATEGORYALLOCATIONS.LIST>"""
    xml = f"""<ENVELOPE>
  <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
  <BODY><IMPORTDATA>
    <REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC>
    <REQUESTDATA><TALLYMESSAGE>
      <VOUCHER VCHTYPE="Payment" ACTION="Create">
        <DATE>{date_str}</DATE><VOUCHERTYPENAME>Payment</VOUCHERTYPENAME><NARRATION>{narration}</NARRATION>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{account}</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-{amount}</AMOUNT>{allocation}
        </ALLLEDGERENTRIES.LIST>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{paid_from}</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>{amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
      </VOUCHER>
    </TALLYMESSAGE></REQUESTDATA>
  </IMPORTDATA></BODY>
</ENVELOPE>"""
    return xml.encode("utf-8")

def _prompt(text: str) -> str:
    return f"""Extract details for a Payment. DO NOT invent ledger names. Extract the exact raw strings from the text.
Return ONLY JSON with:
- amount: (number)
- date: (DD-MM-YYYY format, default to today {datetime.datetime.now().strftime('%d-%m-%Y')})
- payee: (raw string of who is being paid)
- paid_from: (raw string of the bank or cash source, null if not mentioned)
- cost_center: (Mahagun, Vvip, or Gulshan; return null if unclear)
- narration: (the exact raw user message)

User text:
{text}"""

from workflows.tally_buffer import get_cached_ledgers as get_ledger_cache


async def resolve_payee(update, context):
    draft = context.user_data[DRAFT_KEY]
    if draft.get("account") and draft["account"] != "Unknown":
        return await resolve_source(update, context)

    raw_payee = draft.get("raw_payee", "")
    if not raw_payee:
        context.user_data["payment_state"] = "WAITING_FOR_PAYEE_SEARCH"
        if update.callback_query:
            await update.callback_query.message.edit_text("Who are you paying? (Type a keyword to search synced ledgers)")
        else:
            await update.message.reply_text("Who are you paying? (Type a keyword to search synced ledgers)")
        return
        
    cache = get_ledger_cache()
    if not cache:
        draft["account"] = raw_payee
        return await resolve_source(update, context)
        
    # Exact Match
    lower_map = {k.lower(): k for k in cache.keys()}
    target = raw_payee.lower().strip()
    
    if target in lower_map:
        # Title case the name for presentation if exact key match
        draft["account"] = lower_map[target].title() if lower_map[target] == lower_map[target].lower() else lower_map[target]
        return await resolve_source(update, context)
        
    # Fuzzy match
    all_names = list(lower_map.keys())
    matches = process.extract(target, all_names, scorer=fuzz.token_sort_ratio, limit=5)
    
    good_matches = [m for m in matches if m[1] > 60]
    
    if good_matches and good_matches[0][1] > 95:
        match_key = good_matches[0][0]
        draft["account"] = lower_map[match_key].title() if lower_map[match_key] == lower_map[match_key].lower() else lower_map[match_key]
        return await resolve_source(update, context)
        
    if good_matches:
        keyboard = []
        choices = {}
        for i, (match_name, score, _) in enumerate(good_matches):
            real_name = lower_map[match_name].title() if lower_map[match_name] == lower_map[match_name].lower() else lower_map[match_name]
            choices[str(i)] = match_name
            keyboard.append([InlineKeyboardButton(f"{real_name}", callback_data=f"payee_sel_{i}")])
            
        context.user_data["fuzzy_payee_choices"] = choices
        keyboard.append([InlineKeyboardButton("🔍 Search Manually", callback_data="payee_search")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.callback_query:
            await update.callback_query.message.edit_text(f"Could not find exact match for '{raw_payee}'.\n\nDid you mean:", reply_markup=reply_markup)
        else:
            await update.message.reply_text(f"Could not find exact match for '{raw_payee}'.\n\nDid you mean:", reply_markup=reply_markup)
        return
        
    # Substring match fallback
    matching_ledgers = [ledger for ledger in all_names if target in ledger]
    if matching_ledgers:
        keyboard = []
        choices = {}
        for i, match_name in enumerate(matching_ledgers[:10]):
            real_name = lower_map[match_name].title() if lower_map[match_name] == lower_map[match_name].lower() else lower_map[match_name]
            choices[str(i)] = match_name
            keyboard.append([InlineKeyboardButton(f"{real_name}", callback_data=f"payee_sel_{i}")])
            
        # Store new choices in context
        context.user_data["fuzzy_payee_choices"] = choices
        keyboard.append([InlineKeyboardButton("🔍 Search Manually", callback_data="payee_search")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = f"I found a few ledgers matching '{raw_payee}'. Please select the correct one:"
        if update.callback_query:
            await update.callback_query.message.edit_text(msg, reply_markup=reply_markup)
        else:
            await update.message.reply_text(msg, reply_markup=reply_markup)
        return
        
    # No matches
    keyboard = [[InlineKeyboardButton("🔍 Search Manually", callback_data="payee_search")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = f"No ledger found for '{raw_payee}'."
    if update.callback_query:
        await update.callback_query.message.edit_text(msg, reply_markup=reply_markup)
    else:
        await update.message.reply_text(msg, reply_markup=reply_markup)

async def resolve_source(update, context):
    draft = context.user_data[DRAFT_KEY]
    if draft.get("paid_from") and draft["paid_from"] != "Unknown":
        return await check_cost_center(update, context)
        
    raw_source = draft.get("raw_paid_from", "")
    cache = get_ledger_cache()
    
    bank_cash_ledgers = []
    lower_map = {}
    for name, data in cache.items():
        if isinstance(data, dict):
            parent = data.get("parent", "").lower()
            if "bank" in parent or "cash" in parent or "bank" in name.lower() or "cash" in name.lower():
                display_name = name.title()
                bank_cash_ledgers.append(display_name)
                lower_map[name.lower()] = display_name
    
    if raw_source and bank_cash_ledgers:
        target = raw_source.lower().strip()
        all_banks = list(lower_map.keys())
        matches = process.extract(target, all_banks, scorer=fuzz.token_sort_ratio, limit=1)
        if matches and matches[0][1] > 80:
            draft["paid_from"] = lower_map[matches[0][0]]
            return await check_cost_center(update, context)

    if not bank_cash_ledgers:
        draft["paid_from"] = "Unknown"
        return await check_cost_center(update, context)
        
    keyboard = []
    choices = {}
    for i, ledger in enumerate(bank_cash_ledgers[:15]):
        choices[str(i)] = ledger
        keyboard.append([InlineKeyboardButton(ledger, callback_data=f"src_sel_{i}")])
        
    context.user_data["source_choices"] = choices
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    msg = "🏦 *Paid from which account?*"
    if update.callback_query:
        await update.callback_query.message.edit_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)

async def check_cost_center(update, context):
    draft = context.user_data[DRAFT_KEY]
    if payment_type(context) == "Expense" and draft.get("cost_center") not in ("Mahagun", "Vvip", "Gulshan"):
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Mahagun", callback_data="cc_Mahagun"), InlineKeyboardButton("Vvip", callback_data="cc_Vvip"), InlineKeyboardButton("Gulshan", callback_data="cc_Gulshan")]])
        if update.callback_query:
            await update.callback_query.message.edit_text("🏢 *Select Cost Center:*", parse_mode="Markdown", reply_markup=keyboard)
        else:
            await update.message.reply_text("🏢 *Select Cost Center:*", parse_mode="Markdown", reply_markup=keyboard)
        return
        
    await show_preview(update, context)

async def show_preview(update, context) -> None:
    data = context.user_data.get(DRAFT_KEY)
    if not data:
        return
    kind = payment_type(context)
    text = (
        f"**Active Mode: 💳 Payment ({kind})**\n\n"
        "📄 *Extracted Payment Data*:\n"
        f"• ACCOUNT: {data.get('account', 'Unknown')}\n"
        f"• AMOUNT: {data.get('amount', 0.0)}\n"
        f"• DATE: {data.get('date', 'Unknown')}\n"
        f"• PAID FROM: {data.get('paid_from', 'Unknown')}\n"
        f"• COST CENTER: {data.get('cost_center', 'None')}\n"
        f"• NARRATION: {data.get('narration', '')}"
    )
    keyboard = []
    if kind == "Expense":
        keyboard.append([InlineKeyboardButton("Mahagun", callback_data="cc_Mahagun"), InlineKeyboardButton("Vvip", callback_data="cc_Vvip"), InlineKeyboardButton("Gulshan", callback_data="cc_Gulshan")])
    keyboard.extend([
        [InlineKeyboardButton("✏️ Edit Account", callback_data="edit_account"), InlineKeyboardButton("✏️ Edit Amount", callback_data="edit_amount")],
        [InlineKeyboardButton("✏️ Edit Date", callback_data="edit_date"), InlineKeyboardButton("✏️ Edit Narration", callback_data="edit_narration")],
        [InlineKeyboardButton("🚀 Push to Tally", callback_data="push_payment_tally")],
    ])
    markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)


async def process_text(update, context, generate_gemini, push_to_tally=None) -> None:
    profiler = {'telegram_received': time.time()}
    context.user_data['profiler'] = profiler

    state = context.user_data.get("payment_state")
    if state == "WAITING_FOR_PAYEE_SEARCH":
        keyword = update.message.text.strip().lower()
        context.user_data["payment_state"] = None
        
        cache = get_ledger_cache()
        matches = [k for k in cache.keys() if keyword in k.lower()]
        
        if not matches:
            keyboard = [[InlineKeyboardButton("🔙 Try Again", callback_data="payee_search")]]
            await update.message.reply_text(f"❌ No ledgers found containing '{keyword}'.", reply_markup=InlineKeyboardMarkup(keyboard))
            return
            
        keyboard = []
        choices = {}
        for i, match_name in enumerate(matches[:10]):
            real_name = match_name.title()
            choices[str(i)] = match_name
            keyboard.append([InlineKeyboardButton(f"{real_name}", callback_data=f"payee_sel_{i}")])
            
        context.user_data["fuzzy_payee_choices"] = choices
        keyboard.append([InlineKeyboardButton("🔍 Search Again", callback_data="payee_search")])
        await update.message.reply_text(f"Select a ledger matching '{keyword}':", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    processing = await update.message.reply_text("Processing payment text with Gemini...")
    try:
        profiler['gemini_start'] = time.time()
        raw_json = await generate_gemini(_prompt(update.message.text), json_mode=True, model_name="gemini-3.5-flash-lite")
        profiler['gemini_end'] = time.time()
        
        data = json.loads(raw_json.strip())
        
        try:
            amount = float(data.get("amount", 0.0) if data.get("amount") is not None else 0.0)
        except:
            amount = 0.0
            
        context.user_data[DRAFT_KEY] = {
            "amount": amount,
            "date": str(data.get("date", "Unknown")),
            "account": None, 
            "raw_payee": data.get("payee", ""),
            "paid_from": None, 
            "raw_paid_from": data.get("paid_from", ""),
            "cost_center": data.get("cost_center"),
            "narration": str(data.get("narration", "")),
        }
        await processing.delete()
        await resolve_payee(update, context)
    except Exception as exc:
        await processing.edit_text(f"Error processing text: {exc}")


async def process_photo(update, context, photo_bytes: bytearray, generate_gemini) -> None:
    profiler = {'telegram_received': time.time()}
    context.user_data['profiler'] = profiler

    processing = await update.message.reply_text("Processing invoice image with Gemini...")
    try:
        image = Image.open(BytesIO(photo_bytes))
        if image.mode != "RGB":
            image = image.convert("RGB")
        instruction = _prompt("Extract the payment details from this receipt.")
        
        profiler['gemini_start'] = time.time()
        raw_json = await generate_gemini([instruction, image], json_mode=True, model_name="gemini-3.5-flash-lite")
        profiler['gemini_end'] = time.time()
        
        data = json.loads(raw_json.strip())
        
        try:
            amount = float(data.get("amount", 0.0) if data.get("amount") is not None else 0.0)
        except:
            amount = 0.0
            
        context.user_data[DRAFT_KEY] = {
            "amount": amount,
            "date": str(data.get("date", "Unknown")),
            "account": None,
            "raw_payee": data.get("payee", ""),
            "paid_from": None,
            "raw_paid_from": data.get("paid_from", ""),
            "cost_center": data.get("cost_center"),
            "narration": str(data.get("narration", "")),
        }
        await processing.delete()
        await resolve_payee(update, context)
    except Exception as exc:
        await processing.edit_text(f"Error processing photo: {exc}")


async def receive_edit(update, context, generate_gemini) -> bool:
    field = context.user_data.get(EDIT_KEY)
    if not field:
        return False
    data = context.user_data.get(DRAFT_KEY)
    if not data:
        context.user_data.pop(EDIT_KEY, None)
        return False
    value = update.message.text.strip()
    if field == "amount":
        try:
            data[field] = float(value)
        except ValueError:
            await update.message.reply_text("Please enter a valid number for amount.")
            return True
    elif field == "date":
        try:
            data[field] = (await generate_gemini(f"Convert '{value}' to DD-MM-YYYY. Return only the date. Current year: {datetime.datetime.now().year}." )).strip()
        except Exception:
            data[field] = value
    else:
        data[field] = value
    context.user_data.pop(EDIT_KEY, None)
    await show_preview(update, context)
    return True


async def handle_callback(update, context, *, push_to_tally, load_db=None, save_db=None) -> bool:
    query = update.callback_query
    data = query.data
    
    if data == "payee_search":
        await query.answer()
        await query.message.edit_text("🔍 Type a keyword to search for the ledger:")
        context.user_data["payment_state"] = "WAITING_FOR_PAYEE_SEARCH"
        return True
        
    if data.startswith("payee_sel_"):
        await query.answer()
        idx = data.split("_")[-1]
        choices = context.user_data.get("fuzzy_payee_choices", {})
        if idx in choices:
            raw_key = choices[idx]
            draft = context.user_data[DRAFT_KEY]
            draft["account"] = raw_key.title()
            context.user_data.pop("fuzzy_payee_choices", None)
            await resolve_source(update, context)
        else:
            await query.message.edit_text("Session expired.")
        return True
        
    if data.startswith("src_sel_"):
        await query.answer()
        idx = data.split("_")[-1]
        choices = context.user_data.get("source_choices", {})
        if idx in choices:
            draft = context.user_data[DRAFT_KEY]
            draft["paid_from"] = choices[idx]
            context.user_data.pop("source_choices", None)
            await check_cost_center(update, context)
        else:
            await query.message.edit_text("Session expired.")
        return True

    callbacks = {"edit_account", "edit_amount", "edit_date", "edit_narration", "push_payment_tally", "download_xml_payment"}
    if not (data.startswith("pf_") or data.startswith("cc_") or data in callbacks):
        return False
        
    if not context.user_data.get(DRAFT_KEY):
        await query.answer()
        await query.message.reply_text("No active payment session found. Please send an image or text first.")
        return True
        
    await query.answer()
    draft = context.user_data.get(DRAFT_KEY)
    
    if data.startswith("cc_"):
        draft["cost_center"] = data.split("_", 1)[1]
        await check_cost_center(update, context)
    elif data.startswith("edit_"):
        field = data.split("_", 1)[1]
        context.user_data[EDIT_KEY] = field
        await query.message.reply_text(f"Please type the new value for {field.replace('_', ' ').title()}:")
    elif data == "push_payment_tally":
        profiler = context.user_data.get('profiler', {})
        
        await query.message.edit_text("Pushing to Tally...")
        xml_bytes = build_xml(draft, payment_type(context))
        profiler['xml_generated'] = time.time()
        
        profiler['tally_req_start'] = time.time()
        status_code, response_text = push_to_tally(
            xml_bytes,
            user_id=update.effective_user.id,
            description=f"Payment: ₹{draft.get('amount', 0)} to {draft.get('account', 'Unknown')}"
        )
        profiler['tally_req_end'] = time.time()
        
        if status_code == 200:
            await query.message.edit_text(f"✅ Success! Payment voucher of {draft.get('amount', 0)} to {draft.get('account', 'Unknown')} pushed to Tally.")
        elif status_code == 202:
            await query.message.edit_text("⏸ Tally is offline. Saved to queue! It will push automatically when your laptop opens.")
            
            # --- Profiling Log ---
            profiler['telegram_reply_sent'] = time.time()
            with open("performance.log", "a") as f:
                f.write(f"--- Payment Voucher Processed at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
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
                    f.write(f"Total Execution Time: {total_time:.2f}s\n\n")
                    
            clear(context)
        elif status_code == 500:
            keyboard = [[InlineKeyboardButton("📥 Download XML", callback_data="download_xml_payment")], [InlineKeyboardButton("🚀 Retry Push to Tally", callback_data="push_payment_tally")]]
            await query.message.edit_text("⚠️ Shop PC is offline or Tally is closed. Your transaction is still available to retry.", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.message.edit_text(f"⚠️ Failed to push to Tally (HTTP {status_code}).\nResponse: {response_text[:200]}")
    elif data == "download_xml_payment":
        await context.bot.send_document(chat_id=query.from_user.id, document=BytesIO(build_xml(draft, payment_type(context))), filename="payment_voucher.xml", caption="Here is your offline XML file.")
    return True
