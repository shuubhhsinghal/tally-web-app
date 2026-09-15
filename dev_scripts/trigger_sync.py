from backend.services.tally_sync_worker import fetch_and_cache_masters
import asyncio

fetch_and_cache_masters()
print("Master sync completed manually.")
