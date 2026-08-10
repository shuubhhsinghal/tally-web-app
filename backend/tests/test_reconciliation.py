"""
Unit test harness for the two-pass GST reconciliation logic.
Run with:  cd "INV Scanner copy" && python3 -m pytest backend/tests/test_reconciliation.py -v
No Gemini API calls — tests run on synthetic pre-extracted JSON only.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backend.services.reconciliation import (
    reconcile_row,
    resolve_single_column_basis,
    reconcile_invoice_items,
)


# ─── helpers ────────────────────────────────────────────────────────────────

def make_raw_item(name, qty, rate_cols, amount_cols, discount_pct=0):
    """Build a raw-extracted item dict (as the model would return it)."""
    return {
        "name":           name,
        "qty":            qty,
        "uom":            "PCS",
        "rate_columns":   [{"header": h, "value": v} for h, v in rate_cols],
        "amount_columns": [{"header": h, "value": v} for h, v in amount_cols],
        "discount_pct":   discount_pct,
    }


# ─── Pass 1: reconcile_row ──────────────────────────────────────────────────

class TestReconcileRow:

    def test_single_column_deferred(self):
        """A single column cannot be resolved at row level — must be deferred."""
        amount, reason = reconcile_row(
            [{"header": "Amount", "value": 157.50}],
            qty=6, gst_rate_pct=5
        )
        assert abs(amount - 157.50) < 0.05
        assert reason == "single_column_pending_invoice_check"

    def test_header_keyword_wins_immediately(self):
        """'Taxable Amount' label resolves without needing GST-pair check."""
        amount, reason = reconcile_row(
            [{"header": "Taxable Amount", "value": 150.0},
             {"header": "Amount",         "value": 157.50}],
            qty=6, gst_rate_pct=5
        )
        assert abs(amount - 150.0) < 0.05
        assert reason == "header_keyword_match"

    def test_confirmed_gst_pair_5pct(self):
        """Two columns, no keyword, ratio = 1.05 → confirmed ex-GST pair."""
        amount, reason = reconcile_row(
            [{"header": "Price",  "value": 150.0},
             {"header": "Net Amt","value": 157.50}],
            qty=6, gst_rate_pct=5
        )
        assert abs(amount - 150.0) < 0.05
        assert reason == "confirmed_ex_gst_pair"

    def test_confirmed_gst_pair_18pct(self):
        amount, reason = reconcile_row(
            [{"header": "Price",  "value": 100.0},
             {"header": "Total", "value": 118.0}],
            qty=2, gst_rate_pct=18
        )
        assert abs(amount - 100.0) < 0.05
        assert reason == "confirmed_ex_gst_pair"

    def test_mrp_is_skipped(self):
        """MRP column must be filtered out; real amount should win."""
        amount, reason = reconcile_row(
            [{"header": "MRP",    "value": 40.0},
             {"header": "Amount", "value": 25.0}],
            qty=6, gst_rate_pct=5
        )
        # After MRP is skipped, only one usable column remains → deferred
        assert abs(amount - 25.0) < 0.05
        assert reason == "single_column_pending_invoice_check"

    def test_three_columns_taxable_wins(self):
        """MRP, Price/Unit, Taxable Amount — taxable keyword must win."""
        amount, reason = reconcile_row(
            [{"header": "MRP",            "value": 60.0},
             {"header": "Price/Unit",     "value": 30.0},
             {"header": "Taxable Amount", "value": 25.0}],
            qty=6, gst_rate_pct=5
        )
        assert abs(amount - 25.0) < 0.05
        assert reason == "header_keyword_match"

    def test_no_columns(self):
        amount, reason = reconcile_row([], qty=6, gst_rate_pct=5)
        assert amount is None
        assert reason == "no_amount_found"

    def test_zero_value_filtered(self):
        amount, reason = reconcile_row(
            [{"header": "Amount",  "value": 0.0},
             {"header": "Taxable", "value": 150.0}],
            qty=6, gst_rate_pct=5
        )
        assert abs(amount - 150.0) < 0.05


# ─── Pass 2: resolve_single_column_basis ────────────────────────────────────

def _pending_items(amounts_and_qtys):
    """Helper: build a list of results already tagged as pending."""
    return [
        {"name": f"Item{i}", "qty": qty, "uom": "PCS", "rate": 0.0,
         "discount": 0, "amount": amt,
         "confidence_reason": "single_column_pending_invoice_check",
         "_rate_columns": [], "_amount_columns": []}
        for i, (qty, amt) in enumerate(amounts_and_qtys)
    ]


class TestResolveSingleColumnBasis:

    def test_exclusive_confirmed_by_anchor(self):
        """Single-column amounts that ARE ex-GST; anchor matches as-is."""
        # Items: qty=6 @ ₹25 = ₹150, qty=6 @ ₹43.57 = ₹261.42  → subtotal ≈ 411.42
        items = _pending_items([(6, 150.0), (6, 261.42)])
        result = resolve_single_column_basis(
            items, gst_rate_pct=5,
            printed_subtotal=411.42, printed_grand_total=None
        )
        for r in result:
            assert r["confidence_reason"] == "confirmed_ex_gst_by_invoice_anchor"
        # Amounts unchanged
        assert abs(result[0]["amount"] - 150.0) < 0.05
        assert abs(result[1]["amount"] - 261.42) < 0.05

    def test_inclusive_corrected_by_anchor(self):
        """Single-column amounts are GST-inclusive; anchor reveals this."""
        # Inclusive: 150 × 1.05 = 157.50;  261.42 × 1.05 ≈ 274.49
        # Printed subtotal (ex-GST) = 411.42
        items = _pending_items([(6, 157.50), (6, 274.49)])
        result = resolve_single_column_basis(
            items, gst_rate_pct=5,
            printed_subtotal=411.42, printed_grand_total=None
        )
        for r in result:
            assert r["confidence_reason"] == "corrected_from_inclusive_by_invoice_anchor"
        # Amounts must be divided by 1.05
        assert abs(result[0]["amount"] - 150.0) < 0.50   # ₹157.50 / 1.05 = 150
        assert abs(result[1]["amount"] - 261.42) < 0.50

    def test_no_anchor_flags_undetermined(self):
        """No printed total anywhere → must NOT guess; flag every item."""
        items = _pending_items([(6, 157.50), (6, 274.49)])
        result = resolve_single_column_basis(
            items, gst_rate_pct=5,
            printed_subtotal=None, printed_grand_total=None
        )
        for r in result:
            assert r["confidence_reason"] == "basis_undetermined_no_anchor"
        # Amounts must be unchanged (we didn't guess)
        assert abs(result[0]["amount"] - 157.50) < 0.05
        assert abs(result[1]["amount"] - 274.49) < 0.05

    def test_anchor_mismatch_flags_undetermined(self):
        """Anchor exists but neither assumption reconciles → flag."""
        items = _pending_items([(6, 200.0)])   # neither 200 nor 200/1.05 ≈ 411.42
        result = resolve_single_column_basis(
            items, gst_rate_pct=5,
            printed_subtotal=411.42, printed_grand_total=None
        )
        assert result[0]["confidence_reason"] == "basis_undetermined_anchor_mismatch"

    def test_no_pending_items_is_noop(self):
        """If no items are pending, the function must return unchanged."""
        items = [{"name": "X", "qty": 1, "amount": 50.0,
                  "confidence_reason": "confirmed_ex_gst_pair",
                  "rate": 50.0, "discount": 0, "uom": "PCS",
                  "_rate_columns": [], "_amount_columns": []}]
        result = resolve_single_column_basis(
            items, gst_rate_pct=5,
            printed_subtotal=50.0, printed_grand_total=None
        )
        assert result[0]["confidence_reason"] == "confirmed_ex_gst_pair"
        assert result[0]["amount"] == 50.0


# ─── Full pipeline: reconcile_invoice_items ──────────────────────────────────

class TestFullPipeline:

    def test_all_single_column_exclusive_confirmed(self):
        """
        Spec case: every row has only one amount column; amounts are ex-GST.
        resolve_single_column_basis() must confirm via printed_subtotal.
        """
        raw = [
            make_raw_item("Sambhar Masala", 6,  [("Rate", 25.0)],    [("Amount", 150.0)]),
            make_raw_item("Chat Masala",    6,  [("Rate", 43.57)],   [("Amount", 261.43)]),
        ]
        result = reconcile_invoice_items(
            raw, gst_rate_pct=5,
            printed_subtotal=411.43, printed_grand_total=None
        )
        for r in result:
            assert r["confidence_reason"] == "confirmed_ex_gst_by_invoice_anchor"
        assert abs(result[0]["amount"] - 150.0)  < 0.05
        assert abs(result[1]["amount"] - 261.43) < 0.05

    def test_all_single_column_inclusive_corrected(self):
        """
        Spec case: every row has one amount column; amounts are GST-inclusive.
        Must be corrected consistently across the whole invoice.
        """
        raw = [
            make_raw_item("Item A", 10, [("Rate", 10.5)],  [("Amount", 105.0)]),  # 100 × 1.05
            make_raw_item("Item B",  5, [("Rate", 21.0)],  [("Amount", 105.0)]),  # 100 × 1.05
        ]
        result = reconcile_invoice_items(
            raw, gst_rate_pct=5,
            printed_subtotal=200.0, printed_grand_total=None   # 100 + 100 = 200
        )
        for r in result:
            assert r["confidence_reason"] == "corrected_from_inclusive_by_invoice_anchor"
        subtotal = sum(r["amount"] for r in result)
        assert abs(subtotal - 200.0) < 1.0

    def test_all_single_column_no_anchor(self):
        """
        Spec case: every row has one amount column AND there is no printed
        subtotal or grand total anywhere on the invoice.
        Must NOT guess — must flag basis_undetermined_no_anchor.
        """
        raw = [
            make_raw_item("Item A", 10, [("Rate", 10.5)], [("Amount", 105.0)]),
            make_raw_item("Item B",  5, [("Rate", 21.0)], [("Amount", 105.0)]),
        ]
        result = reconcile_invoice_items(
            raw, gst_rate_pct=5,
            printed_subtotal=None, printed_grand_total=None
        )
        for r in result:
            assert r["confidence_reason"] == "basis_undetermined_no_anchor"
        # Amounts must be UNCHANGED — no correction was applied
        assert abs(result[0]["amount"] - 105.0) < 0.05
        assert abs(result[1]["amount"] - 105.0) < 0.05

    def test_two_column_sadashiv_traders_style(self):
        """
        Spec case: both a Taxable Amount and a GST-inclusive Amount column
        on every row (like Sadashiv Traders invoices). Must resolve row-by-row
        without needing the invoice-level anchor at all.
        """
        raw = [
            make_raw_item("Sambhar Masala", 6, [("Price", 25.0)],
                          [("Taxable Amount", 150.0), ("Amount", 157.50)]),
            make_raw_item("Chat Masala",    6, [("Price", 43.57)],
                          [("Taxable Amount", 261.43), ("Amount", 274.50)]),
        ]
        result = reconcile_invoice_items(
            raw, gst_rate_pct=5,
            printed_subtotal=411.43, printed_grand_total=None
        )
        for r in result:
            # Resolved at row level — should NOT need invoice anchor
            assert r["confidence_reason"] in ("header_keyword_match", "confirmed_ex_gst_pair")
        assert abs(result[0]["amount"] - 150.0)  < 0.05
        assert abs(result[1]["amount"] - 261.43) < 0.05

    def test_mrp_not_mistaken_for_ex_gst(self):
        """
        Spec case: MRP, Price/Unit, and Taxable Amount all present.
        MRP must not be picked just because it's smaller than the inclusive total.
        """
        raw = [
            make_raw_item("Sambhar Masala", 6,
                          [("MRP", 40.0), ("Price/Unit", 25.0)],
                          [("MRP Amount",      240.0),
                           ("Taxable Amount",  150.0),
                           ("Invoice Amount",  157.50)]),
        ]
        result = reconcile_invoice_items(
            raw, gst_rate_pct=5,
            printed_subtotal=150.0, printed_grand_total=None
        )
        assert abs(result[0]["amount"] - 150.0) < 0.05

    def test_regression_correct_invoice_unchanged(self):
        """
        Regression guard: a correctly-extracting invoice with a single Taxable
        Amount column and a matching printed_subtotal must pass through unaltered.
        """
        raw = [
            make_raw_item("Shahi Paneer 40gm", 6, [("Rate", 35.71)],
                          [("Taxable Amount", 214.29)]),
        ]
        result = reconcile_invoice_items(
            raw, gst_rate_pct=5,
            printed_subtotal=214.29, printed_grand_total=None
        )
        assert abs(result[0]["amount"] - 214.29) < 0.05
        # "Taxable Amount" header → header_keyword_match (row resolved in Pass 1)
        # Then Pass 2 sees no pending items and returns unchanged
        assert result[0]["confidence_reason"] in (
            "header_keyword_match",
            "confirmed_ex_gst_by_invoice_anchor",
        )


# ─── Aditya Agency pattern: half-slab rate extracted ─────────────────────────

class TestHalfSlabRateDiscovery:
    """
    Aditya Agency invoice: CGST=2.5% + SGST=2.5% printed in table (combined = 5%).
    Gemini likely extracts gst_rate=2.5. The Amount Rs. column is GST-inclusive
    (rate_per_unit × qty × 1.05). The printed Sub Total Value = ex-GST subtotal.
    The reconciler must auto-discover that 5% is the correct combined rate.
    """

    def _aditya_items(self):
        """5 rows mirroring the actual Aditya Agency invoice."""
        # Unit Price (MRP-style), Discount%, Price Rs. (post-disc ex-GST rate),
        # Amount Rs. = qty × rate × 1.05 (GST-inclusive)
        rows = [
            # (name, qty, price_rs, amount_rs)
            ("DM PASATA PENNE BOGO WW", 6,  106.66, 671.96),
            ("DM PASTA SPAGHETTI WW",   4,  106.67, 448.02),
            ("DM PIZZA & PASTA SAUCE",  6,   29.95, 188.69),
            ("DM SECHEZWAN SAUCE 80G",  6,   24.11, 152.49),
            ("DM SAUCE GREEN CHILI",   12,   20.17, 254.14),
        ]
        items = []
        for name, qty, price_rs, amount_rs in rows:
            items.append({
                "name": name, "qty": qty, "uom": "PCS",
                "rate_columns":   [{"header": "Price Rs.", "value": price_rs}],
                "amount_columns": [{"header": "Amount Rs.", "value": amount_rs}],
                "discount_pct": 0,
            })
        return items

    def test_half_slab_corrected_with_subtotal(self):
        """
        gst_rate=2.5 (half-slab as printed), but real factor=1.05.
        Should auto-discover and correct.
        """
        raw = self._aditya_items()
        # Compute what the ex-GST subtotal would be (sum of Price Rs. × qty)
        ex_gst_subtotal = sum(r["qty"] * safe_float(r["rate_columns"][0]["value"])
                               for r in raw)

        result = reconcile_invoice_items(
            raw, gst_rate_pct=2.5,
            printed_subtotal=round(ex_gst_subtotal, 2),
            printed_grand_total=None
        )
        for r in result:
            assert r["confidence_reason"] == "corrected_from_inclusive_by_invoice_anchor", \
                f"{r['name']}: expected corrected, got {r['confidence_reason']}"

        # Each corrected amount should ≈ qty × price_rs
        original = self._aditya_items()
        for r, orig in zip(result, original):
            expected = orig["qty"] * safe_float(orig["rate_columns"][0]["value"])
            assert abs(r["amount"] - expected) < 1.00, \
                f"{r['name']}: expected ~{expected:.2f}, got {r['amount']}"

    def test_correct_rate_still_works(self):
        """If gst_rate is already correct (5), no auto-discovery needed."""
        raw = self._aditya_items()
        ex_gst_subtotal = sum(r["qty"] * safe_float(r["rate_columns"][0]["value"])
                               for r in raw)
        result = reconcile_invoice_items(
            raw, gst_rate_pct=5,
            printed_subtotal=round(ex_gst_subtotal, 2),
            printed_grand_total=None
        )
        for r in result:
            assert r["confidence_reason"] == "corrected_from_inclusive_by_invoice_anchor"


# helper needed by test class above
from backend.services.reconciliation import safe_float
