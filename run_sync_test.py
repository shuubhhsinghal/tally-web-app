import asyncio
from backend.services.tally_reporting_sync import async_sync_cost_centres, async_sync_vouchers
from backend.services.tally_sync_service import fetch_and_cache_masters
from backend.services.tally_closing_stock_sync import sync_tally_closing_balances

async def main():
    print("Syncing ledgers/masters...")
    fetch_and_cache_masters()
    
    print("Syncing Cost Centres...")
    await async_sync_cost_centres()
    
    print("Syncing closing balances...")
    sync_tally_closing_balances()
    
    print("Syncing Vouchers...")
    await async_sync_vouchers("20261001", "20261101")
    print("Sync Complete!")

if __name__ == "__main__":
    asyncio.run(main())
