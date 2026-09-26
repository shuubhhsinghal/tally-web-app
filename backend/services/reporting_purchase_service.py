import json
from backend.database import get_db

# Purchases are posted from three different places, each with its own
# offline_queue description prefix and payload shape -- unlike Sales, there's
# no single flat "amount" field to sum:
#   - Accounting mode (backend/routers/purchase.py): "Purchase Invoice: ..." --
#     a flat `amount` field.
#   - Item Wise mode, main invoice (backend/routers/purchase_item.py): "Purchase
#     Item Invoice: ..." -- no flat amount; it's sum(items[].amount) + cgst +
#     sgst + igst + rounding_off. (When GST is included in the item rate
#     rather than shown separately, cgst/sgst/igst are already 0 in the
#     payload, so this formula is correct either way.)
#   - Item Wise mode, return (backend/routers/purchase_item.py): "Purchase
#     Return (Debit Note): ..." -- reuses the ENTIRE original invoice's
#     payload (not its own), so the actual return items live under
#     payload['adjustment']['items'], each with its own qty/rate. A return
#     reduces net purchases, matching the confirmed-side SUM(amount * -1)
#     sign convention for a synced Debit Note.
_PURCHASE_QUEUE_DESCRIPTION_PATTERNS = (
    "Purchase Invoice:%",
    "Purchase Item Invoice:%",
    "Purchase Return (Debit Note):%",
)

# A Stock Transfer's accounting leg (backend/routers/stock_transfer.py) posts
# a Journal voucher against the "inter store transfer" ledger -- parented
# under Purchase Accounts in Tally, cost-centre allocated so the receiving
# store's purchases increase and the sending store's decrease. It's not a
# supplier invoice (no "supplier" field in its payload at all), so it's
# deliberately excluded from _PURCHASE_QUEUE_DESCRIPTION_PATTERNS (used by
# the Bills list / Supplier analysis, which are supplier-invoice-specific)
# but included here for the summary total/trend, matching how the confirmed
# side already includes it via plain ledger-group summation, with no
# supplier restriction.
_PURCHASE_GROUP_QUEUE_DESCRIPTION_PATTERNS = _PURCHASE_QUEUE_DESCRIPTION_PATTERNS + (
    "Stock Transfer:%(Accounting)",
)

# Used only by the headline summary/trend/store-comparison queries below
# (calculate_purchases, get_purchases_trend, get_store_comparisons) --
# permanently excludes these three voucher shapes -- each is posted
# exclusively by this app (Purchase/Debit Note directly, matching
# _PURCHASE_QUEUE_DESCRIPTION_PATTERNS above; the inter-store-transfer
# Journal matching _PURCHASE_GROUP_QUEUE_DESCRIPTION_PATTERNS' extra entry)
# and is instead counted permanently from the offline queue the moment it's
# SYNCED (get_queue_purchases_trend). Mirrors get_ledger_current_balance's
# loan-voucher exclusion and reporting_sales_service.calculate_sales'
# Sales-voucher exclusion, for the identical reason: a Tally-confirmed
# voucher can lag up to 30 minutes behind an already-successful post via the
# separate reporting sync, and a plain number has no row to double-count.
#
# Deliberately NOT applied to get_purchase_bills / get_supplier_purchase_analysis
# (the per-invoice drill-down views): those still count a Purchase/Debit Note
# only once truly confirmed by Tally, same as before this constant existed,
# and only merge in PENDING queue rows (_fetch_pending_purchase_bill_rows) --
# never SYNCED ones. A drill-down row is never deleted from the queue once
# SYNCED, so folding it in there too would double-count it forever once the
# real confirmed row also appeared, not just for the ~30 minutes until the
# reporting sync catches up -- and permanently excluding Purchase/Debit Note
# from these views instead would permanently hide the GST/item-level detail
# a confirmed voucher provides, which the queue payload doesn't preserve.
# So a purchase can briefly (same ~30 min window as before) be reflected in
# the headline total before it's visible in Bills/By-supplier -- an existing,
# narrower characteristic this change doesn't newly introduce.
_PURCHASE_QUEUE_OWNED_VOUCHER_EXCLUSION_SQL = """
          AND rv.voucher_type NOT IN ('Purchase', 'Debit Note')
          AND NOT (rv.voucher_type = 'Journal' AND COALESCE(rv.narration, '') LIKE 'Inter-store transfer:%')
"""

def _compute_purchase_queue_amount(description: str, payload: dict) -> list:
    """Returns a list of (signed_amount, store_or_None) pairs for one
    purchase-related offline_queue row's payload -- almost always exactly one
    pair, except a Stock Transfer's accounting leg, which produces two (one
    per store, since a single queued entry moves both sides of the transfer
    at once). Returns [] if the description doesn't match any known pattern."""
    if description.startswith("Purchase Return (Debit Note):"):
        adjustment = payload.get('adjustment') or {}
        items = adjustment.get('items') or []
        amount = sum(float(i.get('qty') or 0) * float(i.get('rate') or 0) for i in items)
        return [(-amount, adjustment.get('store'))]
    if description.startswith("Purchase Invoice:"):
        return [(float(payload.get('amount') or 0), payload.get('cost_center'))]
    if description.startswith("Purchase Item Invoice:"):
        items = payload.get('items') or []
        items_total = sum(float(i.get('amount') or 0) for i in items)
        gst_total = (
            float(payload.get('cgst') or 0) + float(payload.get('sgst') or 0)
            + float(payload.get('igst') or 0) + float(payload.get('rounding_off') or 0)
        )
        return [(items_total + gst_total, payload.get('cost_center'))]
    if description.startswith("Stock Transfer:") and description.endswith("(Accounting)"):
        total_amount = float(payload.get('total_amount') or 0)
        return [
            (total_amount, payload.get('to_store')),
            (-total_amount, payload.get('from_store')),
        ]
    return []

def get_unsynced_purchases(start_date: str, end_date: str, cost_centre: str = None) -> dict:
    """Purchase entries (and returns) posted from this app but not yet
    reflected in the report above -- mirrors get_unsynced_sales, see that
    docstring for the general rationale. pending_amount/failed_amount are
    NET signed totals (a pending return reduces the total, same as a synced
    one would)."""
    with get_db() as conn:
        cursor = conn.cursor()
        placeholders = " OR ".join(["description LIKE ?"] * len(_PURCHASE_GROUP_QUEUE_DESCRIPTION_PATTERNS))
        cursor.execute(f"""
            SELECT payload, status, description FROM offline_queue
            WHERE operation_type = 'POST_VOUCHER' AND ({placeholders})
              AND status IN ('PENDING', 'FAILED')
        """, list(_PURCHASE_GROUP_QUEUE_DESCRIPTION_PATTERNS))
        rows = cursor.fetchall()

    pending_count = failed_count = 0
    pending_amount = failed_amount = 0.0
    for row in rows:
        try:
            payload = json.loads(row['payload']) if row['payload'] else {}
        except (json.JSONDecodeError, TypeError):
            continue

        date = payload.get('tally_date')
        if not date or not (start_date <= date <= end_date):
            continue

        for amount, store in _compute_purchase_queue_amount(row['description'], payload):
            if cost_centre:
                if cost_centre == "Unallocated":
                    if store:
                        continue
                elif store != cost_centre:
                    continue

            if row['status'] == 'FAILED':
                failed_count += 1
                failed_amount += amount
            else:
                pending_count += 1
                pending_amount += amount

    return {
        "pending_count": pending_count,
        "pending_amount": round(pending_amount, 2),
        "failed_count": failed_count,
        "failed_amount": round(failed_amount, 2),
    }

def get_queue_purchases_trend(start_date: str, end_date: str, cost_centre: str = None) -> list[dict]:
    """Every purchase/return this app has ever posted -- PENDING or SYNCED --
    that's safe to fold into the report's own figures, same per-date/per-store
    shape as get_purchases_trend, and the same delivery_uncertain exclusion
    rationale as reporting_sales_service.get_queue_sales_trend. A SYNCED row
    is trusted here permanently (see _PURCHASE_QUEUE_OWNED_VOUCHER_EXCLUSION_SQL
    on the confirmed side), not just until the reporting sync catches up."""
    with get_db() as conn:
        cursor = conn.cursor()
        placeholders = " OR ".join(["description LIKE ?"] * len(_PURCHASE_GROUP_QUEUE_DESCRIPTION_PATTERNS))
        cursor.execute(f"""
            SELECT payload, description FROM offline_queue
            WHERE operation_type = 'POST_VOUCHER' AND ({placeholders})
              AND status IN ('PENDING', 'SYNCED')
        """, list(_PURCHASE_GROUP_QUEUE_DESCRIPTION_PATTERNS))
        rows = cursor.fetchall()

    trend_dict = {}
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

        for amount, store_raw in _compute_purchase_queue_amount(row['description'], payload):
            store = store_raw or 'Unallocated'

            if cost_centre:
                if cost_centre == "Unallocated":
                    if store != 'Unallocated':
                        continue
                elif store != cost_centre:
                    continue

            if date not in trend_dict:
                trend_dict[date] = {"date": date, "Combined": 0.0, "Combined_invoices": 0}
            trend_dict[date][store] = trend_dict[date].get(store, 0.0) + amount
            trend_dict[date]["Combined"] += amount
            trend_dict[date]["Combined_invoices"] += 1

    return list(trend_dict.values())

def _fetch_pending_purchase_bill_rows(start_date: str, end_date: str, cost_centre: str = None, supplier_name: str = None) -> list[dict]:
    """Per-transaction synthetic bill rows for PENDING (non-delivery_uncertain)
    purchase/return queue entries -- same scope and inclusion rules as
    get_queue_purchases_trend, but one row per queue entry (not grouped by
    date), so it can be merged directly into get_purchase_bills' and
    get_supplier_purchase_analysis's per-voucher lists. Without this, the
    summary total (which already includes pending) wouldn't reconcile with
    what these drill-down lists show.

    Deliberately stays PENDING-only (unlike get_queue_purchases_trend, which
    also includes SYNCED for the headline summary/trend/store-comparison):
    once a Purchase/Debit Note voucher is SYNCED, this app's ONLY way to
    later drill into its real GST/item-level detail is via the confirmed
    reporting_vouchers row the separate reporting sync eventually creates.
    A synthetic queue row is never deleted once SYNCED, so folding it in here
    too would double-count it forever (not just for the ~30 minutes until
    that sync catches up) once the real row also appears. The summary total
    doesn't have this problem (a plain number has no row to duplicate), which
    is why only it gets the fuller fix -- see
    _PURCHASE_QUEUE_OWNED_VOUCHER_EXCLUSION_SQL's docstring."""
    with get_db() as conn:
        cursor = conn.cursor()
        placeholders = " OR ".join(["description LIKE ?"] * len(_PURCHASE_QUEUE_DESCRIPTION_PATTERNS))
        cursor.execute(f"""
            SELECT id, payload, description FROM offline_queue
            WHERE operation_type = 'POST_VOUCHER' AND status = 'PENDING' AND ({placeholders})
        """, list(_PURCHASE_QUEUE_DESCRIPTION_PATTERNS))
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

        movements = _compute_purchase_queue_amount(row['description'], payload)
        if not movements:
            continue
        amount, store_raw = movements[0]
        if amount == 0:
            continue
        store = store_raw or 'Unallocated'

        if cost_centre:
            if cost_centre == "Unallocated":
                if store != 'Unallocated':
                    continue
            elif store != cost_centre:
                continue

        supplier = payload.get('supplier') or 'Unknown'
        if supplier_name and supplier != supplier_name:
            continue

        entries.append({
            "voucher_id": f"pending-{row['id']}",
            "date": date,
            "voucher_number": None,
            "supplier_name": supplier,
            "net_purchases": round(amount, 2),
            "store_name": store,
            "is_pending": True,
        })

    return entries

def calculate_purchases(start_date: str, end_date: str, cost_centre: str = None) -> float:
    """
    Net Purchases = SUM(amount * -1) where ledgers.parent = 'Purchase Accounts'
    This elegantly handles Normal Purchases (Debit) as positive and Purchase Returns (Credit) as negative.
    Excludes GST ledgers inherently.

    Uses strict Cost Centre logic:
    Allocated: SUM(rca.amount * -1) where rca.cost_centre_name = ?
    Unallocated: SUM(rle.amount * -1) where rca.ledger_entry_id IS NULL
    Combined: SUM(allocated) + SUM(unallocated)

    See _PURCHASE_QUEUE_OWNED_VOUCHER_EXCLUSION_SQL: Purchase/Debit
    Note/inter-store-transfer-Journal vouchers are permanently excluded here
    and counted from the offline queue instead (get_queue_purchases_trend).
    """
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        # 1. Allocated Purchases
        allocated_query = """
            SELECT COALESCE(SUM(rca.amount * -1), 0.0) as allocated_purchases
            FROM reporting_vouchers rv
            JOIN reporting_ledger_entries rle ON rv.id = rle.voucher_id
            JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
            JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
            WHERE l.parent = 'Purchase Accounts'
              AND CAST(rv.date AS INTEGER) >= CAST(? AS INTEGER)
              AND CAST(rv.date AS INTEGER) <= CAST(? AS INTEGER)
        """ + _PURCHASE_QUEUE_OWNED_VOUCHER_EXCLUSION_SQL
        allocated_params = [start_date, end_date]
        if cost_centre and cost_centre != "Unallocated":
            allocated_query += " AND rca.cost_centre_name = ?"
            allocated_params.append(cost_centre)

        cursor.execute(allocated_query, allocated_params)
        allocated_amount = cursor.fetchone()['allocated_purchases']

        # 2. Unallocated Purchases
        unallocated_amount = 0.0
        if not cost_centre or cost_centre == "Unallocated":
            unallocated_query = """
                SELECT COALESCE(SUM(rle.amount * -1), 0.0) as unallocated_purchases
                FROM reporting_vouchers rv
                JOIN reporting_ledger_entries rle ON rv.id = rle.voucher_id
                JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
                LEFT JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
                WHERE l.parent = 'Purchase Accounts'
                  AND rca.ledger_entry_id IS NULL
                  AND CAST(rv.date AS INTEGER) >= CAST(? AS INTEGER)
                  AND CAST(rv.date AS INTEGER) <= CAST(? AS INTEGER)
            """ + _PURCHASE_QUEUE_OWNED_VOUCHER_EXCLUSION_SQL
            cursor.execute(unallocated_query, [start_date, end_date])
            unallocated_amount = cursor.fetchone()['unallocated_purchases']
            
        if cost_centre and cost_centre != "Unallocated":
            return allocated_amount
        if cost_centre == "Unallocated":
            return unallocated_amount
            
        return allocated_amount + unallocated_amount

def get_store_comparisons(start_date: str, end_date: str) -> list[dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        
        allocated_query = """
            SELECT 
                rca.cost_centre_name as store_name,
                COALESCE(SUM(rca.amount * -1), 0.0) as net_purchases
            FROM reporting_vouchers rv
            JOIN reporting_ledger_entries rle ON rv.id = rle.voucher_id
            JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
            JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
            WHERE l.parent = 'Purchase Accounts'
              AND CAST(rv.date AS INTEGER) >= CAST(? AS INTEGER)
              AND CAST(rv.date AS INTEGER) <= CAST(? AS INTEGER)
        """ + _PURCHASE_QUEUE_OWNED_VOUCHER_EXCLUSION_SQL + """
            GROUP BY store_name
        """
        cursor.execute(allocated_query, [start_date, end_date])
        results = [dict(row) for row in cursor.fetchall()]

        unallocated_query = """
            SELECT
                'Unallocated' as store_name,
                COALESCE(SUM(rle.amount * -1), 0.0) as net_purchases
            FROM reporting_vouchers rv
            JOIN reporting_ledger_entries rle ON rv.id = rle.voucher_id
            JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
            LEFT JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
            WHERE l.parent = 'Purchase Accounts'
              AND rca.ledger_entry_id IS NULL
              AND CAST(rv.date AS INTEGER) >= CAST(? AS INTEGER)
              AND CAST(rv.date AS INTEGER) <= CAST(? AS INTEGER)
        """ + _PURCHASE_QUEUE_OWNED_VOUCHER_EXCLUSION_SQL
        cursor.execute(unallocated_query, [start_date, end_date])
        unallocated = cursor.fetchone()
        
        if unallocated and unallocated['net_purchases'] != 0:
            results.append(dict(unallocated))
            
        return results

def get_purchases_trend(start_date: str, end_date: str, cost_centre: str = None) -> list[dict]:
    trend_dict = {}
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        if cost_centre != 'Unallocated':
            allocated_query = """
                SELECT 
                    rv.date,
                    rca.cost_centre_name as store_name,
                    COALESCE(SUM(rca.amount * -1), 0.0) as net_purchases,
                    COUNT(DISTINCT rv.id) as invoice_count
                FROM reporting_vouchers rv
                JOIN reporting_ledger_entries rle ON rv.id = rle.voucher_id
                JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
                JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
                WHERE l.parent = 'Purchase Accounts'
                  AND CAST(rv.date AS INTEGER) >= CAST(? AS INTEGER)
                  AND CAST(rv.date AS INTEGER) <= CAST(? AS INTEGER)
            """ + _PURCHASE_QUEUE_OWNED_VOUCHER_EXCLUSION_SQL
            params = [start_date, end_date]
            if cost_centre:
                allocated_query += " AND rca.cost_centre_name = ?"
                params.append(cost_centre)

            allocated_query += " GROUP BY rv.date, store_name"
            
            cursor.execute(allocated_query, params)
            for row in cursor.fetchall():
                date = row['date']
                store = row['store_name']
                amt = row['net_purchases']
                inv_count = row['invoice_count']
                if date not in trend_dict:
                    trend_dict[date] = {"date": date, "Combined": 0.0, "Combined_invoices": 0}
                trend_dict[date][store] = trend_dict[date].get(store, 0.0) + amt
                trend_dict[date]["Combined"] += amt
                trend_dict[date]["Combined_invoices"] += inv_count
                
        if not cost_centre or cost_centre == 'Unallocated':
            unallocated_query = """
                SELECT 
                    rv.date,
                    'Unallocated' as store_name,
                    COALESCE(SUM(rle.amount * -1), 0.0) as net_purchases,
                    COUNT(DISTINCT rv.id) as invoice_count
                FROM reporting_vouchers rv
                JOIN reporting_ledger_entries rle ON rv.id = rle.voucher_id
                JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
                LEFT JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
                WHERE l.parent = 'Purchase Accounts'
                  AND rca.ledger_entry_id IS NULL
                  AND CAST(rv.date AS INTEGER) >= CAST(? AS INTEGER)
                  AND CAST(rv.date AS INTEGER) <= CAST(? AS INTEGER)
            """ + _PURCHASE_QUEUE_OWNED_VOUCHER_EXCLUSION_SQL + """
                GROUP BY rv.date
            """
            cursor.execute(unallocated_query, [start_date, end_date])
            for row in cursor.fetchall():
                date = row['date']
                store = row['store_name']
                amt = row['net_purchases']
                inv_count = row['invoice_count']
                if amt != 0:
                    if date not in trend_dict:
                        trend_dict[date] = {"date": date, "Combined": 0.0, "Combined_invoices": 0}
                    trend_dict[date][store] = trend_dict[date].get(store, 0.0) + amt
                    trend_dict[date]["Combined"] += amt
                    trend_dict[date]["Combined_invoices"] += inv_count
                
    # Sort chronologically
    sorted_trend = sorted(trend_dict.values(), key=lambda x: x["date"])
    return sorted_trend

def get_item_purchase_analysis(start_date: str, end_date: str, page: int = 1, limit: int = 50, search: str = "", sort_by: str = "value_desc", cost_centre: str = None) -> dict:
    """
    Returns paginated item analysis.
    Uses inventory entries to get quantity and purchase value.
    Multiplying billed_qty * -1 and amount * -1 correctly applies Tally sign logic 
    (Debit/Purchase = negative amount, Credit/Return = positive amount in XML)
    """
    offset = (page - 1) * limit
    
    # We must join ledgers and Cost Centres to filter by Cost Centre,
    # because inventory entries do not have Cost Centres attached directly.
    # However, to avoid duplicating rows if there are multiple Cost Centres per voucher,
    # we filter vouchers by cost centre first if cost_centre is specified.
    
    # Wait, if a single voucher has multiple items and multiple cost centres,
    # tying an item to a cost centre precisely is difficult if the XML doesn't provide
    # item-level cost centre tags. We will just filter the voucher if it contains the cost centre,
    # or if we are doing combined, we just sum everything.
    
    # To keep it completely accurate, we sum inventory entries per voucher.
    # If cost_centre is specified, we only include vouchers that have that cost centre.
    # We also apply search and sorting.
    
    base_query = """
        FROM reporting_inventory_entries rie
        JOIN reporting_vouchers rv ON rie.voucher_id = rv.id
        WHERE CAST(rv.date AS INTEGER) >= CAST(? AS INTEGER)
          AND CAST(rv.date AS INTEGER) <= CAST(? AS INTEGER)
    """
    params = [start_date, end_date]
    
    if cost_centre:
        if cost_centre == "Unallocated":
            base_query += """ 
                AND EXISTS (
                    SELECT 1 FROM reporting_ledger_entries rle
                    JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
                    LEFT JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
                    WHERE rle.voucher_id = rv.id AND l.parent = 'Purchase Accounts' AND rca.ledger_entry_id IS NULL
                )
            """
        else:
            base_query += """
                AND EXISTS (
                    SELECT 1 FROM reporting_ledger_entries rle
                    JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
                    JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
                    WHERE rle.voucher_id = rv.id AND l.parent = 'Purchase Accounts' AND rca.cost_centre_name = ?
                )
            """
            params.append(cost_centre)
            
    if search:
        base_query += " AND rie.stock_item_name LIKE ?"
        params.append(f"%{search}%")
        
    count_query = f"SELECT COUNT(DISTINCT rie.stock_item_name) as total {base_query}"
    
    order_clause = "ORDER BY total_value DESC"
    if sort_by == "value_asc":
        order_clause = "ORDER BY total_value ASC"
    elif sort_by == "qty_desc":
        order_clause = "ORDER BY total_qty DESC"
    elif sort_by == "qty_asc":
        order_clause = "ORDER BY total_qty ASC"
    elif sort_by == "name_asc":
        order_clause = "ORDER BY rie.stock_item_name ASC"
    elif sort_by == "name_desc":
        order_clause = "ORDER BY rie.stock_item_name DESC"
        
    data_query = f"""
        SELECT 
            rie.stock_item_name as item_name,
            -- quantity sign depends on amount sign (Debit/Purchase vs Credit/Return)
            SUM(rie.billed_qty * CASE 
                WHEN rie.amount < 0 THEN 1 
                WHEN rie.amount > 0 THEN -1 
                ELSE 1 
            END) as total_qty,
            SUM(rie.amount * -1) as total_value,
            COUNT(DISTINCT rv.id) as vouchers_count,
            COUNT(DISTINCT rv.party_ledger_name) as suppliers_count
        {base_query}
        GROUP BY rie.stock_item_name
        {order_clause}
        LIMIT ? OFFSET ?
    """
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        cursor.execute(count_query, params)
        total_items = cursor.fetchone()['total']
        
        data_params = params + [limit, offset]
        cursor.execute(data_query, data_params)
        items = [dict(row) for row in cursor.fetchall()]
        
        # Round the values
        for item in items:
            item['total_qty'] = round(item['total_qty'], 2) if item['total_qty'] else 0.0
            item['total_value'] = round(item['total_value'], 2) if item['total_value'] else 0.0
            
            # Extract UOM if available (often format is "6.00 PCS")
            # Wait, the stock items table has unit. Let's fetch unit properly.
            item['uom'] = ""
            
        # Let's attach proper UOM from stock_items table if we have it
        if items:
            item_names = [i['item_name'] for i in items]
            placeholders = ",".join(["?"] * len(item_names))
            cursor.execute(f"SELECT name, unit FROM stock_items WHERE name IN ({placeholders})", item_names)
            uom_map = {row['name'].lower(): row['unit'] for row in cursor.fetchall()}
            for item in items:
                item['uom'] = uom_map.get(item['item_name'].lower(), "")

        return {
            "items": items,
            "total_count": total_items,
            "page": page,
            "limit": limit
        }

def get_supplier_purchase_analysis(start_date: str, end_date: str, page: int = 1, limit: int = 50, search: str = "", sort_by: str = "value_desc", cost_centre: str = None) -> dict:
    offset = (page - 1) * limit
    
    where_clause = """
        WHERE CAST(rv.date AS INTEGER) >= CAST(? AS INTEGER)
          AND CAST(rv.date AS INTEGER) <= CAST(? AS INTEGER)
    """
    params = [start_date, end_date]
    
    if cost_centre:
        if cost_centre == "Unallocated":
            where_clause += """ 
                AND EXISTS (
                    SELECT 1 FROM reporting_ledger_entries rle
                    JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
                    LEFT JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
                    WHERE rle.voucher_id = rv.id AND l.parent = 'Purchase Accounts' AND rca.ledger_entry_id IS NULL
                )
            """
        else:
            where_clause += """
                AND EXISTS (
                    SELECT 1 FROM reporting_ledger_entries rle
                    JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
                    JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
                    WHERE rle.voucher_id = rv.id AND l.parent = 'Purchase Accounts' AND rca.cost_centre_name = ?
                )
            """
            params.append(cost_centre)
    else:
        where_clause += """
            AND EXISTS (
                SELECT 1 FROM reporting_ledger_entries rle
                JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
                WHERE rle.voucher_id = rv.id AND l.parent = 'Purchase Accounts'
            )
        """
            
    if search:
        where_clause += " AND rv.party_ledger_name LIKE ?"
        params.append(f"%{search}%")
        
    # cost_centre is a normal FastAPI query param -- string-interpolating it
    # directly (as this used to do) is a SQL-injection-shaped pattern even
    # though it's not exploitable via the router today. Bound as ordinary
    # parameters instead, same as the rest of this query.
    #
    # No LIMIT/OFFSET here -- pending queue entries (fetched separately below)
    # have to be merged into these same per-supplier totals before sorting
    # and paginating, so that happens once, in Python, below.
    data_query = f"""
        WITH VoucherPurchases AS (
            SELECT
                rv.id as voucher_id,
                rv.party_ledger_name,
                COALESCE(SUM(
                    CASE
                        WHEN rca.id IS NOT NULL AND ? != 'Unallocated' AND (? = '' OR rca.cost_centre_name = ?) THEN rca.amount * -1
                        WHEN ? = 'Unallocated' AND rca.id IS NULL THEN rle.amount * -1
                        WHEN ? = '' AND rca.id IS NULL THEN rle.amount * -1
                        ELSE 0
                    END
                ), 0.0) as voucher_net_purchases
            FROM reporting_vouchers rv
            JOIN reporting_ledger_entries rle ON rv.id = rle.voucher_id
            JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
            LEFT JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
            {where_clause}
              AND l.parent = 'Purchase Accounts'
            GROUP BY rv.id, rv.party_ledger_name
        )
        SELECT
            COALESCE(party_ledger_name, 'Unknown') as supplier_name,
            COUNT(DISTINCT voucher_id) as vouchers_count,
            SUM(voucher_net_purchases) as net_purchases
        FROM VoucherPurchases
        WHERE voucher_net_purchases != 0
        GROUP BY party_ledger_name
    """

    with get_db() as conn:
        cursor = conn.cursor()

        cost_centre_val = cost_centre or ""
        case_params = [cost_centre_val, cost_centre_val, cost_centre_val, cost_centre_val, cost_centre_val]
        data_params = case_params + params

        cursor.execute(data_query, data_params)
        suppliers = {row['supplier_name']: dict(row) for row in cursor.fetchall()}

    pending_rows = _fetch_pending_purchase_bill_rows(start_date, end_date, cost_centre)
    for row in pending_rows:
        name = row['supplier_name']
        if name not in suppliers:
            suppliers[name] = {"supplier_name": name, "vouchers_count": 0, "net_purchases": 0.0}
        suppliers[name]["vouchers_count"] += 1
        suppliers[name]["net_purchases"] += row['net_purchases']

    items = [s for s in suppliers.values() if s['net_purchases'] != 0]
    if search:
        search_lower = search.lower()
        items = [s for s in items if search_lower in s['supplier_name'].lower()]

    if sort_by == "value_asc":
        items.sort(key=lambda s: s['net_purchases'])
    elif sort_by == "bills_desc":
        items.sort(key=lambda s: s['vouchers_count'], reverse=True)
    elif sort_by == "bills_asc":
        items.sort(key=lambda s: s['vouchers_count'])
    elif sort_by == "name_asc":
        items.sort(key=lambda s: s['supplier_name'])
    elif sort_by == "name_desc":
        items.sort(key=lambda s: s['supplier_name'], reverse=True)
    else:  # value_desc (default)
        items.sort(key=lambda s: s['net_purchases'], reverse=True)

    total_items = len(items)
    page_slice = items[offset:offset + limit]
    for item in page_slice:
        item['net_purchases'] = round(item['net_purchases'], 2)

    return {
        "suppliers": page_slice,
        "total_count": total_items,
        "page": page,
        "limit": limit
    }

def get_purchase_bills(start_date: str, end_date: str, cost_centre: str = None, supplier_name: str = None, page: int = 1, limit: int = 50, sort_by: str = "date_desc") -> dict:
    offset = (page - 1) * limit
    
    where_clause = """
        WHERE CAST(rv.date AS INTEGER) >= CAST(? AS INTEGER)
          AND CAST(rv.date AS INTEGER) <= CAST(? AS INTEGER)
          AND rv.voucher_type IN ('Purchase', 'Debit Note', 'Purchase Return')
    """
    params = [start_date, end_date]
    
    if supplier_name:
        where_clause += " AND rv.party_ledger_name = ?"
        params.append(supplier_name)
        
    if cost_centre:
        if cost_centre == "Unallocated":
            where_clause += """ 
                AND EXISTS (
                    SELECT 1 FROM reporting_ledger_entries rle
                    JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
                    LEFT JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
                    WHERE rle.voucher_id = rv.id AND l.parent = 'Purchase Accounts' AND rca.ledger_entry_id IS NULL
                )
            """
        else:
            where_clause += """
                AND EXISTS (
                    SELECT 1 FROM reporting_ledger_entries rle
                    JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
                    JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
                    WHERE rle.voucher_id = rv.id AND l.parent = 'Purchase Accounts' AND rca.cost_centre_name = ?
                )
            """
            params.append(cost_centre)
    else:
        where_clause += """
            AND EXISTS (
                SELECT 1 FROM reporting_ledger_entries rle
                JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
                WHERE rle.voucher_id = rv.id AND l.parent = 'Purchase Accounts'
            )
        """
        
    # cost_centre is a normal FastAPI query param -- string-interpolating it
    # directly (as this used to do) is a SQL-injection-shaped pattern even
    # though it's not exploitable via the router today. Bound as ordinary
    # parameters instead, same as the rest of this query.
    #
    # No LIMIT/OFFSET/ORDER BY here -- pending queue entries (fetched
    # separately below) have to be merged, sorted, and paginated together
    # with these confirmed rows, so that happens once, in Python, below
    # (same pattern as reporting_daybook_service.get_daybook). Without this,
    # this list wouldn't reconcile with the summary total above it, which
    # already includes pending purchases/returns.
    data_query = f"""
        WITH VoucherPurchases AS (
            SELECT
                rv.id as voucher_id,
                rv.date,
                rv.voucher_number,
                rv.party_ledger_name as supplier_name,
                COALESCE(SUM(
                    CASE
                        WHEN rca.id IS NOT NULL AND ? != 'Unallocated' AND (? = '' OR rca.cost_centre_name = ?) THEN rca.amount * -1
                        WHEN ? = 'Unallocated' AND rca.id IS NULL THEN rle.amount * -1
                        WHEN ? = '' AND rca.id IS NULL THEN rle.amount * -1
                        ELSE 0
                    END
                ), 0.0) as net_purchases
            FROM reporting_vouchers rv
            JOIN reporting_ledger_entries rle ON rv.id = rle.voucher_id
            JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
            LEFT JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
            {where_clause}
              AND l.parent = 'Purchase Accounts'
            GROUP BY rv.id
        )
        SELECT
            voucher_id,
            date,
            voucher_number,
            COALESCE(supplier_name, 'Unknown') as supplier_name,
            net_purchases,
            ? as store_name
        FROM VoucherPurchases
        WHERE net_purchases != 0
    """

    with get_db() as conn:
        cursor = conn.cursor()

        cost_centre_val = cost_centre or ""
        case_params = [cost_centre_val, cost_centre_val, cost_centre_val, cost_centre_val, cost_centre_val]
        data_params = case_params + params + [cost_centre or "Combined"]

        cursor.execute(data_query, data_params)
        items = [dict(row) for row in cursor.fetchall()]

        for item in items:
            item['net_purchases'] = round(item['net_purchases'], 2) if item['net_purchases'] else 0.0
            item['is_pending'] = False

    pending_items = _fetch_pending_purchase_bill_rows(start_date, end_date, cost_centre, supplier_name)
    combined = items + pending_items

    if sort_by == "date_asc":
        combined.sort(key=lambda b: b['date'])
    elif sort_by == "value_desc":
        combined.sort(key=lambda b: b['net_purchases'], reverse=True)
    elif sort_by == "value_asc":
        combined.sort(key=lambda b: b['net_purchases'])
    else:  # date_desc (default)
        combined.sort(key=lambda b: b['date'], reverse=True)

    total_items = len(combined)
    page_slice = combined[offset:offset + limit]

    return {
        "bills": page_slice,
        "total_count": total_items,
        "page": page,
        "limit": limit
    }

def get_bill_details(voucher_id: int) -> dict:
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Get basic voucher info
        cursor.execute("""
            SELECT id, date, voucher_number, voucher_type, party_ledger_name, reference, reference_date 
            FROM reporting_vouchers 
            WHERE id = ?
        """, [voucher_id])
        vch_row = cursor.fetchone()
        if not vch_row:
            return None
            
        voucher = dict(vch_row)
        
        # Get Cost Centres attached to this voucher (for purchases)
        cursor.execute("""
            SELECT DISTINCT rca.cost_centre_name
            FROM reporting_ledger_entries rle
            JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
            JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
            WHERE rle.voucher_id = ? AND l.parent = 'Purchase Accounts'
        """, [voucher_id])
        ccs = [r['cost_centre_name'] for r in cursor.fetchall()]
        
        # Check if unallocated
        cursor.execute("""
            SELECT 1
            FROM reporting_ledger_entries rle
            JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
            LEFT JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
            WHERE rle.voucher_id = ? AND l.parent = 'Purchase Accounts' AND rca.id IS NULL
        """, [voucher_id])
        if cursor.fetchone():
            ccs.append("Unallocated")
            
        voucher['store_name'] = ", ".join(ccs) if ccs else "Unknown"
        
        # Get items
        cursor.execute("""
            SELECT 
                rie.stock_item_name as item_name,
                rie.stock_item_name as stock_item_name,
                (rie.billed_qty * CASE WHEN rie.amount < 0 THEN 1 WHEN rie.amount > 0 THEN -1 ELSE 1 END) as quantity,
                rie.billed_qty,
                (rie.amount * -1) as value,
                rie.amount,
                rie.rate
            FROM reporting_inventory_entries rie
            WHERE rie.voucher_id = ?
        """, [voucher_id])
        
        items = [dict(row) for row in cursor.fetchall()]
        
        # Get Purchase Value (Net Purchase Accounts)
        cursor.execute("""
            SELECT COALESCE(SUM(amount * -1), 0.0) as purchase_value
            FROM reporting_ledger_entries rle
            JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
            WHERE rle.voucher_id = ? AND l.parent = 'Purchase Accounts'
        """, [voucher_id])
        purchase_value = cursor.fetchone()['purchase_value']
        
        # Get Taxes and Rounding
        cursor.execute("""
            SELECT 
                rle.ledger_name,
                (rle.amount * -1) as value
            FROM reporting_ledger_entries rle
            JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
            WHERE rle.voucher_id = ? AND (l.parent = 'Duties & Taxes' OR LOWER(rle.ledger_name) LIKE '%round%')
        """, [voucher_id])
        
        taxes_raw = cursor.fetchall()
        
        cgst = sgst = igst = other_taxes = rounding = 0.0
        
        for row in taxes_raw:
            lname = row['ledger_name'].lower()
            val = row['value']
            
            if 'round' in lname:
                rounding += val
            elif 'cgst' in lname:
                cgst += val
            elif 'sgst' in lname:
                sgst += val
            elif 'igst' in lname:
                igst += val
            else:
                other_taxes += val
                
        # Get Supplier Payable (amount posted to party_ledger_name)
        # Note: Credit to supplier is positive amount in our DB (since Purchase is negative amount for Debits, Credits are positive)
        # We want to display the payable as positive value for purchases. 
        # So we don't multiply by -1 for the supplier payable if it's a normal purchase (Credit to Supplier).
        # Wait, if amount is Positive for Credit, then `amount` IS the payable value.
        # But for Purchase Return (Debit to Supplier), amount is Negative for Debit.
        # So `amount` handles the sign perfectly if we want Payable to be positive for purchase and negative for return.
        
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0.0) as supplier_payable
            FROM reporting_ledger_entries
            WHERE voucher_id = ? AND ledger_name = ?
        """, [voucher_id, voucher['party_ledger_name']])
        
        supplier_row = cursor.fetchone()
        supplier_payable = supplier_row['supplier_payable'] if supplier_row else 0.0
        
        return {
            "voucher": voucher,
            "items": items,
            "purchase_value": round(purchase_value, 2) if purchase_value else 0.0,
            "taxes": {
                "cgst": round(cgst, 2),
                "sgst": round(sgst, 2),
                "igst": round(igst, 2),
                "other": round(other_taxes, 2)
            },
            "rounding": round(rounding, 2),
            "supplier_payable": round(supplier_payable, 2)
        }
