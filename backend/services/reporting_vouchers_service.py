from backend.database import get_db

def get_voucher_details(voucher_id: int) -> dict:
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Voucher Header
        cursor.execute("""
            SELECT id, date, voucher_number, voucher_type, party_ledger_name,
                   narration, tally_guid, reference, reference_date,
                   effective_date, cheque_number, cheque_date, bank_name
            FROM reporting_vouchers
            WHERE id = ?
        """, (voucher_id,))
        voucher = cursor.fetchone()
        
        if not voucher:
            return None
            
        voucher_dict = dict(voucher)
        
        # Ledger Entries
        cursor.execute("""
            SELECT id, ledger_name, amount, is_deemed_positive
            FROM reporting_ledger_entries
            WHERE voucher_id = ?
        """, (voucher_id,))
        ledgers = [dict(r) for r in cursor.fetchall()]
        
        # Cost Centre Allocations for Ledgers
        for ledg in ledgers:
            cursor.execute("""
                SELECT cost_centre_name, amount
                FROM reporting_cost_centre_allocations
                WHERE ledger_entry_id = ?
            """, (ledg['id'],))
            ledg['cost_centre_allocations'] = [dict(r) for r in cursor.fetchall()]
        
        # Inventory Entries
        cursor.execute("""
            SELECT stock_item_name, billed_qty, amount
            FROM reporting_inventory_entries
            WHERE voucher_id = ?
        """, (voucher_id,))
        inventory = [dict(r) for r in cursor.fetchall()]
        
        return {
            "voucher": voucher_dict,
            "ledgers": ledgers,
            "inventory": inventory
        }
