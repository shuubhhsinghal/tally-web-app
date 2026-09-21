from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from backend.database import (
    any_users_exist, create_user, get_user_by_username, verify_password,
    create_session, delete_session, list_users, deactivate_user, get_active_stores
)

router = APIRouter()

def _public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "name": user["name"],
        "username": user["username"],
        "store_name": user["store_name"],
        "is_owner": bool(user["is_owner"]),
    }

class SetupRequest(BaseModel):
    name: str
    username: str
    password: str

@router.post("/setup")
def setup_owner_account(payload: SetupRequest):
    """Creates the first (owner) account. Only works once -- refuses if any
    account already exists, so this can't be replayed to create a second
    owner or take over an already-set-up app."""
    if any_users_exist():
        raise HTTPException(status_code=400, detail="Setup already completed.")
    if not payload.username.strip() or not payload.password:
        raise HTTPException(status_code=400, detail="Username and password are required.")
    if len(payload.password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters.")

    user = create_user(payload.name.strip() or payload.username, payload.username.strip(), payload.password, None, True)
    token = create_session(user["id"])
    return {"token": token, "user": _public_user(user)}

class LoginRequest(BaseModel):
    username: str
    password: str

@router.post("/login")
def login(payload: LoginRequest):
    user = get_user_by_username(payload.username.strip())
    if not user or not user["active"] or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect username or password.")

    token = create_session(user["id"])
    return {"token": token, "user": _public_user(user)}

@router.post("/logout")
def logout(request: Request):
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        delete_session(auth[7:])
    return {"message": "Logged out"}

@router.get("/me")
def get_me(request: Request):
    # Populated by the auth middleware for any already-authenticated request.
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return _public_user(user)

@router.get("/needs-setup")
def needs_setup():
    return {"needs_setup": not any_users_exist()}

class CreateStaffRequest(BaseModel):
    name: str
    username: str
    password: str
    store_name: str

@router.get("/users")
def get_users(request: Request):
    current = _require_owner(request)
    return list_users()

@router.post("/users")
def add_staff_user(payload: CreateStaffRequest, request: Request):
    _require_owner(request)

    if not payload.username.strip() or not payload.password:
        raise HTTPException(status_code=400, detail="Username and password are required.")
    if len(payload.password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters.")
    if get_user_by_username(payload.username.strip()):
        raise HTTPException(status_code=400, detail="That username is already taken.")

    store_names = [s['store_name'] for s in get_active_stores()]
    if payload.store_name not in store_names:
        raise HTTPException(status_code=400, detail=f"'{payload.store_name}' is not a known store.")

    user = create_user(payload.name.strip() or payload.username, payload.username.strip(), payload.password, payload.store_name, False)
    return _public_user(user)

@router.delete("/users/{user_id}")
def remove_staff_user(user_id: int, request: Request):
    current = _require_owner(request)
    if user_id == current["id"]:
        raise HTTPException(status_code=400, detail="You can't remove your own account.")
    deactivate_user(user_id)
    return {"message": "Removed"}

def _require_owner(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not user["is_owner"]:
        raise HTTPException(status_code=403, detail="Only the owner account can manage staff.")
    return user
