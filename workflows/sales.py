"""Sales workflow kept independent from Purchase, Payment, and bank imports."""

from __future__ import annotations

import datetime
import json
from io import BytesIO
from typing import Any, Awaitable, Callable
from xml.sax.saxutils import escape

import openpyxl
from PIL import Image
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


MODE = "Sales"
DRAFT_KEY = "sales_draft"
EDIT_KEY = "sales_edit_field"


def _sanitize_xml(value: Any) -> str:
    return escape(str(value if value is not None else ""), {'"': "&quot;", "'": "&apos;"})


def _draft(context) -> dict[str, Any] | None:
    return context.user_data.get(DRAFT_KEY)


def has_draft(context) -> bool:
    return _draft(context) is not None


def clear(context) -> None:
    context.user_data.pop(DRAFT_KEY, None)
    context.user_data.pop(EDIT_KEY, None)


def sales_account(payment_type: str, cost_center: str) -> str:
    payment_type = payment_type.lower()
    cost_center = cost_center.lower()
    if payment_type == "cash":
        return {"mahagun": "Cash Mahagun", "vvip": "Cash Vvip", "gulshan": "Cash Gulshan"}.get(cost_center, "Unknown Cash")
    if cost_center == "vvip" and ("gpay" in payment_type or "google pay" in payment_type):
        return "Gpay Vvip"
    return {"mahagun": "Paytm Mahagun", "vvip": "Paytm Vvip", "gulshan": "Paytm Gulshan"}.get(cost_center, "Unknown Paytm")


def build_xml(sales_data: dict[str, Any]) -> bytes:
    try:
        date_str = datetime.datetime.strptime(str(sales_data.get("date", "")), "%d-%m-%Y").strftime("%Y%m%d")
    except ValueError:
        date_str = datetime.datetime.now().strftime("%Y%m%d")
    amount = sales_data.get("amount", 0)
    account = _sanitize_xml(sales_data.get("account", "Cash"))
    cost_center = sales_data.get("cost_center")
    if cost_center in ("None", "null", None, ""):
        cost_center = None
    allocation = ""
    if cost_center:
        allocation = f"""
              <CATEGORYALLOCATIONS.LIST>
                <CATEGORY>Primary Cost Category</CATEGORY>
                <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
                <COSTCENTREALLOCATIONS.LIST>
                  <NAME>{_sanitize_xml(cost_center)}</NAME>
                  <AMOUNT>{amount}</AMOUNT>
                </COSTCENTREALLOCATIONS.LIST>
              </CATEGORYALLOCATIONS.LIST>"""
    xml = f"""<ENVELOPE>
  <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
  <BODY><IMPORTDATA>
    <REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC>
    <REQUESTDATA><TALLYMESSAGE>
      <VOUCHER VCHTYPE="Sales" ACTION="Create">
        <DATE>{date_str}</DATE><NARRATION>Sales Entry</NARRATION><VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{account}</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-{amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>Sales</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>{amount}</AMOUNT>{allocation}
        </ALLLEDGERENTRIES.LIST>
      </VOUCHER>
    </TALLYMESSAGE></REQUESTDATA>
  </IMPORTDATA></BODY>
</ENVELOPE>"""
    return xml.encode("utf-8")


def _prompt(text: str) -> str:
    current_date = datetime.datetime.now().strftime("%d %B %Y")
    return f"""CRITICAL INSTRUCTION: You must respond INSTANTLY. DO NOT generate any reasoning, thoughts, or <think> blocks. Output ONLY the raw JSON object and nothing else.

You are an expert OCR extractor. Extract exactly one sales entry as JSON.
Keys: date (DD-MM-YYYY; default today), amount (float), cost_center (Mahagun, Vvip, Gulshan; default None), payment_type (cash, online, or gpay; default cash).
The user may enter only one sale per message. Do not create multiple records.
The current date is {current_date}. Convert natural language dates into DD-MM-YYYY.
Return only valid JSON.

User text:
{text}"""


def _record(raw: dict[str, Any]) -> dict[str, Any]:
    payment_type = str(raw.get("payment_type", "cash")).lower()
    cost_center = str(raw.get("cost_center", "None")).title()
    try:
        amount = float(raw.get("amount", 0.0) if raw.get("amount") is not None else 0.0)
    except (TypeError, ValueError):
        amount = 0.0
    return {
        "date": str(raw.get("date", "Unknown")),
        "amount": amount,
        "cost_center": cost_center,
        "payment_type": payment_type,
        "account": sales_account(payment_type, cost_center),
    }


async def show_preview(update, context) -> None:
    data = _draft(context)
    if not data:
        return
    payment_type = data.get("payment_type", "cash")
    cost_center = data.get("cost_center", "None")
    account = sales_account(payment_type, cost_center)
    data["account"] = account
    text = (
        "**Active Mode: 📈 Sales**\n\n"
        "📄 *Extracted Sales Data*:\n"
        f"• ACCOUNT: {account}\n"
        f"• PAYMENT TYPE: {payment_type.title()}\n"
        f"• AMOUNT: {data.get('amount', 0.0)}\n"
        f"• DATE: {data.get('date', 'Unknown')}\n"
        f"• COST CENTER: {cost_center}"
    )
    keyboard = [
        [InlineKeyboardButton("Mahagun", callback_data="cc_Mahagun"), InlineKeyboardButton("Vvip", callback_data="cc_Vvip"), InlineKeyboardButton("Gulshan", callback_data="cc_Gulshan")],
        [InlineKeyboardButton("✏️ Edit Amount", callback_data="edit_amount"), InlineKeyboardButton("✏️ Edit Date", callback_data="edit_date")],
        [InlineKeyboardButton("🚀 Push to Tally", callback_data="push_sales_tally")],
    ]
    if update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def _accept_records(update, context, records: list[dict[str, Any]]) -> None:
    normalized = [_record(record) for record in records]
    if len(normalized) != 1:
        await update.message.reply_text("⚠️ Please send only one sales entry at a time.")
        return
    context.user_data[DRAFT_KEY] = normalized[0]
    await show_preview(update, context)


async def process_text(update, context, generate_gemini) -> None:
    processing = await update.message.reply_text("Processing sales text with Gemini...")
    try:
        raw = json.loads((await generate_gemini(_prompt(update.message.text), json_mode=True, model_name="gemini-3.5-flash-lite")).strip())
        records = raw.get("records", []) or [raw]
        await processing.delete()
        await _accept_records(update, context, records)
    except Exception as exc:
        await processing.edit_text(f"Error processing text: {exc}")


async def process_photo(update, context, photo_bytes: bytearray, generate_gemini) -> None:
    processing = await update.message.reply_text("Processing invoice image with Gemini...")
    try:
        image = Image.open(BytesIO(photo_bytes))
        if image.mode != "RGB":
            image = image.convert("RGB")
        instruction = "CRITICAL INSTRUCTION: You must respond INSTANTLY. DO NOT generate any reasoning, thoughts, or <think> blocks. Output ONLY the raw JSON object and nothing else.\n\nExtract exactly one sales entry from this image. Return JSON with date, amount, cost_center, and payment_type; use DD-MM-YYYY for the date."
        raw = json.loads((await generate_gemini([instruction, image], json_mode=True, model_name="gemini-3.5-flash-lite")).strip())
        records = raw.get("records", []) or [raw]
        await processing.delete()
        await _accept_records(update, context, records)
    except Exception as exc:
        await processing.edit_text(f"Error processing photo: {exc}")


async def receive_edit(update, context, generate_gemini) -> bool:
    field = context.user_data.get(EDIT_KEY)
    if not field:
        return False
    draft = _draft(context)
    if not draft:
        context.user_data.pop(EDIT_KEY, None)
        return False
    value = update.message.text.strip()
    if field == "amount":
        try:
            draft[field] = float(value)
        except ValueError:
            await update.message.reply_text("Please enter a valid number for amount.")
            return True
    else:
        try:
            prompt = f"Convert '{value}' into DD-MM-YYYY. Return only the date. Current year: {datetime.datetime.now().year}."
            draft[field] = (await generate_gemini(prompt, model_name="gemini-3.5-flash-lite")).strip()
        except Exception:
            draft[field] = value
    context.user_data.pop(EDIT_KEY, None)
    await show_preview(update, context)
    return True


async def handle_callback(update, context, *, push_to_tally) -> bool:
    query = update.callback_query
    data = query.data
    callbacks = {"edit_amount", "edit_date", "push_sales_tally", "download_xml_sales"}
    if not (data.startswith("cc_") or data in callbacks):
        return False
    if not has_draft(context):
        await query.message.reply_text("No active sales session found. Please send an image or text first.")
        return True
    if data.startswith("cc_"):
        _draft(context)["cost_center"] = data.split("_", 1)[1]
        await show_preview(update, context)
    elif data.startswith("edit_"):
        context.user_data[EDIT_KEY] = data.split("_", 1)[1]
        await query.message.reply_text(f"Please type the new value for {data.split('_', 1)[1].title()}:")
    elif data == "push_sales_tally":
        draft = _draft(context)
        status_code, response_text = push_to_tally(
            build_xml(draft),
            user_id=update.effective_user.id,
            description=f"Sales: ₹{draft.get('amount', 0)} for {draft.get('cost_center', 'None')}"
        )
        if status_code == 200:
            await query.message.edit_text(f"✅ Success! Sales voucher of {draft.get('amount', 0)} for {draft.get('cost_center', 'None')} pushed to Tally.")
            clear(context)
        elif status_code == 202:
            await query.message.edit_text("⏸ Tally is offline. Saved to queue! It will push automatically when your laptop opens.")
            clear(context)
        elif status_code == 500:
            keyboard = [[InlineKeyboardButton("📥 Download XML", callback_data="download_xml_sales")], [InlineKeyboardButton("🚀 Retry Push to Tally", callback_data="push_sales_tally")]]
            await query.message.edit_text("⚠️ Shop PC is offline or Tally is closed. Your transaction is still available to retry.", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.message.edit_text(f"⚠️ Failed to push to Tally (HTTP {status_code}).\nResponse: {response_text[:200]}")
    else:
        await context.bot.send_document(chat_id=query.from_user.id, document=BytesIO(build_xml(_draft(context))), filename="sales_voucher.xml", caption="Here is your offline XML file.")
    return True


async def export_queue(context, user_id: int, load_db, save_db, next_number) -> None:
    db = load_db(MODE)
    key = str(user_id)
    db.setdefault(key, [])
    if has_draft(context):
        db[key].append(_draft(context).copy())
        save_db(db, MODE)
        clear(context)
    if not db[key]:
        await context.bot.send_message(chat_id=user_id, text="No invoices to generate Excel for Sales. Please add an invoice first.")
        return
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = MODE
    sheet.append(["DATE", "ACCOUNT", "SALES", "COST CENTER", "VCH TYPE", "AMOUNT", "accounting mode"])
    for sale in db[key]:
        sheet.append([sale.get("date", "Unknown"), sales_account(sale.get("payment_type", "cash"), sale.get("cost_center", "None")), "SALES", sale.get("cost_center", "None"), "SALES", sale.get("amount", 0.0), "Accounting Invoice"])
    file = BytesIO()
    workbook.save(file)
    file.seek(0)
    filename = f"Sales_{next_number()}.xlsx"
    await context.bot.send_document(chat_id=user_id, document=file, filename=filename, caption=f"Here is your generated Sales Excel file: {filename}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]))
    db[key] = []
    save_db(db, MODE)
