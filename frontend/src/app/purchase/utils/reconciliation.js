// Tolerances
const TOLERANCE_ROW_GST = 0.50;
const TOLERANCE_SUBTOTAL = 1.00;
const TOLERANCE_GST_TOTAL = 2.00;
const TOLERANCE_GRAND_TOTAL = 3.00;

export function safeFloat(val, def = 0.0) {
  if (val === null || val === undefined) return def;
  try {
    const cleanStr = String(val).replace(/₹/g, "").replace(/,/g, "").trim();
    if (!cleanStr) return def;
    const parsed = parseFloat(cleanStr);
    return isNaN(parsed) ? def : parsed;
  } catch (e) {
    return def;
  }
}

export function detectGstBasis(extractedData) {
  const items = extractedData.items || [];
  
  // Level 1 - Header checks
  let headerStr = String(extractedData.active_amount_header || "").toLowerCase();
  if (!headerStr) {
    headerStr = (extractedData.detected_headers || []).join(" ").toLowerCase();
  }
  
  const exclusiveKeywords = ["taxable value", "taxable amount", "basic amount", "ex-gst", "ex gst"];
  if (exclusiveKeywords.some(k => headerStr.includes(k))) return "exclusive";
  
  const inclusiveKeywords = ["amount incl", "inclusive", "net amount incl", "total incl"];
  if (inclusiveKeywords.some(k => headerStr.includes(k))) return "inclusive";
  
  // Level 2 - Math comparison
  const subtotalPrinted = safeFloat(extractedData.subtotal_printed);
  const grandTotalPrinted = safeFloat(extractedData.grand_total_printed);
  
  const sumLineAmounts = items.reduce((acc, item) => acc + safeFloat(item.line_amount), 0);
  const subtotalBasis = extractedData.subtotal_basis || "unknown";
  
  if (subtotalPrinted > 0) {
    if (subtotalBasis === "exclusive") {
      if (Math.abs(sumLineAmounts - subtotalPrinted) <= TOLERANCE_SUBTOTAL) return "exclusive";
    } else if (subtotalBasis === "inclusive") {
      if (Math.abs(sumLineAmounts - subtotalPrinted) <= TOLERANCE_SUBTOTAL) return "inclusive";
    } else if (subtotalBasis === "unknown") {
      if (Math.abs(sumLineAmounts - subtotalPrinted) <= TOLERANCE_SUBTOTAL) {
        if (grandTotalPrinted > 0 && (grandTotalPrinted - subtotalPrinted) > TOLERANCE_GRAND_TOTAL) {
          return "exclusive";
        }
      }
    }
  }

  if (grandTotalPrinted > 0) {
    if (Math.abs(sumLineAmounts - grandTotalPrinted) <= TOLERANCE_GRAND_TOTAL) {
      return "inclusive";
    }
  }
  
  return "unknown";
}

export function calculateAndReconcileV4(extractedData, gstRecordingMethod, userGstBasisOverride = null, selectedAmountHeader = null) {
  const rawItems = extractedData.items || [];
  
  // 0. Extract all unique amount headers
  const headerSet = new Set();
  for (const item of rawItems) {
    for (const candidate of (item.amount_candidates || [])) {
      if (candidate.header) headerSet.add(candidate.header);
    }
  }
  const availableAmountHeaders = Array.from(headerSet).sort();
  
  // Pre-process items to lock in the line_amount based on selected header
  let fallbackHeader = null;
  if (availableAmountHeaders.length > 0) {
    const prefs = ["taxable value", "taxable amount", "amount"];
    for (const pref of prefs) {
      for (const h of availableAmountHeaders) {
        if (h.toLowerCase().includes(pref)) {
          fallbackHeader = h;
          break;
        }
      }
      if (fallbackHeader) break;
    }
    if (!fallbackHeader) fallbackHeader = availableAmountHeaders[0];
  }
  
  const activeAmountHeader = selectedAmountHeader ? selectedAmountHeader : fallbackHeader;
  
  // Deep clone items so we don't mutate raw extraction data
  const items = JSON.parse(JSON.stringify(rawItems));
  
  for (const item of items) {
    let chosenVal = null;
    if (activeAmountHeader) {
      const candidate = (item.amount_candidates || []).find(c => c.header === activeAmountHeader);
      if (candidate) chosenVal = candidate.value;
    }
    if (chosenVal === null) chosenVal = item.line_amount;
    
    // We allow manual overrides from the frontend if they edited it directly
    if (item._manual_line_amount !== undefined) chosenVal = item._manual_line_amount;
    
    item.line_amount = safeFloat(chosenVal);
    // Allow manual overrides for qty
    if (item._manual_qty !== undefined) item.qty = item._manual_qty;
  }
  
  // Shallow clone for the mutation step
  const processedData = { ...extractedData, items, active_amount_header: activeAmountHeader };
  
  let gstBasis = "unknown";
  if (userGstBasisOverride && userGstBasisOverride !== "unknown") {
    gstBasis = userGstBasisOverride;
  } else {
    gstBasis = detectGstBasis(processedData);
  }
  
  const calculatedItems = [];
  let calcSubtotal = 0.0;
  let calcCgst = 0.0;
  let calcSgst = 0.0;
  let calcIgst = 0.0;
  
  const taxType = extractedData.tax_type || "local";
  
  for (const item of items) {
    const qty = safeFloat(item.qty);
    const lineAmount = safeFloat(item.line_amount);
    const itemGstRate = safeFloat(item.gst_rate_on_row) || safeFloat(extractedData.gst_rate_metadata);
    
    let rate = 0.0;
    let exGstAmount = 0.0;
    let gstAmount = 0.0;
    let finalAmount = 0.0;
    
    if (qty > 0 && lineAmount > 0 && gstBasis !== "unknown") {
      if (gstRecordingMethod === "separate_ledger" && gstBasis === "exclusive") {
        rate = lineAmount / qty;
        exGstAmount = lineAmount;
        gstAmount = lineAmount * (itemGstRate / 100.0);
        finalAmount = lineAmount + gstAmount;
      } else if (gstRecordingMethod === "separate_ledger" && gstBasis === "inclusive") {
        exGstAmount = lineAmount / (1 + (itemGstRate / 100.0));
        rate = exGstAmount / qty;
        gstAmount = lineAmount - exGstAmount;
        finalAmount = lineAmount;
      } else if (gstRecordingMethod === "included_in_rate" && gstBasis === "exclusive") {
        gstAmount = lineAmount * (itemGstRate / 100.0);
        finalAmount = lineAmount + gstAmount;
        rate = finalAmount / qty;
        exGstAmount = lineAmount;
      } else if (gstRecordingMethod === "included_in_rate" && gstBasis === "inclusive") {
        rate = lineAmount / qty;
        finalAmount = lineAmount;
        exGstAmount = lineAmount / (1 + (itemGstRate / 100.0));
        gstAmount = lineAmount - exGstAmount;
      }
    }
    
    calcSubtotal += exGstAmount;
    
    if (taxType === "local") {
      calcCgst += gstAmount / 2;
      calcSgst += gstAmount / 2;
    } else {
      calcIgst += gstAmount;
    }
    
    calculatedItems.push({
      ...item, // Keep raw properties like mapped_name
      name: item.name,
      ex_gst_amount: Number(exGstAmount.toFixed(2)),
      gst_amount_calculated: Number(gstAmount.toFixed(2)),
      final_amount: Number(finalAmount.toFixed(2)),
      rate: Number(rate.toFixed(2)),
      qty: qty,
      uom: item.uom
    });
  }
  
  const calculatedData = {
    gst_basis: gstBasis,
    gst_treatment: gstRecordingMethod,
    items: calculatedItems,
    available_amount_headers: availableAmountHeaders,
    active_amount_header: activeAmountHeader,
    calculated_cgst: Number(calcCgst.toFixed(2)),
    calculated_sgst: Number(calcSgst.toFixed(2)),
    calculated_igst: Number(calcIgst.toFixed(2)),
    calculated_taxable_subtotal: Number(calcSubtotal.toFixed(2)),
  };
  
  if (gstBasis === "unknown") {
    return {
      calculated_data: calculatedData,
      reconciliation_data: { confidence: "UNKNOWN_BASIS", messages: [] }
    };
  }
  
  const rowCountExtracted = items.length;
  const rowCountPhysical = safeFloat(extractedData.physical_row_count);
  const rowCountMatch = (rowCountPhysical > 0) ? (rowCountExtracted === rowCountPhysical) : true;
  
  const subtotalPrinted = safeFloat(extractedData.subtotal_printed);
  const subtotalBasis = extractedData.subtotal_basis || "unknown";
  
  let subtotalMatch = null;
  const calcInclusiveTotal = calculatedItems.reduce((acc, it) => acc + (it.final_amount || 0), 0);
  
  // Comparing like with like
  if (subtotalPrinted > 0) {
    if (subtotalBasis === "exclusive") {
      subtotalMatch = Math.abs(calcSubtotal - subtotalPrinted) <= TOLERANCE_SUBTOTAL;
    } else if (subtotalBasis === "inclusive") {
      subtotalMatch = Math.abs(calcInclusiveTotal - subtotalPrinted) <= TOLERANCE_SUBTOTAL;
    }
  }
  
  const gstPrinted = safeFloat(extractedData.cgst_printed) + safeFloat(extractedData.sgst_printed) + safeFloat(extractedData.igst_printed);
  const gstCalculated = calcCgst + calcSgst + calcIgst;
  let gstMatch = null;
  if (gstPrinted > 0) {
    gstMatch = Math.abs(gstCalculated - gstPrinted) <= TOLERANCE_GST_TOTAL;
  }
  
  const grandTotalPrinted = safeFloat(extractedData.grand_total_printed);
  const roundOffPrinted = safeFloat(extractedData.round_off_printed);
  
  const grandTotalCalculated = calcSubtotal + gstCalculated + roundOffPrinted;
  
  let grandTotalMatch = null;
  let grandTotalDiff = 0.0;
  if (grandTotalPrinted > 0) {
    grandTotalDiff = grandTotalCalculated - grandTotalPrinted;
    grandTotalMatch = Math.abs(grandTotalDiff) <= TOLERANCE_GRAND_TOTAL;
  }
  
  let confidence = "HIGH_CONFIDENCE";
  const messages = [];
  
  if (!rowCountMatch && rowCountPhysical > 0) {
    confidence = "REVIEW_REQUIRED";
    messages.push(`Row count mismatch (Extracted: ${rowCountExtracted}, Printed: ${rowCountPhysical})`);
  }
  
  if (subtotalMatch === false) {
    confidence = "REVIEW_REQUIRED";
    const printedSub = subtotalPrinted.toFixed(2);
    if (subtotalBasis === "exclusive") {
      messages.push(`Subtotal (taxable) mismatch (Calculated: ${calcSubtotal.toFixed(2)}, Printed: ${printedSub})`);
    } else {
      messages.push(`Subtotal (inclusive) mismatch (Calculated: ${calcInclusiveTotal.toFixed(2)}, Printed: ${printedSub})`);
    }
  }
  
  if (gstMatch === false) {
    confidence = "REVIEW_REQUIRED";
    messages.push(`GST mismatch (Calculated: ${gstCalculated.toFixed(2)}, Printed: ${gstPrinted.toFixed(2)})`);
  }
  
  if (grandTotalMatch === false) {
    confidence = "REVIEW_REQUIRED";
    messages.push(`Grand total mismatch (Calculated: ${grandTotalCalculated.toFixed(2)}, Printed: ${grandTotalPrinted.toFixed(2)})`);
  } else if (grandTotalMatch === true && Math.abs(grandTotalDiff) > 0.10 && roundOffPrinted === 0) {
    confidence = "REVIEW_REQUIRED";
    messages.push(`Grand total differs by ${grandTotalDiff.toFixed(2)} — possible round-off, please verify.`);
  }
  
  return {
    calculated_data: calculatedData,
    reconciliation_data: {
      row_count_extracted: rowCountExtracted,
      row_count_physical: rowCountPhysical,
      row_count_match: rowCountMatch,
      subtotal_calculated: Number(calcSubtotal.toFixed(2)),
      subtotal_printed: subtotalPrinted > 0 ? subtotalPrinted : null,
      subtotal_match: subtotalMatch,
      gst_calculated: Number(gstCalculated.toFixed(2)),
      gst_printed: gstPrinted > 0 ? gstPrinted : null,
      gst_match: gstMatch,
      grand_total_calculated: Number(grandTotalCalculated.toFixed(2)),
      grand_total_printed: grandTotalPrinted > 0 ? grandTotalPrinted : null,
      grand_total_match: grandTotalMatch,
      round_off_printed: roundOffPrinted !== 0 ? roundOffPrinted : null,
      confidence,
      messages
    }
  };
}
