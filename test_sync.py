import asyncio
from backend.services.tally_reporting_sync import async_sync_vouchers
from backend.services.tally_reporting_stock_sync import async_sync_monthly_stock

async def main():
    # Only run the stock sync for test
    res = await async_sync_monthly_stock("20260401", "20260531", "http://100.125.198.3:9000")
    print("Failed months:", res)

asyncio.run(main())
