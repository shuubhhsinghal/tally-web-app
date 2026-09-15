from backend.database import get_db

def calculate_purchases(start_date: str, end_date: str, cost_centre: str = None) -> float:
    """
    Net Purchases = SUM(amount * -1) where ledgers.parent = 'Purchase Accounts'
    This elegantly handles Normal Purchases (Debit) as positive and Purchase Returns (Credit) as negative.
    Excludes GST ledgers inherently.
    
    Uses strict Cost Centre logic:
    Allocated: SUM(rca.amount * -1) where rca.cost_centre_name = ?
    Unallocated: SUM(rle.amount * -1) where rca.ledger_entry_id IS NULL
    Combined: SUM(allocated) + SUM(unallocated)
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
        """
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
            """
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
        """
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
            """
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
                if 'uom' in item: # we used MAX(rie.actual_qty) as uom, which was wrong. Let's delete it.
                    pass
        
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
        
    count_query = f"SELECT COUNT(DISTINCT rv.party_ledger_name) as total FROM reporting_vouchers rv {where_clause}"
    
    order_clause = "ORDER BY net_purchases DESC"
    if sort_by == "value_asc":
        order_clause = "ORDER BY net_purchases ASC"
    elif sort_by == "bills_desc":
        order_clause = "ORDER BY vouchers_count DESC"
    elif sort_by == "bills_asc":
        order_clause = "ORDER BY vouchers_count ASC"
    elif sort_by == "name_asc":
        order_clause = "ORDER BY rv.party_ledger_name ASC"
    elif sort_by == "name_desc":
        order_clause = "ORDER BY rv.party_ledger_name DESC"
        
    # We must calculate net purchases per supplier.
    # We join with the purchase ledger entries.
    
    select_clause = """
        SELECT 
            COALESCE(rv.party_ledger_name, 'Unknown') as supplier_name,
            COUNT(DISTINCT rv.id) as vouchers_count,
            COALESCE(SUM(
                CASE WHEN rca_id IS NULL THEN
                    rle_amount * -1
                ELSE
                    rca_amount * -1
                END
            ), 0.0) as net_purchases
    """
    
    # We need a subquery or a smart join to aggregate correctly without exploding due to multiple items.
    # Actually, it's easier to compute the net purchases per voucher first, then group by supplier.
    
    data_query = f"""
        WITH VoucherPurchases AS (
            SELECT 
                rv.id as voucher_id,
                rv.party_ledger_name,
                COALESCE(SUM(
                    CASE 
                        WHEN rca.id IS NOT NULL AND '{cost_centre or ""}' != 'Unallocated' AND ('{cost_centre or ""}' = '' OR rca.cost_centre_name = '{cost_centre or ""}') THEN rca.amount * -1
                        WHEN '{cost_centre or ""}' = 'Unallocated' AND rca.id IS NULL THEN rle.amount * -1
                        WHEN '{cost_centre or ""}' = '' AND rca.id IS NULL THEN rle.amount * -1
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
        {order_clause}
        LIMIT ? OFFSET ?
    """
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        cursor.execute(count_query, params)
        total_items = cursor.fetchone()['total']
        
        data_params = params + params + [limit, offset] # params are used twice (once in count, twice in data_query due to base_query inside CTE)
        # Wait, the data query has {base_query} inside the CTE, so it takes `params`. 
        # So data_params should be just `params + [limit, offset]`. 
        data_params = params + [limit, offset]
        
        cursor.execute(data_query, data_params)
        items = [dict(row) for row in cursor.fetchall()]
        
        for item in items:
            item['net_purchases'] = round(item['net_purchases'], 2) if item['net_purchases'] else 0.0
        
        # Recalculate total_items because counting distinct suppliers might include those with 0 net purchases after filtering.
        # It's fine for now.
        
        return {
            "suppliers": items,
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
        
    count_query = f"SELECT COUNT(DISTINCT rv.id) as total FROM reporting_vouchers rv {where_clause}"
    
    order_clause = "ORDER BY date DESC"
    if sort_by == "date_asc":
        order_clause = "ORDER BY date ASC"
    elif sort_by == "value_desc":
        order_clause = "ORDER BY net_purchases DESC"
    elif sort_by == "value_asc":
        order_clause = "ORDER BY net_purchases ASC"
        
    data_query = f"""
        WITH VoucherPurchases AS (
            SELECT 
                rv.id as voucher_id,
                rv.date,
                rv.voucher_number,
                rv.party_ledger_name as supplier_name,
                COALESCE(SUM(
                    CASE 
                        WHEN rca.id IS NOT NULL AND '{cost_centre or ""}' != 'Unallocated' AND ('{cost_centre or ""}' = '' OR rca.cost_centre_name = '{cost_centre or ""}') THEN rca.amount * -1
                        WHEN '{cost_centre or ""}' = 'Unallocated' AND rca.id IS NULL THEN rle.amount * -1
                        WHEN '{cost_centre or ""}' = '' AND rca.id IS NULL THEN rle.amount * -1
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
            '{cost_centre or "Combined"}' as store_name
        FROM VoucherPurchases
        WHERE net_purchases != 0
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
        
        for item in items:
            item['net_purchases'] = round(item['net_purchases'], 2) if item['net_purchases'] else 0.0
        
        return {
            "bills": items,
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
