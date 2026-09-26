import json
from backend.database import get_db
import sqlite3

# Same scope and inclusion rules as Sales/Purchases/Creditors reporting -- see
# reporting_sales_service.get_queue_sales_trend and
# reporting_purchase_service.get_queue_purchases_trend for the general
# rationale (PENDING folded in, FAILED and delivery_uncertain excluded to
# avoid a double-count once a real sync confirms or rejects the item).
#
# Stock Transfer's "(Physical)" leg and Repack's Stock Journal are
# deliberately NOT included here even though they're real vouchers: neither
# posts any ALLLEDGERENTRIES.LIST, so they carry zero ledger-entry amount
# even once confirmed (Repack is handled separately below via its own
# REPACK_VOUCHER operation_type, purely for immediate visibility -- see
# _fetch_pending_repack_entries). Stock Transfer's "(Accounting)" leg is
# included here since it's a real Journal with real ledger entries.
_DAYBOOK_QUEUE_DESCRIPTION_PATTERNS = (
    "Sales:%",
    "Purchase Invoice:%",
    "Purchase Item Invoice:%",
    "Purchase Return (Debit Note):%",
    "Payment:%",
    "Stock Transfer:% (Accounting)",
)

def _fetch_confirmed_daybook_keys(start_date: str, end_date: str) -> tuple[set, set, set, set]:
    """Confirmed-side keys for the same window, used to drop a SYNCED queue
    row once its own voucher has appeared in reporting_vouchers -- without
    this, a queue row (never deleted after it syncs) would double count
    forever alongside the now-visible confirmed voucher. Same approach as
    reporting_creditors_service._fetch_confirmed_movement_keys, extended with
    Sales (which Creditors never sees) and Stock Transfer/Repack (which have
    no ledger-side counterpart in Creditors at all).

    Purchase/Debit Note match on (party_ledger_name, reference) -- REFERENCE
    is set verbatim from the app's own invoice_number at post time and is a
    reliable exact key. Sales/Payment have no such field, so they match on
    (voucher_type, party_ledger_name, date, amount, narration) instead --
    deliberately loose, but a voucher sharing all five with another is
    indistinguishable from a duplicate even by hand, so this is safe.

    Ledger names are lowercased before going into either key -- see the long
    comment on reporting_creditors_service._fetch_confirmed_movement_keys for
    why: a posting router's own metadata endpoint shows ledgers .title()-cased,
    that's what gets submitted and posted, but Tally's Day Book export returns
    the ledger's real stored casing, which can differ (e.g. a ledger actually
    named "cash mahagun" posts fine but comes back as "cash mahagun", not
    "Cash Mahagun"). An exact-case match would silently never fire for such a
    ledger and double-count forever once its SYNCED row's voucher confirms --
    caught live testing this against a real "cash mahagun" ledger.

    Stock Transfer's Accounting-leg Journal moves value between the SAME
    ledger name ("inter store transfer") on both legs, so there's no
    meaningful single "party" to key on -- matches on (date, amount,
    narration) instead, scoped to that narration prefix so it can't collide
    with an unrelated Journal (e.g. a loan's interest accrual) that happens
    to land on the same date/amount.

    Repack matches on tally_guid instead of anything fuzzy: repack.py posts a
    deterministic `f"REPACK-{repack_id}"` <GUID>, which Tally preserves
    verbatim and returns as-is in the confirmed voucher -- a GUID match is
    exact, not a heuristic, whenever one is available like this."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT party_ledger_name, reference
            FROM reporting_vouchers
            WHERE voucher_type IN ('Purchase', 'Debit Note')
              AND date >= ? AND date <= ?
              AND reference IS NOT NULL AND reference != ''
        """, (start_date, end_date))
        reference_keys = {((r['party_ledger_name'] or '').strip().lower(), r['reference']) for r in cursor.fetchall()}

        cursor.execute("""
            SELECT v.voucher_type, v.party_ledger_name, v.date, COALESCE(v.narration, '') as narration,
                   COALESCE(
                       (SELECT SUM(ABS(amount)) FROM reporting_ledger_entries WHERE voucher_id = v.id AND ledger_name = v.party_ledger_name AND v.party_ledger_name != ''),
                       (SELECT SUM(ABS(amount)) FROM reporting_ledger_entries WHERE voucher_id = v.id AND amount < 0)
                   ) as amount
            FROM reporting_vouchers v
            WHERE v.voucher_type IN ('Sales', 'Payment') AND v.date >= ? AND v.date <= ?
        """, (start_date, end_date))
        fuzzy_keys = {
            (r['voucher_type'], (r['party_ledger_name'] or '').strip().lower(), r['date'], round(r['amount'] or 0, 2), r['narration'])
            for r in cursor.fetchall()
        }

        cursor.execute("""
            SELECT date, COALESCE(narration, '') as narration,
                   COALESCE(
                       (SELECT SUM(ABS(amount)) FROM reporting_ledger_entries WHERE voucher_id = reporting_vouchers.id AND ledger_name = 'inter store transfer' AND amount > 0),
                       0
                   ) as amount
            FROM reporting_vouchers
            WHERE voucher_type = 'Journal' AND date >= ? AND date <= ?
              AND narration LIKE 'Inter-store transfer:%'
        """, (start_date, end_date))
        journal_keys = {(r['date'], round(r['amount'] or 0, 2), r['narration']) for r in cursor.fetchall()}

        cursor.execute("""
            SELECT tally_guid FROM reporting_vouchers
            WHERE date >= ? AND date <= ? AND tally_guid IS NOT NULL
        """, (start_date, end_date))
        guid_keys = {r['tally_guid'] for r in cursor.fetchall()}

    return reference_keys, fuzzy_keys, journal_keys, guid_keys

def _fetch_pending_daybook_entries(start_date: str, end_date: str, cost_centre: str = None) -> list[dict]:
    """Synthesizes a voucher-like row (same shape as a confirmed reporting_vouchers
    row, plus is_pending=True) for each non-delivery_uncertain queue entry in
    the date range that hasn't appeared in reporting_vouchers yet.

    Includes SYNCED rows (not just PENDING) so a live-posted voucher doesn't
    vanish from the daybook for the up-to-30-minute gap before the periodic
    reporting sync re-fetches it from Tally -- see _fetch_confirmed_daybook_keys,
    which is what keeps a SYNCED row from then double-counting once that sync
    catches up."""
    with get_db() as conn:
        cursor = conn.cursor()
        placeholders = " OR ".join(["description LIKE ?"] * len(_DAYBOOK_QUEUE_DESCRIPTION_PATTERNS))
        cursor.execute(f"""
            SELECT id, payload, description, created_at, status FROM offline_queue
            WHERE operation_type = 'POST_VOUCHER' AND status IN ('PENDING', 'SYNCED') AND ({placeholders})
        """, list(_DAYBOOK_QUEUE_DESCRIPTION_PATTERNS))
        rows = cursor.fetchall()

    reference_keys, fuzzy_keys, journal_keys, guid_keys = _fetch_confirmed_daybook_keys(start_date, end_date)

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
        store_candidates = None  # set below only for the multi-store Stock Transfer case
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
        elif description.startswith("Stock Transfer:") and description.endswith("(Accounting)"):
            # The Accounting leg moves value between the SAME ledger name
            # ("inter store transfer") under two different cost centres --
            # there's no single natural "party", and it belongs to both
            # stores at once, unlike every other type here.
            voucher_type = "Journal"
            party = "inter store transfer"
            amount = abs(float(payload.get('total_amount') or 0))
            from_store, to_store = payload.get('from_store'), payload.get('to_store')
            store = "Multiple" if (from_store and to_store and from_store != to_store) else (from_store or to_store)
            store_candidates = {s for s in (from_store, to_store) if s}
            # stock_transfer.py's own qty field is always a Python float
            # (pydantic-enforced) by the time it's rendered into the posted
            # XML's narration -- forcing float() here too keeps this
            # reconstruction byte-for-byte identical regardless of how qty
            # happened to round-trip through this payload's own JSON storage
            # (a bare int here would render as "5" instead of "5.0" and
            # silently break the dedup match).
            narration = f"Inter-store transfer: {float(payload.get('qty') or 0)} pcs of {payload.get('item_name')}"
        else:
            continue

        if store_candidates is None:
            store_candidates = {store} if store else set()

        if row['status'] == 'SYNCED':
            party_key = (party or '').strip().lower()
            if voucher_type in ('Purchase', 'Debit Note'):
                if (party_key, payload.get('invoice_number')) in reference_keys:
                    continue
            elif voucher_type in ('Sales', 'Payment'):
                key = (voucher_type, party_key, date, round(amount, 2), narration)
                if key in fuzzy_keys:
                    continue
            elif voucher_type == 'Journal':
                if (date, round(amount, 2), narration) in journal_keys:
                    continue

        if cost_centre:
            if cost_centre.lower() == "unallocated":
                if store_candidates:
                    continue
            elif cost_centre != "Combined" and cost_centre not in store_candidates:
                continue

        vtype_lower = voucher_type.lower()
        if vtype_lower in ('sales', 'payment'):
            debit, credit = 0.0, amount
        elif vtype_lower in ('purchase', 'receipt'):
            debit, credit = amount, 0.0
        else:
            # Debit Note, Journal and anything else neutral -- same fallback
            # as the confirmed-side classification below.
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
            # Confirmed (Tally-synced) vouchers only carry a date, never a
            # time of day -- this queued-at timestamp is the only real
            # per-transaction time this app has, so the daybook UI only
            # shows a time for still-pending entries.
            "created_at": row['created_at'],
        })

    entries.extend(_fetch_pending_repack_entries(start_date, end_date, cost_centre, guid_keys))
    return entries

def _fetch_pending_repack_entries(start_date: str, end_date: str, cost_centre: str, confirmed_guids: set) -> list[dict]:
    """Repack posts through a completely different path (operation_type
    REPACK_VOUCHER, not POST_VOUCHER, with a minimal {repack_id, created_by,
    tally_date} payload -- see repack.py's /execute) and its Stock Journal
    voucher has no ALLLEDGERENTRIES.LIST at all, so it's money-neutral (₹0)
    even once confirmed. This still gives it immediate visibility rather
    than a ~30-minute blank spot, matching the other 5 types, at the cost of
    always showing ₹0 -- consistent with what the confirmed row will show
    once synced, so there's no before/after amount flicker.

    Dedup is by tally_guid, not the fuzzy narration/amount matching used
    elsewhere: repack.py posts a deterministic f"REPACK-{repack_id}" <GUID>,
    which Tally preserves and returns verbatim, so this is an exact match."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, payload, created_at, status FROM offline_queue
            WHERE operation_type = 'REPACK_VOUCHER' AND status IN ('PENDING', 'SYNCED')
        """)
        rows = cursor.fetchall()
        if not rows:
            return []

        repack_ids = []
        parsed_rows = []
        for row in rows:
            try:
                payload = json.loads(row['payload']) if row['payload'] else {}
            except (json.JSONDecodeError, TypeError):
                continue
            repack_id = payload.get('repack_id')
            if not repack_id:
                continue
            parsed_rows.append((row, payload))
            repack_ids.append(repack_id)

        if not repack_ids:
            return []

        placeholders = ",".join(["?"] * len(repack_ids))
        cursor.execute(f"""
            SELECT id, store_name, dest_item_name, dest_qty FROM repack_operations
            WHERE id IN ({placeholders})
        """, repack_ids)
        ops_by_id = {r['id']: dict(r) for r in cursor.fetchall()}

    entries = []
    for row, payload in parsed_rows:
        op = ops_by_id.get(payload.get('repack_id'))
        if not op:
            continue

        # repack_operations has no date column of its own (see repack.py) --
        # tally_date lives only in this queue payload, added specifically so
        # this function doesn't have to guess from created_at.
        date = payload.get('tally_date') or (row['created_at'] or '').replace('-', '')[:8]
        if not date or not (start_date <= date <= end_date):
            continue

        store = op.get('store_name')
        if cost_centre:
            if cost_centre.lower() == "unallocated":
                if store:
                    continue
            elif cost_centre != "Combined" and store != cost_centre:
                continue

        if row['status'] == 'SYNCED' and f"REPACK-{payload['repack_id']}" in confirmed_guids:
            continue

        entries.append({
            "id": f"pending-repack-{row['id']}",
            "tally_guid": None,
            "date": date,
            "voucher_number": None,
            "voucher_type": "Stock Journal",
            # No real party ledger exists for a Stock Journal (no
            # ALLLEDGERENTRIES.LIST at all) -- the confirmed row will show
            # blank/"Unknown" here too once synced. Using the output item
            # name instead is a deliberate, informative stand-in for the
            # pending row, since the daybook list itself doesn't render
            # narration inline (only the voucher-detail modal does, and
            # pending rows aren't clickable) -- otherwise this entry would
            # say nothing about what was actually repacked.
            "party_ledger_name": op.get('dest_item_name'),
            "narration": f"Repack: {op.get('dest_qty')} of {op.get('dest_item_name')} at {store}",
            "reference": None,
            "reference_date": None,
            "effective_date": None,
            "cheque_number": None,
            "cheque_date": None,
            "bank_name": None,
            "amount": 0.0,
            "cost_centres": store,
            "store_display": store or "Unallocated",
            "debit": 0.0,
            "credit": 0.0,
            "is_pending": True,
            "created_at": row['created_at'],
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
