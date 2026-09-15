import os
import tempfile
import re
import datetime
from google import genai
from google.genai import types
from json_repair import repair_json

def call_metadata_extraction_v4(images: list[bytes], is_retry: bool = False) -> dict:
    """Extracts metadata (supplier, invoice number, date, taxes, printed totals, row count) from un-cropped images."""
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
        You are an expert accountant extracting metadata from Indian purchase invoices.
        You are receiving UN-CROPPED image(s) of the full invoice (potentially spanning multiple pages).
        Extract the Supplier Name, Invoice Number, Date, Taxes, Totals, and Row Count.
        DO NOT extract the line items. Leave the "items" array empty.
        
        CRITICAL EXTRACTION RULES:
        1. Dates must be YYYY-MM-DD. If year is missing, assume {year}.
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
            base_prompt += "\nWARNING: Previous extraction failed. Double check the values."

        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=uploaded_files + [base_prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
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
            
        # Deterministic GST Rate normalization
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
        
    except Exception as e:
        for p in tmp_paths:
            if os.path.exists(p):
                os.unlink(p)
        raise e
