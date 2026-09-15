import asyncio
from backend.services.tally_reporting_sync import fetch_and_store_vouchers
async def run():
    print("Running reporting sync...")
    await asyncio.to_thread(fetch_and_store_vouchers, "20260401", "20270331")
    print("Done")
asyncio.run(run())
