from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.database import (
    create_stock_count, get_stock_count, list_stock_counts, get_stock_count_lines,
    upsert_stock_count_line, update_stock_count_line, delete_stock_count_line,
    complete_stock_count, get_purchase_rate, get_all_stock_items, get_active_stores
)
from backend.services.auth_helpers import enforce_store_access

router = APIRouter()


def _line_with_amount(line: dict) -> dict:
    return {**line, "amount": round(line["qty"] * line["rate"], 2)}


def _session_with_lines(session: dict) -> dict:
    lines = [_line_with_amount(l) for l in get_stock_count_lines(session["id"])]
    total_value = round(sum(l["amount"] for l in lines), 2)
    return {**session, "lines": lines, "total_value": total_value}


def _require_draft_session(count_id: int, current_user: dict) -> dict:
    session = get_stock_count(count_id)
    if not session:
        raise HTTPException(status_code=404, detail="Stock count not found.")
    enforce_store_access(current_user, session["store"], "count stock for")
    if session["status"] != "draft":
        raise HTTPException(status_code=400, detail="This stock count has already been completed.")
    return session


@router.get("/metadata")
def get_metadata():
    items = get_all_stock_items()
    stores = [s['store_name'] for s in get_active_stores()]
    return {"items": sorted(i['name'] for i in items), "stores": stores}


class StartRequest(BaseModel):
    store: str


@router.post("")
def start_count(payload: StartRequest, request: Request):
    current_user = request.state.user
    enforce_store_access(current_user, payload.store, "count stock for")
    count_id = create_stock_count(payload.store, created_by=current_user.get("name"))
    return _session_with_lines(get_stock_count(count_id))


@router.get("")
def list_counts(request: Request):
    current_user = request.state.user
    sessions = list_stock_counts()
    if not current_user['is_owner']:
        sessions = [s for s in sessions if s['store'] == current_user['store_name']]
    return sessions


@router.get("/{count_id}")
def get_count(count_id: int, request: Request):
    current_user = request.state.user
    session = get_stock_count(count_id)
    if not session:
        raise HTTPException(status_code=404, detail="Stock count not found.")
    enforce_store_access(current_user, session["store"], "view this stock count for")
    return _session_with_lines(session)


class AddLineRequest(BaseModel):
    item_name: str
    qty: float
    rate: Optional[float] = None
    uom: Optional[str] = None


@router.post("/{count_id}/lines")
def add_line(count_id: int, payload: AddLineRequest, request: Request):
    current_user = request.state.user
    _require_draft_session(count_id, current_user)

    rate = payload.rate
    if rate is None:
        rate = get_purchase_rate(payload.item_name)

    line = upsert_stock_count_line(count_id, payload.item_name, payload.qty, rate, payload.uom)
    return _line_with_amount(line)


class UpdateLineRequest(BaseModel):
    qty: Optional[float] = None
    rate: Optional[float] = None


@router.patch("/{count_id}/lines/{line_id}")
def update_line(count_id: int, line_id: int, payload: UpdateLineRequest, request: Request):
    current_user = request.state.user
    _require_draft_session(count_id, current_user)

    line = update_stock_count_line(count_id, line_id, payload.qty, payload.rate)
    if not line:
        raise HTTPException(status_code=404, detail="Line not found in this stock count.")
    return _line_with_amount(line)


@router.delete("/{count_id}/lines/{line_id}")
def remove_line(count_id: int, line_id: int, request: Request):
    current_user = request.state.user
    _require_draft_session(count_id, current_user)

    if not delete_stock_count_line(count_id, line_id):
        raise HTTPException(status_code=404, detail="Line not found in this stock count.")
    return {"status": "deleted"}


@router.post("/{count_id}/complete")
def complete_count(count_id: int, request: Request):
    current_user = request.state.user
    _require_draft_session(count_id, current_user)

    completed = complete_stock_count(count_id)
    return _session_with_lines(completed)
