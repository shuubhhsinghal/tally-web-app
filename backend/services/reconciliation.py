"""
Pure-logic GST reconciliation helpers.
Importable independently of the Gemini/CV pipeline so they can be unit-tested
without any external dependencies.

Two-pass design (matches the two failure modes described in the spec):

  Pass 1 — reconcile_row():
    Runs on every item individually.
    - Two or more competing columns → resolve row-by-row via GST% pair check.
    - Exactly one column → cannot be resolved at row level; tagged
      "single_column_pending_invoice_check" for Pass 2.

  Pass 2 — resolve_single_column_basis():
    Runs once per invoice on all items still tagged "pending".
    Uses printed_subtotal / printed_grand_total as ground truth to determine,
    for the invoice as a whole, whether those single-column amounts were
    ex-GST or GST-inclusive — and applies the correction consistently.
"""

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def safe_float(val) -> float:
    if val is None or val == "":
        return 0.0
    try:
        if isinstance(val, str):
            val = val.replace(",", "")
        return float(val)
    except (ValueError, TypeError):
        return 0.0


# Column header keywords
_TAXABLE_KEYWORDS   = ["taxable", "basic", "base", "ex-gst", "ex gst", "net rate",
                        "taxable amount", "taxable value"]
_INCLUSIVE_KEYWORDS = ["grand total", "net amount", "net payable", "invoice amount",
                        "total amount"]
_SKIP_KEYWORDS      = ["mrp", "max retail", "maximum retail"]


def _header_matches(header: str, keywords: list) -> bool:
    h = (header or "").lower()
    return any(kw in h for kw in keywords)


# ─────────────────────────────────────────────────────────────────────────────
# Pass 1 — per-row reconciliation
# ─────────────────────────────────────────────────────────────────────────────

def reconcile_row(
    amount_columns: list,
    qty: float,
    gst_rate_pct: float,
    tolerance: float = 0.03,
) -> tuple:
    """
    Resolve the ex-GST amount for a single line item from its amount_columns.
    Returns (amount_ex_gst, confidence_reason).

    confidence_reason values emitted here:
      "no_amount_found"                    – no usable value found at all
      "single_column_pending_invoice_check"– exactly one column; basis cannot be
                                             determined at row level; deferred to Pass 2
      "header_keyword_match"               – a column header contains "taxable"/"basic"/etc.
      "confirmed_ex_gst_pair"              – two columns relate by exactly gst_rate%;
                                             the smaller is confidently the ex-GST figure
      "unconfirmed_low_confidence"         – multiple columns but none of the above applied
    """
    # Filter: skip MRP and similarly irrelevant columns
    usable = [c for c in (amount_columns or [])
              if safe_float(c.get("value")) > 0
              and not _header_matches(c.get("header", ""), _SKIP_KEYWORDS)]

    if not usable:
        return None, "no_amount_found"

    # Single column — defer to invoice-level pass
    if len(usable) == 1:
        return safe_float(usable[0]["value"]), "single_column_pending_invoice_check"

    # --- Priority 1: header keyword match ---
    taxable_cols = [c for c in usable if _header_matches(c.get("header", ""), _TAXABLE_KEYWORDS)]
    if taxable_cols:
        val = min(safe_float(c["value"]) for c in taxable_cols)
        return val, "header_keyword_match"

    # --- Priority 2: GST-pair check ---
    values = sorted(safe_float(c["value"]) for c in usable)
    smaller, larger = values[0], values[-1]

    if gst_rate_pct and gst_rate_pct > 0 and larger > 0:
        expected_larger = smaller * (1 + gst_rate_pct / 100)
        ratio_diff = abs(expected_larger - larger) / larger
        if ratio_diff < tolerance:
            return smaller, "confirmed_ex_gst_pair"

    # --- Priority 3: fall back to smallest non-inclusive value ---
    non_inclusive = [c for c in usable
                     if not _header_matches(c.get("header", ""), _INCLUSIVE_KEYWORDS)]
    if non_inclusive:
        val = min(safe_float(c["value"]) for c in non_inclusive)
        return val, "unconfirmed_low_confidence"

    return smaller, "unconfirmed_low_confidence"


# ─────────────────────────────────────────────────────────────────────────────
# Pass 2 — invoice-level basis resolution for single-column rows
# ─────────────────────────────────────────────────────────────────────────────

def resolve_single_column_basis(
    items: list,
    gst_rate_pct: float,
    printed_subtotal,       # float | None
    printed_grand_total,    # float | None
    tolerance: float = 0.03,
) -> list:
    """
    For every item tagged 'single_column_pending_invoice_check', determine —
    once, for the whole invoice — whether those single-column amounts are
    ex-GST or GST-inclusive, then apply a consistent correction.

    confidence_reason values emitted here:
      "confirmed_ex_gst_by_invoice_anchor"          – anchor confirmed ex-GST basis
      "corrected_from_inclusive_by_invoice_anchor"  – was inclusive; amounts divided
                                                       by (1 + gst_rate/100)
      "basis_undetermined_no_anchor"                – no printed total on the invoice
      "basis_undetermined_anchor_mismatch"          – neither assumption matches the anchor
    """
    PENDING = "single_column_pending_invoice_check"
    pending = [r for r in items if r.get("confidence_reason") == PENDING]

    if not pending:
        return items

    # Sum of already-confirmed (multi-column) items
    confirmed_sum = sum(
        r["amount"] for r in items
        if r.get("confidence_reason") not in (PENDING, "no_amount_found", "computed_qty_x_rate")
    )

    # Raw sum of the pending (single-column) items as extracted
    raw_sum = sum(r["amount"] for r in pending)

    # Determine best available anchor
    anchor = None
    if printed_subtotal is not None and safe_float(printed_subtotal) > 0:
        anchor = safe_float(printed_subtotal)
    elif printed_grand_total is not None and safe_float(printed_grand_total) > 0:
        # Grand total includes taxes — subtract them if we have gst_rate
        # (this is a rough approximation; printed_subtotal is always preferred)
        anchor = safe_float(printed_grand_total)

    if anchor is None:
        # No ground truth anywhere — flag and return unchanged
        for r in pending:
            r["confidence_reason"] = "basis_undetermined_no_anchor"
        return items

    # The amount the pending items should sum to, once confirmed items are accounted for
    target = anchor - confirmed_sum

    if target <= 0:
        # Confirmed items already exceed anchor — something else is wrong; don't guess
        for r in pending:
            r["confidence_reason"] = "basis_undetermined_anchor_mismatch"
        return items

    as_exclusive_diff  = abs(raw_sum - target) / target
    factor             = 1 + gst_rate_pct / 100 if gst_rate_pct else 1.0
    as_inclusive_diff  = abs(raw_sum / factor - target) / target if factor != 1.0 else 1.0

    if as_exclusive_diff < tolerance:
        # Amounts are already ex-GST — confirm and move on
        for r in pending:
            r["confidence_reason"] = "confirmed_ex_gst_by_invoice_anchor"

    elif as_inclusive_diff < tolerance:
        # Amounts were GST-inclusive — divide every pending item
        for r in pending:
            r["amount"] = round(r["amount"] / factor, 2)
            r["rate"]   = round(r["rate"]   / factor, 4) if r.get("rate") else r.get("rate", 0.0)
            r["confidence_reason"] = "corrected_from_inclusive_by_invoice_anchor"

    else:
        # Neither assumption matches — flag for human review
        for r in pending:
            r["confidence_reason"] = "basis_undetermined_anchor_mismatch"

    return items


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point — two-pass reconciliation
# ─────────────────────────────────────────────────────────────────────────────

def reconcile_invoice_items(
    raw_items: list,
    gst_rate_pct: float,
    printed_subtotal,       # float | None
    printed_grand_total=None,  # float | None
) -> list:
    """
    Full two-pass reconciliation pipeline. Returns a list of flat item dicts with:
      name, qty, uom, rate, discount, amount, confidence_reason,
      _rate_columns, _amount_columns
    """
    results = []

    for item in raw_items:
        qty          = safe_float(item.get("qty"))
        discount_pct = safe_float(item.get("discount_pct") or item.get("discount", 0))

        # Resolve rate column (prefer taxable-labelled; else take smallest non-MRP)
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
