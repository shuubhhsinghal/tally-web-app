# Item Wise Purchase Mode — Complete Technical Documentation

This document provides an exhaustive, granular breakdown of the **Item Wise Purchase Mode** in the "Mom's Pride" accounting web app. It covers the full end-to-end flow — from the moment a user photographs a supplier invoice to the final Tally XML voucher being queued for synchronization.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture & Data Flow](#2-architecture--data-flow)
3. [Frontend — The User Interface](#3-frontend--the-user-interface)
4. [Backend — The Extraction Pipeline](#4-backend--the-extraction-pipeline)
5. [The GST Reconciliation Engine](#5-the-gst-reconciliation-engine)
6. [Item Mapping to Tally Master](#6-item-mapping-to-tally-master)
7. [Tally XML Voucher Generation](#7-tally-xml-voucher-generation)
8. [How Accuracy Is Ensured (Invoice Fidelity)](#8-how-accuracy-is-ensured-invoice-fidelity)
9. [Error Handling & Retry Logic](#9-error-handling--retry-logic)
10. [Key Files Reference](#10-key-files-reference)

---

## 1. Overview

**Item Wise Purchase Mode** is the advanced purchase-entry flow that allows a user to:

1. **Upload a photo or PDF** of a supplier invoice.
2. Have the backend **automatically extract** the invoice header (supplier, date, invoice number) and every **line item** (name, quantity, UOM, rate, amount, discount) using **Google Gemini 3.5 Flash-Lite**.
3. **Reconcile** the extracted numbers deterministically to ensure the amounts match the printed invoice (ex-GST vs GST-inclusive).
4. **Map** each raw invoice item name to a standard **Tally Stock Item** (with fuzzy matching, aliases, and manual override).
5. **Review & edit** everything in a mobile-friendly UI before pushing.
6. Generate a **complex Tally XML Purchase voucher** (with inventory entries, cost-center allocations, GST ledgers, and rounding) and queue it for sync.

The mode is toggled via a **Segmented Control** on the `/purchase` page, switching between `AccountingMode` (simple expense booking) and `ItemWiseMode` (this flow).

---

## 2. Architecture & Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  FRONTEND (Next.js)                                                         │
│  /purchase → ItemWiseMode.js                                                │
│                                                                             │
│  1. User selects invoice image/PDF                                           │
│  2. POST /api/purchase-item/extract  (multipart/form-data)                  │
│  3. Renders extracted invoice + line items for review/edit                   │
│  4. User maps items, adjusts qty/rate/taxes                                  │
│  5. POST /api/purchase-item/post  (JSON payload)                            │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  BACKEND (FastAPI)                                                          │
│                                                                             │
│  /extract endpoint → extraction_engine.process_invoice()                    │
│     ├─ Step 1: flatten_document()        (image preprocessor)               │
│     ├─ Step 2: call_metadata_extraction() (Gemini — full invoice)           │
│     ├─ Step 3: crop_item_table()         (Tesseract table detection)        │
│     ├─ Step 4: call_gemini_extraction()  (Gemini — cropped item rows)       │
│     ├─ Step 5: reconcile_invoice_items() (deterministic GST math)           │
│     └─ Step 6: item mapping (alias cache → Gemini text mapper)              │
│                                                                             │
│  /post endpoint → generates Tally XML → queue_operation()                   │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  SQLite offline_queue                                                       │
│  → tally_sync_worker.py polls PENDING items                                 │
│  → POSTs XML to Tally HTTP server (http://100.90.163.23:9000)               │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Frontend — The User Interface

**File:** `frontend/src/app/purchase/ItemWiseMode.js`

### 3.1 Initial State — Upload Screen

When no invoice has been extracted yet, the component renders a **dashed-border upload zone**:

- A hidden `<input type="file" accept="image/*,.pdf">`.
- A large upload icon and the text **"Take a photo of the invoice"**.
- While extraction is in progress, a **spinner** replaces the icon and the text changes to **"AI is reading invoice..."**.

### 3.2 Metadata Loading

On mount, the component fetches two endpoints in parallel:

| Endpoint | Purpose |
|----------|---------|
| `GET /api/purchase-item/metadata` | Returns `suppliers` (ledgers under Sundry Creditors/Loans), `stock_items` (Tally master), `uoms`, and `aliases` (saved item mappings). |
| `GET /api/purchase-item/items` | Returns the `purchase_rates` cache — historic item prices used to validate known items. |

These populate the supplier datalist, the Tally item mapping dropdown, and the UOM suggestions.

### 3.3 File Selection & Extraction Trigger

```js
const handleFileChange = async (e) => {
  const selectedFile = e.target.files[0];
  setFile(selectedFile);
  setPreviewUrl(URL.createObjectURL(selectedFile));

  setIsExtracting(true);
  const fd = new FormData();
  fd.append("file", selectedFile);

  const res = await fetch("http://127.0.0.1:8000/api/purchase-item/extract", {
    method: "POST",
    body: fd,
  });
  // ... parse response, setInvoice(), setItems()
};
```

The response populates:

- **`invoice`** state: `supplier`, `invoice_number`, `date`, `cost_center` (defaults to `"Mahagun"`), `cgst`, `sgst`, `igst`, `rounding_off`, `gst_rate`, `tax_type`.
- **`items`** state: the array of extracted line items.

### 3.4 Auto-Tax Recalculation (useEffect)

A key `useEffect` watches `items`, `invoice.gst_rate`, and `invoice.tax_type`:

```js
useEffect(() => {
  if (!invoice || invoice.gst_rate === "" || invoice.gst_rate === null) return;
  const subtotal = items.reduce((sum, item) => sum + (Number(item.amount) || 0), 0);
  const rate = Number(invoice.gst_rate);
  if (rate >= 0) {
    const totalTax = subtotal * (rate / 100);
    let newCgst = 0, newSgst = 0, newIgst = 0;
    if (invoice.tax_type === 'interstate') {
      newIgst = parseFloat(totalTax.toFixed(2));
    } else {
      newCgst = parseFloat((totalTax / 2).toFixed(2));
      newSgst = parseFloat((totalTax / 2).toFixed(2));
    }
    const preRoundTotal = subtotal + newCgst + newSgst + newIgst;
    const roundedTotal = Math.round(preRoundTotal);
    const rounding = parseFloat((roundedTotal - preRoundTotal).toFixed(2));

    if (newCgst !== Number(invoice.cgst) || newSgst !== Number(invoice.sgst) ||
        newIgst !== Number(invoice.igst) || rounding !== Number(invoice.rounding_off)) {
      setInvoice(prev => ({ ...prev, cgst: newCgst, sgst: newSgst, igst: newIgst, rounding_off: rounding }));
    }
  }
}, [items, invoice?.gst_rate, invoice?.tax_type]);
```

**What this does:** Whenever the line items, GST rate, or tax type change, the frontend **recomputes** CGST/SGST/IGST and the rounding-off amount from the item subtotal. This keeps the totals mathematically consistent with the invoice even after the user edits quantities or rates. It only updates state if the computed values differ from the AI-extracted ones (avoiding infinite loops).

### 3.5 Invoice Header Card

Rendered as an editable card with:

- **Invoice Date** (date input)
- **Invoice No.** (text input)
- **Supplier** — a text input with a `<datalist>` of cached suppliers (allows typing a new one)
- **Cost Center** — a `<Select>` with stores: `Mahagun`, `Vvip`, `Gulshan`
- **"View original photo"** link — opens the uploaded image in a new tab for visual verification.

### 3.6 Line Item Cards

Each extracted item renders as a **collapsible Card**:

**Collapsed state:**
- Item name (bold)
- Amount (right-aligned, `₹ X.XX`)
- A status badge:
  - **"Matched"** (green) — if `mapped_name` exists in the Tally stock cache or purchase-rate cache.
  - **"Needs Match"** (amber) — if the item is unmapped.
- `qty uom × ₹rate` summary line.
- A chevron indicating it's tappable.

**Expanded (Edit Mode) state:**
- **Item Name** (from invoice) — editable text.
- **Tally Item Mapping** — a `SearchableSelect` listing all Tally stock items. Selecting one:
  - Sets `mapped_name` and `is_mapped = true`.
  - Fires `POST /api/purchase-item/save-alias` to persist the mapping for future invoices.
- **Quantity** (number input) — editing qty or rate auto-recomputes `amount = qty × rate`.
- **UOM** — text input with a `<datalist>` of cached UOMs + common defaults (PCS, NOS, KGS, BOX, PKT).
- **Rate (₹)** — number input.
- **Amount (₹)** — number input (bold, editable directly).
- **Remove Item** button.

### 3.7 Taxes & Totals Card

- Shows **Subtotal** (sum of item amounts).
- A **"Taxes & Totals"** header with an **Edit** toggle.
- When editing, exposes:
  - **GST Rate (%)** — number input.
  - **Tax Type** — `Local` (CGST+SGST) or `Interstate` (IGST).
  - **CGST / SGST** (for local) or **IGST** (for interstate) — editable.
  - **Rounding** — editable.
- When not editing, shows the computed GST total.
- **Grand Total** = Subtotal + CGST + SGST + IGST + Rounding.

### 3.8 Push to Tally

```js
const handlePost = async () => {
  const normalizeStr = (str) => (str || "").replace(/\s+/g, ' ').trim().toLowerCase();
  const hasUnknownItems = items.some(item => {
    const mappedName = normalizeStr(item.mapped_name);
    return !Object.keys(itemCache).some(key => normalizeStr(key) === mappedName);
  });

  if (hasUnknownItems) {
    showToast("Please map all items to existing Tally items before pushing.", 'error');
    return;
  }

  let tallyDate = new Date().toISOString().split('T')[0].replace(/-/g, '');
  if (invoice.date) tallyDate = invoice.date.replace(/-/g, '');

  const res = await fetch("http://127.0.0.1:8000/api/purchase-item/post", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...invoice, tally_date: tallyDate, items })
  });
  // ...
};
```

**Validation before posting:** Every item must have a `mapped_name` that exists in the Tally stock cache. If any item is unmapped, the push is blocked with an error toast. This prevents invalid stock items from reaching Tally.

---

## 4. Backend — The Extraction Pipeline

**File:** `backend/services/extraction_engine.py`

The `process_invoice(image_bytes)` function orchestrates a **5-step hybrid pipeline** combining computer vision, OCR, and two separate Gemini calls.

### Step 1 — Image Flattening (`flatten_document`)

**File:** `backend/services/extraction_v3/image_preprocessor.py`

```python
def flatten_document(image_bytes: bytes) -> bytes:
    """Bypass fragile OpenCV contour detection to prevent accidental metadata cropping."""
    return image_bytes
```

Currently a **pass-through** — it returns the image unchanged. The comment explains this is intentional: earlier OpenCV contour-detection attempts risked accidentally cropping away metadata, so the function was simplified to a no-op. (The `four_point_transform` and `order_points` helpers remain for potential future use.)

### Step 2 — Metadata Extraction (`call_metadata_extraction`)

**File:** `backend/services/extraction_v3/metadata_extractor.py`

This is the **first Gemini call**, performed on the **full, un-cropped invoice image**. Its purpose is to extract the invoice header and tax summary — NOT the line items.

**The prompt instructs Gemini to extract:**
- `supplier_name` — the main vendor.
- `invoice_number`
- `date` (YYYY-MM-DD; missing year defaults to current year).
- `cgst`, `sgst`, `igst`, `rounding_off` — exact amounts from the bottom tax summary.
- `gst_rate` — the primary GST rate (5, 12, 18, etc.).
- `tax_type` — `"local"` if CGST/SGST, `"interstate"` if IGST.
- `printed_subtotal` — the sum of line items **before** GST (looks for labels like "Sub Total", "Taxable Total", "Taxable Value", "Total Taxable Amount"). Returns `null` if not clearly present.
- `printed_grand_total` — the final payable amount (looks for "Total", "Invoice Amount", "Net Amount", "Grand Total"). Returns `null` if not clearly present.
- `items` — explicitly instructed to be an **empty array**.

**Why `printed_subtotal` / `printed_grand_total` matter:** These are the **ground-truth anchors** used later by the reconciliation engine to determine whether extracted amounts are ex-GST or GST-inclusive. They are the key to ensuring the extracted data matches the printed invoice.

The response is parsed with `json_repair` (handles Gemini occasionally wrapping output in markdown code fences) and validated to be a dict.

### Step 3 — Table Cropping (`crop_item_table`)

**File:** `backend/services/extraction_v3/table_detector.py`

Uses **Tesseract OCR** to locate the item table boundaries:

```python
pytesseract.pytesseract.tesseract_cmd = '/opt/homebrew/bin/tesseract'  # Apple Silicon path

def crop_item_table(image_bytes: bytes) -> bytes:
    # 1. Get bounding boxes for all text via Tesseract
    d = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)

    # 2. Find TOP boundary — the table header row
    for i, word in enumerate(d['text']):
        clean_word = word.strip().lower()
        if clean_word in ["description", "goods", "item", "particulars"]:
            top_y = max(0, d['top'][i] - 10)   # crop just above header
            break

    # 3. Find BOTTOM boundary — the tax/totals footer
    for i, word in enumerate(d['text']):
        clean_word = word.strip().lower()
        if clean_word in ["sgst", "cgst", "subtotal", "total", "round"]:
            if d['top'][i] > (img.shape[0] / 3):   # only accept in bottom half
                bottom_y = d['top'][i] - 10
                break

    # 4. Crop if boundaries make sense, else fallback to full image
    if top_y < bottom_y and (bottom_y - top_y) > 100:
        cropped_img = img[top_y:bottom_y, 0:img.shape[1]]
    else:
        cropped_img = img

    return cropped_img  # re-encoded as JPEG bytes
```

**Purpose:** By cropping strictly to the item rows, the second Gemini call is **focused only on the table**, dramatically reducing hallucination risk from headers, footers, bank details, and GST summaries. The `top_y - 10` offset keeps the column headers visible so Gemini can read column names (needed for the raw-column transcription).

### Step 4 — Item Extraction (`call_gemini_extraction`)

**File:** `backend/services/extraction_engine.py`

This is the **second Gemini call**, performed on the **cropped item-table image**. Its design is deliberately **non-interpretive** — it transcribes raw columns rather than deciding which is "correct."

**Key prompt instructions:**
1. **Row alignment (CRITICAL):** Use the serial number on the far left as a strict anchor. Read perfectly straight across the row. Never mix values from two rows.
2. **Multi-line items:** Item names that wrap to a second line must be merged into ONE item. Do not create a new entry unless the line has its own distinct qty/rate/amount.
3. **UOM:** Extract exactly as printed (PCS, KGS, BOX, PKT, etc.); blank if none.
4. **Discounts:** Look for a "Disc %" or "Discount" column.
5. **RAW COLUMN TRANSCRIPTION (CRITICAL):** For each row, transcribe ALL rate-like columns into `rate_columns` and ALL amount-like columns into `amount_columns`, each as `{"header": "<column header>", "value": <number>}`. The code (not Gemini) determines which is ex-GST.

**Expected JSON schema:**

```json
{
  "items": [
    {
      "name": "...",
      "qty": 0.0,
      "uom": "PCS",
      "rate_columns": [{"header": "Price/Unit", "value": 0.0}],
      "amount_columns": [
        {"header": "Taxable Amount", "value": 0.0},
        {"header": "Amount", "value": 0.0}
      ],
      "discount_pct": 0.0
    }
  ]
}
```

**Retry mode:** If the first extraction yields unresolved items, a second call is made with an appended warning: *"Re-read the item table carefully. Make sure you have captured EVERY numeric column header and value... Do not omit any column."*

### Step 5 — Reconciliation (`reconcile_invoice_items`)

**File:** `backend/services/reconciliation.py`

This is the **deterministic math engine** that ensures extracted amounts match the printed invoice. It is fully unit-testable and independent of Gemini/CV. See [Section 5](#5-the-gst-reconciliation-engine) for the full deep-dive.

### Step 6 — Item Mapping (in `purchase_item.py`)

After extraction, the router performs item-to-Tally-master mapping. See [Section 6](#6-item-mapping-to-tally-master).

---

## 5. The GST Reconciliation Engine

**File:** `backend/services/reconciliation.py`

This is the heart of **invoice fidelity**. It uses a **two-pass design** to resolve which of the multiple numeric columns on an invoice is the true **ex-GST** amount.

### Helper: `safe_float`

```python
def safe_float(val) -> float:
    if val is None or val == "":
        return 0.0
    try:
        if isinstance(val, str):
            val = val.replace(",", "")
        return float(val)
    except (ValueError, TypeError):
        return 0.0
```

Robustly converts values (strips commas) and returns `0.0` on failure.

### Column Header Keyword Classification

```python
_TAXABLE_KEYWORDS   = ["taxable", "basic", "base", "ex-gst", "ex gst", "net rate",
                        "taxable amount", "taxable value"]
_INCLUSIVE_KEYWORDS = ["grand total", "net amount", "net payable", "invoice amount",
                        "total amount"]
_SKIP_KEYWORDS      = ["mrp", "max retail", "maximum retail"]
```

- **Taxable keywords** → strongly indicate an ex-GST amount.
- **Inclusive keywords** → strongly indicate a GST-inclusive amount.
- **Skip keywords** → MRP columns are ignored entirely.

### Pass 1 — `reconcile_row()` (per-item)

For each line item, this function resolves the ex-GST amount from its `amount_columns`:

```python
def reconcile_row(amount_columns, qty, gst_rate_pct, tolerance=0.03):
    # 1. Filter usable columns: value > 0 AND not an MRP column
    usable = [c for c in (amount_columns or [])
              if safe_float(c.get("value")) > 0
              and not _header_matches(c.get("header", ""), _SKIP_KEYWORDS)]

    if not usable:
        return None, "no_amount_found"

    # 2. Single column → defer to invoice-level pass
    if len(usable) == 1:
        return safe_float(usable[0]["value"]), "single_column_pending_invoice_check"

    # 3. Priority 1: header keyword match (e.g. "Taxable Amount")
    taxable_cols = [c for c in usable if _header_matches(c.get("header", ""), _TAXABLE_KEYWORDS)]
    if taxable_cols:
        val = min(safe_float(c["value"]) for c in taxable_cols)
        return val, "header_keyword_match"

    # 4. Priority 2: GST-pair check
    values = sorted(safe_float(c["value"]) for c in usable)
    smaller, larger = values[0], values[-1]
    if gst_rate_pct and gst_rate_pct > 0 and larger > 0:
        expected_larger = smaller * (1 + gst_rate_pct / 100)
        ratio_diff = abs(expected_larger - larger) / larger
        if ratio_diff < tolerance:
            return smaller, "confirmed_ex_gst_pair"

    # 5. Priority 3: fall back to smallest non-inclusive value
    non_inclusive = [c for c in usable
                     if not _header_matches(c.get("header", ""), _INCLUSIVE_KEYWORDS)]
    if non_inclusive:
        val = min(safe_float(c["value"]) for c in non_inclusive)
        return val, "unconfirmed_low_confidence"

    return smaller, "unconfirmed_low_confidence"
```

**Logic explained:**
- If a column header contains "taxable"/"basic"/"base", that's the ex-GST amount (highest confidence).
- If two columns differ by exactly the GST rate (e.g., 100 vs 118 at 18% GST), the smaller is confidently the ex-GST figure.
- If only one column exists, it can't be resolved at row level → deferred to Pass 2.
- Fallback: smallest non-inclusive value.

**Confidence reasons emitted:**
| Reason | Meaning |
|--------|---------|
| `no_amount_found` | No usable value at all. |
| `single_column_pending_invoice_check` | Exactly one column; basis undetermined at row level. |
| `header_keyword_match` | Column header contained "taxable"/"basic"/etc. |
| `confirmed_ex_gst_pair` | Two columns related by exactly the GST rate; smaller is ex-GST. |
| `unconfirmed_low_confidence` | Multiple columns but none of the above applied. |

### Pass 2 — `resolve_single_column_basis()` (invoice-level)

For all items tagged `single_column_pending_invoice_check`, this determines — **once for the whole invoice** — whether those single-column amounts are ex-GST or GST-inclusive, using the printed totals as ground truth:

```python
def resolve_single_column_basis(items, gst_rate_pct, printed_subtotal, printed_grand_total, tolerance=0.03):
    PENDING = "single_column_pending_invoice_check"
    pending = [r for r in items if r.get("confidence_reason") == PENDING]
    if not pending:
        return items

    # Sum of already-confirmed (multi-column) items
    confirmed_sum = sum(r["amount"] for r in items
                        if r.get("confidence_reason") not in (PENDING, "no_amount_found", "computed_qty_x_rate"))

    # Raw sum of the pending (single-column) items as extracted
    raw_sum = sum(r["amount"] for r in pending)

    # Determine best available anchor (printed_subtotal preferred)
    anchor = None
    if printed_subtotal is not None and safe_float(printed_subtotal) > 0:
        anchor = safe_float(printed_subtotal)
    elif printed_grand_total is not None and safe_float(printed_grand_total) > 0:
        anchor = safe_float(printed_grand_total)   # rough approximation

    if anchor is None:
        for r in pending:
            r["confidence_reason"] = "basis_undetermined_no_anchor"
        return items

    # The amount the pending items should sum to
    target = anchor - confirmed_sum
    if target <= 0:
        for r in pending:
            r["confidence_reason"] = "basis_undetermined_anchor_mismatch"
        return items

    as_exclusive_diff = abs(raw_sum - target) / target
    factor = 1 + gst_rate_pct / 100 if gst_rate_pct else 1.0
    as_inclusive_diff = abs(raw_sum / factor - target) / target if factor != 1.0 else 1.0

    if as_exclusive_diff < tolerance:
        # Amounts are already ex-GST — confirm
        for r in pending:
            r["confidence_reason"] = "confirmed_ex_gst_by_invoice_anchor"
    elif as_inclusive_diff < tolerance:
        # Amounts were GST-inclusive — divide every pending item by (1 + rate)
        for r in pending:
            r["amount"] = round(r["amount"] / factor, 2)
            r["rate"]   = round(r["rate"]   / factor, 4) if r.get("rate") else r.get("rate", 0.0)
            r["confidence_reason"] = "corrected_from_inclusive_by_invoice_anchor"
    else:
        # Neither assumption matches — flag for human review
        for r in pending:
            r["confidence_reason"] = "basis_undetermined_anchor_mismatch"

    return items
```

**Logic explained:**
- The **anchor** is the printed subtotal (preferred) or grand total.
- `target = anchor - confirmed_sum` is what the pending items *should* sum to.
- If `raw_sum ≈ target` → the amounts are already ex-GST → confirmed.
- If `raw_sum / (1 + rate) ≈ target` → the amounts were GST-inclusive → **divide every pending item** by `(1 + rate)` to convert to ex-GST.
- If neither matches → flagged `basis_undetermined_anchor_mismatch` for human review.

**Confidence reasons emitted:**
| Reason | Meaning |
|--------|---------|
| `confirmed_ex_gst_by_invoice_anchor` | Anchor confirmed ex-GST basis. |
| `corrected_from_inclusive_by_invoice_anchor` | Was inclusive; amounts divided by `(1 + rate)`. |
| `basis_undetermined_no_anchor` | No printed total on the invoice. |
| `basis_undetermined_anchor_mismatch` | Neither assumption matched the anchor. |

### Main Entry — `reconcile_invoice_items()`

```python
def reconcile_invoice_items(raw_items, gst_rate_pct, printed_subtotal, printed_grand_total=None):
    results = []
    for item in raw_items:
        qty          = safe_float(item.get("qty"))
        discount_pct = safe_float(item.get("discount_pct") or item.get("discount", 0))

        # Resolve rate column (prefer taxable-labelled; else smallest non-MRP)
        rate_cols = item.get("rate_columns") or []
        usable_rate = [c for c in rate_cols
                       if not _header_matches(c.get("header", ""), _SKIP_KEYWORDS)
                       and safe_float(c.get("value")) > 0]
        if usable_rate:
            taxable_rate = [c for c in usable_rate
                            if _header_matches(c.get("header", ""), _TAXABLE_KEYWORDS)]
            rate = (safe_float(taxable_rate[0]["value"]) if taxable_rate
                    else min(safe_float(c["value"]) for c in usable_rate))
        else:
            rate = 0.0

        # Pass 1: per-row amount resolution
        amount, confidence_reason = reconcile_row(
            amount_columns=item.get("amount_columns") or [],
            qty=qty,
            gst_rate_pct=gst_rate_pct,
        )

        if amount is None:
            amount = round(qty * rate, 2)
            confidence_reason = "computed_qty_x_rate"

        results.append({
            "name":              item.get("name", "Unknown"),
            "qty":               qty,
            "uom":               item.get("uom", "PCS"),
            "rate":              round(rate, 4),
            "discount":          discount_pct,
            "amount":            round(amount, 2),
            "confidence_reason": confidence_reason,
            "_rate_columns":     item.get("rate_columns", []),
            "_amount_columns":   item.get("amount_columns", []),
        })

    # Pass 2: invoice-level resolution for single-column rows
    results = resolve_single_column_basis(
        items=results,
        gst_rate_pct=gst_rate_pct,
        printed_subtotal=printed_subtotal,
        printed_grand_total=printed_grand_total,
    )
    return results
```

**Output:** Each item becomes a flat dict with `name`, `qty`, `uom`, `rate`, `discount`, `amount`, `confidence_reason`, plus the raw `_rate_columns` / `_amount_columns` for debugging.

### Retry Trigger in `extraction_engine.py`

```python
needs_retry = any(
    r.get("confidence_reason") in ("no_amount_found",
                                    "basis_undetermined_anchor_mismatch")
    for r in reconciled
)
if needs_retry:
    # Re-run Gemini item extraction with the retry warning, then re-reconcile
    item_data_retry = call_gemini_extraction(cropped_image, is_retry=True)
    reconciled = reconcile_invoice_items(
        item_data_retry.get("items", []), gst_rate_pct, printed_subtotal, printed_grand_total
    )
```

If any item is genuinely unresolved (`no_amount_found` or `basis_undetermined_anchor_mismatch`), the **entire item extraction is re-run** with a warning prompt, then re-reconciled. This is a self-healing loop.

---

## 6. Item Mapping to Tally Master

**File:** `backend/routers/purchase_item.py` (inside the `/extract` endpoint)

After reconciliation, the router maps each raw invoice item to a Tally stock item.

### 6.1 Normalization

```python
def normalize_item_name(name: str) -> str:
    name = name.lower()
    name = re.sub(r'[^a-z0-9\s]', '', name)      # strip non-alphanumeric
    name = re.sub(r'\s+gm\s+', 'g ', name)        # "400 gm" → "400 g"
    name = re.sub(r'\s+gm$', 'g', name)           # "400gm" → "400g"
    name = re.sub(r'\s+', ' ', name).strip()
    return name
```

This handles common invoice-vs-Tally naming differences (case, spacing, "gm" vs "g").

### 6.2 Alias Cache & Exact Match

```python
stock_cache = {normalize_item_name(i['name']): i for i in stock_items}
aliases = get_all_aliases()   # {normalized_original: mapped_name}

for item in raw_items:
    norm_name = normalize_item_name(original_name)
    # 1. Check alias cache
    if norm_name in aliases:
        mapped_name = aliases[norm_name]
        match_found = True
    else:
        # 2. Check stock cache (exact normalized match)
        for stock_key, stock_data in stock_cache.items():
            if stock_key == norm_name or stock_data['name'].lower().strip() == original_name.lower().strip():
                mapped_name = stock_data['name']
                mapped_unit = stock_data.get('unit', mapped_unit)
                match_found = True
                break
    item['mapped_name'] = mapped_name if match_found else ""
    item['mapped_unit'] = mapped_unit
    item['is_mapped'] = match_found
```

### 6.3 Gemini Text Mapper (for unmapped items)

If any items remain unmapped, a **third Gemini call** (text-only, no image) is made:

- Sends the unmapped raw items + the full Tally master stock list.
- Instructs Gemini to find exact matches accounting for typos, case, and spacing (e.g., `'400g'` vs `'400gm'`).
- Returns updated items with `mapped_name` / `mapped_unit`.

**Critical safety check:** The AI's suggested mapping is **only trusted if the mapped name actually exists in the Tally master list**:

```python
if mapped_row['mapped_name'] in tally_item_names:
    out_row['mapped_name'] = mapped_row['mapped_name']
    out_row['is_mapped'] = True
    # fetch correct unit from stock cache
```

This prevents hallucinated item names from reaching Tally.

### 6.4 Supplier Auto-Correction

```python
matches = difflib.get_close_matches(supplier_name, cached_suppliers_list, n=1, cutoff=0.8)
if matches:
    mapped_supplier = matches[0]   # auto-correct to the closest Tally supplier
```

Uses `difflib.get_close_matches` (80% similarity cutoff) to auto-correct the extracted supplier name to the closest Tally Sundry Creditor.

### 6.5 Alias Persistence

When the user manually maps an item in the UI, the frontend calls:

```
POST /api/purchase-item/save-alias
Body: { "original_name": "...", "mapped_name": "..." }
```

This saves the mapping to the `item_aliases` table so **future invoices with the same item name auto-map** without user intervention.

---

## 7. Tally XML Voucher Generation

**File:** `backend/routers/purchase_item.py` (the `/post` endpoint)

The `post_purchase_item` function builds a highly complex Tally XML Purchase voucher.

### 7.1 Inventory Entries

For each item:

```xml
<INVENTORYENTRIES.LIST>
    <STOCKITEMNAME>{mapped_name}</STOCKITEMNAME>
    <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
    <RATE>{rate}/{unit}</RATE>
    <DISCOUNT>{discount}</DISCOUNT>
    <AMOUNT>-{amount:.2f}</AMOUNT>
    <ACTUALQTY> {qty} {unit}</ACTUALQTY>
    <BILLEDQTY> {qty} {unit}</BILLEDQTY>
    <ACCOUNTINGALLOCATIONS.LIST>
        <LEDGERNAME>Purchase</LEDGERNAME>
        <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
        <AMOUNT>-{amount:.2f}</AMOUNT>
        <CATEGORYALLOCATIONS.LIST>
            <CATEGORY>Primary Cost Category</CATEGORY>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <COSTCENTREALLOCATIONS.LIST>
                <NAME>{cost_center}</NAME>
                <AMOUNT>-{amount:.2f}</AMOUNT>
            </COSTCENTREALLOCATIONS.LIST>
        </CATEGORYALLOCATIONS.LIST>
    </ACCOUNTINGALLOCATIONS.LIST>
</INVENTORYENTRIES.LIST>
```

Each item is allocated to the **Purchase** ledger and the selected **cost center** (store).

### 7.2 Ledger Entries

**Supplier entry** (with bill allocation):

```xml
<LEDGERENTRIES.LIST>
    <LEDGERNAME>{supplier}</LEDGERNAME>
    <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
    <AMOUNT>{grand_total:.2f}</AMOUNT>
    <BILLALLOCATIONS.LIST>
        <NAME>{invoice_number}</NAME>
        <BILLTYPE>New Ref</BILLTYPE>
        <AMOUNT>{grand_total:.2f}</AMOUNT>
    </BILLALLOCATIONS.LIST>
</LEDGERENTRIES.LIST>
```

**GST ledgers** (conditionally added):

```xml
<!-- If CGST > 0 -->
<LEDGERENTRIES.LIST>
    <LEDGERNAME>Input CGST</LEDGERNAME>
    <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
    <AMOUNT>-{cgst:.2f}</AMOUNT>
</LEDGERENTRIES.LIST>

<!-- If SGST > 0 -->
<LEDGERENTRIES.LIST>
    <LEDGERNAME>Input SGST</LEDGERNAME>
    <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
    <AMOUNT>-{sgst:.2f}</AMOUNT>
</LEDGERENTRIES.LIST>

<!-- If IGST > 0 -->
<LEDGERENTRIES.LIST>
    <LEDGERNAME>Input IGST</LEDGERNAME>
    <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
    <AMOUNT>-{igst:.2f}</AMOUNT>
</LEDGERENTRIES.LIST>
```

**Rounding Off ledger** (if `|rounding_off| >= 0.01`):

```xml
<LEDGERENTRIES.LIST>
    <LEDGERNAME>Rounding Off</LEDGERNAME>
    <ISDEEMEDPOSITIVE>{is_debit}</ISDEEMEDPOSITIVE>
    <AMOUNT>{amount_str}</AMOUNT>
</LEDGERENTRIES.LIST>
```

### 7.3 Master Alterations (GST Ledger Setup)

The XML also includes `Alter` actions to ensure the GST ledgers are correctly configured in Tally:

```xml
<TALLYMESSAGE xmlns:UDF="TallyUDF">
    <LEDGER ACTION="Alter" NAME="Input CGST">
        <NAME.LIST><NAME>Input CGST</NAME></NAME.LIST>
        <PARENT>Duties & Taxes</PARENT>
        <TAXTYPE>GST</TAXTYPE>
        <GSTDUTYHEAD>Central Tax</GSTDUTYHEAD>
    </LEDGER>
</TALLYMESSAGE>
<!-- Same pattern for Input SGST (State Tax) and Input IGST (Integrated Tax) -->
<!-- Plus Rounding Off ledger under Indirect Expenses with ROUNDINGMETHOD -->
```

### 7.4 The Voucher

```xml
<VOUCHER VCHTYPE="Purchase" ACTION="Create">
    <DATE>{tally_date}</DATE>
    <GUID>PII-{timestamp}</GUID>
    <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
    <REFERENCE>{invoice_number}</REFERENCE>
    <VOUCHERNUMBER>{invoice_number}</VOUCHERNUMBER>
    <PARTYLEDGERNAME>{supplier}</PARTYLEDGERNAME>
    <PARTYNAME>{supplier}</PARTYNAME>
    <PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>
    <ISINVOICE>Yes</ISINVOICE>
    {inventory_xml}
    {ledger_xml}
</VOUCHER>
```

### 7.5 Posting / Queueing

```python
try:
    response = requests.post(TALLY_URL, data=xml.encode('utf-8'), timeout=15)
    if "<LINEERROR>" in response.text:
        raise HTTPException(status_code=400, detail=f"Tally rejected the entry: {response.text}")
    return {"status": "success", "message": "Successfully posted to Tally."}
except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
    queue_operation("POST_VOUCHER", xml, payload.model_dump(),
                    f"Purchase Item Invoice: {inv_no} from {supplier}")
    return {"status": "queued", "message": "Tally is offline. Invoice saved to queue and will push automatically."}
```

- If Tally is online → posts directly, returns success.
- If Tally is offline → saves to the `offline_queue` for the background worker to push later.

### 7.6 Purchase Rate Recording

```python
if rate:
    record_purchase_rate(name.lower(), float(rate), payload.supplier,
                         payload.date if hasattr(payload, 'date') else datetime.datetime.now().strftime("%Y-%m-%d"))
```

Each item's rate is recorded in the `purchase_rates` table (upsert by item name) — this powers the **Stock Transfer** preview's rate lookup.

---

## 8. How Accuracy Is Ensured (Invoice Fidelity)

This is the most important section. The system uses **multiple layers of defense** to ensure the extracted data matches the printed invoice:

### Layer 1 — Focused Image Cropping
- The item table is **cropped** to exclude headers, footers, bank details, and GST summaries.
- This prevents Gemini from being distracted by irrelevant text and reduces hallucination.

### Layer 2 — Raw Column Transcription (No Interpretation by AI)
- Gemini is explicitly told **NOT to decide** which column is "correct."
- It transcribes **ALL** rate-like and amount-like columns with their headers.
- The **deterministic reconciliation engine** (not the AI) decides which is ex-GST.

### Layer 3 — Deterministic GST Math (Two-Pass)
- **Pass 1:** Per-row resolution using header keywords and GST-pair checks.
- **Pass 2:** Invoice-level resolution using **printed subtotal/grand total as ground truth**.
- If single-column amounts are found to be GST-inclusive, they are **mathematically divided** by `(1 + rate)` to convert to ex-GST.

### Layer 4 — Self-Healing Retry
- If any item is unresolved (`no_amount_found` or `basis_undetermined_anchor_mismatch`), the **entire extraction is re-run** with a warning prompt, then re-reconciled.

### Layer 5 — Frontend Auto-Tax Recalculation
- The frontend **recomputes** CGST/SGST/IGST and rounding from the item subtotal whenever items change.
- This guarantees the grand total is always mathematically consistent.

### Layer 6 — Item Mapping Validation
- AI-suggested item mappings are **only trusted if they exist in the Tally master list**.
- The UI **blocks posting** if any item is unmapped.
- Aliases persist for future auto-mapping.

### Layer 7 — Supplier Auto-Correction
- `difflib.get_close_matches` (80% cutoff) corrects the extracted supplier to the closest Tally Sundry Creditor.

### Layer 8 — Human Review & Edit
- Every field (header, items, taxes) is **editable** in the UI before pushing.
- The user can view the **original photo** side-by-side for verification.

### Layer 9 — Tally as Final Gatekeeper
- Tally itself validates the XML and returns `<LINEERROR>` on rejection.
- The queue worker captures and stores the error message for the dashboard's Queue Management view.

---

## 9. Error Handling & Retry Logic

| Scenario | Handling |
|----------|----------|
| Gemini API key missing | `ValueError("GEMINI_API_KEY not configured")` → 500 error. |
| Gemini extraction fails | Exception caught, temp file cleaned up, re-raised → 500 error. |
| Unresolved items after reconciliation | Re-run Gemini item extraction with retry warning, re-reconcile. |
| Tally offline during post | Voucher saved to `offline_queue` with status `PENDING`; background worker pushes later. |
| Tally rejects XML | `HTTPException(400, "Tally rejected the entry: ...")` with the Tally error text. |
| Unmapped items at push time | Frontend blocks with toast: "Please map all items to existing Tally items before pushing." |
| Invalid JSON from Gemini | `json_repair` fixes markdown-wrapped or malformed JSON. |
| Temp file cleanup | `try/finally` ensures uploaded files are deleted from disk and Gemini storage. |

---

## 10. Key Files Reference

| File | Role |
|------|------|
| `frontend/src/app/purchase/ItemWiseMode.js` | The entire Item Wise UI: upload, review, edit, tax calc, push. |
| `frontend/src/app/purchase/page.js` | Segmented control toggling Accounting vs Item Wise mode. |
| `backend/routers/purchase_item.py` | `/extract`, `/post`, `/metadata`, `/items`, `/create-supplier`, `/create-item`, `/save-alias` endpoints. |
| `backend/services/extraction_engine.py` | Orchestrates the 5-step extraction pipeline. |
| `backend/services/reconciliation.py` | The deterministic two-pass GST reconciliation engine. |
| `backend/services/extraction_v3/metadata_extractor.py` | First Gemini call — invoice header + tax summary + printed totals. |
| `backend/services/extraction_v3/table_detector.py` | Tesseract-based item table cropping. |
| `backend/services/extraction_v3/image_preprocessor.py` | Image flattening (currently a pass-through). |
| `backend/database.py` | SQLite schema + `queue_operation`, `save_alias`, `record_purchase_rate`, master caches. |
| `backend/services/tally_sync_worker.py` | Background worker that pushes queued XML to Tally. |

---

## Summary

**Item Wise Purchase Mode** is a sophisticated, multi-stage pipeline that combines:

1. **Computer vision** (Tesseract) to isolate the item table.
2. **Two Gemini calls** — one for invoice metadata, one for raw line-item transcription.
3. **A deterministic two-pass GST reconciliation engine** that uses printed totals as ground truth to guarantee amounts match the invoice.
4. **Fuzzy/alias/AI item mapping** with strict validation against the Tally master.
5. **A mobile-first review UI** with full editability and auto-tax recalculation.
6. **Complex Tally XML generation** with inventory entries, cost-center allocations, GST ledgers, and rounding.
7. **Offline-first queueing** so invoices are never lost when Tally is offline.

The design philosophy is: **let the AI read, let the math decide, let the human confirm, and let Tally validate.** This layered approach maximizes invoice fidelity while minimizing the risk of incorrect data reaching the accounting system.