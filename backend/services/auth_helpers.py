from typing import Optional
from fastapi import HTTPException


def enforce_store_access(current_user: dict, store: str, action: str = "post to") -> None:
    """Raises 403 if a non-owner user's assigned store doesn't match the
    store on the request they're making. An owner (store_name is None on
    their own row) always passes."""
    if not current_user['is_owner'] and store != current_user['store_name']:
        raise HTTPException(status_code=403, detail=f"Your account can only {action} {current_user['store_name']}.")


def enforce_store_access_either(current_user: dict, store_a: str, store_b: str, action: str = "move stock for") -> None:
    """Same as enforce_store_access, but for operations that inherently
    span two stores (a stock transfer) -- passes if EITHER side is the
    caller's own store."""
    if not current_user['is_owner'] and current_user['store_name'] not in (store_a, store_b):
        raise HTTPException(status_code=403, detail=f"Your account can only {action} {current_user['store_name']}.")


def resolve_view_store_filter(current_user: dict, requested_store: Optional[str]) -> Optional[str]:
    """What store a read/list endpoint should actually filter by. A staff
    account always gets forced to their own store, regardless of what was
    requested (e.g. a tampered query param) -- an owner's request passes
    through unchanged, including None (no filter, see everything)."""
    if current_user['is_owner']:
        return requested_store
    return current_user['store_name']


def enforce_report_row_store_access(current_user: dict, comma_joined_store_names: str) -> None:
    """For a single voucher/bill detail lookup by id -- the reporting
    services return their store attribution as a comma-joined string (a
    voucher can span multiple cost centres), e.g. "Mahagun, Gulshan" or
    "Unallocated"/"Unknown". Raises 404 (not 403) so a staff account
    guessing/incrementing ids can't even confirm another store's voucher
    exists, matching the same IDOR protection as the Queue's single-item
    endpoints."""
    if current_user['is_owner']:
        return
    stores_on_row = [s.strip() for s in (comma_joined_store_names or "").split(",")]
    if current_user['store_name'] not in stores_on_row:
        raise HTTPException(status_code=404, detail="Not found")
