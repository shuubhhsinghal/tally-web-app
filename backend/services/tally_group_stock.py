from typing import Dict

def fetch_stock_balances_from_db(start_month: str, end_month: str) -> Dict[str, Dict[str, float]]:
    """
    Fetches the monthly stock snapshot from SQLite.
    start_month and end_month should be in 'YYYY-MM' format.

    Returns whatever stores it has data for -- a store whose opening or
    closing ledger is missing for either boundary month is simply omitted
    from the result, rather than this raising and taking the whole report
    down. calculate_pl_for_store already has its own per-store handling for
    exactly this (see its `missing_stores` list and the None-cogs/None-
    gross_profit fallback) -- this used to raise instead, which meant one
    store's stock sync lagging behind (e.g. Tally offline for a while)
    blanked out the ENTIRE P&L, including the other stores' fully-available
    Sales/Purchases/Expenses figures, and the frontend's own ready-built
    per-store "stock data missing" banner never got a chance to render."""
    from backend.database import get_db
    bals = {}

    required_ledgers = {"stock mahagun", "stock gulshan", "stock vvip"}

    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT ledger_name, opening_balance FROM reporting_monthly_stock WHERE year_month = ?", (start_month,))
        start_data = {r['ledger_name']: r['opening_balance'] for r in cursor.fetchall()}

        cursor.execute("SELECT ledger_name, closing_balance FROM reporting_monthly_stock WHERE year_month = ?", (end_month,))
        end_data = {r['ledger_name']: r['closing_balance'] for r in cursor.fetchall()}

        for ledger in required_ledgers:
            if ledger not in start_data or ledger not in end_data:
                continue

            store_key = ledger.replace("stock ", "").strip()
            # Normalize signs just like Tally (if they were stored natively from Tally, they might already be negative)
            # Actually, the sync script stores the raw values from Tally XML (which are negative).
            # We must apply the same normalization: presentation_stock = -signed_tally_amount
            bals[store_key] = {
                'opening': -start_data[ledger],
                'closing': -end_data[ledger]
            }

    return bals
