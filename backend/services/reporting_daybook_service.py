from backend.database import get_db
import sqlite3

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
            
        # Sorting
        order_clause = "ORDER BY v.date DESC, v.id DESC"
        if sort_by == "date_asc":
            order_clause = "ORDER BY v.date ASC, v.id ASC"
        elif sort_by == "amount_desc":
            order_clause = "ORDER BY amount DESC, v.date DESC, v.id DESC"
        elif sort_by == "amount_asc":
            order_clause = "ORDER BY amount ASC, v.date ASC, v.id ASC"
            
        # Count total
        count_query = f"SELECT COUNT(*) as c FROM ({base_query})"
        cursor.execute(count_query, params)
        total_rows = cursor.fetchone()['c']
        
        # Paginate
        final_query = f"{base_query} {order_clause} LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        cursor.execute(final_query, params)
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
                
            vouchers.append(v_dict)
            
        return {
            "vouchers": vouchers,
            "total": total_rows,
            "page": page,
            "limit": limit
        }
