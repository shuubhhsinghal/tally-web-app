import os
import json
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from backend.database import get_db
from backend.connector.manager import connector_manager
from backend.services.auth_helpers import resolve_view_store_filter

router = APIRouter()

# Best-effort structured view of an offline_queue row for the activity feed --
# each transaction type's payload shape was mapped out from the routers that
# actually post it (sales.py, purchase.py, purchase_item.py, payment.py,
# loans.py, bank_statement.py). Falls back to (None, None, None) for types
# not worth parsing here (transfers, stock transfers, repack, master
# creation) -- the frontend just shows the raw description for those,
# unchanged from before this feed existed.
def _parse_activity_row(operation_type: str, description: str, payload_str: str):
    """Returns (party, amount, sub_label, type_label), any of which may be None."""
    if operation_type != 'POST_VOUCHER' or not payload_str:
        return None, None, None, None
    try:
        payload = json.loads(payload_str)
    except (json.JSONDecodeError, TypeError):
        return None, None, None, None
    if not isinstance(payload, dict):
        return None, None, None, None

    def _num(key):
        try:
            return float(payload.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    if description.startswith("Sales:"):
        ledger = payload.get('ledger') or ''
        party = "Cash sale (walk-in)" if ledger.strip().lower() == 'cash' else ledger
        return party, _num('amount'), payload.get('cost_center'), 'Sale'

    if description.startswith("Purchase Invoice:"):
        return payload.get('supplier'), -_num('amount'), 'Credit', 'Purchase'

    if description.startswith("Purchase Item Invoice:"):
        items = payload.get('items') or []
        items_total = sum(float(i.get('amount') or 0) for i in items)
        gst_total = _num('cgst') + _num('sgst') + _num('igst') + _num('rounding_off')
        return payload.get('supplier'), -(items_total + gst_total), 'Credit', 'Purchase'

    if description.startswith("Purchase Return (Debit Note):"):
        adjustment = payload.get('adjustment') or {}
        items = adjustment.get('items') or []
        amount = sum(float(i.get('qty') or 0) * float(i.get('rate') or 0) for i in items)
        return payload.get('supplier'), amount, 'Credit', 'Purchase return'

    if description.startswith("Payment:"):
        return payload.get('debit_ledger'), -_num('amount'), payload.get('credit_ledger'), 'Payment'

    if description.startswith("Journal:"):
        return payload.get('credit_ledger'), _num('amount'), payload.get('debit_ledger'), 'Journal'

    if description.startswith("Loan Received:"):
        return payload.get('lender_name'), _num('principal_amount'), payload.get('received_into_ledger'), 'Loan received'

    if description.startswith("Bank Stmt:") and not description.startswith("Bank Stmt Transfer:"):
        bank_ledger = payload.get('bank_ledger_name')
        is_receipt = payload.get('debit_ledger') == bank_ledger
        amount = _num('amount')
        # The counterparty is whichever side of the entry isn't the bank
        # ledger -- credit_ledger for a receipt (money coming in from them),
        # debit_ledger for a payment (money going out to them). This payload
        # never actually has a top-level 'ledger' key, so reading that (as
        # this used to) always returned None.
        party = payload.get('credit_ledger') if is_receipt else payload.get('debit_ledger')
        return party, (amount if is_receipt else -amount), bank_ledger, ('Receipt' if is_receipt else 'Payment')

    return None, None, None, None

@router.get("/stats")
def get_dashboard_stats(request: Request):
    # A staff account only ever sees its own store's figures here -- the
    # owner (store_filter stays None) sees everything, same as before.
    store_filter = resolve_view_store_filter(request.state.user, None)

    queue_count = 0
    cache_count = 0
    failed_count = 0
    review_count = 0

    # 1. Check SQLite Queue
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            if store_filter:
                cursor.execute(f"SELECT COUNT(*) as count FROM offline_queue WHERE status = 'PENDING' AND {STORE_MATCH_SQL}", (store_filter, store_filter, store_filter, store_filter))
            else:
                cursor.execute("SELECT COUNT(*) as count FROM offline_queue WHERE status = 'PENDING'")
            row = cursor.fetchone()
            if row:
                queue_count = row['count']

            cursor.execute("SELECT COUNT(*) as count FROM stock_items")
            row = cursor.fetchone()
            if row:
                cache_count = row['count']

            # True total, regardless of the 10-item activity feed cap or is_hidden state.
            if store_filter:
                cursor.execute(f"SELECT COUNT(*) as count FROM offline_queue WHERE status = 'FAILED' AND {STORE_MATCH_SQL}", (store_filter, store_filter, store_filter, store_filter))
            else:
                cursor.execute("SELECT COUNT(*) as count FROM offline_queue WHERE status = 'FAILED'")
            row = cursor.fetchone()
            if row:
                failed_count = row['count']

            cursor.execute("SELECT COUNT(*) as count FROM purchase_drafts WHERE status != 'POSTED'")
            row = cursor.fetchone()
            if row:
                review_count = row['count']

            cursor.execute("SELECT COUNT(*) as count FROM bank_statement_drafts")
            row = cursor.fetchone()
            if row:
                review_count += row['count']
    except Exception as e:
        print(f"Error fetching stats: {e}")

    # 2. Today's sales -- Tally-confirmed (from the reporting sync) plus
    # every sale this app itself has posted, PENDING or already SYNCED, so
    # the figure doesn't silently drop whenever Tally is unreachable, and
    # doesn't temporarily vanish for the up-to-30-minutes between a sale
    # posting live and the separate reporting sync re-confirming it (see
    # calculate_sales' and get_queue_sales_trend's docstrings -- the two are
    # a matching pair: calculate_sales permanently excludes VCHTYPE=Sales
    # vouchers, counted here from the queue instead). Deliberately excludes
    # FAILED entries -- those need a fix/retry before they count as a real
    # sale, and get surfaced separately via failed_count instead.
    today_sales = 0.0
    today_sales_pending_count = 0
    try:
        from backend.services.reporting_sales_service import calculate_sales, get_unsynced_sales, get_queue_sales_trend
        today_str = datetime.now().strftime("%Y%m%d")
        confirmed = calculate_sales(today_str, today_str, store_filter)
        unsynced = get_unsynced_sales(today_str, today_str, store_filter)
        queue_amount = sum(row['Combined'] for row in get_queue_sales_trend(today_str, today_str, store_filter))
        today_sales = round(confirmed + queue_amount, 2)
        today_sales_pending_count = unsynced['pending_count']
    except Exception as e:
        print(f"Error computing today's sales: {e}")

    # 3. Check Tally Server Status -- "online" means Tally itself is
    # reachable right now (the connector's own periodic local check), not
    # just that the connector's WebSocket is alive. Still a plain in-memory
    # read, no network round-trip from this side.
    tally_online = connector_manager.is_tally_reachable()

    return {
        "queue_count": queue_count,
        "cache_count": cache_count,
        "failed_count": failed_count,
        "today_sales": today_sales,
        "today_sales_pending_count": today_sales_pending_count,
        "tally_online": tally_online,
        "review_count": review_count
    }

@router.get("/activity")
def get_recent_activity(
    request: Request,
    status: Optional[str] = Query(None, description="Filter by status, e.g. FAILED"),
    include_hidden: bool = Query(False, description="Include items hidden by 'Clear Finished'"),
    limit: int = Query(10, ge=1, le=500)
):
    store_filter = resolve_view_store_filter(request.state.user, None)
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            conditions = []
            params = []
            if not include_hidden:
                conditions.append("is_hidden = 0")
            if status:
                conditions.append("status = ?")
                params.append(status)
            if store_filter:
                conditions.append(STORE_MATCH_SQL)
                params += [store_filter, store_filter, store_filter, store_filter]
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            params.append(limit)
            cursor.execute(
                f"SELECT id, operation_type, status, description, created_at, is_hidden, payload "
                f"FROM offline_queue {where} ORDER BY id DESC LIMIT ?",
                params
            )
            rows = cursor.fetchall()
            results = []
            for row in rows:
                item = dict(row)
                payload_str = item.pop('payload', None)
                party, amount, sub_label, type_label = _parse_activity_row(item['operation_type'], item['description'] or '', payload_str)
                item['party'] = party
                item['amount'] = amount
                item['sub_label'] = sub_label
                item['type_label'] = type_label
                results.append(item)
            return results
    except Exception as e:
        print(f"Error fetching activity: {e}")
        return []

QUEUE_PAGE_CAP = 200
QUEUE_STATUSES = ("PENDING", "FAILED", "SYNCED")

# A transaction "belongs" to a store if its payload carries a matching cost_center
# (sales/purchase/payment/bank_statement) or from_store/to_store (stock transfer --
# shows under BOTH stores it touches), or if it's a repack voucher whose
# repack_operations row records that store. Fund transfers and master creates never
# carry a store field at all, so they never match this and fall into "Unallocated".
STORE_MATCH_SQL = """(
    json_extract(offline_queue.payload, '$.cost_center') = ?
    OR json_extract(offline_queue.payload, '$.from_store') = ?
    OR json_extract(offline_queue.payload, '$.to_store') = ?
    OR (offline_queue.operation_type = 'REPACK_VOUCHER' AND EXISTS (
        SELECT 1 FROM repack_operations ro
        WHERE ro.id = json_extract(offline_queue.payload, '$.repack_id') AND ro.store_name = ?
    ))
)"""

UNALLOCATED_MATCH_SQL = """(
    json_extract(offline_queue.payload, '$.cost_center') IS NULL
    AND json_extract(offline_queue.payload, '$.from_store') IS NULL
    AND json_extract(offline_queue.payload, '$.to_store') IS NULL
    AND NOT (offline_queue.operation_type = 'REPACK_VOUCHER' AND EXISTS (
        SELECT 1 FROM repack_operations ro
        WHERE ro.id = json_extract(offline_queue.payload, '$.repack_id') AND ro.store_name IS NOT NULL
    ))
)"""

def _require_item_store_access(item_id: int, current_user: dict):
    """Single-item version of the STORE_MATCH_SQL filter above -- the list
    endpoints already filter by store, but a staff account could still hit
    a specific item's URL directly by guessing/incrementing its id, so each
    single-item endpoint needs its own ownership check too."""
    if current_user['is_owner']:
        return
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT operation_type, payload FROM offline_queue WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Item not found")
        try:
            payload = json.loads(row['payload']) if row['payload'] else {}
        except (json.JSONDecodeError, TypeError):
            payload = {}
        store = current_user['store_name']
        if payload.get('cost_center') == store or payload.get('from_store') == store or payload.get('to_store') == store:
            return
        if row['operation_type'] == 'REPACK_VOUCHER' and payload.get('repack_id'):
            cursor.execute("SELECT store_name FROM repack_operations WHERE id = ?", (payload['repack_id'],))
            repack_row = cursor.fetchone()
            if repack_row and repack_row['store_name'] == store:
                return
    raise HTTPException(status_code=404, detail="Item not found")

# Almost every real voucher shares operation_type='POST_VOUCHER' (sales, purchase,
# payment, transfer, stock transfer, bank statement all use it), so transaction TYPE
# can only be told apart by the description prefix each router writes at queue time.
# These prefixes are verified mutually exclusive -- none is a string-prefix of another.
# Master-creation rows (Create Ledger/Item/UOM) are intentionally not offered here;
# they still appear under "All Types" but aren't a selectable transaction type.
TYPE_FILTERS = {
    "SALES": ("offline_queue.description LIKE ?", ["Sales:%"]),
    "PURCHASE": ("offline_queue.description LIKE ?", ["Purchase Invoice:%"]),
    "PURCHASE_ITEM": (
        "(offline_queue.description LIKE ? OR offline_queue.description LIKE ?)",
        ["Purchase Item Invoice:%", "Purchase Return (Adjustment):%"],
    ),
    "PAYMENT": ("offline_queue.description LIKE ?", ["Payment:%"]),
    "TRANSFER": ("offline_queue.description LIKE ?", ["Transfer:%"]),
    "STOCK_TRANSFER": ("offline_queue.description LIKE ?", ["Stock Transfer:%"]),
    "BANK_STATEMENT": ("offline_queue.description LIKE ?", ["Bank Stmt:%"]),
    "REPACK": ("offline_queue.operation_type = ?", ["REPACK_VOUCHER"]),
}

def _build_queue_where(status: str, store: Optional[str], type: Optional[str], current_user: dict):
    if status not in QUEUE_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {', '.join(QUEUE_STATUSES)}")
    if type is not None and type not in TYPE_FILTERS:
        raise HTTPException(status_code=400, detail=f"type must be one of {', '.join(TYPE_FILTERS.keys())}")

    # A staff account can't widen this past their own store by editing the
    # query param -- their store always wins, regardless of what was asked.
    store = resolve_view_store_filter(current_user, store)

    where = "offline_queue.status = ?"
    params = [status]
    if store == "Unallocated":
        where += f" AND {UNALLOCATED_MATCH_SQL}"
    elif store:
        where += f" AND {STORE_MATCH_SQL}"
        params += [store, store, store, store]
    if type:
        type_sql, type_params = TYPE_FILTERS[type]
        where += f" AND {type_sql}"
        params += type_params
    return where, params

@router.get("/queue")
def get_queue_page(
    request: Request,
    status: str = Query(..., description="One of PENDING, FAILED, SYNCED"),
    store: Optional[str] = Query(None, description="A store name, or 'Unallocated'; omit for all stores"),
    type: Optional[str] = Query(None, description=f"One of {', '.join(TYPE_FILTERS.keys())}; omit for all types"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
):
    where, params = _build_queue_where(status, store, type, request.state.user)

    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) as count FROM offline_queue WHERE {where}", params)
            actual_total = cursor.fetchone()['count']
            total = min(actual_total, QUEUE_PAGE_CAP)

            offset = (page - 1) * limit
            items = []
            if offset < QUEUE_PAGE_CAP:
                # Full ledger view -- unlike /activity, is_hidden is deliberately ignored here.
                fetch_limit = min(limit, QUEUE_PAGE_CAP - offset)
                cursor.execute(
                    f"SELECT offline_queue.id, offline_queue.operation_type, offline_queue.status, "
                    f"offline_queue.description, offline_queue.created_at, offline_queue.is_hidden, "
                    f"offline_queue.error_message, offline_queue.payload "
                    f"FROM offline_queue WHERE {where} ORDER BY offline_queue.id DESC LIMIT ? OFFSET ?",
                    params + [fetch_limit, offset]
                )
                rows = cursor.fetchall()
                items = []
                for row in rows:
                    row_item = dict(row)
                    payload_str = row_item.pop('payload', None)
                    party, amount, sub_label, type_label = _parse_activity_row(row_item['operation_type'], row_item['description'] or '', payload_str)
                    row_item['party'] = party
                    row_item['amount'] = amount
                    row_item['sub_label'] = sub_label
                    row_item['type_label'] = type_label
                    items.append(row_item)
            return {"items": items, "total": total, "page": page, "limit": limit}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error fetching queue page: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/queue/clear-failed")
def clear_failed_queue(
    request: Request,
    store: Optional[str] = Query(None, description="A store name, or 'Unallocated'; omit for all stores"),
    type: Optional[str] = Query(None, description=f"One of {', '.join(TYPE_FILTERS.keys())}; omit for all types"),
):
    """Permanently removes every FAILED item matching the given store/type
    filters, the same way the single-item Delete button does -- setting
    is_hidden alone (like the Dashboard's "Clear Finished") would have no
    visible effect here, since the Queue page is a full ledger view that
    deliberately ignores is_hidden. A row whose delivery to Tally is
    unconfirmed (delivery_uncertain) is left alone rather than deleted,
    since it may already exist in Tally and needs a human to verify first."""
    from backend.database import delete_master_queue, cancel_pending_queue_item
    where, params = _build_queue_where("FAILED", store, type, request.state.user)
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT id, operation_type FROM offline_queue WHERE {where}", params)
            rows = cursor.fetchall()

        cleared = 0
        skipped = 0
        for row in rows:
            if row['operation_type'] in ('CREATE_LEDGER', 'CREATE_ITEM', 'CREATE_UOM'):
                delete_master_queue(row['id'])
                cleared += 1
                continue
            success, _ = cancel_pending_queue_item(row['id'])
            if success:
                cleared += 1
            else:
                skipped += 1
        return {"message": f"Cleared {cleared} failed item(s)", "cleared": cleared, "skipped": skipped}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")

@router.post("/queue/retry-failed")
def retry_failed_queue(
    request: Request,
    store: Optional[str] = Query(None, description="A store name, or 'Unallocated'; omit for all stores"),
    type: Optional[str] = Query(None, description=f"One of {', '.join(TYPE_FILTERS.keys())}; omit for all types"),
):
    """Retries every FAILED item matching the given store/type filters, the
    same way the single-item retry button does -- except an item whose
    delivery to Tally is unconfirmed (delivery_uncertain) is left alone
    rather than blocking the whole batch, since retrying it risks a
    duplicate voucher and it needs a human to verify in Tally first."""
    where, params = _build_queue_where("FAILED", store, type, request.state.user)
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT id, payload FROM offline_queue WHERE {where}", params)
            rows = cursor.fetchall()

            retried = 0
            skipped_uncertain = 0
            for row in rows:
                payload_dict = json.loads(row['payload']) if row['payload'] else {}
                if payload_dict.get('delivery_uncertain') is True:
                    skipped_uncertain += 1
                    continue
                cursor.execute(
                    "UPDATE offline_queue SET status = 'PENDING', error_message = NULL, is_hidden = 0, updated_at = datetime('now', 'localtime') WHERE id = ?",
                    (row['id'],)
                )
                # No-op for non-master rows -- only CREATE_LEDGER/CREATE_ITEM/CREATE_UOM
                # rows have a matching pending_masters entry to reset.
                cursor.execute(
                    "UPDATE pending_masters SET status = 'PENDING', error_message = NULL, updated_at = datetime('now', 'localtime') WHERE queue_id = ?",
                    (row['id'],)
                )
                retried += 1
            conn.commit()
            return {"message": f"Retrying {retried} item(s)", "retried": retried, "skipped_uncertain": skipped_uncertain}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")

class QueueUpdatePayload(BaseModel):
    payload: str
    xml_data: str

@router.get("/activity/{item_id}")
def get_activity_detail(item_id: int, request: Request):
    _require_item_store_access(item_id, request.state.user)
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM offline_queue WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Item not found")
            return dict(row)
    except HTTPException:
        raise
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")

@router.put("/activity/{item_id}")
def update_activity(item_id: int, data: QueueUpdatePayload, request: Request):
    _require_item_store_access(item_id, request.state.user)
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT operation_type FROM offline_queue WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if row and row['operation_type'] in ('CREATE_LEDGER', 'CREATE_ITEM', 'CREATE_UOM'):
                raise HTTPException(status_code=400, detail="Cannot raw-edit master identities. Delete and recreate instead.")
                
            cursor.execute(
                "UPDATE offline_queue SET payload = ?, xml_data = ?, status = 'PENDING', error_message = NULL, updated_at = datetime('now', 'localtime') WHERE id = ?",
                (data.payload, data.xml_data, item_id)
            )
            conn.commit()
            return {"message": "Updated and set to PENDING"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")

@router.delete("/activity/{item_id}")
def delete_activity(item_id: int, request: Request):
    _require_item_store_access(item_id, request.state.user)
    try:
        from backend.database import delete_master_queue
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT operation_type, status, payload FROM offline_queue WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Item not found")

            # Masters keep the stricter original rule: a PENDING master reservation
            # is never deletable, since it may be blocking/unblocking other queued
            # items that depend on it.
            if row['operation_type'] in ('CREATE_LEDGER', 'CREATE_ITEM', 'CREATE_UOM'):
                if row['status'] == 'PENDING':
                    raise HTTPException(status_code=400, detail="Cannot delete a master record that is still pending sync to Tally.")
                delete_master_queue(item_id)
                return {"message": "Deleted master queue and reservation successfully"}

        # Ordinary (non-master) rows: shared guards (status, delivery_uncertain,
        # compare-and-delete race guard, purchase-rate cleanup) live in
        # database.py's cancel_pending_queue_item so bank-statement transfer
        # merging (backend/routers/bank_statement.py) can reuse the same logic.
        from backend.database import cancel_pending_queue_item
        success, error_message = cancel_pending_queue_item(item_id)
        if not success:
            status_code = 409 if error_message and ("unconfirmed" in error_message or "just changed" in error_message) else 400
            raise HTTPException(status_code=status_code, detail=error_message)
        return {"message": "Deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")

@router.post("/activity/clear")
def clear_finished_activity(request: Request):
    store_filter = resolve_view_store_filter(request.state.user, None)
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            if store_filter:
                cursor.execute(f"UPDATE offline_queue SET is_hidden = 1 WHERE status IN ('SYNCED', 'FAILED') AND {STORE_MATCH_SQL}", (store_filter, store_filter, store_filter, store_filter))
            else:
                cursor.execute("UPDATE offline_queue SET is_hidden = 1 WHERE status IN ('SYNCED', 'FAILED')")
            conn.commit()
            return {"message": "Finished activities cleared successfully"}
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")

@router.post("/activity/{item_id}/retry")
def retry_activity(item_id: int, request: Request):
    _require_item_store_access(item_id, request.state.user)
    try:
        from backend.database import retry_master
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT operation_type, payload FROM offline_queue WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Item not found")

            if row['operation_type'] in ('CREATE_LEDGER', 'CREATE_ITEM', 'CREATE_UOM'):
                retry_master(item_id)
                return {"message": "Retrying master..."}

            payload_dict = json.loads(row['payload']) if row['payload'] else {}
            if payload_dict.get('delivery_uncertain') is True:
                raise HTTPException(
                    status_code=409,
                    detail="Delivery to Tally is unconfirmed for this transaction. Verify manually in Tally whether it was already recorded before retrying -- retrying now risks creating a duplicate voucher."
                )

            cursor.execute(
                "UPDATE offline_queue SET status = 'PENDING', error_message = NULL, is_hidden = 0, updated_at = datetime('now', 'localtime') WHERE id = ?",
                (item_id,)
            )
            conn.commit()
            return {"message": "Retrying..."}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")

@router.post("/activity/{item_id}/confirm-not-delivered")
def confirm_not_delivered(item_id: int, request: Request):
    """Unlocks Delete/Retry/Edit again on an item whose delivery to Tally
    was left unconfirmed (delivery_uncertain) -- e.g. the connector dropped
    mid-request, or (historically) the bytes/JSON serialization bug from
    early connector testing. We can't check Tally ourselves; this exists so
    a human who HAS checked (and confirmed the voucher is NOT there) has a
    way out, instead of the item being permanently stuck with no recovery
    path. Does not retry or delete anything by itself -- just clears the
    flag so the normal actions become available again."""
    _require_item_store_access(item_id, request.state.user)
    from backend.database import set_delivery_uncertain
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM offline_queue WHERE id = ?", (item_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Item not found")
    set_delivery_uncertain(item_id, False)
    return {"message": "Unlocked -- delivery marked as not confirmed in Tally"}


class RebuildPayload(BaseModel):
    payload: dict

@router.post("/activity/{item_id}/rebuild")
def rebuild_activity_xml(item_id: int, data: RebuildPayload, request: Request):
    """
    Accepts an updated JSON payload dict, regenerates Tally XML from scratch,
    and updates both columns in the queue. Dispatches by payload shape: an
    'items' key means a purchase item invoice, a 'ledger'+'amount' shape means
    a sales entry.
    """
    _require_item_store_access(item_id, request.state.user)
    current_user = request.state.user
    if not current_user['is_owner']:
        new_store = data.payload.get('cost_center') or data.payload.get('store')
        if new_store and new_store != current_user['store_name']:
            raise HTTPException(status_code=403, detail=f"Your account can only edit entries for {current_user['store_name']}.")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT payload FROM offline_queue WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Item not found")
        stored_payload = json.loads(row['payload']) if row['payload'] else {}
        if stored_payload.get('delivery_uncertain') is True:
            raise HTTPException(
                status_code=409,
                detail="Delivery to Tally is unconfirmed for this transaction. Verify manually in Tally whether it was already recorded before editing -- editing and resending now risks creating a duplicate voucher."
            )

    p = data.payload
    if "items" in p:
        return _rebuild_purchase_item_voucher(item_id, p)
    elif "ledger" in p and "amount" in p:
        return _rebuild_sales_voucher(item_id, p)
    else:
        raise HTTPException(status_code=400, detail="Only purchase item vouchers and sales entries can be rebuilt.")

def _rebuild_purchase_item_voucher(item_id: int, p: dict):
    import datetime
    from xml.sax.saxutils import escape as _escape

    try:
        supplier = _escape(str(p.get("supplier", "")))
        inv_no = _escape(str(p.get("invoice_number", "")))
        tally_date = str(p.get("tally_date", ""))
        cc = _escape(str(p.get("cost_center", "")))
        cgst = float(p.get("cgst", 0))
        sgst = float(p.get("sgst", 0))
        igst = float(p.get("igst", 0))
        rounding_off = float(p.get("rounding_off", 0))

        inventory_xml = ""
        item_subtotal = 0.0

        for item in p.get("items", []):
            name = _escape(str(item.get("mapped_name") or item.get("name", "")))
            qty = float(item.get("qty", 0))
            unit = str(item.get("mapped_unit") or item.get("uom") or "PCS")
            if not unit or unit == "Not Applicable":
                unit = "PCS"
            unit = _escape(unit)
            rate = float(item.get("rate", 0))
            amount = float(item.get("amount", 0))
            discount = float(item.get("discount", 0))
            item_subtotal += amount

            inventory_xml += f"""
        <INVENTORYENTRIES.LIST>
            <STOCKITEMNAME>{name}</STOCKITEMNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <RATE>{rate}/{unit}</RATE>
            <DISCOUNT>{discount}</DISCOUNT>
            <AMOUNT>-{amount:.2f}</AMOUNT>
            <ACTUALQTY> {qty} {unit}</ACTUALQTY>
            <BILLEDQTY> {qty} {unit}</BILLEDQTY>
            <ACCOUNTINGALLOCATIONS.LIST>
                <LEDGERNAME>Purchase</LEDGERNAME>
                <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                <AMOUNT>-{amount:.2f}</AMOUNT>
                <CATEGORYALLOCATIONS.LIST>
                    <CATEGORY>Primary Cost Category</CATEGORY>
                    <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                    <COSTCENTREALLOCATIONS.LIST>
                        <NAME>{cc}</NAME>
                        <AMOUNT>-{amount:.2f}</AMOUNT>
                    </COSTCENTREALLOCATIONS.LIST>
                </CATEGORYALLOCATIONS.LIST>
            </ACCOUNTINGALLOCATIONS.LIST>
        </INVENTORYENTRIES.LIST>"""

        calculated_grand_total = item_subtotal + cgst + sgst + igst + rounding_off

        ledger_xml = f"""
        <LEDGERENTRIES.LIST>
            <LEDGERNAME>{supplier}</LEDGERNAME>
            <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
            <AMOUNT>{calculated_grand_total:.2f}</AMOUNT>
            <BILLALLOCATIONS.LIST>
                <NAME>{inv_no}</NAME>
                <BILLTYPE>New Ref</BILLTYPE>
                <AMOUNT>{calculated_grand_total:.2f}</AMOUNT>
            </BILLALLOCATIONS.LIST>
        </LEDGERENTRIES.LIST>"""

        if cgst > 0:
            ledger_xml += f"""
        <LEDGERENTRIES.LIST>
            <LEDGERNAME>Input CGST</LEDGERNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <AMOUNT>-{cgst:.2f}</AMOUNT>
        </LEDGERENTRIES.LIST>"""

        if sgst > 0:
            ledger_xml += f"""
        <LEDGERENTRIES.LIST>
            <LEDGERNAME>Input SGST</LEDGERNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <AMOUNT>-{sgst:.2f}</AMOUNT>
        </LEDGERENTRIES.LIST>"""

        if igst > 0:
            ledger_xml += f"""
        <LEDGERENTRIES.LIST>
            <LEDGERNAME>Input IGST</LEDGERNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <AMOUNT>-{igst:.2f}</AMOUNT>
        </LEDGERENTRIES.LIST>"""

        if abs(rounding_off) >= 0.01:
            is_debit = "Yes" if rounding_off > 0 else "No"
            amount_str = f"-{abs(rounding_off):.2f}" if rounding_off > 0 else f"{abs(rounding_off):.2f}"
            ledger_xml += f"""
        <LEDGERENTRIES.LIST>
            <LEDGERNAME>Rounding Off</LEDGERNAME>
            <ISDEEMEDPOSITIVE>{is_debit}</ISDEEMEDPOSITIVE>
            <AMOUNT>{amount_str}</AMOUNT>
        </LEDGERENTRIES.LIST>"""

        guid = f"PII-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}-EDIT"

        master_xml = """
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <LEDGER ACTION="Alter" NAME="Input CGST">
                        <NAME.LIST><NAME>Input CGST</NAME></NAME.LIST>
                        <PARENT>Duties &amp; Taxes</PARENT>
                        <TAXTYPE>GST</TAXTYPE>
                        <GSTDUTYHEAD>Central Tax</GSTDUTYHEAD>
                    </LEDGER>
                </TALLYMESSAGE>
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <LEDGER ACTION="Alter" NAME="Input SGST">
                        <NAME.LIST><NAME>Input SGST</NAME></NAME.LIST>
                        <PARENT>Duties &amp; Taxes</PARENT>
                        <TAXTYPE>GST</TAXTYPE>
                        <GSTDUTYHEAD>State Tax</GSTDUTYHEAD>
                    </LEDGER>
                </TALLYMESSAGE>
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <LEDGER ACTION="Alter" NAME="Input IGST">
                        <NAME.LIST><NAME>Input IGST</NAME></NAME.LIST>
                        <PARENT>Duties &amp; Taxes</PARENT>
                        <TAXTYPE>GST</TAXTYPE>
                        <GSTDUTYHEAD>Integrated Tax</GSTDUTYHEAD>
                    </LEDGER>
                </TALLYMESSAGE>
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <LEDGER ACTION="Alter" NAME="Rounding Off">
                        <NAME.LIST><NAME>Rounding Off</NAME></NAME.LIST>
                        <PARENT>Indirect Expenses</PARENT>
                        <ROUNDINGMETHOD>Normal Rounding</ROUNDINGMETHOD>
                        <ROUNDLIMIT>1</ROUNDLIMIT>
                    </LEDGER>
                </TALLYMESSAGE>"""

        xml = f"""<ENVELOPE>
    <HEADER>
        <TALLYREQUEST>Import Data</TALLYREQUEST>
    </HEADER>
    <BODY>
        <IMPORTDATA>
            <REQUESTDESC>
                <REPORTNAME>All Masters</REPORTNAME>
                <STATICVARIABLES>
                    <SVCURRENTCOMPANY>##SVCURRENTCOMPANY</SVCURRENTCOMPANY>
                </STATICVARIABLES>
            </REQUESTDESC>
            <REQUESTDATA>{master_xml}
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <VOUCHER VCHTYPE="Purchase" ACTION="Create">
                        <DATE>{tally_date}</DATE>
                        <GUID>{guid}</GUID>
                        <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
                        <REFERENCE>{inv_no}</REFERENCE>
                        <VOUCHERNUMBER>{inv_no}</VOUCHERNUMBER>
                        <PARTYLEDGERNAME>{supplier}</PARTYLEDGERNAME>
                        <PARTYNAME>{supplier}</PARTYNAME>
                        <PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>
                        <ISINVOICE>Yes</ISINVOICE>
                        {inventory_xml}
                        {ledger_xml}
                    </VOUCHER>
                </TALLYMESSAGE>
            </REQUESTDATA>
        </IMPORTDATA>
    </BODY>
</ENVELOPE>"""

        new_payload_str = json.dumps(p)

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE offline_queue SET payload = ?, xml_data = ?, status = 'PENDING', error_message = NULL, updated_at = datetime('now', 'localtime') WHERE id = ? AND status = 'PENDING'",
                (new_payload_str, xml, item_id)
            )
            conn.commit()
            if cursor.rowcount == 0:
                raise HTTPException(status_code=409, detail="Item is not in PENDING state or not found. Cannot edit synced transactions.")

        return {"message": "Rebuilt and saved successfully"}

    except HTTPException:
        raise
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")

def _rebuild_sales_voucher(item_id: int, p: dict):
    from xml.sax.saxutils import escape as _escape
    from backend.database import resolve_cost_center_for_ledger, get_store_mapping

    try:
        ledger = _escape(str(p.get("ledger", "")))
        amount = float(p.get("amount", 0))
        tally_date = str(p.get("tally_date", ""))
        narration = _escape(str(p.get("narration", "")))

        # Newer queued items carry an explicit store; older ones predate that
        # field, so fall back to the legacy ledger-name inference for those.
        store = p.get("store")
        if store:
            store_mapping = get_store_mapping(store)
            if not store_mapping:
                raise HTTPException(status_code=400, detail=f"Store '{store}' does not have a mapped Cost Centre. Please configure it.")
            cost_center = store_mapping['cost_center_name']
        else:
            cost_center = resolve_cost_center_for_ledger(p.get("ledger", ""))
        allocation = ""
        if cost_center:
            allocation = f"""
              <CATEGORYALLOCATIONS.LIST>
                <CATEGORY>Primary Cost Category</CATEGORY>
                <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
                <COSTCENTREALLOCATIONS.LIST>
                  <NAME>{_escape(cost_center)}</NAME>
                  <AMOUNT>{amount}</AMOUNT>
                </COSTCENTREALLOCATIONS.LIST>
              </CATEGORYALLOCATIONS.LIST>"""

        xml = f"""<ENVELOPE>
  <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
  <BODY><IMPORTDATA>
    <REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC>
    <REQUESTDATA><TALLYMESSAGE>
      <VOUCHER VCHTYPE="Sales" ACTION="Create">
        <DATE>{tally_date}</DATE>
        <NARRATION>{narration}</NARRATION>
        <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{ledger}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          <AMOUNT>-{amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>Sales</LEDGERNAME>
          <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          <AMOUNT>{amount}</AMOUNT>{allocation}
        </ALLLEDGERENTRIES.LIST>
      </VOUCHER>
    </TALLYMESSAGE></REQUESTDATA>
  </IMPORTDATA></BODY>
</ENVELOPE>"""

        new_payload = dict(p)
        if cost_center:
            new_payload['cost_center'] = cost_center
        else:
            new_payload.pop('cost_center', None)
        new_payload_str = json.dumps(new_payload)

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE offline_queue SET payload = ?, xml_data = ?, status = 'PENDING', error_message = NULL, updated_at = datetime('now', 'localtime') WHERE id = ? AND status = 'PENDING'",
                (new_payload_str, xml, item_id)
            )
            conn.commit()
            if cursor.rowcount == 0:
                raise HTTPException(status_code=409, detail="Item is not in PENDING state or not found. Cannot edit synced transactions.")

        return {"message": "Rebuilt and saved successfully"}

    except HTTPException:
        raise
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")
