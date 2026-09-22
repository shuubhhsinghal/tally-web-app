from typing import Dict

def fetch_stock_balances_from_db(start_month: str, end_month: str) -> Dict[str, Dict[str, float]]:
    """
    Fetches the monthly stock snapshot from SQLite.
    start_month and end_month should be in 'YYYY-MM' format.
    Raises Exception if data is missing or incomplete for either boundary.
    """
    from backend.database import get_db
    bals = {}
    
    required_ledgers = {"stock mahagun", "stock gulshan", "stock vvip"}
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Check completeness for start_month
        cursor.execute("SELECT ledger_name, opening_balance FROM reporting_monthly_stock WHERE year_month = ?", (start_month,))
        start_rows = cursor.fetchall()
        start_data = {r['ledger_name']: r['opening_balance'] for r in start_rows}
        
        if not required_ledgers.issubset(set(start_data.keys())):
            missing = required_ledgers - set(start_data.keys())
            raise Exception(f"Stock data for the requested months is incomplete. Missing ledgers {missing} in {start_month}. Please run Reporting Sync for this period.")
            
        # Check completeness for end_month
        cursor.execute("SELECT ledger_name, closing_balance FROM reporting_monthly_stock WHERE year_month = ?", (end_month,))
        end_rows = cursor.fetchall()
        end_data = {r['ledger_name']: r['closing_balance'] for r in end_rows}
        
        if not required_ledgers.issubset(set(end_data.keys())):
            missing = required_ledgers - set(end_data.keys())
            raise Exception(f"Stock data for the requested months is incomplete. Missing ledgers {missing} in {end_month}. Please run Reporting Sync for this period.")
            
        for ledger in required_ledgers:
            store_key = ledger.replace("stock ", "").strip()
            # Normalize signs just like Tally (if they were stored natively from Tally, they might already be negative)
            # Actually, the sync script stores the raw values from Tally XML (which are negative).
            # We must apply the same normalization: presentation_stock = -signed_tally_amount
            op_val = start_data[ledger]
            cl_val = end_data[ledger]
            
            bals[store_key] = {
                'opening': -op_val,
                'closing': -cl_val
            }
            
    return bals
