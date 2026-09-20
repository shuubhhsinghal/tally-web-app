from fastapi import APIRouter
from backend.services.tally_sync_worker import flush_offline_queue
from backend.services.tally_reporting_sync import async_sync_cost_centres, async_sync_vouchers
from backend.connector.manager import connector_manager

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
    # "Online" now means "a Tally connector is currently connected to us" --
    # a plain in-memory check, no network round-trip needed (and no round
    # trip through the connector to Tally itself either, since that would
    # reintroduce the same timeout/ambiguity questions this collapses away).
    return {"online": connector_manager.is_connected()}
