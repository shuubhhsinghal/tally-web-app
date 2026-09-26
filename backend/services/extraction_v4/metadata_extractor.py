import os
import base64
import tempfile
import re
import datetime
from concurrent.futures import ThreadPoolExecutor
from google import genai
from google.genai import types
from openai import OpenAI
from json_repair import repair_json
from backend.services.extraction_v4.retry import call_with_retry, get_extraction_provider

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _build_metadata_prompt(is_retry: bool = False) -> str:
    today = datetime.datetime.now().strftime("%d-%m-%Y")
    year = datetime.datetime.now().year

    prompt = f"""
        The current date is {today}.
        You are an expert accountant extracting metadata from Indian purchase invoices.
        You are receiving UN-CROPPED image(s) of the full invoice (potentially spanning multiple pages).
        Extract the Supplier Name, Invoice Number, Date, Taxes, Totals, and Row Count.
        DO NOT extract the line items. Leave the "items" array empty.

        CRITICAL EXTRACTION RULES:
        1. Dates must be YYYY-MM-DD. If year is missing, assume {year}.
           - "date" must be the INVOICE DATE -- the date the invoice/bill itself was issued,
             typically labeled "Invoice Date", "Bill Date", or just "Date" near the invoice number.
           - Many invoices ALSO print a separate "Due Date" / "Payment Due Date" / "Due On" --
             the date payment is expected, always on or after the invoice date. That is a
             DIFFERENT field. NEVER extract the due date into "date", even if it is printed more
             prominently, first, or closer to the invoice number than the actual invoice date.
        2. supplier_name must be the main vendor issuing the invoice.
        3. Extract the exact CGST, SGST, IGST, and rounding_off amounts from the bottom summary.
        4. TAX RATE EXTRACTION (HIGHEST PRIORITY):
           - Look for an explicit tax summary / GST summary section containing labels like CGST, SGST, IGST.
           - Extract the explicit cgst_rate, sgst_rate, and igst_rate from this summary section.
           - NEVER use random percentages from item descriptions, product names, MRP text, scheme text, discount text, or handwritten notes merely because they contain '%'.
           - If the tax summary is ambiguous or no reliable explicit tax-summary rate exists, return null for the rates. Do not invent a rate.
        5. Return 'local' if CGST/SGST is applied, 'interstate' if IGST is applied.
        6. printed_subtotal: The sum of the item rows before (or sometimes including) tax.
           - subtotal_basis: Use EXPLICIT invoice evidence to determine what this subtotal represents.
             If the label is "Taxable Value", "Basic Amount", "Total Excl GST", return "exclusive".
             If the label is "Net Amount", "Total Incl GST", return "inclusive".
             If the label is just "Subtotal" or "Total" and it's ambiguous, return "unknown". NEVER guess.
        7. printed_grand_total: Look for the final "Total", "Invoice Amount", "Net Amount", or
           "Grand Total" — the final amount the buyer must pay including all taxes.
           Return null if not clearly present.
        8. physical_row_count: Count the exact number of physical item rows printed in the main table. Ignore totals rows, subtotal rows, empty rows, or multi-line item description continuations.
        9. explicit_round_off: Extract explicit round-off amount if printed. Else null.
        10. MISSING TOTALS SECTION: If the tax/totals summary block (subtotal, CGST, SGST, IGST,
            round-off, grand total) is not visible ANYWHERE in the supplied image(s) -- for
            example because the item table is cut off with a "Continue..."/"contd."-style marker
            and the actual totals are on a further page you were not given -- return null for
            printed_subtotal, cgst, sgst, igst, rounding_off, AND printed_grand_total. Do NOT
            estimate, back-compute from the visible item rows, or guess these values. Only report
            numbers that are genuinely printed in a visible tax/totals summary section.

        Return ONLY a valid JSON object matching this structure:
        {{
          "supplier_name": "...",
          "invoice_number": "...",
          "date": "YYYY-MM-DD",
          "items": [],
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
          "grand_total": 0.0,
          "printed_subtotal": null,
          "subtotal_basis": "unknown",
          "printed_grand_total": null,
          "physical_row_count": 0
        }}
        """

    if is_retry:
        prompt += "\nWARNING: Previous extraction failed. Double check the values."

    return prompt


def _normalize_metadata(data: dict) -> dict:
    if not isinstance(data, dict):
        data = {}

    def safe_float(val):
        if val is None: return None
        try:
            return float(str(val).replace('%', '').strip())
        except ValueError:
            return None

    c_rate = safe_float(data.get("cgst_rate"))
    s_rate = safe_float(data.get("sgst_rate"))
    i_rate = safe_float(data.get("igst_rate"))

    if c_rate is not None and s_rate is not None:
        data["gst_rate"] = c_rate + s_rate
    elif i_rate is not None:
        data["gst_rate"] = i_rate

    return data


def _parse_json_response(raw_text: str) -> dict:
    raw_text = raw_text.strip()
    raw_text = re.sub(r'^```json\s*', '', raw_text)
    raw_text = re.sub(r'\s*```$', '', raw_text)
    return repair_json(raw_text, return_objects=True)


# ─────────────────────────────────────────────────────────────────────────────
# Gemini implementation
# ─────────────────────────────────────────────────────────────────────────────

def _call_gemini_metadata_v4(images: list[bytes], is_retry: bool = False) -> dict:
    """Extracts metadata (supplier, invoice number, date, taxes, printed totals, row count) from un-cropped images."""
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
        # Upload pages concurrently -- these are independent network round
        # trips to the Gemini File API, so a multi-page invoice no longer
        # pays for them one at a time. ThreadPoolExecutor.map preserves
        # input order in its results, so page order is unaffected.
        with ThreadPoolExecutor(max_workers=max(1, len(tmp_paths))) as pool:
            uploaded_files = list(pool.map(lambda p: client.files.upload(file=p), tmp_paths))

        prompt = _build_metadata_prompt(is_retry)

        response = call_with_retry(lambda: client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=uploaded_files + [prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        ))
        u = response.usage_metadata
        if u:
            print(f"[GEMINI TOKENS] metadata extraction: prompt={u.prompt_token_count} output={u.candidates_token_count} total={u.total_token_count}", flush=True)

        for uf in uploaded_files:
            client.files.delete(name=uf.name)
        for p in tmp_paths:
            os.unlink(p)

        data = _parse_json_response(response.text)
        return _normalize_metadata(data)

    except Exception as e:
        for p in tmp_paths:
            if os.path.exists(p):
                os.unlink(p)
        raise e


# ─────────────────────────────────────────────────────────────────────────────
# Qwen 3.5 Flash implementation (via OpenRouter's OpenAI-compatible API)
# ─────────────────────────────────────────────────────────────────────────────

def _call_qwen_metadata_v4(images: list[bytes], is_retry: bool = False) -> dict:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not configured")

    model = os.getenv("QWEN_MODEL", "qwen/qwen3.5-flash-02-23")
    client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL, timeout=60.0)

    prompt = _build_metadata_prompt(is_retry)
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
        # Force Qwen's fast/non-thinking mode -- see extraction_engine.py's
        # item-extraction call for why.
        extra_body={"reasoning": {"enabled": False}},
        # Safety cap -- metadata is a small, fixed-shape object; this is
        # generous headroom against a runaway generation, not a real ceiling.
        max_tokens=1500,
    ))
    u = response.usage
    if u:
        print(f"[QWEN TOKENS] metadata extraction: prompt={u.prompt_tokens} output={u.completion_tokens} total={u.total_tokens}", flush=True)

    data = _parse_json_response(response.choices[0].message.content)
    return _normalize_metadata(data)


# ─────────────────────────────────────────────────────────────────────────────
# Provider dispatch
# ─────────────────────────────────────────────────────────────────────────────

def call_metadata_extraction_v4(images: list[bytes], is_retry: bool = False, provider_override: str = None) -> dict:
    provider = get_extraction_provider(provider_override)
    if provider == "qwen":
        return _call_qwen_metadata_v4(images, is_retry)
    return _call_gemini_metadata_v4(images, is_retry)
