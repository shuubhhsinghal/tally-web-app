import os
import re
import cv2
import numpy as np
import tempfile
import datetime
import json
from typing import Optional, List
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from json_repair import repair_json

from backend.services.extraction_v3.image_preprocessor import flatten_document
from backend.services.extraction_v3.table_detector import crop_item_table
from backend.services.extraction_v3.metadata_extractor import call_metadata_extraction
from backend.services.reconciliation import safe_float

# ─────────────────────────────────────────────────────────────────────────────
# Image pre-processing
# ─────────────────────────────────────────────────────────────────────────────

def preprocess_image(image_bytes: bytes) -> bytes:
    """Auto-deskew, increase contrast, and sharpen for better OCR."""
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    contrast_img = clahe.apply(gray)
    _, buffer = cv2.imencode('.jpg', contrast_img)
    return buffer.tobytes()


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schema — pure transcription (Phase 1 of 3)
# Gemini acts as a raw OCR transcriber only. It never calculates, interprets,
# or infers Tally amounts. Every numeric field is optional/nullable because
# printed invoices vary and a column may be missing.
# ─────────────────────────────────────────────────────────────────────────────

class PrintedItemRow(BaseModel):
    reasoning: str = Field(description=(
        "Step-by-step explanation of what numbers and units are visibly printed on this row. "
        "Describe what you see; never compute or derive values."
    ))
    name: str = Field(description=(
        "The item/product description as printed on the invoice row. "
        "Transcribe the name verbatim exactly as printed (do not translate or edit)."
    ))
    printed_qty: Optional[float] = Field(default=None, description="Quantity as printed on the invoice row.")
    printed_uom: Optional[str] = Field(default=None, description=(
        "Unit of measure as printed on the invoice row (e.g., PCS, KGS, PKT, NOS, LTR). "
        "Transcribe exactly what is printed. Leave null if no UOM column is present."
    ))
    printed_rate: Optional[float] = Field(default=None, description="Rate as printed on the invoice row.")
    printed_discount_pct: Optional[float] = Field(default=None, description="Discount percentage as printed on the invoice row.")
    printed_gst_pct: Optional[float] = Field(default=None, description="GST percentage as printed on the invoice row (e.g., 5.0, 18.0).")
    printed_amount: Optional[float] = Field(default=None, description="Amount as printed on the invoice row.")


class PrintTranscriptionResponse(BaseModel):
    """Flat dictionary schema — no nested column arrays. Every numeric field and
    the UOM (except `reasoning`) are nullable so missing printed columns do not
    force empty arrays."""
    detected_headers: List[str]
    items: List[PrintedItemRow]


# ─────────────────────────────────────────────────────────────────────────────
# Gemini item extraction (pure transcription schema)
# ─────────────────────────────────────────────────────────────────────────────

def call_gemini_extraction(images: list[bytes], column_mapping: dict = None, is_retry: bool = False) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not configured")

    client = genai.Client(api_key=api_key)

    uploaded_files = []
    tmp_paths = []
    for img in images:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            tmp.write(img)
            tmp_paths.append(tmp.name)

    try:
        for tmp_path in tmp_paths:
            uploaded_files.append(client.files.upload(file=tmp_path))

        today = datetime.datetime.now().strftime("%d-%m-%Y")
        year = datetime.datetime.now().year

        base_prompt = f"""
        The current date is {today}.
        You are a precise OCR transcription assistant extracting itemized rows from Indian purchase invoices.
        You are receiving an image that has been strictly cropped to ONLY contain the item rows.

        CRITICAL BOUNDARIES:
        - ONLY extract rows inside the main item table.
        - IGNORE headers, footers, bank details, terms, GST summary, transport, and totals.

ITEM EXTRACTION OUTPUT FORMAT:
        Extract every line item from the invoice table into a JSON object with these EXACT keys:
        - "reasoning": step-by-step description of what numbers/units are visibly printed
        - "name": full item description
        - "printed_qty": quantity as a number (e.g. 6.0)
        - "printed_uom": unit of measure (e.g. "PCS", "KGS", "PKT")
        - "printed_rate": the printed unit rate/price as a number (e.g. 106.66)
        - "printed_discount_pct": printed discount percentage as a number (e.g. 0.0 or 50.0)
        - "printed_gst_pct": printed GST percentage as a number (e.g. 5.0, 18.0, 40.0)
        - "printed_amount": printed line amount/total as a number (e.g. 671.96)

        Also return "detected_headers" as a list of strings at the root level, containing the 
        actual visible table header names from left to right. IMPORTANT: Do NOT split multi-line 
        headers into multiple list items. For example, if a column header spans two lines like 
        "Description" over "of Goods", combine them into a single string like "Description of Goods". 
        The length of the `detected_headers` list MUST exactly match the number of columns in the table.
        Do not convert every heading into generic terms. Preserve terms like "Rate (Incl. of Tax)".

        DO NOT return empty lists or nested objects for `printed_rate` or `printed_amount`. Return raw numbers
        directly in "printed_rate" and "printed_amount".

CRITICAL TRANSCRIPTION RULES:
        0. ITEM NAME (CRITICAL): For every row, transcribe the item/product description into the
           `name` field, verbatim exactly as printed on the invoice. Do NOT translate, abbreviate,
           or edit the name. If the item name wraps to multiple lines, merge them into one name.
        1. ROW ALIGNMENT (CRITICAL): Use the Serial Number on the far left as a strict anchor.
           Read perfectly straight across the row. Never mix values from two different rows.
        2. MULTI-LINE ITEMS: Item names frequently wrap to a second line on printed invoices.
           Merge them into ONE item row. Do NOT create a new item entry unless the line has
           its own distinct quantity, rate, and amount values.
        3. PURE TRANSCRIPTION (CRITICAL — DO NOT INTERPRET OR CALCULATE):
           - Transcribe ONLY what is visibly printed on the row.
           - NEVER compute, derive, or infer any value. No GST math, no ex-GST conversions,
             no qty × rate multiplication, no totals, no rounding.
           - If a value is not printed on the row, leave that field as null.
        4. REASONING FIELD (CRITICAL): For every row, fill `reasoning` with a short
           step-by-step description of exactly which numbers are visibly printed on that row
and in which printed columns. This is for auditability and enables downstream
           deterministic logic to interpret the raw printed data.
        5. DISCOUNTS: If a "Disc %" or "Discount" column is printed, transcribe the percentage.
           Otherwise set `printed_discount_pct` to null.
        6. UNIT OF MEASURE (UOM): If a "UOM", "Unit", or "Packing" column is printed on the row,
           transcribe it exactly into `printed_uom` (e.g., PCS, KGS, PKT, NOS, LTR, BTL).
           Do NOT standardize or translate the unit — copy it verbatim as printed.
           If no unit column is printed on the row, set `printed_uom` to null.
        7. INDIAN INVOICE COLUMN HEADER MAPPING (CRITICAL): Indian invoices use many
           different header names for the same underlying data. Map the invoice table columns as follows:
           - "name": full item description
           - "printed_qty": quantity as a number
           - "printed_uom": unit of measure (e.g., "PCS", "KGS")
           - "printed_rate": the actual billed unit price (Look for headers like "Price Rs.", "Net Rate", "Billed Rate", or "Taxable Rate"). 
           - "printed_discount_pct": printed discount percentage
           - "printed_gst_pct": printed GST or tax percentage
           - "printed_amount": printed line amount/total

           CRITICAL CONSTRAINTS FOR RATE: 
           If the invoice has both a "List Price" (or "MRP") and a "Price Rs." column, you MUST extract the "Price Rs." as the rate. NEVER extract "List Price" or "MRP" as the rate if a separate billed rate column exists.
           
           In your `reasoning` field, explicitly state which column header you used to determine the rate.
        8. FLAT SCHEMA ONLY (CRITICAL): Return a flat dictionary for each item.
           Do NOT return nested arrays like `rate_columns`, `amount_columns`,
           `_rate_columns`, or `_amount_columns`. Only the flat keys `reasoning`,
           `name`, `printed_qty`, `printed_uom`, `printed_rate`,
           `printed_discount_pct`, `printed_gst_pct`, and `printed_amount` are allowed.
        9. DO NOT OMIT PRINTED NUMBERS (CRITICAL): If a number is visibly printed in the
           rate column, tax column, or the amount column on the invoice image, you MUST transcribe it.
           Do NOT return 0 or null for `printed_rate` or `printed_amount` when there are numbers printed
           in those columns. Only return null when the column is genuinely absent or blank
           on that row.

        Return ONLY valid JSON matching the enforced response schema.
        """

        if column_mapping:
            base_prompt += f"""
        MAPPED EXTRACTION - STRICT COLUMN INSTRUCTIONS:
        The user has explicitly identified the meaning of these invoice columns:
        Quantity = "{column_mapping.get('qty_header', 'Not present')}"
        UOM = "{column_mapping.get('uom_header', 'Not present')}"
        Rate = "{column_mapping.get('rate_header', 'Not present')}"
        Discount = "{column_mapping.get('discount_header', 'Not present')}"
        Amount = "{column_mapping.get('amount_header', 'Not present')}"

        You MUST use exactly these column headers to extract the respective fields.
        Read every physical item row straight across.
        Do NOT choose another Rate/Price column.
        Do NOT substitute MRP.
        Do NOT substitute List Price.
        Do NOT choose "Rate Incl Tax" when the selected Rate is "Price".
        Do NOT derive values.
        Do NOT calculate GST.
        Do NOT apply discount.
        Simply transcribe the cells from the exact selected columns.
        If a selected cell is blank, unreadable, or missing (e.g. no discount printed on this row), return null.
        NEVER borrow a discount, rate, or any number from an adjacent row.
        A blank discount cell must behave as null (0%), not inherit from another row.
        The backend will perform all accounting interpretation.
        """

        if is_retry:
            base_prompt += """
        WARNING: The previous transcription was insufficient.
        Re-read the item table carefully. Make sure you have transcribed EVERY row and
        every printed number (quantity, rate, discount, amount) that appears on each row.
        Do not guess or calculate — only transcribe what is visibly printed.
        """

        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=uploaded_files + [base_prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=PrintTranscriptionResponse,
            ),
        )

        for uf in uploaded_files:
            client.files.delete(name=uf.name)
        for p in tmp_paths:
            os.unlink(p)

        raw_text = response.text.strip()
        raw_text = re.sub(r'^```json\s*', '', raw_text)
        raw_text = re.sub(r'\s*```$', '', raw_text)
        data = repair_json(raw_text, return_objects=True)

        if not isinstance(data, dict):
            data = {}

        return data

    except Exception as e:
        for p in tmp_paths:
            if os.path.exists(p):
                os.unlink(p)
        raise e


# ─────────────────────────────────────────────────────────────────────────────
# Backward-compat shim — kept so purchase_item.py (stock matching) still works
# ─────────────────────────────────────────────────────────────────────────────

def validate_and_score_items(extracted_data: dict, is_retry: bool = False) -> dict:
    """
    DEPRECATED — now a thin shim.  Real reconciliation happens in reconcile_invoice_items().
    This function is called from the old code path in purchase_item.py (stock matching stage)
    but item-level math validation is no longer meaningful here because the items have
    already been reconciled before this point.  We just propagate them unchanged.
    """
    return extracted_data


# ─────────────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

def process_invoice(images: list[bytes], column_mapping: dict = None) -> dict:
    # V3 Preprocessing Pipeline
    flattened_images = [flatten_document(img) for img in images]

    # 1. Metadata Extraction (Un-cropped) — now also captures printed_subtotal / printed_grand_total
    print("--- [EXTRACTION ENGINE] Step 1: Metadata Extraction ---", flush=True)
    metadata = call_metadata_extraction(flattened_images)

    gst_rate_pct       = float(metadata.get("gst_rate") or 0)
    printed_subtotal   = metadata.get("printed_subtotal")
    printed_grand_total = metadata.get("printed_grand_total")
    if printed_subtotal is not None:
        printed_subtotal = safe_float(printed_subtotal) or None
    if printed_grand_total is not None:
        printed_grand_total = safe_float(printed_grand_total) or None

    # 2. Crop Image (only crop the first page where headers usually are)
    cropped_images = [crop_item_table(flattened_images[0])] + flattened_images[1:]

    # 3. Item Extraction (Cropped) — model returns a flat schema (no nested arrays)
    print("--- [EXTRACTION ENGINE] Step 2: Item Extraction (flat schema) ---", flush=True)
    item_data = call_gemini_extraction(cropped_images, column_mapping=column_mapping, is_retry=False)
    raw_items = item_data.get("items", [])
    detected_headers = item_data.get("detected_headers", [])

# 4. Flat schema is enforced end-to-end. Each item carries its own raw
    #    `printed_*` fields (printed_qty, printed_uom, printed_rate,
    #    printed_discount_pct, printed_amount) plus `name` and `reasoning`,
    #    transcribed directly from the printed row. The deterministic math
    #    reconciler runs downstream in purchase_item.py (reconcile_full_invoice),
    #    so we do NOT re-map via the legacy nested-column reconciler here (which
    #    would zero out rate/amount and emit empty `_rate_columns`/`_amount_columns`
    #    arrays).
    print(f"--- [EXTRACTION ENGINE] Step 3: Passing {len(raw_items)} flat items "
          f"(gst_rate={gst_rate_pct}%, printed_subtotal={printed_subtotal}, "
          f"printed_grand_total={printed_grand_total}) ---", flush=True)

    # 5. Merge back — items already have flat raw keys (name, reasoning, printed_*).
    #    The deterministic reconciler maps these to Tally-ready qty/uom/rate/discount/amount.
    metadata["items"] = raw_items
    metadata["detected_headers"] = detected_headers

    return metadata
