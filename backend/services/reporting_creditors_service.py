import json
from datetime import timedelta
from backend.database import get_db
from backend.utils.reporting_utils import get_financial_year, parse_date, format_date

# A supplier's payable balance can move for three reasons that might still be
# sitting in the local offline queue (Tally unreachable / not yet re-synced):
# a new purchase (increases what you owe), a return/Debit Note (decreases),
# or a payment (decreases). Each has its own description prefix and payload
# shape -- see backend/services/reporting_purchase_service.py's
# _compute_purchase_queue_amount for the purchase/return payload shapes,
# reused here with the same reasoning. Payments come from
# backend/routers/payment.py: {debit_ledger (the party being paid),
# credit_ledger (bank/cash), amount, tally_date, ...}.
_SUPPLIER_QUEUE_DESCRIPTION_PATTERNS = (
    "Purchase Invoice:%",
    "Purchase Item Invoice:%",
    "Purchase Return (Debit Note):%",
    "Payment:%",
)

def _fetch_pending_supplier_movements(start_date: str, end_date: str, supplier_name: str = None) -> list[dict]:
    """Synthetic movement rows (same shape as a confirmed reporting_ledger_entries
    row, plus is_pending=True and no voucher_id) for pending purchase/return/
    payment queue entries -- optionally scoped to one supplier, otherwise
    across all of them (for the overview list, to avoid an extra query per
    supplier). Excludes delivery_uncertain rows for the same double-count
    safety reason as the Sales/Purchases pending-trend functions."""
    # Tally-style dates arrive as either YYYYMMDD or YYYY-MM-DD depending on
    # the caller (get_creditor_ledger_movements normalizes its own inputs the
    # same way) -- normalize here too so a plain string comparison below is
    # always comparing like-for-like, regardless of which caller reaches this
    # function first.
    start_date = start_date.replace('-', '')
    end_date = end_date.replace('-', '')
    with get_db() as conn:
        cursor = conn.cursor()
        placeholders = " OR ".join(["description LIKE ?"] * len(_SUPPLIER_QUEUE_DESCRIPTION_PATTERNS))
        cursor.execute(f"""
            SELECT payload, description FROM offline_queue
            WHERE operation_type = 'POST_VOUCHER' AND status = 'PENDING' AND ({placeholders})
        """, list(_SUPPLIER_QUEUE_DESCRIPTION_PATTERNS))
        rows = cursor.fetchall()

    movements = []
    for row in rows:
        try:
            payload = json.loads(row['payload']) if row['payload'] else {}
        except (json.JSONDecodeError, TypeError):
            continue
        if payload.get('delivery_uncertain') is True:
            continue

        date = payload.get('tally_date')
        if not date or not (start_date <= date <= end_date):
            continue

        description = row['description']
        if description.startswith("Payment:"):
            ledger = payload.get('debit_ledger')
            amount = -float(payload.get('amount') or 0)
            voucher_type = 'Payment'
        elif description.startswith("Purchase Return (Debit Note):"):
            ledger = payload.get('supplier')
            adjustment = payload.get('adjustment') or {}
            items = adjustment.get('items') or []
            amount = -sum(float(i.get('qty') or 0) * float(i.get('rate') or 0) for i in items)
            voucher_type = 'Debit Note'
        elif description.startswith("Purchase Invoice:"):
            ledger = payload.get('supplier')
            amount = float(payload.get('amount') or 0)
            voucher_type = 'Purchase'
        elif description.startswith("Purchase Item Invoice:"):
            ledger = payload.get('supplier')
            items = payload.get('items') or []
            items_total = sum(float(i.get('amount') or 0) for i in items)
            gst_total = (
                float(payload.get('cgst') or 0) + float(payload.get('sgst') or 0)
                + float(payload.get('igst') or 0) + float(payload.get('rounding_off') or 0)
            )
            amount = items_total + gst_total
            voucher_type = 'Purchase'
        else:
            continue

        if not ledger:
            continue
        if supplier_name and ledger != supplier_name:
            continue

        movements.append({
            "date": date,
            "voucher_type": voucher_type,
            "voucher_number": None,
            "reference": None,
            "amount": amount,
            "is_deemed_positive": None,
            "voucher_id": None,
            "ledger_name": ledger,
            "is_pending": True,
        })

    return movements

def get_creditor_ledger_movements(supplier_name: str, start_date: str, end_date: str, _pending_movements: list = None, _pending_movements_before_period: list = None):
    """
    Returns the opening balance for the period, the closing balance,
    and a chronological list of all ledger movements within the period.

    `_pending_movements`, when supplied, is a pre-fetched list from
    _fetch_pending_supplier_movements (unscoped to a supplier) that this
    function filters down to `supplier_name` -- used by
    get_all_creditors_overview so it doesn't re-scan the offline queue once
    per supplier. Callers hitting this function directly (e.g. the
    per-supplier ledger endpoint) can omit it and it's fetched here instead.

    `_pending_movements_before_period` is the same idea but for the
    FY-start-to-period-start window used to roll the opening balance forward
    (see below) -- also optional, also pre-fetched by the overview caller.
    """
    start_date = start_date.replace('-', '')
    end_date = end_date.replace('-', '')

    with get_db() as conn:
        cursor = conn.cursor()

        # 1. Master Opening Balance
        cursor.execute("SELECT opening_balance FROM ledgers WHERE name = ?", (supplier_name,))
        row = cursor.fetchone()
        master_ob = row['opening_balance'] if row else 0.0

        # 2. Movements before start_date but after FY start
        fy_start, _ = get_financial_year(start_date)
        cursor.execute("""
            SELECT SUM(rle.amount) as pre_movement
            FROM reporting_ledger_entries rle
            JOIN reporting_vouchers rv ON rle.voucher_id = rv.id
            WHERE rle.ledger_name = ? AND rv.date >= ? AND rv.date < ?
        """, (supplier_name, fy_start, start_date))
        pre_row = cursor.fetchone()
        pre_movement = pre_row['pre_movement'] if pre_row and pre_row['pre_movement'] else 0.0

        # Pending (not-yet-synced) queue entries dated before the period but
        # after FY start also need to be rolled into the opening balance --
        # otherwise a purchase/payment that hasn't synced to Tally yet is
        # invisible on any report starting after its date (too early for the
        # period's own movement list, and absent from reporting_ledger_entries
        # for the pre_movement query above), silently understating the
        # opening balance until sync catches up. Window end is exclusive of
        # start_date to match the confirmed query's `rv.date < start_date`
        # and avoid double-counting against the period's own pending fetch.
        pre_period_end = format_date(parse_date(start_date) - timedelta(days=1))
        if _pending_movements_before_period is not None:
            pending_before_period = [m for m in _pending_movements_before_period if m['ledger_name'] == supplier_name]
        else:
            pending_before_period = _fetch_pending_supplier_movements(fy_start, pre_period_end, supplier_name)
        pending_before_amount = sum(m['amount'] for m in pending_before_period)

        period_opening_confirmed = master_ob + pre_movement
        period_opening = period_opening_confirmed + pending_before_amount

        # 3. Movements inside the period
        cursor.execute("""
            SELECT rv.date, rv.voucher_type, rv.voucher_number, rv.reference, rle.amount, rle.is_deemed_positive, rv.id as voucher_id
            FROM reporting_ledger_entries rle
            JOIN reporting_vouchers rv ON rle.voucher_id = rv.id
            WHERE rle.ledger_name = ? AND rv.date >= ? AND rv.date <= ?
            ORDER BY rv.date ASC, rv.id ASC
        """, (supplier_name, start_date, end_date))

        confirmed_movements = [dict(r) for r in cursor.fetchall()]
        for m in confirmed_movements:
            m['is_pending'] = False

    if _pending_movements is not None:
        pending_movements = [m for m in _pending_movements if m['ledger_name'] == supplier_name]
    else:
        pending_movements = _fetch_pending_supplier_movements(start_date, end_date, supplier_name)

    all_movements = sorted(confirmed_movements + pending_movements, key=lambda m: m['date'])

    period_movement_confirmed = sum(m['amount'] for m in confirmed_movements)
    pending_amount = sum(m['amount'] for m in pending_movements)
    period_movement = period_movement_confirmed + pending_amount
    # Confirmed-only chain throughout, so this reflects what Tally itself
    # would currently show (ignoring anything still in the queue) -- must not
    # mix in the blended (pending-inclusive) period_opening above.
    period_closing_confirmed = period_opening_confirmed + period_movement_confirmed
    period_closing = period_opening + period_movement

    # Summarize for overview classification -- computed across confirmed AND
    # pending movements together, so a pending purchase/return/payment shows
    # up in the right bucket immediately rather than only once it syncs.
    purchases = sum(m['amount'] for m in all_movements if m['voucher_type'] == 'Purchase' and m['amount'] > 0)
    returns = sum(abs(m['amount']) for m in all_movements if m['voucher_type'] in ('Debit Note', 'Credit Note', 'Purchase Return') and m['amount'] < 0)
    payments = sum(abs(m['amount']) for m in all_movements if m['voucher_type'] == 'Payment' and m['amount'] < 0)

    # Any other movement
    classified_total = sum(m['amount'] for m in all_movements if m['voucher_type'] == 'Purchase' and m['amount'] > 0) \
                       - returns - payments

    other_adjustments = period_movement - classified_total

    return {
        "period_opening": period_opening,
        "period_opening_confirmed": period_opening_confirmed,
        "pending_opening_amount": round(pending_before_amount, 2),
        "period_closing": period_closing,
        "period_closing_confirmed": period_closing_confirmed,
        "pending_amount": round(pending_amount, 2),
        "movements": all_movements,
        "summary": {
            "purchases": purchases,
            "returns": returns,
            "payments": payments,
            "other_adjustments": other_adjustments,
            "net_movement": period_movement
        }
    }

def get_all_creditors_overview(start_date: str, end_date: str):
    """
    Returns the overview (opening, purchases, payments, returns, adjustments, closing)
    for all Sundry Creditors in the system.
    """
    with get_db() as conn:
        cursor = conn.cursor()

        # We only care about ledgers under Sundry Creditors group.
        # parent LIKE '%Creditor%' is a naive way, but Tally allows nested groups under Sundry Creditors.
        # We can fetch all ledgers with parent 'Sundry Creditors'.
        cursor.execute("SELECT name, opening_balance FROM ledgers WHERE parent = 'Sundry Creditors'")
        creditors = [dict(r) for r in cursor.fetchall()]

    # Fetched once for all suppliers, rather than once per supplier inside the
    # loop below -- get_creditor_ledger_movements' own confirmed-side query is
    # still a per-supplier N+1 (pre-existing, left as-is), but there's no need
    # to add a second one for the pending side too.
    all_pending = _fetch_pending_supplier_movements(start_date, end_date)

    # Same idea for the FY-start-to-period-start window used to roll the
    # opening balance forward -- every supplier shares the same start_date,
    # so this window (and thus this fetch) is identical for all of them.
    fy_start, _ = get_financial_year(start_date)
    pre_period_end = format_date(parse_date(start_date) - timedelta(days=1))
    all_pending_before_period = _fetch_pending_supplier_movements(fy_start, pre_period_end)

    results = []
    for c in creditors:
        # We can reuse the detailed function for simplicity. If performance is an issue,
        # this can be optimized into a single GROUP BY SQL query later.
        movements = get_creditor_ledger_movements(
            c['name'], start_date, end_date,
            _pending_movements=all_pending,
            _pending_movements_before_period=all_pending_before_period,
        )
        results.append({
            "supplier_name": c['name'],
            "period_opening": movements['period_opening'],
            "pending_opening_amount": movements['pending_opening_amount'],
            "purchases": movements['summary']['purchases'],
            "returns": movements['summary']['returns'],
            "payments": movements['summary']['payments'],
            "other_adjustments": movements['summary']['other_adjustments'],
            "period_closing": movements['period_closing'],
            "period_closing_confirmed": movements['period_closing_confirmed'],
            "pending_amount": movements['pending_amount'],
        })

    return results
