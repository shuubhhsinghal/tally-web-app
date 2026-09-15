import asyncio
from backend.services.tally_sync_worker import fetch_and_cache_masters
async def run():
    print("Running master sync...")
    await asyncio.to_thread(fetch_and_cache_masters)
    print("Done")
asyncio.run(run())
