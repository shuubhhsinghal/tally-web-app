from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from backend.database import get_db
from backend.utils.reporting_utils import check_data_completeness, get_previous_period

def calculate_sales(start_date: str, end_date: str, cost_centre: str = None) -> float:
    """
    Net Sales = SUM(alloc.amount * -1) where ledgers.parent = 'Sales Accounts'
    This elegantly handles Credit (Income) as positive and Debit (Returns) as negative.
    Excludes GST ledgers inherently.
    """
    query = """
        SELECT 
            COALESCE(SUM(
                (CASE WHEN rca.id IS NOT NULL THEN rca.amount ELSE rle.amount END) * 
                (CASE WHEN rle.is_deemed_positive = 1 THEN -1 ELSE 1 END)
            ), 0.0) as net_sales
        FROM reporting_vouchers rv
        JOIN reporting_ledger_entries rle ON rv.id = rle.voucher_id
        JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
        LEFT JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
        WHERE l.parent = 'Sales Accounts'
          AND CAST(rv.date AS INTEGER) >= CAST(? AS INTEGER)
          AND CAST(rv.date AS INTEGER) <= CAST(? AS INTEGER)
    """
    params = [start_date, end_date]
    
    if cost_centre:
        if cost_centre == "Unallocated":
            query += " AND rca.id IS NULL"
        else:
            query += " AND rca.cost_centre_name = ?"
            params.append(cost_centre)
            
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchone()['net_sales']

def get_store_comparisons(start_date: str, end_date: str) -> list[dict]:
    query = """
        SELECT 
            COALESCE(rca.cost_centre_name, 'Unallocated') as store_name,
            COALESCE(SUM(
                (CASE WHEN rca.id IS NOT NULL THEN rca.amount ELSE rle.amount END) * 
                (CASE WHEN rle.is_deemed_positive = 1 THEN -1 ELSE 1 END)
            ), 0.0) as net_sales
        FROM reporting_vouchers rv
        JOIN reporting_ledger_entries rle ON rv.id = rle.voucher_id
        JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
        LEFT JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
        WHERE l.parent = 'Sales Accounts'
          AND CAST(rv.date AS INTEGER) >= CAST(? AS INTEGER)
          AND CAST(rv.date AS INTEGER) <= CAST(? AS INTEGER)
        GROUP BY store_name
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(query, [start_date, end_date])
        return [dict(row) for row in cursor.fetchall()]

def get_sales_trend(start_date: str, end_date: str, cost_centre: str = None) -> list[dict]:
    query = """
        SELECT 
            rv.date,
            COALESCE(rca.cost_centre_name, 'Unallocated') as store_name,
            COALESCE(SUM(
                (CASE WHEN rca.id IS NOT NULL THEN rca.amount ELSE rle.amount END) * 
                (CASE WHEN rle.is_deemed_positive = 1 THEN -1 ELSE 1 END)
            ), 0.0) as net_sales,
            COUNT(DISTINCT rv.id) as invoice_count
        FROM reporting_vouchers rv
        JOIN reporting_ledger_entries rle ON rv.id = rle.voucher_id
        JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
        LEFT JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
        WHERE l.parent = 'Sales Accounts'
          AND CAST(rv.date AS INTEGER) >= CAST(? AS INTEGER)
          AND CAST(rv.date AS INTEGER) <= CAST(? AS INTEGER)
    """
    params = [start_date, end_date]
    
    if cost_centre:
        if cost_centre == "Unallocated":
            query += " AND rca.ledger_entry_id IS NULL"
        else:
            query += " AND rca.cost_centre_name = ?"
            params.append(cost_centre)
            
    query += """
        GROUP BY rv.date, store_name
        ORDER BY rv.date ASC
    """
    
    trend_dict = {}
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        for row in cursor.fetchall():
            date = row['date']
            store = row['store_name']
            amt = row['net_sales']
            inv_count = row['invoice_count']
            
            if date not in trend_dict:
                trend_dict[date] = {"date": date, "Combined": 0.0, "Combined_invoices": 0}
                
            trend_dict[date][store] = trend_dict[date].get(store, 0.0) + amt
            trend_dict[date]["Combined"] += amt
            trend_dict[date]["Combined_invoices"] += inv_count
            
    return list(trend_dict.values())

def get_sales_bills(start_date: str, end_date: str, cost_centre: str = None, page: int = 1, limit: int = 50, sort_by: str = "date_desc") -> dict:
    offset = (page - 1) * limit
    
    base_query = """
        FROM reporting_vouchers rv
        WHERE CAST(rv.date AS INTEGER) >= CAST(? AS INTEGER)
          AND CAST(rv.date AS INTEGER) <= CAST(? AS INTEGER)
          AND rv.voucher_type IN ('Sales', 'Credit Note', 'Sales Return')
    """
    params = [start_date, end_date]
    
    if cost_centre:
        if cost_centre == "Unallocated":
            base_query += """ 
                AND EXISTS (
                    SELECT 1 FROM reporting_ledger_entries rle
                    JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
                    LEFT JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
                    WHERE rle.voucher_id = rv.id AND l.parent = 'Sales Accounts' AND rca.ledger_entry_id IS NULL
                )
            """
        else:
            base_query += """
                AND EXISTS (
                    SELECT 1 FROM reporting_ledger_entries rle
                    JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
                    JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
                    WHERE rle.voucher_id = rv.id AND l.parent = 'Sales Accounts' AND rca.cost_centre_name = ?
                )
            """
            params.append(cost_centre)
    else:
        base_query += """
            AND EXISTS (
                SELECT 1 FROM reporting_ledger_entries rle
                JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
                WHERE rle.voucher_id = rv.id AND l.parent = 'Sales Accounts'
            )
        """
        
    count_query = f"SELECT COUNT(DISTINCT rv.id) as total {base_query}"
    
    order_clause = "ORDER BY date DESC"
    if sort_by == "date_asc":
        order_clause = "ORDER BY date ASC"
    elif sort_by == "value_desc":
        order_clause = "ORDER BY net_sales DESC"
    elif sort_by == "value_asc":
        order_clause = "ORDER BY net_sales ASC"
        
    data_query = f"""
        WITH VoucherSales AS (
            SELECT 
                rv.id as voucher_id,
                rv.date,
                rv.voucher_number,
                rv.party_ledger_name as customer_name,
                COALESCE(SUM(
                    CASE 
                        WHEN rca.id IS NOT NULL AND '{cost_centre or ""}' != 'Unallocated' AND ('{cost_centre or ""}' = '' OR rca.cost_centre_name = '{cost_centre or ""}') THEN rca.amount
                        WHEN '{cost_centre or ""}' = 'Unallocated' AND rca.id IS NULL THEN rle.amount
                        WHEN '{cost_centre or ""}' = '' AND rca.id IS NULL THEN rle.amount
                        ELSE 0
                    END
                ), 0.0) as net_sales
            {base_query}
            JOIN reporting_ledger_entries rle ON rv.id = rle.voucher_id
            JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
            LEFT JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
            WHERE l.parent = 'Sales Accounts'
            GROUP BY rv.id
        )
        SELECT 
            voucher_id,
            date,
            voucher_number,
            COALESCE(customer_name, 'Unknown') as customer_name,
            net_sales,
            '{cost_centre or "Combined"}' as store_name
        FROM VoucherSales
        WHERE net_sales != 0
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
            item['net_sales'] = round(item['net_sales'], 2) if item['net_sales'] else 0.0
        
        return {
            "bills": items,
            "total_count": total_items,
            "page": page,
            "limit": limit
        }

def get_sales_bill_details(voucher_id: int) -> dict:
    with get_db() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, date, voucher_number, voucher_type, party_ledger_name 
            FROM reporting_vouchers 
            WHERE id = ?
        """, [voucher_id])
        vch_row = cursor.fetchone()
        if not vch_row:
            return None
            
        voucher = dict(vch_row)
        
        cursor.execute("""
            SELECT DISTINCT rca.cost_centre_name
            FROM reporting_ledger_entries rle
            JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
            JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
            WHERE rle.voucher_id = ? AND l.parent = 'Sales Accounts'
        """, [voucher_id])
        ccs = [r['cost_centre_name'] for r in cursor.fetchall()]
        
        cursor.execute("""
            SELECT 1
            FROM reporting_ledger_entries rle
            JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
            LEFT JOIN reporting_cost_centre_allocations rca ON rle.id = rca.ledger_entry_id
            WHERE rle.voucher_id = ? AND l.parent = 'Sales Accounts' AND rca.id IS NULL
        """, [voucher_id])
        if cursor.fetchone():
            ccs.append("Unallocated")
            
        voucher['store_name'] = ", ".join(ccs) if ccs else "Unknown"
        
        # Items
        cursor.execute("""
            SELECT 
                rie.stock_item_name as item_name,
                (rie.billed_qty * CASE WHEN rie.amount < 0 THEN 1 WHEN rie.amount > 0 THEN -1 ELSE 1 END) as quantity,
                (rie.amount * -1) as value
            FROM reporting_inventory_entries rie
            WHERE rie.voucher_id = ?
        """, [voucher_id])
        items = [dict(row) for row in cursor.fetchall()]
        
        # Wait, for Sales, amount < 0 (Debit) means Sales Return! (Because Sales is Credit / positive amount).
        # Let's fix the quantity sign for Sales.
        # If amount < 0 (Debit), it's a Return -> quantity should be negative.
        # If amount > 0 (Credit), it's a Sale -> quantity should be positive.
        # Let's redo items fetch for Sales.
        cursor.execute("""
            SELECT 
                rie.stock_item_name as item_name,
                (rie.billed_qty * CASE WHEN rie.amount > 0 THEN 1 WHEN rie.amount < 0 THEN -1 ELSE 1 END) as quantity,
                (rie.amount) as value
            FROM reporting_inventory_entries rie
            WHERE rie.voucher_id = ?
        """, [voucher_id])
        items = [dict(row) for row in cursor.fetchall()]
        
        # Net Sales Accounts Value
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0.0) as sales_value
            FROM reporting_ledger_entries rle
            JOIN ledgers l ON LOWER(rle.ledger_name) = LOWER(l.name)
            WHERE rle.voucher_id = ? AND l.parent = 'Sales Accounts'
        """, [voucher_id])
        sales_value = cursor.fetchone()['sales_value']
        
        # Taxes and Rounding
        cursor.execute("""
            SELECT 
                rle.ledger_name,
                (rle.amount) as value
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
                
        # Customer Receivable (amount posted to party_ledger_name)
        # Debit to customer = Positive amount (Normal Sale). 
        # Credit to customer = Negative amount (Return).
        # We want receivable to be positive. But wait, `amount` is Negative for Debits.
        # Yes, amount < 0 means Debit. So for a Normal Sale, the customer is Debited (amount < 0).
        # We want it to show as a positive receivable. So we multiply by -1.
        cursor.execute("""
            SELECT COALESCE(SUM(amount * -1), 0.0) as customer_receivable
            FROM reporting_ledger_entries
            WHERE voucher_id = ? AND ledger_name = ?
        """, [voucher_id, voucher['party_ledger_name']])
        
        customer_row = cursor.fetchone()
        customer_receivable = customer_row['customer_receivable'] if customer_row else 0.0
        
        return {
            "voucher": voucher,
            "items": items,
            "sales_value": round(sales_value, 2) if sales_value else 0.0,
            "taxes": {
                "cgst": round(cgst, 2),
                "sgst": round(sgst, 2),
                "igst": round(igst, 2),
                "other": round(other_taxes, 2)
            },
            "rounding": round(rounding, 2),
            "customer_receivable": round(customer_receivable, 2)
        }
