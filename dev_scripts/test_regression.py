from datetime import datetime
from backend.services.tally_group_stock import fetch_stock_balances_from_tally, fetch_stock_balances_from_db

def run_regression():
    tally_april = fetch_stock_balances_from_tally(datetime(2026, 4, 1).date(), datetime(2026, 4, 30).date())
    db_april = fetch_stock_balances_from_db("2026-04", "2026-04")
    
    tally_may = fetch_stock_balances_from_tally(datetime(2026, 5, 1).date(), datetime(2026, 5, 31).date())
    db_may = fetch_stock_balances_from_db("2026-05", "2026-05")
    
    print("--- APRIL 2026 ---")
    print("Tally:", tally_april)
    print("DB:", db_april)
    print("Match:", tally_april == db_april)
    
    print("--- MAY 2026 ---")
    print("Tally:", tally_may)
    print("DB:", db_may)
    print("Match:", tally_may == db_may)

run_regression()
