"""Transfer workflow (Contra Vouchers) for Fund Transfers."""

from __future__ import annotations

import datetime
import json
import re
from typing import Any
from xml.sax.saxutils import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from rapidfuzz import process, fuzz

MODE = "Transfer"
DRAFT_KEY = "transfer_draft"


def _sanitize_xml(value: Any) -> str:
    return escape(str(value if value is not None else ""), {'"': "&quot;", "'": "&apos;"})

def clear(context) -> None:
    context.user_data.pop(DRAFT_KEY, None)
    context.user_data.pop("transfer_state", None)
    context.user_data.pop("transfer_fuzzy_choices", None)

from workflows.tally_buffer import get_cached_ledgers as get_ledger_cache


def build_transfer_xml(date_str: str, from_ledger: str, to_ledger: str, amount: float, narration: str) -> bytes:
    from_ledger_xml = _sanitize_xml(from_ledger)
    to_ledger_xml = _sanitize_xml(to_ledger)
    narration_xml = _sanitize_xml(narration)
    
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
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER ACTION="Create" VCHTYPE="Contra">
            <DATE>{date_str}</DATE>
            <VOUCHERTYPENAME>Contra</VOUCHERTYPENAME>
            <PARTYLEDGERNAME>{to_ledger_xml}</PARTYLEDGERNAME>
            <NARRATION>{narration_xml}</NARRATION>
            <PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{from_ledger_xml}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{amount}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{to_ledger_xml}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{amount}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""
    return xml.encode("utf-8")

async def process_text(update, context, generate_gemini=None) -> None:
    text = update.message.text.strip()
    
    state = context.user_data.get("transfer_state")
    if state == "WAITING_FOR_FROM_SEARCH":
        keyword = text.lower()
        context.user_data["transfer_state"] = None
        await handle_manual_search(update, context, keyword, "from")
        return
    elif state == "WAITING_FOR_TO_SEARCH":
        keyword = text.lower()
        context.user_data["transfer_state"] = None
        await handle_manual_search(update, context, keyword, "to")
        return
    elif state and state.startswith("WAITING_FOR_EDIT_"):
        field = state.split("_")[-1].lower()
        context.user_data["transfer_state"] = None
        draft = context.user_data.get(DRAFT_KEY)
        if not draft:
            await update.message.reply_text("Session expired.")
            return
            
        if field == "amount":
            try:
                draft["amount"] = float(text.replace(",", ""))
            except ValueError:
                await update.message.reply_text("Invalid amount format. Please try again.")
                return
        elif field == "date":
            today = datetime.datetime.now()
            date_match = re.search(r'\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\b', text)
            if date_match:
                d, m, y = date_match.groups()
                if len(y) == 2: y = "20" + y
                draft["date"] = f"{y}{int(m):02d}{int(d):02d}"
                draft["display_date"] = f"{int(d):02d}-{int(m):02d}-{y}"
            elif re.search(r'\byesterday\b', text, re.IGNORECASE):
                yesterday = today - datetime.timedelta(days=1)
                draft["date"] = yesterday.strftime("%Y%m%d")
                draft["display_date"] = yesterday.strftime("%d-%m-%Y")
            else:
                draft["date"] = today.strftime("%Y%m%d")
                draft["display_date"] = today.strftime("%d-%m-%Y")
        elif field == "from":
            draft["raw_from"] = text
            draft["from_ledger"] = None
            return await resolve_ledger(update, context, "from")
        elif field == "to":
            draft["raw_to"] = text
            draft["to_ledger"] = None
            return await resolve_ledger(update, context, "to")
            
        await show_preview(update, context)
        return

    if not generate_gemini:
        await update.message.reply_text("Gemini AI is not configured. Cannot parse natural language.")
        return
        
    current_date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    today_str = datetime.datetime.now().strftime("%Y%m%d")
    
    prompt = (
        "You are a financial transaction intent parser. Extract fund transfer details from the user's text.\n"
        "CRITICAL RULE FOR LEDGERS:\n"
        "- 'from_ledger': The source account from which money is being sent / taken OUT (e.g., in 'cash mahagun to union bank 1000', cash mahagun is from_ledger).\n"
        "- 'to_ledger': The destination account into which money is being deposited / sent TO (e.g., union bank is to_ledger).\n"
        "Return ONLY a valid JSON object with keys: 'amount' (float), 'from_ledger' (string), 'to_ledger' (string), 'date' (YYYYMMDD string).\n"
        f"If a date or relative term like 'yesterday', 'today', '1 april' is mentioned, parse and convert it to YYYYMMDD for year {datetime.datetime.now().year}. If no date is mentioned, use today's date: {today_str}.\n\n"
        f"Today's date is {current_date_str}.\n"
        f"User Text: {text}"
    )
    
    msg = await update.message.reply_text("⏳ Parsing transfer details...")
    
    try:
        raw = await generate_gemini(prompt, json_mode=True)
        data = json.loads(raw)
        amount = float(data['amount'])
        raw_from = data['from_ledger']
        raw_to = data['to_ledger']
        parsed_date = data['date']
        
        # Safe format check for YYYYMMDD
        if len(parsed_date) == 8 and parsed_date.isdigit():
            display_date = f"{parsed_date[-2:]}-{parsed_date[4:6]}-{parsed_date[:4]}"
        else:
            parsed_date = today_str
            display_date = datetime.datetime.now().strftime("%d-%m-%Y")
            
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"❌ Could not parse transfer details: {e}")
        return
    
    context.user_data[DRAFT_KEY] = {
        "amount": amount,
        "date": parsed_date,
        "display_date": display_date,
        "raw_from": raw_from,
        "raw_to": raw_to,
        "from_ledger": None,
        "to_ledger": None,
        "narration": text
    }
    
    await resolve_ledger(update, context, "from")

async def handle_manual_search(update, context, keyword: str, side: str):
    cache = get_ledger_cache()
    matches = [k for k in cache.keys() if keyword in k.lower()]
    
    if not matches:
        keyboard = [[InlineKeyboardButton("🔙 Try Again", callback_data=f"transfer_search_{side}")]]
        await update.message.reply_text(f"❌ No ledgers found containing '{keyword}'.", reply_markup=InlineKeyboardMarkup(keyboard))
        return
        
    keyboard = []
    choices = {}
    for i, match_name in enumerate(matches[:10]):
        real_name = match_name.title()
        choices[str(i)] = match_name
        keyboard.append([InlineKeyboardButton(f"{real_name}", callback_data=f"transfer_sel_{side}_{i}")])
        
    context.user_data["transfer_fuzzy_choices"] = choices
    keyboard.append([InlineKeyboardButton("🔍 Search Again", callback_data=f"transfer_search_{side}")])
    
    msg = f"Select the **{'Source (From)' if side == 'from' else 'Destination (To)'}** ledger matching '{keyword}':"
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def resolve_ledger(update, context, side: str):
    draft = context.user_data[DRAFT_KEY]
    raw_name = draft.get(f"raw_{side}", "")
    
    cache = get_ledger_cache()
    if not cache:
        draft[f"{side}_ledger"] = raw_name
        return await advance_workflow(update, context)
        
    lower_map = {k.lower(): k for k in cache.keys()}
    target = raw_name.lower().strip()
    
    # Exact Match
    if target in lower_map:
        draft[f"{side}_ledger"] = lower_map[target]
        return await advance_workflow(update, context)
        
    # Fuzzy match
    all_names = list(lower_map.keys())
    matches = process.extract(target, all_names, scorer=fuzz.token_sort_ratio, limit=5)
    good_matches = [m for m in matches if m[1] > 60]
    
    if good_matches and good_matches[0][1] > 95:
        match_key = good_matches[0][0]
        draft[f"{side}_ledger"] = lower_map[match_key]
        return await advance_workflow(update, context)
        
    if good_matches:
        keyboard = []
        choices = {}
        for i, (match_name, score, _) in enumerate(good_matches):
            real_name = lower_map[match_name]
            choices[str(i)] = match_name
            keyboard.append([InlineKeyboardButton(f"{real_name}", callback_data=f"transfer_sel_{side}_{i}")])
            
        context.user_data["transfer_fuzzy_choices"] = choices
        keyboard.append([InlineKeyboardButton("🔍 Search Manually", callback_data=f"transfer_search_{side}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = f"Could not find exact match for '{raw_name}'.\n\nDid you mean:"
        if update.callback_query:
            await update.callback_query.message.edit_text(msg, reply_markup=reply_markup)
        else:
            await update.message.reply_text(msg, reply_markup=reply_markup)
        return
        
    # Substring match fallback
    matching_ledgers = [ledger for ledger in all_names if target in ledger]
    if matching_ledgers:
        keyboard = []
        choices = {}
        for i, match_name in enumerate(matching_ledgers[:10]):
            real_name = lower_map[match_name]
            choices[str(i)] = match_name
            keyboard.append([InlineKeyboardButton(f"{real_name}", callback_data=f"transfer_sel_{side}_{i}")])
            
        context.user_data["transfer_fuzzy_choices"] = choices
        keyboard.append([InlineKeyboardButton("🔍 Search Manually", callback_data=f"transfer_search_{side}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = f"I found a few ledgers matching '{raw_name}'. Please select the correct one:"
        if update.callback_query:
            await update.callback_query.message.edit_text(msg, reply_markup=reply_markup)
        else:
            await update.message.reply_text(msg, reply_markup=reply_markup)
        return
        
    # No matches
    keyboard = [[InlineKeyboardButton("🔍 Search Manually", callback_data=f"transfer_search_{side}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = f"No ledger found for '{raw_name}'."
    if update.callback_query:
        await update.callback_query.message.edit_text(msg, reply_markup=reply_markup)
    else:
        await update.message.reply_text(msg, reply_markup=reply_markup)

async def advance_workflow(update, context):
    draft = context.user_data[DRAFT_KEY]
    if not draft.get("from_ledger"):
        return await resolve_ledger(update, context, "from")
    if not draft.get("to_ledger"):
        return await resolve_ledger(update, context, "to")
        
    await show_preview(update, context)

async def show_preview(update, context):
    draft = context.user_data[DRAFT_KEY]
    amt = draft["amount"]
    f_led = draft["from_ledger"]
    t_led = draft["to_ledger"]
    d_date = draft.get("display_date", datetime.datetime.now().strftime("%d-%m-%Y"))
    
    msg = f"🔄 **Fund Transfer Summary**\n\n"
    msg += f"📅 **Date:** {d_date}\n"
    msg += f"💰 **Amount:** ₹{amt:,.2f}\n"
    msg += f"📤 **From (Credit):** {f_led}\n"
    msg += f"📥 **To (Debit):** {t_led}\n"
    
    keyboard = [
        [
            InlineKeyboardButton("✏️ Amount", callback_data="transfer_edit_amount"),
            InlineKeyboardButton("✏️ Date", callback_data="transfer_edit_date")
        ],
        [
            InlineKeyboardButton("✏️ From", callback_data="transfer_edit_from"),
            InlineKeyboardButton("✏️ To", callback_data="transfer_edit_to")
        ],
        [InlineKeyboardButton("✅ Confirm & Push", callback_data="transfer_confirm")],
        [InlineKeyboardButton("❌ Cancel", callback_data="transfer_cancel")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.message.edit_text(msg, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode="Markdown")

async def handle_callback(update, context, *, push_to_tally, load_db=None, save_db=None) -> bool:
    query = update.callback_query
    data = query.data
    
    if data.startswith("transfer_search_"):
        await query.answer()
        side = data.split("_")[-1]
        await query.message.edit_text("🔍 Type a keyword to search for the ledger:")
        context.user_data["transfer_state"] = f"WAITING_FOR_{side.upper()}_SEARCH"
        return True
        
    if data.startswith("transfer_edit_"):
        await query.answer()
        field = data.split("_")[-1]
        context.user_data["transfer_state"] = f"WAITING_FOR_EDIT_{field.upper()}"
        prompts = {
            "amount": "Please type the new amount:",
            "date": "Please type the new date (e.g., DD-MM-YYYY or 'today'):",
            "from": "Please type the new source ledger name:",
            "to": "Please type the new destination ledger name:"
        }
        await query.message.reply_text(prompts.get(field, "Please type the new value:"))
        return True
        
    if data.startswith("transfer_sel_"):
        await query.answer()
        parts = data.split("_")
        side = parts[2]
        idx = parts[3]
        
        choices = context.user_data.get("transfer_fuzzy_choices", {})
        if idx in choices:
            raw_key = choices[idx]
            cache = get_ledger_cache()
            lower_map = {k.lower(): k for k in cache.keys()}
            real_name = lower_map.get(raw_key, raw_key.title())
            
            draft = context.user_data[DRAFT_KEY]
            draft[f"{side}_ledger"] = real_name
            context.user_data.pop("transfer_fuzzy_choices", None)
            await advance_workflow(update, context)
        else:
            await query.message.edit_text("Session expired.")
        return True
        
    if data == "transfer_confirm":
        await query.answer()
        draft = context.user_data[DRAFT_KEY]
        xml_bytes = build_transfer_xml(
            draft["date"], 
            draft["from_ledger"], 
            draft["to_ledger"], 
            draft["amount"], 
            draft["narration"]
        )
        
        await query.message.edit_text("⏳ Pushing Contra voucher to Tally...")
        status, response = push_to_tally(
            xml_bytes,
            user_id=update.effective_user.id,
            description=f"Fund Transfer: ₹{draft['amount']} from {draft['from_ledger']} to {draft['to_ledger']}"
        )
        
        is_success = False
        if status == 200:
            if "<CREATED>1</CREATED>" in response or "<CREATED>" in response:
                if "<ERRORS>0</ERRORS>" in response and "<EXCEPTIONS>0</EXCEPTIONS>" in response:
                    is_success = True
                    
        if is_success:
            await query.message.edit_text("✅ Fund Transfer voucher created successfully in Tally!")
        elif status == 202:
            await query.message.edit_text("⏸ Tally is offline. Saved to queue! It will push automatically when your laptop opens.")
        else:
            await query.message.edit_text(f"❌ Failed to push transfer to Tally.\nStatus: {status}\nResponse: {response}")
            
        clear(context)
        return True
        
    if data == "transfer_cancel":
        await query.answer()
        await query.message.edit_text("❌ Transfer cancelled.")
        clear(context)
        return True
        
    return False
