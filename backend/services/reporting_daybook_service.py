import json
from backend.database import get_db
import sqlite3

# Same scope and inclusion rules as Sales/Purchases/Creditors reporting -- see
# reporting_sales_service.get_pending_sales_trend and
# reporting_purchase_service.get_pending_purchases_trend for the general
# rationale (PENDING folded in, FAILED and delivery_uncertain excluded to
# avoid a double-count once a real sync confirms or rejects the item).
_DAYBOOK_QUEUE_DESCRIPTION_PATTERNS = (
    "Sales:%",
    "Purchase Invoice:%",
    "Purchase Item Invoice:%",
    "Purchase Return (Debit Note):%",
    "Payment:%",
)

def _fetch_pending_daybook_entries(start_date: str, end_date: str, cost_centre: str = None) -> list[dict]:
    """Synthesizes a voucher-like row (same shape as a confirmed reporting_vouchers
    row, plus is_pending=True) for each PENDING, non-delivery_uncertain queue
    entry in the date range."""
    with get_db() as conn:
        cursor = conn.cursor()
        placeholders = " OR ".join(["description LIKE ?"] * len(_DAYBOOK_QUEUE_DESCRIPTION_PATTERNS))
        cursor.execute(f"""
            SELECT id, payload, description FROM offline_queue
            WHERE operation_type = 'POST_VOUCHER' AND status = 'PENDING' AND ({placeholders})
        """, list(_DAYBOOK_QUEUE_DESCRIPTION_PATTERNS))
        rows = cursor.fetchall()

    entries = []
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
        narration = payload.get('narration') or ""
        if description.startswith("Sales:"):
            voucher_type = "Sales"
            party = payload.get('ledger')
            amount = abs(float(payload.get('amount') or 0))
            store = payload.get('cost_center')
        elif description.startswith("Payment:"):
            voucher_type = "Payment"
            party = payload.get('debit_ledger')
            amount = abs(float(payload.get('amount') or 0))
            store = payload.get('cost_center')
        elif description.startswith("Purchase Return (Debit Note):"):
            voucher_type = "Debit Note"
            party = payload.get('supplier')
            adjustment = payload.get('adjustment') or {}
            items = adjustment.get('items') or []
            amount = abs(sum(float(i.get('qty') or 0) * float(i.get('rate') or 0) for i in items))
            store = adjustment.get('store')
        elif description.startswith("Purchase Invoice:"):
            voucher_type = "Purchase"
            party = payload.get('supplier')
            amount = abs(float(payload.get('amount') or 0))
            store = payload.get('cost_center')
        elif description.startswith("Purchase Item Invoice:"):
            voucher_type = "Purchase"
            party = payload.get('supplier')
            items = payload.get('items') or []
            items_total = sum(float(i.get('amount') or 0) for i in items)
            gst_total = (
                float(payload.get('cgst') or 0) + float(payload.get('sgst') or 0)
                + float(payload.get('igst') or 0) + float(payload.get('rounding_off') or 0)
            )
            amount = abs(items_total + gst_total)
            store = payload.get('cost_center')
        else:
            continue

        if cost_centre:
            if cost_centre.lower() == "unallocated":
                if store:
                    continue
            elif cost_centre != "Combined" and store != cost_centre:
                continue

        vtype_lower = voucher_type.lower()
        if vtype_lower in ('sales', 'payment'):
            debit, credit = 0.0, amount
        elif vtype_lower in ('purchase', 'receipt'):
            debit, credit = amount, 0.0
        else:
            # Debit Note and anything else neutral -- same fallback as the
            # confirmed-side classification below.
            debit, credit = amount, 0.0

        entries.append({
            "id": f"pending-{row['id']}",
            "tally_guid": None,
            "date": date,
            "voucher_number": None,
            "voucher_type": voucher_type,
            "party_ledger_name": party,
            "narration": narration,
            "reference": None,
            "reference_date": None,
            "effective_date": None,
            "cheque_number": None,
            "cheque_date": None,
            "bank_name": None,
            "amount": round(amount, 2),
            "cost_centres": store,
            "store_display": store or "Unallocated",
            "debit": round(debit, 2),
            "credit": round(credit, 2),
            "is_pending": True,
        })

    return entries

def get_daybook(start_date: str, end_date: str, cost_centre: str = None, page: int = 1, limit: int = 50, search: str = "", sort_by: str = "date_desc"):
    offset = (page - 1) * limit

    with get_db() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Base query to get vouchers with their computed total amount
        # Total amount = sum(abs(amount)) where is_deemed_positive = 1

        # We need to compute the true transaction magnitude for each voucher.
        # The most accurate representation is the amount posted to the primary party ledger.
        # If no party ledger exists (or is empty), we fallback to the sum of all debits (amount < 0).
        amount_subquery = """
            SELECT v.id as voucher_id,
                   COALESCE(
                       (SELECT SUM(ABS(amount)) FROM reporting_ledger_entries WHERE voucher_id = v.id AND ledger_name = v.party_ledger_name AND v.party_ledger_name != ''),
                       (SELECT SUM(ABS(amount)) FROM reporting_ledger_entries WHERE voucher_id = v.id AND amount < 0)
                   ) as total_amount
            FROM reporting_vouchers v
        """

        # We also need to get cost centres for the voucher to show in the "Store" column
        # A voucher might have multiple cost centres
        cc_subquery = """
            SELECT rle.voucher_id, GROUP_CONCAT(DISTINCT rcca.cost_centre_name) as stores
            FROM reporting_ledger_entries rle
            JOIN reporting_cost_centre_allocations rcca ON rle.id = rcca.ledger_entry_id
            GROUP BY rle.voucher_id
        """

        base_query = f"""
            SELECT v.id, v.tally_guid, v.date, v.voucher_number, v.voucher_type,
                   v.party_ledger_name, v.narration, v.reference, v.reference_date,
                   v.effective_date, v.cheque_number, v.cheque_date, v.bank_name,
                   COALESCE(a.total_amount, 0) as amount,
                   cc.stores as cost_centres
            FROM reporting_vouchers v
            LEFT JOIN ({amount_subquery}) a ON v.id = a.voucher_id
            LEFT JOIN ({cc_subquery}) cc ON v.id = cc.voucher_id
            WHERE v.date >= ? AND v.date <= ?
        """
        params = [start_date, end_date]

        if cost_centre:
            if cost_centre.lower() == "unallocated":
                base_query += " AND (cc.stores IS NULL OR cc.stores = '')"
            elif cost_centre != "Combined":
                base_query += " AND cc.stores LIKE ?"
                params.append(f"%{cost_centre}%")

        if search:
            base_query += """ AND (
                v.voucher_number LIKE ? OR
                v.voucher_type LIKE ? OR
                v.party_ledger_name LIKE ? OR
                v.narration LIKE ?
            )"""
            search_param = f"%{search}%"
            params.extend([search_param] * 4)

        # No LIMIT/OFFSET here -- pending queue entries (fetched separately
        # below) have to be merged, sorted, and paginated together with these
        # confirmed rows, so pagination happens once, in Python, below.
        cursor.execute(base_query, params)
        rows = cursor.fetchall()

        vouchers = []
        for r in rows:
            v_dict = dict(r)
            # Determine Store display
            stores = v_dict.get('cost_centres')
            if not stores:
                v_dict['store_display'] = "Unallocated"
            else:
                store_list = [s.strip() for s in stores.split(',')]
                if len(store_list) > 1:
                    v_dict['store_display'] = "Multiple"
                else:
                    v_dict['store_display'] = store_list[0]

            # Set Debit/Credit
            # To show actual accounting direction:
            # Sales -> Credit
            # Purchase -> Debit
            # Payment -> Credit
            # Receipt -> Debit
            # Contra -> Transfer
            vtype = v_dict['voucher_type'].lower()
            if vtype in ['sales', 'payment']:
                v_dict['credit'] = v_dict['amount']
                v_dict['debit'] = 0.0
            elif vtype in ['purchase', 'receipt']:
                v_dict['debit'] = v_dict['amount']
                v_dict['credit'] = 0.0
            else:
                # Contra, Journal, etc.
                # In Tally, Contra shows as Debit/Inwards in cash book for receiving, Credit/Outwards for paying.
                # In daybook, it typically just shows the amount in both or one. Let's just put it in Debit for neutral.
                v_dict['debit'] = v_dict['amount']
                v_dict['credit'] = 0.0

            v_dict['is_pending'] = False
            vouchers.append(v_dict)

    pending_entries = _fetch_pending_daybook_entries(start_date, end_date, cost_centre)
    if search:
        search_lower = search.lower()
        pending_entries = [
            e for e in pending_entries
            if search_lower in (e['voucher_type'] or '').lower()
            or search_lower in (e['party_ledger_name'] or '').lower()
            or search_lower in (e['narration'] or '').lower()
        ]

    combined = vouchers + pending_entries

    if sort_by == "date_asc":
        combined.sort(key=lambda v: v['date'])
    elif sort_by == "amount_desc":
        combined.sort(key=lambda v: v['amount'], reverse=True)
    elif sort_by == "amount_asc":
        combined.sort(key=lambda v: v['amount'])
    else:  # date_desc (default)
        combined.sort(key=lambda v: v['date'], reverse=True)

    total_rows = len(combined)
    page_slice = combined[offset:offset + limit]

    return {
        "vouchers": page_slice,
        "total": total_rows,
        "page": page,
        "limit": limit
    }
