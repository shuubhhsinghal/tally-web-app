import os
import re
import cv2
import base64
import numpy as np
import tempfile
import datetime
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from openai import OpenAI
from json_repair import repair_json

from backend.services.image_processing.image_preprocessor import flatten_document
from backend.services.image_processing.table_detector import crop_item_table
from backend.services.extraction_v4.metadata_extractor import call_metadata_extraction_v4
from backend.services.extraction_v4.retry import call_with_retry, get_extraction_provider
from backend.services.reconciliation import safe_float

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schema — pure transcription for V4
# ─────────────────────────────────────────────────────────────────────────────

class AmountCandidate(BaseModel):
    header: str = Field(description="The exact text of the column header this amount belongs to.")
    value: float = Field(description="The extracted monetary value.")

class PrintedItemRowV4(BaseModel):
    reasoning: str = Field(description=(
        "Step-by-step explanation of what numbers and units are visibly printed on this row. "
        "Explicitly list all the amount columns printed."
    ))
    name: str = Field(description="The full item description as printed on the invoice row.")
    qty: Optional[float] = Field(default=None, description="Quantity as printed.")
    uom: Optional[str] = Field(default=None, description="Unit of measure as printed (e.g. PCS, KG).")
    amount_candidates: List[AmountCandidate] = Field(description="Extract ALL monetary total/subtotal values printed on this row (e.g., Taxable Value, Amount, Gross). Exclude unit rate/MRP.", default_factory=list)
    line_amount: Optional[float] = Field(default=None, description="Fallback field if you cannot identify specific headers.")
    gst_rate_on_row: Optional[float] = Field(default=None, description="GST % printed on this row, or null if not printed.")

class PrintTranscriptionResponseV4(BaseModel):
    detected_headers: List[str]
    items: List[PrintedItemRowV4]

# ─────────────────────────────────────────────────────────────────────────────
# Shared prompt (identical instructions regardless of which model reads them)
# ─────────────────────────────────────────────────────────────────────────────

def _build_item_prompt(is_retry: bool = False) -> str:
    today = datetime.datetime.now().strftime("%d-%m-%Y")

    prompt = f"""
        The current date is {today}.
        You are a precise OCR transcription assistant extracting itemized rows from Indian purchase invoices.
        You are receiving one or more image(s) of an invoice, which may span multiple pages. The first page may be cropped to the item table.
        You MUST inspect ALL supplied images/pages. Page 2 and beyond must be treated as a continuation of Page 1.

        CRITICAL BOUNDARIES:
        - ONLY extract rows inside the main item table(s) across ALL pages.
        - Continue ignoring headers, footers, bank details, terms, GST summary, transport, and totals outside the item tables.

        ITEM EXTRACTION OUTPUT FORMAT:
        Extract every line item across ALL pages into ONE SINGLE JSON array with these EXACT keys.
        Do NOT replace or overwrite rows from earlier pages when processing later pages. Preserve the original page/table order:
        - "reasoning": What the AI read — useful for debugging.
        - "name": The item/product DESCRIPTION column text ONLY, as printed. If the table has a
          separate "Item Code"/"SKU"/"Article Code"/"HSN Code" column, you MUST exclude that
          value from "name" entirely -- do not prefix or merge it with the description, even if
          the two columns are printed close together.
        - "qty": Quantity as printed.
          MULTI-PART QUANTITY: some invoices print quantity as a slash-separated triple headed
          "Case/Box/Pc" (or similar) instead of one plain number, e.g. "0/2/0" or "4/0/0". The
          three positions are, in order: number of Cases / number of Boxes / number of loose
          Pieces. Normally only ONE of the three positions is non-zero for a given row -- when
          that's the case, "qty" is simply THAT non-zero number (pure transcription, no
          arithmetic needed), and "uom" is the matching label: "Case" for the 1st position,
          "Box" for the 2nd, "Pc" for the 3rd. Example: "0/2/0" -> qty=2, uom="Box".
          "4/0/0" -> qty=4, uom="Case". "0/0/6" -> qty=6, uom="Pc".
          Never confuse this column with MRP -- MRP is always a separate, single plain number
          (the item's printed retail price) that often sits right next to this breakdown; NEVER
          put an MRP value, or any per-unit Rate, into "qty".
        - "uom": Unit of measure as printed (KG, PCS, etc.) -- see the MULTI-PART QUANTITY rule
          above for what to use when "qty" came from a Case/Box/Pc breakdown.
        - "amount_candidates": A list of objects containing "header" and "value" for EVERY monetary column printed for this row (e.g. Taxable Amount, Total Amount, Net Value). EXCLUDE unit rate/MRP.
        - "gst_rate_on_row": GST % printed on this row, or null if not printed.

        Also return "detected_headers" as a list of strings at the root level, containing the
        actual visible table header names from left to right.

        CRITICAL TRANSCRIPTION RULES FOR V4:
        0. NO RATES: You must NOT extract or calculate an accounting rate. Do NOT include a rate.
        1. NO CALCULATIONS: You must NOT calculate amount from quantity x rate, nor calculate GST, nor apply discounts. Only extract what is printed.
        2. UNREADABLE ROWS: If quantity cannot be reliably read, use null. Never invent missing values or convert missing to zero.
        3. GST RATE ON ROW: ONLY extract gst_rate_on_row if there is a DEDICATED column for GST %. Do NOT extract percentages (like "12%") from the item name or description. If no dedicated column exists, return null.

        AMOUNT CANDIDATES EXTRACTION RULE (CRITICAL):
        You must extract ALL possible amount columns (except rates/MRP/discounts) as candidates, preserving their exact printed column headers.
        For example, if an invoice row has a 'Taxable Value' column and a 'Total Amount' column, extract BOTH as candidates.

        You must NEVER extract the following as an amount candidate:
        - MRP
        - List Price
        - PTR / NLC / Unit Rate
        - Discount columns

        Return ONLY a valid JSON object with this exact structure:
        {{
          "detected_headers": ["...", "..."],
          "items": [
            {{
              "reasoning": "...",
              "name": "...",
              "qty": 0.0,
              "uom": "...",
              "amount_candidates": [{{"header": "...", "value": 0.0}}],
              "line_amount": null,
              "gst_rate_on_row": null
            }}
          ]
        }}
        """

    if is_retry:
        prompt += """
        WARNING: The previous transcription was insufficient or row counts mismatched.
        Re-read the item table carefully row-by-row using the Serial Number as a strict anchor.
        Make sure you have transcribed EVERY row and every printed number.
        Do not guess or calculate — only transcribe what is visibly printed.
        """

    return prompt


def _build_qwen_item_prompt(is_retry: bool = False) -> str:
    """Qwen-specific variant of the item prompt. Differs from _build_item_prompt
    in two ways the Gemini path doesn't need: (1) no "reasoning" field -- Gemini's
    response_schema forces one, but for Qwen it was pure wasted output tokens
    (the largest single cost in a ~14K-token item-extraction call) with nothing
    downstream ever reading it; (2) explicit JSON-number-formatting rules and a
    worked example -- Gemini's response_schema enforces types server-side
    (constrained decoding), but Qwen's looser json_object mode has no such
    enforcement and needs to be told plainly not to emit "5%" or "89.54" as
    strings."""
    today = datetime.datetime.now().strftime("%d-%m-%Y")

    prompt = f"""
        The current date is {today}.
        You are a precise OCR transcription assistant extracting itemized rows from Indian purchase invoices.
        You are receiving one or more image(s) of an invoice, which may span multiple pages.
        You MUST inspect ALL supplied images/pages. Page 2 and beyond must be treated as a continuation of Page 1.

        CRITICAL BOUNDARIES:
        - ONLY extract rows inside the main item table(s) across ALL pages.
        - Ignore headers, footers, bank details, terms, GST summary, transport, and totals outside the item tables.
        - Extract every line item across ALL pages into ONE SINGLE JSON array. Do NOT replace or
          overwrite rows from earlier pages when processing later pages -- preserve the original
          page/table order.

        CRITICAL JSON DATA TYPE RULES (STRICTLY ENFORCED):
        - "qty" and "value" MUST be pure JSON numbers (floats), never strings.
          Correct: 89.54   Incorrect: "89.54"
        - Do NOT include symbols like '%', '₹', or ',' in any number.
          Correct: 5.0   Incorrect: "5%"

        OUTPUT FIELDS (per item row):
        - "name": The item/product DESCRIPTION column text ONLY, as printed. If the table has a
          separate "Item Code"/"SKU"/"Article Code"/"HSN Code" column, exclude that value from
          "name" entirely -- do not prefix or merge it with the description.
        - "qty": Quantity as printed. Use null if it cannot be reliably read -- never invent a
          value or convert a missing one to zero.
          * MULTI-PART QUANTITY: some invoices print quantity as a slash-separated triple headed
            "Case/Box/Pc" (or similar) instead of one plain number, e.g. "0/2/0" or "4/0/0". The
            three positions are, in order: number of Cases / number of Boxes / number of loose
            Pieces. Normally only ONE of the three positions is non-zero for a given row -- when
            that's the case, "qty" is simply THAT non-zero number (pure transcription, no
            arithmetic needed), and "uom" is the matching label: "Case" for the 1st position,
            "Box" for the 2nd, "Pc" for the 3rd. Example: "0/2/0" -> qty=2, uom="Box".
            "4/0/0" -> qty=4, uom="Case". "0/0/6" -> qty=6, uom="Pc".
            Never confuse this column with MRP -- MRP is always a separate, single
            plain number (the item's printed retail price) that often sits right next to this
            breakdown; NEVER put an MRP value, or any per-unit Rate, into "qty".
        - "uom": Unit of measure as printed (KG, PCS, etc.) -- see the MULTI-PART QUANTITY rule
          above for what to use when "qty" came from a Case/Box/Pc breakdown.
        - "amount_candidates": A list of {{"header", "value"}} objects for EVERY monetary total/
          subtotal column printed for this row (e.g. Taxable Value, Total Amount, Net Value).
          A Unit Rate or Price for a single item is NOT a candidate -- if a column equals
          (Total Amount / Qty), it is a Unit Rate. NEVER include MRP, List Price, PTR/NLC/Unit
          Rate, or Discount columns as candidates.
        - "line_amount": Fallback total, ONLY if you cannot confidently assign the row to any of
          the headers you listed in "amount_candidates". Otherwise null.

        Also return "detected_headers" as a list of strings at the root level, containing the
        actual visible table header names from left to right.

        NO CALCULATIONS: You must NOT calculate amount from quantity x rate, nor calculate GST,
        nor apply discounts. Only transcribe numbers that are visibly printed.

        Return ONLY a valid JSON object with this exact structure:
        {{
          "detected_headers": ["...", "..."],
          "items": [
            {{
              "name": "...",
              "qty": 0.0,
              "uom": "...",
              "amount_candidates": [{{"header": "...", "value": 0.0}}],
              "line_amount": null
            }}
          ]
        }}

        EXAMPLE -- a row with Qty=3, Unit Price=89.54, Amount=282.06:
        {{
          "name": "ALOO BHUJIA 420G",
          "qty": 3.0,
          "uom": "Pcs",
          "amount_candidates": [{{"header": "Amount(₹)", "value": 282.06}}],
          "line_amount": null
        }}
        (The Unit Price 89.54 was ignored -- Total/Qty makes it a Unit Rate, not a candidate.)

        Return ONLY valid JSON. Do not include markdown formatting like ```json.
        """

    if is_retry:
        prompt += """
        WARNING: The previous transcription was insufficient or row counts mismatched.
        Re-read the item table carefully row-by-row using the Serial Number as a strict anchor.
        Make sure you have transcribed EVERY row and every printed number.
        Do not guess or calculate — only transcribe what is visibly printed.
        """

    return prompt


def _parse_json_response(raw_text: str) -> dict:
    raw_text = raw_text.strip()
    raw_text = re.sub(r'^```json\s*', '', raw_text)
    raw_text = re.sub(r'\s*```$', '', raw_text)
    data = repair_json(raw_text, return_objects=True)
    return data if isinstance(data, dict) else {}


# ─────────────────────────────────────────────────────────────────────────────
# Gemini item extraction (V4 schema)
# ─────────────────────────────────────────────────────────────────────────────

def _call_gemini_extraction_v4(images: list[bytes], is_retry: bool = False) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not configured")

    # Explicit timeout -- without one, a stalled request (e.g. after Gemini's
    # own retry-worthy 503) can hang the background extraction thread
    # indefinitely instead of failing so the retry/FAILED-status path can
    # take over.
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=60000))

    uploaded_files = []
    tmp_paths = []
    for img in images:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            tmp.write(img)
            tmp_paths.append(tmp.name)

    try:
        # Upload pages concurrently -- see metadata_extractor.py for why.
        with ThreadPoolExecutor(max_workers=max(1, len(tmp_paths))) as pool:
            uploaded_files = list(pool.map(lambda p: client.files.upload(file=p), tmp_paths))

        prompt = _build_item_prompt(is_retry)

        response = call_with_retry(lambda: client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=uploaded_files + [prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=PrintTranscriptionResponseV4,
            ),
        ))
        u = response.usage_metadata
        if u:
            print(f"[GEMINI TOKENS] item table extraction: prompt={u.prompt_token_count} output={u.candidates_token_count} total={u.total_token_count}", flush=True)

        for uf in uploaded_files:
            client.files.delete(name=uf.name)
        for p in tmp_paths:
            os.unlink(p)

        return _parse_json_response(response.text)

    except Exception as e:
        for p in tmp_paths:
            if os.path.exists(p):
                os.unlink(p)
        raise e


# ─────────────────────────────────────────────────────────────────────────────
# Qwen 3.5 Flash item extraction (via OpenRouter's OpenAI-compatible API)
# ─────────────────────────────────────────────────────────────────────────────

def _call_qwen_extraction_v4(images: list[bytes], is_retry: bool = False) -> dict:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not configured")

    model = os.getenv("QWEN_MODEL", "qwen/qwen3.5-flash-02-23")
    client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL, timeout=60.0)

    prompt = _build_qwen_item_prompt(is_retry)
    image_parts = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{base64.b64encode(img).decode()}"},
        }
        for img in images
    ]

    response = call_with_retry(lambda: client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": image_parts + [{"type": "text", "text": prompt}]}],
        response_format={"type": "json_object"},
        # Force Qwen's fast/non-thinking mode -- this is a transcription task,
        # not a reasoning one, and thinking mode burns a large chunk of the
        # per-call latency generating hidden reasoning tokens no one reads.
        extra_body={"reasoning": {"enabled": False}},
        # Safety cap, not a normal ceiling -- generous enough that a real
        # invoice (even a large one) never gets truncated, but bounds the
        # cost of a pathological/runaway generation.
        max_tokens=6000,
    ))
    u = response.usage
    if u:
        print(f"[QWEN TOKENS] item table extraction: prompt={u.prompt_tokens} output={u.completion_tokens} total={u.total_tokens}", flush=True)

    return _parse_json_response(response.choices[0].message.content)


def _build_qwen_combined_prompt() -> str:
    """Single-call Qwen prompt asking for both invoice metadata and item rows
    together. Only viable for Qwen: unlike Gemini (which needs an un-cropped
    page for metadata and a cropped table for items), Qwen's two calls now
    read the identical, unmodified image -- no crop, no enhancement (see
    purchase_drafts.py) -- so there's no accuracy reason left to pay for
    sending that image to the model twice."""
    today = datetime.datetime.now().strftime("%d-%m-%Y")
    year = datetime.datetime.now().year

    return f"""
        The current date is {today}.
        You are a precise OCR and data-extraction assistant analyzing Indian purchase invoices.
        You are receiving one or more image(s) of the full, un-cropped invoice, which may span
        multiple pages. You MUST inspect ALL supplied images/pages. Page 2 and beyond must be
        treated as a continuation of Page 1.

        Extract TWO things from these images into ONE JSON response: (1) invoice-level metadata
        from the header/footer/tax-summary areas, and (2) every line item from the main item
        table(s).

        =====================================================================
        PART 1 -- INVOICE METADATA
        =====================================================================
        1. Dates must be YYYY-MM-DD. If year is missing, assume {year}.
           - "date" must be the INVOICE DATE -- the date the invoice/bill itself was issued,
             typically labeled "Invoice Date", "Bill Date", or just "Date" near the invoice number.
           - Many invoices ALSO print a separate "Due Date" / "Payment Due Date" / "Due On" --
             the date payment is expected, always on or after the invoice date. That is a
             DIFFERENT field. NEVER extract the due date into "date", even if it is printed more
             prominently, first, or closer to the invoice number than the actual invoice date.
        2. "supplier_name" must be the main vendor issuing the invoice.
        3. Extract the exact CGST, SGST, IGST, and rounding_off amounts from the bottom summary.
        4. TAX RATE EXTRACTION (HIGHEST PRIORITY):
           - Look for an explicit tax summary / GST summary section containing labels like CGST, SGST, IGST.
           - Extract the explicit cgst_rate, sgst_rate, and igst_rate from this summary section.
           - NEVER use random percentages from item descriptions, product names, MRP text, scheme text, discount text, or handwritten notes merely because they contain '%'.
           - If the tax summary is ambiguous or no reliable explicit tax-summary rate exists, return null for the rates. Do not invent a rate.
        5. "tax_type": "local" if CGST/SGST is applied, "interstate" if IGST is applied.
        6. "printed_subtotal": The sum of the item rows before (or sometimes including) tax.
           - "subtotal_basis": Use EXPLICIT invoice evidence. "Taxable Value"/"Basic Amount"/"Total Excl GST" -> "exclusive". "Net Amount"/"Total Incl GST" -> "inclusive". Ambiguous "Subtotal"/"Total" -> "unknown". NEVER guess.
        7. "printed_grand_total": The final "Total"/"Invoice Amount"/"Net Amount"/"Grand Total" --
           the final amount the buyer must pay including all taxes. Null if not clearly present.
        8. "physical_row_count": Count the exact number of physical item rows printed in the main
           table. Ignore totals rows, subtotal rows, empty rows, or multi-line item description
           continuations. This MUST match the number of entries you return in "items" below.
        9. "explicit_round_off": Extract explicit round-off amount if printed. Else null.
        10. MISSING TOTALS SECTION: If the tax/totals summary block (subtotal, CGST, SGST, IGST,
            round-off, grand total) is not visible ANYWHERE in the supplied image(s) -- for
            example because the item table is cut off with a "Continue..."/"contd."-style marker
            and the actual totals are on a further page you were not given -- return null for
            printed_subtotal, cgst, sgst, igst, rounding_off, AND printed_grand_total. Do NOT
            estimate, back-compute from the visible item rows, or guess these values. Only report
            numbers that are genuinely printed in a visible tax/totals summary section.

        =====================================================================
        PART 2 -- LINE ITEMS
        =====================================================================
        CRITICAL BOUNDARIES:
        - ONLY extract rows inside the main item table(s) across ALL pages.
        - Ignore headers, footers, bank details, terms, GST summary, transport, and totals outside the item tables.
        - Preserve the original page/table order; do not overwrite earlier-page rows with later ones.

        CRITICAL JSON DATA TYPE RULES (STRICTLY ENFORCED):
        - "qty" and "value" MUST be pure JSON numbers (floats), never strings. Correct: 89.54   Incorrect: "89.54"
        - Do NOT include symbols like '%', '₹', or ',' in any number.

        Per-item fields:
        - "name": The item/product DESCRIPTION column text ONLY, as printed. Exclude any separate
          "Item Code"/"SKU"/"Article Code"/"HSN Code" column value entirely.
        - "qty": Quantity as printed. Use null if unreadable -- never invent a value or zero it.
          * MULTI-PART QUANTITY: some invoices print quantity as a slash-separated triple headed
            "Case/Box/Pc" (or similar) instead of one plain number, e.g. "0/2/0" or "4/0/0". The
            three positions are, in order: number of Cases / number of Boxes / number of loose
            Pieces. Normally only ONE of the three positions is non-zero for a given row -- when
            that's the case, "qty" is simply THAT non-zero number (pure transcription, no
            arithmetic needed), and "uom" is the matching label: "Case" for the 1st position,
            "Box" for the 2nd, "Pc" for the 3rd. Example: "0/2/0" -> qty=2, uom="Box".
            "4/0/0" -> qty=4, uom="Case". "0/0/6" -> qty=6, uom="Pc".
            Never confuse this column with MRP -- MRP is always a separate, single
            plain number (the item's printed retail price) that often sits right next to this
            breakdown; NEVER put an MRP value, or any per-unit Rate, into "qty".
        - "uom": Unit of measure as printed (KG, PCS, etc.) -- see the MULTI-PART QUANTITY rule
          above for what to use when "qty" came from a Case/Box/Pc breakdown.
        - "amount_candidates": {{"header", "value"}} objects for EVERY monetary total/subtotal
          column printed for this row. A Unit Rate/Price (Total / Qty) is NOT a candidate. NEVER
          include MRP, List Price, PTR/NLC/Unit Rate, or Discount columns as candidates.
        - "line_amount": Fallback total, ONLY if you cannot confidently assign the row to any of
          the headers you listed in "amount_candidates". Otherwise null.

        NO CALCULATIONS: You must NOT calculate amount from quantity x rate, nor calculate GST,
        nor apply discounts. Only transcribe numbers that are visibly printed.

        =====================================================================
        Return ONLY a valid JSON object with this exact structure:
        {{
          "supplier_name": "...",
          "invoice_number": "...",
          "date": "YYYY-MM-DD",
          "cgst": 0.0,
          "sgst": 0.0,
          "igst": 0.0,
          "rounding_off": 0.0,
          "explicit_round_off": null,
          "cgst_rate": null,
          "sgst_rate": null,
          "igst_rate": null,
          "gst_rate": 0,
          "tax_type": "local",
          "printed_subtotal": null,
          "subtotal_basis": "unknown",
          "printed_grand_total": null,
          "physical_row_count": 0,
          "detected_headers": ["...", "..."],
          "items": [
            {{
              "name": "...",
              "qty": 0.0,
              "uom": "...",
              "amount_candidates": [{{"header": "...", "value": 0.0}}],
              "line_amount": null
            }}
          ]
        }}

        Do not include markdown formatting like ```json.
        """


def _call_qwen_combined_v4(images: list[bytes]) -> dict:
    """Single Qwen call returning both invoice metadata and item rows. Only
    used for the first pass -- a row-count-mismatch retry still falls back
    to the smaller, item-only _call_qwen_extraction_v4 (is_retry=True),
    since metadata rarely needs re-extracting and that path already exists
    for Gemini too."""
    from backend.services.extraction_v4.metadata_extractor import _normalize_metadata

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not configured")

    model = os.getenv("QWEN_MODEL", "qwen/qwen3.5-flash-02-23")
    # Longer timeout than the split calls -- this one call now has to
    # generate everything the two parallel calls used to produce between
    # them, sequentially, so it legitimately needs more wall-clock time.
    client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL, timeout=120.0)

    prompt = _build_qwen_combined_prompt()
    image_parts = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{base64.b64encode(img).decode()}"},
        }
        for img in images
    ]

    response = call_with_retry(lambda: client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": image_parts + [{"type": "text", "text": prompt}]}],
        response_format={"type": "json_object"},
        extra_body={"reasoning": {"enabled": False}},
        max_tokens=7500,
    ))
    u = response.usage
    if u:
        print(f"[QWEN TOKENS] combined metadata+item extraction: prompt={u.prompt_tokens} output={u.completion_tokens} total={u.total_tokens}", flush=True)

    data = _parse_json_response(response.choices[0].message.content)
    return _normalize_metadata(data)


# ─────────────────────────────────────────────────────────────────────────────
# Provider dispatch
# ─────────────────────────────────────────────────────────────────────────────

def call_item_extraction_v4(images: list[bytes], is_retry: bool = False, provider_override: str = None) -> dict:
    provider = get_extraction_provider(provider_override)
    if provider == "qwen":
        return _call_qwen_extraction_v4(images, is_retry)
    return _call_gemini_extraction_v4(images, is_retry)


def process_invoice_v4(images: list[bytes], provider_override: str = None) -> dict:
    """
    Main V4 pipeline function.
    Returns: extracted_data
    """
    # V4 utilizes V3's safe image processing
    flattened_images = [flatten_document(img) for img in images]

    provider = get_extraction_provider(provider_override)

    # Crop Image -- local/OCR-only, needed before item extraction can start.
    # Gemini was tuned/verified against this crop; for Qwen it's skipped
    # (full flattened page sent instead) -- the heuristic corner-detection
    # crop was never validated against Qwen's OCR, and Qwen's upload path
    # also skips the enhancement pipeline (see purchase_drafts.py), so its
    # metadata and item images are now identical either way.
    if provider == "qwen":
        item_images = flattened_images
    else:
        item_images = [crop_item_table(flattened_images[0])] + flattened_images[1:]

    if provider == "qwen":
        # One combined call instead of two -- Qwen's metadata and item calls
        # read the identical image now (see above), so there's no accuracy
        # reason left to send that image to the model twice. This trades
        # away the parallel calls' latency benefit (this one call runs
        # sequentially where two used to overlap) for a real reduction in
        # per-invoice token cost (the image and prompt boilerplate are each
        # only sent once).
        print("--- [EXTRACTION V4] Step 1+2: Combined Metadata + Item Extraction (Qwen, single call) ---", flush=True)
        metadata = _call_qwen_combined_v4(item_images)
        raw_items = metadata.get("items", [])
        detected_headers = metadata.get("detected_headers", [])
    else:
        # Metadata extraction (reads the un-cropped pages) and item
        # extraction (reads the cropped table) are independent model calls
        # -- neither depends on the other's output, only the row-count-
        # mismatch retry below does. Running them concurrently instead of
        # back-to-back roughly halves the dominant cost of the pipeline
        # (two ~model-latency network round trips).
        print("--- [EXTRACTION V4] Step 1+2: Metadata + Item Extraction (parallel) ---", flush=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            metadata_future = pool.submit(call_metadata_extraction_v4, flattened_images, False, provider_override)
            items_future = pool.submit(call_item_extraction_v4, item_images, False, provider_override)
            metadata = metadata_future.result()
            item_data = items_future.result()
        raw_items = item_data.get("items", [])
        detected_headers = item_data.get("detected_headers", [])

    gst_rate_pct = float(metadata.get("gst_rate") or 0)

    # Check for row count mismatch and retry once. Note this retry only ever
    # re-asks for items, never metadata -- even after a combined Qwen first
    # pass, the mismatch is specifically about item rows, so there's no
    # reason to pay for re-extracting metadata that was presumably fine.
    physical_count = metadata.get("physical_row_count")
    if physical_count is not None and int(physical_count) != len(raw_items):
        print(f"--- [EXTRACTION V4] Row count mismatch: Extracted {len(raw_items)}, Expected {physical_count}. Retrying... ---", flush=True)
        item_data_retry = call_item_extraction_v4(item_images, is_retry=True, provider_override=provider_override)
        raw_items = item_data_retry.get("items", [])
        detected_headers = item_data_retry.get("detected_headers", [])

    metadata["items"] = raw_items
    metadata["detected_headers"] = detected_headers

    # Bundle as extracted_data
    extracted_data = {
        "supplier": metadata.get("supplier_name"),
        "invoice_number": metadata.get("invoice_number"),
        "date": metadata.get("date"),
        "subtotal_printed": metadata.get("printed_subtotal"),
        "subtotal_basis": metadata.get("subtotal_basis", "unknown"),
        "cgst_printed": metadata.get("cgst"),
        "sgst_printed": metadata.get("sgst"),
        "igst_printed": metadata.get("igst"),
        "round_off_printed": metadata.get("explicit_round_off") or metadata.get("rounding_off"),
        "grand_total_printed": metadata.get("printed_grand_total"),
        "physical_row_count": metadata.get("physical_row_count"),
        "gst_rate_metadata": gst_rate_pct,
        "tax_type": metadata.get("tax_type", "local"),
        "detected_headers": detected_headers,
        "items": raw_items
    }

    return extracted_data
