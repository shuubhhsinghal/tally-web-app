import re

# Tolerances
TOLERANCE_ROW_GST     = 0.50
TOLERANCE_SUBTOTAL    = 1.00
TOLERANCE_GST_TOTAL   = 2.00
TOLERANCE_GRAND_TOTAL = 3.00

def safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        clean_str = str(val).replace("₹", "").replace(",", "").strip()
        return float(clean_str) if clean_str else default
    except (ValueError, TypeError):
        return default

def detect_gst_basis(extracted_data):
    """
    Detect whether line amounts are inclusive or exclusive of GST.
    Level 1: Explicit column header
    Level 2: Mathematical comparison against anchors
    Level 3: Unknown
    """
    # Level 1 - Header checks
    items = extracted_data.get("items", [])
    header_str = str(extracted_data.get("active_amount_header") or "").lower()
    if not header_str:
        header_str = " ".join(extracted_data.get("detected_headers", [])).lower()
        
    if any(k in header_str for k in ["taxable value", "taxable amount", "basic amount", "ex-gst", "ex gst"]):
        return "exclusive"
    if any(k in header_str for k in ["amount incl", "inclusive", "net amount incl", "total incl"]):
        return "inclusive"
        
    # Level 2 - Math comparison
    subtotal_printed = safe_float(extracted_data.get("subtotal_printed"))
    grand_total_printed = safe_float(extracted_data.get("grand_total_printed"))
    
    sum_line_amounts = sum(safe_float(item.get("line_amount")) for item in items)
    
    # Only use subtotal for math if we know it's exclusive (taxable) or if it's explicitly ambiguous
    # The requirement: "Use explicit invoice evidence to determine subtotal type. If ambiguous... do not use the assumption silently"
    subtotal_basis = extracted_data.get("subtotal_basis", "unknown")
    
    if subtotal_printed > 0:
        if subtotal_basis == "exclusive":
            if abs(sum_line_amounts - subtotal_printed) <= TOLERANCE_SUBTOTAL:
                return "exclusive"
        elif subtotal_basis == "inclusive":
            if abs(sum_line_amounts - subtotal_printed) <= TOLERANCE_SUBTOTAL:
                return "inclusive"
        elif subtotal_basis == "unknown":
            if abs(sum_line_amounts - subtotal_printed) <= TOLERANCE_SUBTOTAL:
                # If sum of lines exactly matches subtotal, and subtotal is visibly less than grand total
                if grand_total_printed > 0 and (grand_total_printed - subtotal_printed) > TOLERANCE_GRAND_TOTAL:
                    return "exclusive"

    if grand_total_printed > 0:
        if abs(sum_line_amounts - grand_total_printed) <= TOLERANCE_GRAND_TOTAL:
            return "inclusive"
            
    return "unknown"

def calculate_and_reconcile_v4(extracted_data: dict, gst_recording_method: str, user_gst_basis_override: str = None, selected_amount_header: str = None) -> dict:
    """
    Takes extracted_data, applies calculation paths, and runs reconciliation checks.
    Returns { calculated_data, reconciliation_data }
    """
    items = extracted_data.get("items", [])
    
    # 0. Extract all unique amount headers
    available_amount_headers = set()
    for item in items:
        for candidate in item.get("amount_candidates", []):
            if candidate.get("header"):
                available_amount_headers.add(candidate.get("header"))
    available_amount_headers = sorted(list(available_amount_headers))
    
    # Pre-process items to lock in the line_amount based on selected header
    # If no selection is made, fallback to an AI default (e.g. 'Taxable Value', 'Amount' or the first candidate)
    fallback_header = None
    if available_amount_headers:
        # Simple heuristic fallback if no user selection yet
        lower_headers = [h.lower() for h in available_amount_headers]
        for pref in ["taxable value", "taxable amount", "amount"]:
            for h in available_amount_headers:
                if pref in h.lower():
                    fallback_header = h
                    break
            if fallback_header: break
        if not fallback_header:
            fallback_header = available_amount_headers[0]
            
    active_amount_header = selected_amount_header if selected_amount_header else fallback_header
    
    # Inject active line_amount into items for basis detection and calculation
    for item in items:
        # Try to find the chosen candidate
        chosen_val = None
        if active_amount_header:
            for candidate in item.get("amount_candidates", []):
                if candidate.get("header") == active_amount_header:
                    chosen_val = candidate.get("value")
                    break
        
        # Fallback to legacy line_amount if candidates missing or selection not found
        if chosen_val is None:
            chosen_val = item.get("line_amount")
            
        item["line_amount"] = safe_float(chosen_val)

    extracted_data["active_amount_header"] = active_amount_header

    # 1. Determine GST Basis
    if user_gst_basis_override and user_gst_basis_override != "unknown":
        gst_basis = user_gst_basis_override
    else:
        gst_basis = detect_gst_basis(extracted_data)
        
    calculated_items = []
    
    calc_subtotal = 0.0
    calc_cgst = 0.0
    calc_sgst = 0.0
    calc_igst = 0.0
    
    tax_type = extracted_data.get("tax_type", "local")
    
    # Process items
    for item in items:
        qty = safe_float(item.get("qty"))
        line_amount = safe_float(item.get("line_amount"))
        item_gst_rate = safe_float(item.get("gst_rate_on_row")) or safe_float(extracted_data.get("gst_rate_metadata"))
        
        rate = 0.0
        ex_gst_amount = 0.0
        gst_amount = 0.0
        final_amount = 0.0
        
        if qty > 0 and line_amount > 0 and gst_basis != "unknown":
            if gst_recording_method == "separate_ledger" and gst_basis == "exclusive": # Path A
                rate = line_amount / qty
                ex_gst_amount = line_amount
                gst_amount = line_amount * (item_gst_rate / 100.0)
                final_amount = line_amount + gst_amount
                
            elif gst_recording_method == "separate_ledger" and gst_basis == "inclusive": # Path B
                ex_gst_amount = line_amount / (1 + (item_gst_rate / 100.0))
                rate = ex_gst_amount / qty
                gst_amount = line_amount - ex_gst_amount
                final_amount = line_amount
                
            elif gst_recording_method == "included_in_rate" and gst_basis == "exclusive": # Path C
                gst_amount = line_amount * (item_gst_rate / 100.0)
                final_amount = line_amount + gst_amount
                rate = final_amount / qty
                ex_gst_amount = line_amount
                
            elif gst_recording_method == "included_in_rate" and gst_basis == "inclusive": # Path D
                rate = line_amount / qty
                final_amount = line_amount
                ex_gst_amount = line_amount / (1 + (item_gst_rate / 100.0))
                gst_amount = line_amount - ex_gst_amount
                
        # Accumulate totals
        calc_subtotal += ex_gst_amount
        
        if tax_type == "local":
            calc_cgst += gst_amount / 2
            calc_sgst += gst_amount / 2
        else:
            calc_igst += gst_amount
            
        # IMPORTANT: User requested exact 2 decimal formatting for all user-facing monetary values
        calculated_items.append({
            "name": item.get("name"),
            "ex_gst_amount": round(ex_gst_amount, 2),
            "gst_amount_calculated": round(gst_amount, 2),
            "final_amount": round(final_amount, 2),
            "rate": round(rate, 2),
            "qty": qty,
            "uom": item.get("uom")
        })
        
    calculated_data = {
        "gst_basis": gst_basis,
        "gst_treatment": gst_recording_method,
        "items": calculated_items,
        "available_amount_headers": available_amount_headers,
        "active_amount_header": active_amount_header
    }
    
    if gst_basis == "unknown":
        # Cannot reconcile if we don't know the basis
        return {
            "calculated_data": calculated_data,
            "reconciliation_data": {
                "confidence": "UNKNOWN_BASIS"
            }
        }
        
    # 2. Reconciliation Checks
    row_count_extracted = len(items)
    row_count_physical = safe_float(extracted_data.get("physical_row_count"))
    row_count_match = True
    if row_count_physical > 0 and row_count_extracted != row_count_physical:
        row_count_match = False
        
    subtotal_printed = safe_float(extracted_data.get("subtotal_printed"))
    subtotal_basis = extracted_data.get("subtotal_basis", "unknown")
    
    subtotal_match = True
    # Only reconcile against subtotal if it's explicitly exclusive. If ambiguous, skip subtotal reconciliation match.
    if subtotal_printed > 0:
        if subtotal_basis == "exclusive":
            subtotal_match = abs(calc_subtotal - subtotal_printed) <= TOLERANCE_SUBTOTAL
        else:
            subtotal_match = None # Cannot verify
    else:
        subtotal_match = None

    gst_printed = safe_float(extracted_data.get("cgst_printed")) + safe_float(extracted_data.get("sgst_printed")) + safe_float(extracted_data.get("igst_printed"))
    gst_calculated = calc_cgst + calc_sgst + calc_igst
    gst_match = True
    if gst_printed > 0:
        gst_match = abs(gst_calculated - gst_printed) <= TOLERANCE_GST_TOTAL
    else:
        gst_match = None

    grand_total_printed = safe_float(extracted_data.get("grand_total_printed"))
    round_off_printed = safe_float(extracted_data.get("round_off_printed"))
    
    grand_total_calculated = calc_subtotal + gst_calculated + round_off_printed
    
    grand_total_match = True
    grand_total_diff = 0.0
    if grand_total_printed > 0:
        grand_total_diff = grand_total_calculated - grand_total_printed
        grand_total_match = abs(grand_total_diff) <= TOLERANCE_GRAND_TOTAL
    else:
        grand_total_match = None
        
    # Determine Confidence
    confidence = "HIGH_CONFIDENCE"
    messages = []
    
    if not row_count_match:
        confidence = "REVIEW_REQUIRED"
        messages.append(f"Row count mismatch (Extracted: {row_count_extracted}, Printed: {row_count_physical})")
        
    if subtotal_match is False:
        confidence = "REVIEW_REQUIRED"
        messages.append(f"Subtotal mismatch (Calculated: {calc_subtotal:.2f}, Printed: {subtotal_printed:.2f})")
        
    if gst_match is False:
        confidence = "REVIEW_REQUIRED"
        messages.append(f"GST mismatch (Calculated: {gst_calculated:.2f}, Printed: {gst_printed:.2f})")
        
    if grand_total_match is False:
        confidence = "REVIEW_REQUIRED"
        messages.append(f"Grand total mismatch (Calculated: {grand_total_calculated:.2f}, Printed: {grand_total_printed:.2f})")
    elif grand_total_match is True and abs(grand_total_diff) > 0.10 and round_off_printed == 0:
        # Unexplained small difference
        confidence = "REVIEW_REQUIRED"
        messages.append(f"Grand total differs by {grand_total_diff:.2f} — possible round-off, please verify.")
        
    reconciliation_data = {
        "row_count_extracted": row_count_extracted,
        "row_count_physical": int(row_count_physical),
        "row_count_match": row_count_match,
        "subtotal_calculated": round(calc_subtotal, 2),
        "subtotal_printed": subtotal_printed if subtotal_printed > 0 else None,
        "subtotal_match": subtotal_match,
        "gst_calculated": round(gst_calculated, 2),
        "gst_printed": gst_printed if gst_printed > 0 else None,
        "gst_match": gst_match,
        "grand_total_calculated": round(grand_total_calculated, 2),
        "grand_total_printed": grand_total_printed if grand_total_printed > 0 else None,
        "grand_total_match": grand_total_match,
        "round_off_printed": round_off_printed if round_off_printed != 0 else None,
        "confidence": confidence,
        "messages": messages
    }
    
    return {
        "calculated_data": calculated_data,
        "reconciliation_data": reconciliation_data
    }
