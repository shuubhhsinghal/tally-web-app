from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from backend.database import (
    any_users_exist, create_user, get_user_by_username, get_user_by_id, verify_password,
    create_session, delete_session, list_users, deactivate_user, get_active_stores,
    set_user_password, delete_other_sessions, delete_sessions_for_user
)

router = APIRouter()

# There's no separate "username" in the UI -- a person's name doubles as
# their login identifier (stored in the `username` column for uniqueness/
# lookup purposes only; that's an implementation detail, never shown).

def _public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "name": user["name"],
        "store_name": user["store_name"],
        "is_owner": bool(user["is_owner"]),
    }

class SetupRequest(BaseModel):
    name: str
    password: str

@router.post("/setup")
def setup_owner_account(payload: SetupRequest):
    """Creates the first (owner) account. Only works once -- refuses if any
    account already exists, so this can't be replayed to create a second
    owner or take over an already-set-up app."""
    if any_users_exist():
        raise HTTPException(status_code=400, detail="Setup already completed.")
    name = payload.name.strip()
    if not name or not payload.password:
        raise HTTPException(status_code=400, detail="Name and password are required.")
    if len(payload.password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters.")

    user = create_user(name, name, payload.password, None, True)
    token = create_session(user["id"])
    return {"token": token, "user": _public_user(user)}

class LoginRequest(BaseModel):
    name: str
    password: str

@router.post("/login")
def login(payload: LoginRequest):
    user = get_user_by_username(payload.name.strip())
    if not user or not user["active"] or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect name or password.")

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
    password: str
    store_name: Optional[str] = None
    is_owner: bool = False

@router.get("/users")
def get_users(request: Request):
    _require_owner(request)
    return [
        {"id": u["id"], "name": u["name"], "store_name": u["store_name"], "is_owner": bool(u["is_owner"]), "active": bool(u["active"])}
        for u in list_users()
    ]

@router.post("/users")
def add_staff_user(payload: CreateStaffRequest, request: Request):
    _require_owner(request)

    name = payload.name.strip()
    if not name or not payload.password:
        raise HTTPException(status_code=400, detail="Name and password are required.")
    if len(payload.password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters.")
    if get_user_by_username(name):
        raise HTTPException(status_code=400, detail="That name is already taken. Try adding a last name or a number.")

    if payload.is_owner:
        # A co-owner gets the same unrestricted access as the account that
        # created it -- every store, plus the ability to manage staff
        # (including other owners, just never their own account -- see
        # remove_staff_user below).
        user = create_user(name, name, payload.password, None, True)
        return _public_user(user)

    store_names = [s['store_name'] for s in get_active_stores()]
    if payload.store_name not in store_names:
        raise HTTPException(status_code=400, detail=f"'{payload.store_name}' is not a known store.")

    user = create_user(name, name, payload.password, payload.store_name, False)
    return _public_user(user)

@router.delete("/users/{user_id}")
def remove_staff_user(user_id: int, request: Request):
    current = _require_owner(request)
    if user_id == current["id"]:
        raise HTTPException(status_code=400, detail="You can't remove your own account.")
    deactivate_user(user_id)
    return {"message": "Removed"}

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

@router.put("/change-password")
def change_password(payload: ChangePasswordRequest, request: Request):
    """Self-service password change for the signed-in account -- owner or
    staff, either can use this on themselves."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not verify_password(payload.current_password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")
    if len(payload.new_password) < 4:
        raise HTTPException(status_code=400, detail="New password must be at least 4 characters.")

    set_user_password(user["id"], payload.new_password)

    auth = request.headers.get("Authorization", "")
    current_token = auth[7:] if auth.startswith("Bearer ") else None
    if current_token:
        delete_other_sessions(user["id"], current_token)
    return {"message": "Password changed"}

class ResetPasswordRequest(BaseModel):
    new_password: str

@router.post("/users/{user_id}/reset-password")
def reset_staff_password(user_id: int, payload: ResetPasswordRequest, request: Request):
    """Lets the owner set a new password for a staff (or co-owner) account
    that's lost theirs -- no separate "forgot password" flow exists, so this
    is the recovery path. Signs that account out everywhere since the owner,
    not the account holder, is the one who just set the new password."""
    _require_owner(request)
    target = get_user_by_id(user_id)
    if not target or not target["active"]:
        raise HTTPException(status_code=404, detail="Account not found.")
    if len(payload.new_password) < 4:
        raise HTTPException(status_code=400, detail="New password must be at least 4 characters.")

    set_user_password(user_id, payload.new_password)
    delete_sessions_for_user(user_id)
    return {"message": "Password reset"}

def _require_owner(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not user["is_owner"]:
        raise HTTPException(status_code=403, detail="Only the owner account can manage team members.")
    return user
