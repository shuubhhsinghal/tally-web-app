from fastapi import APIRouter
from backend.services.tally_sync_worker import flush_offline_queue
from backend.services.tally_reporting_sync import async_sync_cost_centres, async_sync_vouchers
from backend.connector.manager import connector_manager
from backend.database import get_db

router = APIRouter()

@router.post("/sync-queue")
async def flush_queue():
    # Manually trigger the flush process
    processed, failed = await flush_offline_queue()

    # Trigger Reporting Sync for the Current FY
    reporting_failed_months = []
    reporting_sync_error = None
    try:
        await async_sync_cost_centres()
        result = await async_sync_vouchers()
        reporting_failed_months = result.get("failed_months") or []
    except Exception as e:
        reporting_sync_error = str(e)
        print(f"Warning: Reporting sync failed during flush: {e}")

    message = f"Manual sync triggered. Processed {processed} items. {failed} failed."
    has_reporting_issue = bool(reporting_failed_months) or reporting_sync_error is not None
    if reporting_sync_error:
        message += f" Reporting sync error: {reporting_sync_error}."
    elif reporting_failed_months:
        message += f" WARNING: Stock sync failed for months: {', '.join(reporting_failed_months)}."

    return {
        "status": "success" if not has_reporting_issue else "partial_failure",
        "message": message,
        "failed_months": reporting_failed_months,
        "reporting_sync_error": reporting_sync_error,
    }

@router.get("/status")
def sync_status():
    # "Online" means Tally itself is reachable right now, per the connector's
    # own periodic local check -- not just "is the connector's WebSocket
    # alive" (which can stay true even if Tally is closed while the laptop
    # and connector script keep running). Still a plain in-memory read, no
    # network round-trip from this side.
    last_synced_at = None
    with get_db() as conn:
        row = conn.execute("SELECT MAX(synced_at) as ts FROM reporting_sync_history").fetchone()
        if row:
            last_synced_at = row["ts"]
    return {"online": connector_manager.is_tally_reachable(), "last_synced_at": last_synced_at}
