import sys
from backend.services.tally_reporting_sync import fetch_and_store_vouchers
from backend.database import get_db

def count_vouchers():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM reporting_vouchers")
        return c.fetchone()[0]

print("Vouchers before:", count_vouchers())
try:
    print("Syncing 20260401 to 20270331...")
    fetch_and_store_vouchers('20260401', '20270331')
    print("Vouchers after:", count_vouchers())
except Exception as e:
    print("Error:", e)
