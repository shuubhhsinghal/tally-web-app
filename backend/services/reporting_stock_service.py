from typing import Dict, Optional
from datetime import datetime, timedelta
from backend.database import get_db

# Store-to-Stock-Ledger Mapping
STORE_STOCK_MAPPING: Dict[str, str] = {
    "mahagun": "stock mahagun",
    "gulshan": "stock gulshan",
    "vvip": "stock vvip"
}

def get_stock_ledger_for_store(store_name: str) -> Optional[str]:
    """Returns the exact ledger name for a given store, or None if unmapped."""
    # Ensure lowercase comparison for safety
    return STORE_STOCK_MAPPING.get(store_name.lower())

def get_closing_stock(ledger_name: str, end_date_str: str) -> float:
    """
    Retrieves the closing stock value for a ledger exactly on or before the given date.
    Returns 0.0 if no value is found.
    Date format: YYYYMMDD
    """
    with get_db() as db:
        # Get the latest entry on or before the requested date.
        row = db.execute('''
            SELECT amount FROM reporting_ledger_closing_balances
            WHERE ledger_name = ? 
              AND date <= ? 
            ORDER BY date DESC LIMIT 1
        ''', (ledger_name, end_date_str)).fetchone()
        
        if row:
            return row[0]
            
    return None

def get_opening_stock(ledger_name: str, start_date_str: str) -> Optional[float]:
    """
    Retrieves the opening stock for a period.
    The rule is: latest closing entry dated < start_date.
    If period starts on 0401 (e.g. 20260401), strictly use the ledger's FY opening balance.
    Date format: YYYYMMDD
    """
    with get_db() as db:
        # 1. FY Opening Stock on Apr 1
        if start_date_str.endswith("0401"):
            master = db.execute('''
                SELECT opening_balance FROM ledgers
                WHERE name = ?
            ''', (ledger_name,)).fetchone()
            
            if master and master[0] is not None:
                return float(master[0])
            return None # Do NOT substitute 0 if missing

        # 2. Opening stock for later periods
        row = db.execute('''
            SELECT amount FROM reporting_ledger_closing_balances
            WHERE ledger_name = ? 
              AND date < ?
            ORDER BY date DESC LIMIT 1
        ''', (ledger_name, start_date_str)).fetchone()
        
        if row:
            return row[0]
            
    return None
