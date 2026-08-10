"""
Algebraic Math Reconciler Engine (Phase 2 of 3).

Deterministic engine that tests algebraic hypotheses against the raw printed
numbers transcribed by the LLM (Phase 1) to deduce the correct Tally-compatible
taxable amount. No AI is involved — pure math.

Hypotheses tested (in priority order):
  1. Pre-Discount Ex-GST          : amount = qty * rate
  2. Post-Discount Ex-GST         : amount = qty * rate * (1 - discount/100)
  3. Pre-Discount GST-Inclusive   : amount = qty * rate * tax_factor
  4. Post-Discount GST-Inclusive  : amount = qty * rate * (1 - discount/100) * tax_factor

The first hypothesis that matches the printed_amount (within ±1.0 tolerance)
wins. GST is then stripped to produce the Tally-compatible taxable base.
"""

from typing import Optional, List, Dict, Any

# Rounding tolerance for matching hypotheses against printed amounts
MATCH_TOLERANCE = 1.0

# Invoice-level tolerance for subtotal comparison
SUBTOTAL_TOLERANCE = 2.0


def safe_float(val, default=0.0) -> float:
    """Convert a value to float safely. Returns `default` if not convertible."""
    if val is None:
        return default
    try:
        # Strip currency symbols, commas, and whitespace
        clean_str = str(val).replace("₹", "").replace(",", "").strip()
        return float(clean_str) if clean_str else default
    except (ValueError, TypeError):
        return default


def _tax_factor(gst_rate_pct: float) -> float:
    """
    Safely calculate the tax factor from the GST rate.
    Handles 0%, None, and negative values without breaking.
    """
    rate = safe_float(gst_rate_pct, default=0.0)
    if rate <= 0:
        return 1.0
    return 1.0 + (rate / 100.0)


def _matches(actual: float, expected: float, tolerance: float = MATCH_TOLERANCE) -> bool:
    """Check if two values match within the given tolerance."""
    if actual is None or expected is None:
        return False
    return abs(actual - expected) <= tolerance


def reconcile_item_math(item: dict, gst_rate_pct: float) -> dict:
    """
    Test algebraic hypotheses against the printed numbers to deduce the
    correct Tally-compatible taxable amount.

    Reads the flat schema keys directly (with legacy `printed_*` fallbacks).
    No AI is involved — pure deterministic math.

    Args:
        item: A flat transcribed item dict with keys:
            name, qty, uom, rate, discount, amount
            (legacy `printed_*` keys are also accepted as fallbacks).
        gst_rate_pct: The GST rate percentage (e.g., 18 for 18%).

    Returns:
        A sanitized dict with strictly calculated:
            - name (str)      # preserved item description (never "Unknown Item")
            - qty (float)
            - rate (float)
            - discount (float)
            - amount (float)  # ex-GST taxable base
            - uom (str)       # unit of measure (uppercased)
    """
    name = str(item.get("name") or "Unknown Item").strip()
    qty = float(item.get("qty") or item.get("printed_qty") or 1.0)
    rate = float(item.get("rate") or item.get("printed_rate") or 0.0)
    disc = float(item.get("discount") or item.get("printed_discount_pct") or 0.0)
    amt = float(item.get("amount") or item.get("printed_amount") or 0.0)
    uom = str(item.get("uom") or "PCS").strip().upper()

    safe_gst = float(gst_rate_pct or 0.0)
    tax_factor = 1.0 + (safe_gst / 100.0)

    # Effective pre/post discount rates
    net_rate_pre = rate * (1.0 - (disc / 100.0))
    net_rate_post = rate

    ex_amt_pre = round(qty * net_rate_pre, 2)
    ex_amt_post = round(qty * net_rate_post, 2)
    inc_amt_post = round(ex_amt_post * tax_factor, 2)
    inc_amt_pre = round(ex_amt_pre * tax_factor, 2)

    final_rate = net_rate_pre if disc > 0 else rate
    final_amount = ex_amt_pre if ex_amt_pre > 0 else amt

    if amt > 0:
        if abs(ex_amt_pre - amt) <= 1.0:
            final_rate = net_rate_pre
            final_amount = ex_amt_pre
        elif abs(ex_amt_post - amt) <= 1.0:
            final_rate = net_rate_post
            final_amount = ex_amt_post
        elif abs(inc_amt_post - amt) <= 1.0:
            final_rate = round(ex_amt_post / qty, 2) if qty > 0 else net_rate_post
            final_amount = ex_amt_post
        elif abs(inc_amt_pre - amt) <= 1.0:
            final_rate = round(ex_amt_pre / qty, 2) if qty > 0 else net_rate_pre
            final_amount = ex_amt_pre

    # Fallback safety: if amount is 0 but rate and qty exist
    if final_amount == 0.0 and rate > 0:
        final_amount = round(qty * rate, 2)

    return {
        "name": name,
        "qty": qty,
        "rate": final_rate,
        "discount": disc,
        "amount": final_amount,
        "uom": uom
    }


def reconcile_mapped_item(item: dict, gst_rate_pct: float, column_mapping: dict) -> dict:
    name = str(item.get("name") or "Unknown Item").strip()
    qty = float(item.get("printed_qty") or item.get("qty") or 1.0)
    rate = float(item.get("printed_rate") or item.get("rate") or 0.0)
    disc = float(item.get("printed_discount_pct") or item.get("discount") or 0.0)
    amt = float(item.get("printed_amount") or item.get("amount") or 0.0)
    uom = str(item.get("printed_uom") or item.get("uom") or "PCS").strip().upper()

    rate_includes_gst = column_mapping.get("rate_includes_gst", False)
    amount_includes_gst = column_mapping.get("amount_includes_gst", False)
    discount_treatment = column_mapping.get("discount_treatment", "ignore")

    safe_gst = float(gst_rate_pct or 0.0)
    tax_factor = 1.0 + (safe_gst / 100.0)

    taxable_rate = round(rate / tax_factor, 2) if rate_includes_gst else rate
    taxable_amt = round(amt / tax_factor, 2) if amount_includes_gst else amt

    final_rate = taxable_rate
    final_discount = 0.0

    if discount_treatment == "already_in_rate":
        final_discount = 0.0
    elif discount_treatment == "apply_to_rate":
        final_discount = disc
    elif discount_treatment == "ignore":
        final_discount = 0.0

    if amt > 0:
        final_amount = taxable_amt
    else:
        net_rate = final_rate * (1.0 - (final_discount / 100.0))
        final_amount = round(qty * net_rate, 2)

    return {
        "name": name,
        "qty": qty,
        "rate": final_rate,
        "discount": final_discount,
        "amount": final_amount,
        "uom": uom
    }

def reconcile_full_invoice(raw_items: list, metadata: dict, column_mapping: dict = None) -> list:
    """
    Run all raw items through reconcile_item_math or reconcile_mapped_item, 
    sum the reconciled amounts, and compare against the printed_subtotal from metadata.

    Args:
        raw_items: List of raw transcribed item dicts.
        metadata: Dict containing at least 'printed_subtotal' and 'gst_rate'.
        column_mapping: Optional mapping dict for mapped extraction.

    Returns:
        The list of reconciled item dicts. If the sum differs from the
        printed_subtotal by more than 2.0, a 'math_warning' string is
        injected into the metadata dict for the frontend.
    """
    gst_rate_pct = safe_float(metadata.get("gst_rate"), default=0.0)
    printed_subtotal = safe_float(metadata.get("printed_subtotal"), default=None)
    if printed_subtotal == 0.0:
        printed_subtotal = None

    reconciled_items = []
    for item in raw_items:
        if column_mapping:
            reconciled = reconcile_mapped_item(item, gst_rate_pct, column_mapping)
        else:
            reconciled = reconcile_item_math(item, gst_rate_pct)
        reconciled_items.append(reconciled)

    # Sum the reconciled amounts
    total = sum(r["amount"] for r in reconciled_items)

    # Compare against printed_subtotal
    if printed_subtotal is not None:
        diff = abs(total - printed_subtotal)
        if diff > SUBTOTAL_TOLERANCE:
            metadata["math_warning"] = (
                f"Reconciled subtotal (₹{total:.2f}) differs from printed "
                f"subtotal (₹{printed_subtotal:.2f}) by ₹{diff:.2f}. "
                f"Please verify the extracted line items."
            )

    return reconciled_items
