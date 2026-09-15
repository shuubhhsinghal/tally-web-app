import requests
from fastapi import APIRouter
from backend.services.tally_sync_worker import flush_offline_queue
from backend.services.tally_reporting_sync import async_sync_cost_centres, async_sync_vouchers
from backend.config import TALLY_URL

router = APIRouter()

@router.post("/sync-queue")
async def flush_queue():
    # Manually trigger the flush process
    processed, failed = flush_offline_queue()
    
    # Trigger Reporting Sync for the Current FY
    try:
        await async_sync_cost_centres()
        await async_sync_vouchers()
    except Exception as e:
        print(f"Warning: Reporting sync failed during flush: {e}")
        
    return {"status": "success", "message": f"Manual sync triggered. Processed {processed} items. {failed} failed."}

@router.get("/status")
def sync_status():
    # Short-timeout connectivity check against Tally.
    # Tally being offline is an expected operating state, not an API crash.
    try:
        response = requests.get(TALLY_URL, timeout=2)
        if response.status_code == 200:
            return {"online": True}
        return {"online": False}
    except Exception:
        return {"online": False}
