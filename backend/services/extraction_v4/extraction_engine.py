from backend.services.pdf_normalizer import normalize_to_pdf
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
from backend.services.extraction_v4.metadata_extractor import call_metadata_extraction_v4
from backend.services.reconciliation import safe_float
from backend.services.image_quality import assess_and_enhance_image

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
# Gemini item extraction (V4 schema)
# ─────────────────────────────────────────────────────────────────────────────

def call_gemini_extraction_v4(images: list[bytes], is_retry: bool = False) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not configured")

    client = genai.Client(api_key=api_key)

    uploaded_files = []
    tmp_paths = []
    for img in images:
        suffix = ".pdf" if img.startswith(b"%PDF") else ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(img)
            tmp_paths.append(tmp.name)

    try:
        for tmp_path in tmp_paths:
            uploaded_files.append(client.files.upload(file=tmp_path))

        today = datetime.datetime.now().strftime("%d-%m-%Y")

        base_prompt = f"""
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
        - "name": Full item description as printed.
        - "qty": Quantity as printed.
        - "uom": Unit of measure as printed (KG, PCS, etc.)
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

        Return ONLY valid JSON matching the enforced response schema.
        """

        if is_retry:
            base_prompt += """
        WARNING: The previous transcription was insufficient or row counts mismatched.
        Re-read the item table carefully row-by-row using the Serial Number as a strict anchor.
        Make sure you have transcribed EVERY row and every printed number.
        Do not guess or calculate — only transcribe what is visibly printed.
        """

        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=uploaded_files + [base_prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=PrintTranscriptionResponseV4,
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


def _run_core_extraction(images: list[bytes], metadata_baseline: dict = None) -> tuple[dict, dict]:
    enhanced_images = []
    
    # 0. Image Preprocessing & Quality Gate
    for i, img_bytes in enumerate(images):
        out_bytes, status, metrics = assess_and_enhance_image(img_bytes)
        
        if status == "UNUSABLE":
            raise ValueError("Image is too blurry or unclear to extract reliably. Please upload a clearer invoice image.")
            
        enhanced_images.append(out_bytes)
        
    # V4 utilizes V3's safe image processing
    flattened_images = [flatten_document(img) for img in enhanced_images]

    # 1. Metadata Extraction
    import copy
    if metadata_baseline is None:
        metadata = call_metadata_extraction_v4(flattened_images)
    else:
        metadata = copy.deepcopy(metadata_baseline)

    gst_rate_pct = float(metadata.get("gst_rate") or 0)

    # 2. Crop Image
    cropped_images = [crop_item_table(flattened_images[0])] + flattened_images[1:]

    # 3. Item Extraction
    item_data = call_gemini_extraction_v4(cropped_images, is_retry=False)
    raw_items = item_data.get("items", [])
    detected_headers = item_data.get("detected_headers", [])

    # Check for row count mismatch and retry once
    physical_count = metadata.get("physical_row_count")
    if physical_count is not None and int(physical_count) != len(raw_items):
        item_data_retry = call_gemini_extraction_v4(cropped_images, is_retry=True)
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

    return extracted_data, metadata


def _validate_extraction(extracted_data: dict) -> tuple[bool, dict]:
    from backend.services.extraction_v4.reconciliation_v4 import calculate_and_reconcile_v4
    gst_rate = extracted_data.get("gst_rate_metadata", 0.0)
    
    res = calculate_and_reconcile_v4(
        extracted_data, 
        gst_recording_method="separate_ledger", 
        user_gst_rate=gst_rate
    )
    recon = res.get("reconciliation_data", {})
    
    row_match = recon.get("row_count_match")
    gt_match = recon.get("grand_total_match")
    sub_match = recon.get("subtotal_match")
    gst_match = recon.get("gst_match")
    conf = recon.get("confidence")

    if conf == "UNKNOWN_BASIS":
        items = extracted_data.get("items", [])
        phys_count = extracted_data.get("physical_row_count")
        if phys_count is not None and int(phys_count) != len(items):
            return False, {"messages": [f"Row count mismatch (Extracted: {len(items)}, Printed: {phys_count})"]}
        return True, recon

    if row_match is False: return False, recon
    if gt_match is False: return False, recon
    if sub_match is False: return False, recon
    if gst_match is False: return False, recon
    if conf == "REVIEW_REQUIRED": return False, recon
        
    return True, recon


def process_invoice_v4(images: list[bytes]) -> dict:
    
    print("--- [EXTRACTION V4] Starting Primary JPEG Extraction ---", flush=True)
    jpeg_data, metadata_baseline = _run_core_extraction(images)
    
    is_valid, recon = _validate_extraction(jpeg_data)
    
    if is_valid:
        print("--- [EXTRACTION V4] Validation PASS: JPEG selected ---", flush=True)
        jpeg_data["extraction_source"] = "jpeg"
        return jpeg_data
        
    print(f"--- [EXTRACTION V4] Validation FAIL (JPEG): {recon.get('messages', [])} ---", flush=True)
    
    # Fallback only if the original image is raster
    if images and not images[0].startswith(b"%PDF"):
        print("--- [EXTRACTION V4] Starting Fallback PDF Extraction ---", flush=True)
        try:
            pdf_bytes, _, _ = normalize_to_pdf(images)
            pdf_data, _ = _run_core_extraction([pdf_bytes], metadata_baseline=metadata_baseline)
            
            pdf_valid, pdf_recon = _validate_extraction(pdf_data)
            if pdf_valid:
                print("--- [EXTRACTION V4] Validation PASS: PDF Fallback selected ---", flush=True)
                pdf_data["extraction_source"] = "pdf_fallback"
                return pdf_data
                
            print(f"--- [EXTRACTION V4] Validation FAIL (PDF): {pdf_recon.get('messages', [])} ---", flush=True)
        except Exception as e:
            print(f"--- [EXTRACTION V4] PDF Fallback Failed: {e} ---", flush=True)

    print("--- [EXTRACTION V4] Both paths failed validation. Defaulting to JPEG. ---", flush=True)
    jpeg_data["extraction_source"] = "jpeg"
    return jpeg_data
