"""Purchase workflow: extraction, review, XML creation, queueing, and export.

This module intentionally owns only Purchase state under ``context.user_data``.
It does not import or alter the Sales, Payment, or Bank Statement workflows.
"""

from __future__ import annotations

import datetime
import json
from io import BytesIO
from typing import Any, Awaitable, Callable
from xml.sax.saxutils import escape

import openpyxl
from PIL import Image
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


MODE = "Purchase"
DRAFT_KEY = "purchase_draft"
EDIT_KEY = "purchase_edit_field"
REQUIRED_KEY = "purchase_required_field"

REQUIRED_FIELDS = (
    ("supplier", "Who is the supplier?"),
    ("date", "On which date was this invoice issued? You can type the date in any format."),
    ("inv_no", "What is the invoice number?"),
    ("amount", "What is the invoice amount?"),
)


def _sanitize_xml(value: Any) -> str:
    return escape(str(value if value is not None else ""), {'"': "&quot;", "'": "&apos;"})


def _draft(context) -> dict[str, Any] | None:
    return context.user_data.get(DRAFT_KEY)


def _set_draft(context, data: dict[str, Any]) -> None:
    context.user_data[DRAFT_KEY] = data


def clear(context) -> None:
    context.user_data.pop(DRAFT_KEY, None)
    context.user_data.pop(EDIT_KEY, None)
    context.user_data.pop(REQUIRED_KEY, None)


def has_draft(context) -> bool:
    return _draft(context) is not None


def is_editing(context) -> bool:
    return EDIT_KEY in context.user_data


def build_xml(invoice_data: dict[str, Any]) -> bytes:
    """Build exactly one Purchase voucher XML document from a reviewed draft."""
    date_raw = str(invoice_data.get("date", ""))
    try:
        day, month, year = date_raw.split("-")
        date_str = f"{year}{month}{day}"
    except ValueError:
        date_str = datetime.datetime.now().strftime("%Y%m%d")

    inv_no = _sanitize_xml(invoice_data.get("inv_no", ""))
    amount = invoice_data.get("amount", 0)
    supplier = _sanitize_xml(invoice_data.get("supplier", "Unknown"))
    cost_center = invoice_data.get("cost_center")
    if cost_center in ("None", "null", None, ""):
        cost_center = None

    cost_center_xml = ""
    if cost_center:
        escaped_center = _sanitize_xml(cost_center)
        cost_center_xml = f"""
              <CATEGORYALLOCATIONS.LIST>
                <CATEGORY>Primary Cost Category</CATEGORY>
                <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                <COSTCENTREALLOCATIONS.LIST>
                  <NAME>{escaped_center}</NAME>
                  <AMOUNT>-{amount}</AMOUNT>
                </COSTCENTREALLOCATIONS.LIST>
              </CATEGORYALLOCATIONS.LIST>"""

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
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{supplier}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          <AMOUNT>{amount}</AMOUNT>
          <BILLALLOCATIONS.LIST>
            <NAME>{inv_no}</NAME><BILLTYPE>New Ref</BILLTYPE><AMOUNT>{amount}</AMOUNT>
          </BILLALLOCATIONS.LIST>
        </ALLLEDGERENTRIES.LIST>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>Purchases</LEDGERNAME>
          <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          <AMOUNT>-{amount}</AMOUNT>{cost_center_xml}
        </ALLLEDGERENTRIES.LIST>
      </VOUCHER>
    </TALLYMESSAGE></REQUESTDATA>
  </IMPORTDATA></BODY>
</ENVELOPE>"""
    return xml.encode("utf-8")


def _prompt(user_text: str) -> str:
    today = datetime.datetime.now().strftime("%d-%m-%Y")
    year = datetime.datetime.now().year
    return f"""
The current date is {today}. Extract the following details from the user's text: supplier, inv_no, date, amount.

CRITICAL DATE RULES:
1. Convert natural language dates ('today', 'yesterday', '1april', '31 jul') to DD-MM-YYYY.
2. If the year is missing, assume {year}.
3. Return only a JSON object with supplier, inv_no, date, amount.

User text:
{user_text}
"""


def _to_draft(extracted: dict[str, Any]) -> dict[str, Any]:
    amount = extracted.get("amount", 0.0)
    try:
        amount = float(amount if amount is not None else 0.0)
    except (TypeError, ValueError):
        amount = 0.0
    return {
        "supplier": str(extracted.get("supplier", "Unknown")),
        "date": str(extracted.get("date", "Unknown")),
        "inv_no": str(extracted.get("inv_no", "Unknown")),
        "amount": amount,
        "cost_center": "None",
    }


def _is_missing(field: str, value: Any) -> bool:
    if field == "amount":
        try:
            return float(value) <= 0
        except (TypeError, ValueError):
            return True
    return value is None or str(value).strip().lower() in {"", "none", "null", "unknown", "n/a"}


async def prompt_for_missing_field(update, context) -> bool:
    """Ask for the next missing required field; return True when a question was sent."""
    draft = _draft(context)
    if not draft:
        return False
    for field, question in REQUIRED_FIELDS:
        if _is_missing(field, draft.get(field)):
            context.user_data[REQUIRED_KEY] = field
            await update.message.reply_text(question)
            return True
    context.user_data.pop(REQUIRED_KEY, None)
    return False


async def show_preview(update, context) -> None:
    data = _draft(context)
    if not data:
        return
    text = (
        "**Active Mode: 🛒 Purchase**\n\n"
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
            InlineKeyboardButton("Gulshan", callback_data="cc_Gulshan"),
        ],
        [
            InlineKeyboardButton("✏️ Edit Amount", callback_data="edit_amount"),
            InlineKeyboardButton("✏️ Edit Inv No", callback_data="edit_inv_no"),
        ],
        [
            InlineKeyboardButton("✏️ Edit Supplier", callback_data="edit_supplier"),
            InlineKeyboardButton("✏️ Edit Date", callback_data="edit_date"),
        ],
        [
            InlineKeyboardButton("➕ Save & Add Next", callback_data="save_next"),
            InlineKeyboardButton("🚀 Push to Tally", callback_data="push_purchase_tally"),
        ],
    ]
    markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")


async def process_text(update, context, generate_gemini: Callable[..., Awaitable[str]]) -> None:
    processing = await update.message.reply_text("Processing purchase text with Gemini...")
    try:
        response_text = await generate_gemini(_prompt(update.message.text), json_mode=True)
        _set_draft(context, _to_draft(json.loads(response_text.strip())))
        await processing.delete()
        if not await prompt_for_missing_field(update, context):
            await show_preview(update, context)
    except Exception as exc:
        await processing.edit_text(f"Error processing text: {exc}")


async def process_photo(update, context, photo_bytes: bytearray, generate_gemini: Callable[..., Awaitable[str]]) -> None:
    processing = await update.message.reply_text("Processing invoice image with Gemini...")
    try:
        image = Image.open(BytesIO(photo_bytes))
        if image.mode != "RGB":
            image = image.convert("RGB")
        contents = [
            "Extract supplier, invoice number, date (DD-MM-YYYY), and total amount from this purchase invoice. Return only JSON with supplier, inv_no, date, amount.",
            image,
        ]
        response_text = await generate_gemini(contents, json_mode=True)
        _set_draft(context, _to_draft(json.loads(response_text.strip())))
        await processing.delete()
        if not await prompt_for_missing_field(update, context):
            await show_preview(update, context)
    except Exception as exc:
        await processing.edit_text(f"Error processing photo: {exc}")


async def receive_pending_field(update, context, generate_gemini: Callable[..., Awaitable[str]]) -> bool:
    """Consume a text response for a required field or an explicit edit."""
    field = context.user_data.get(REQUIRED_KEY) or context.user_data.get(EDIT_KEY)
    if not field:
        return False
    draft = _draft(context)
    if not draft:
        context.user_data.pop(EDIT_KEY, None)
        return False
    value = update.message.text.strip()
    is_required_answer = field == context.user_data.get(REQUIRED_KEY)
    if field == "amount":
        try:
            draft[field] = float(value)
        except ValueError:
            await update.message.reply_text("Please enter a valid number for amount.")
            return True
    elif field == "date":
        prompt = f"Convert '{value}' into DD-MM-YYYY. Return only the date. Current year: {datetime.datetime.now().year}."
        try:
            draft[field] = (await generate_gemini(prompt)).strip()
        except Exception:
            draft[field] = value
    else:
        draft[field] = value
    context.user_data.pop(REQUIRED_KEY, None)
    context.user_data.pop(EDIT_KEY, None)
    if is_required_answer and await prompt_for_missing_field(update, context):
        return True
    await show_preview(update, context)
    return True


async def handle_callback(
    update,
    context,
    *,
    load_db: Callable[[str], dict],
    save_db: Callable[[dict, str], None],
    push_to_tally: Callable[[bytes], tuple[int, str]],
) -> bool:
    """Handle only Purchase callback data. Returns False for other workflows."""
    query = update.callback_query
    data = query.data
    purchase_callbacks = {"save_next", "push_purchase_tally", "download_xml_purchase"}
    if not (data.startswith("cc_") or data.startswith("edit_") or data in purchase_callbacks):
        return False
    if not has_draft(context) and data != "generate_excel":
        await query.message.reply_text("No active purchase session found. Please send an image or text first.")
        return True

    if data.startswith("cc_"):
        _draft(context)["cost_center"] = data.split("_", 1)[1]
        await show_preview(update, context)
    elif data.startswith("edit_"):
        field = data.split("_", 1)[1]
        if field not in {"amount", "inv_no", "supplier", "date"}:
            return False
        context.user_data[EDIT_KEY] = field
        await query.message.reply_text(f"Please type the new value for {field.replace('_', ' ').title()}:")
    elif data == "save_next":
        user_id = query.from_user.id
        db = load_db(MODE)
        key = str(user_id)
        db.setdefault(key, []).append(_draft(context).copy())
        save_db(db, MODE)
        clear(context)
        await query.message.reply_text(
            f"Invoice added to queue (Total: {len(db[key])}). Send the next photo or text.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]),
        )
    elif data == "push_purchase_tally":
        draft = _draft(context)
        status_code, response_text = push_to_tally(
            build_xml(draft),
            user_id=update.effective_user.id,
            description=f"Purchase: Invoice {draft.get('inv_no', 'N/A')} from {draft.get('supplier', 'Unknown')}"
        )
        if status_code == 200:
            await query.message.edit_text(f"✅ Success! Invoice {draft.get('inv_no', 'N/A')} from {draft.get('supplier', 'Unknown')} pushed to Tally.")
            clear(context)
        elif status_code == 202:
            await query.message.edit_text("⏸ Tally is offline. Saved to queue! It will push automatically when your laptop opens.")
            clear(context)
        elif status_code == 500:
            keyboard = [
                [InlineKeyboardButton("📥 Download XML", callback_data="download_xml_purchase")],
                [InlineKeyboardButton("🚀 Retry Push to Tally", callback_data="push_purchase_tally")],
            ]
            await query.message.edit_text("⚠️ Shop PC is offline or Tally is closed. Your transaction is still available to retry.", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.message.edit_text(f"⚠️ Failed to push to Tally (HTTP {status_code}). Please fix the supplier name or details.\nResponse: {response_text[:200]}")
    elif data == "download_xml_purchase":
        await context.bot.send_document(chat_id=query.from_user.id, document=BytesIO(build_xml(_draft(context))), filename="purchase_voucher.xml", caption="Here is your offline XML file.")
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
        await context.bot.send_message(chat_id=user_id, text="No invoices to generate Excel for Purchase. Please add an invoice first.")
        return

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = MODE
    sheet.append(["SUPPLIER", "DATE", "INV NO", "AMOUNT", "PURCHASE LEDGER", "COST CENTER", "ACCOUNTING MODE", "INV TYPE"])
    for invoice in db[key]:
        sheet.append([
            invoice.get("supplier", "Unknown"), invoice.get("date", "Unknown"), invoice.get("inv_no", "Unknown"), invoice.get("amount", 0.0),
            "Purchase", invoice.get("cost_center", "None"), "As Voucher", "Purchase",
        ])
    file = BytesIO()
    workbook.save(file)
    file.seek(0)
    filename = f"Purchase_{next_number()}.xlsx"
    await context.bot.send_document(
        chat_id=user_id,
        document=file,
        filename=filename,
        caption=f"Here is your generated Purchase Excel file: {filename}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]),
    )
    db[key] = []
    save_db(db, MODE)
