import os
import pytest

os.environ["TESTING"] = "true"

from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db, init_db

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM users")
        conn.execute("DELETE FROM offline_queue")
        conn.commit()
    yield


def _setup_owner(username="owner", password="ownerpass"):
    res = client.post("/api/auth/setup", json={"name": "Owner", "username": username, "password": password})
    assert res.status_code == 200
    return res.json()


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# --- Setup ---

def test_needs_setup_true_when_no_users():
    assert client.get("/api/auth/needs-setup").json()["needs_setup"] is True


def test_setup_creates_owner_and_returns_session():
    data = _setup_owner()
    assert data["user"]["is_owner"] is True
    assert data["user"]["store_name"] is None
    assert data["token"]

    assert client.get("/api/auth/needs-setup").json()["needs_setup"] is False


def test_setup_refuses_once_a_user_exists():
    _setup_owner()
    res = client.post("/api/auth/setup", json={"name": "Second", "username": "second", "password": "whatever"})
    assert res.status_code == 400


def test_setup_rejects_short_password():
    res = client.post("/api/auth/setup", json={"name": "Owner", "username": "owner", "password": "abc"})
    assert res.status_code == 400


# --- Login ---

def test_login_with_correct_credentials():
    _setup_owner("owner", "ownerpass")
    res = client.post("/api/auth/login", json={"username": "owner", "password": "ownerpass"})
    assert res.status_code == 200
    assert res.json()["token"]


def test_login_with_wrong_password_is_rejected():
    _setup_owner("owner", "ownerpass")
    res = client.post("/api/auth/login", json={"username": "owner", "password": "nope"})
    assert res.status_code == 401


def test_login_with_unknown_username_is_rejected():
    _setup_owner("owner", "ownerpass")
    res = client.post("/api/auth/login", json={"username": "nobody", "password": "whatever"})
    assert res.status_code == 401


# --- Session enforcement ---

def test_protected_endpoint_rejects_invalid_token():
    res = client.get("/api/dashboard/stats", headers=_auth_headers("not-a-real-token"))
    assert res.status_code == 401


def test_protected_endpoint_accepts_valid_token():
    data = _setup_owner()
    res = client.get("/api/dashboard/stats", headers=_auth_headers(data["token"]))
    assert res.status_code == 200


def test_me_returns_current_user():
    data = _setup_owner("owner", "ownerpass")
    res = client.get("/api/auth/me", headers=_auth_headers(data["token"]))
    assert res.status_code == 200
    assert res.json()["username"] == "owner"


def test_logout_invalidates_the_session():
    data = _setup_owner()
    token = data["token"]
    assert client.get("/api/auth/me", headers=_auth_headers(token)).status_code == 200

    res = client.post("/api/auth/logout", headers=_auth_headers(token))
    assert res.status_code == 200

    assert client.get("/api/auth/me", headers=_auth_headers(token)).status_code == 401


# --- Staff accounts ---

def _create_staff(owner_token, username="staff1", password="staffpass", store="Mahagun"):
    res = client.post(
        "/api/auth/users",
        json={"name": "Staff One", "username": username, "password": password, "store_name": store},
        headers=_auth_headers(owner_token),
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_owner_can_create_staff_account():
    owner = _setup_owner()
    staff = _create_staff(owner["token"])
    assert staff["is_owner"] is False
    assert staff["store_name"] == "Mahagun"


def test_staff_login_works():
    owner = _setup_owner()
    _create_staff(owner["token"], "staff1", "staffpass", "Mahagun")

    res = client.post("/api/auth/login", json={"username": "staff1", "password": "staffpass"})
    assert res.status_code == 200
    assert res.json()["user"]["store_name"] == "Mahagun"


def test_staff_cannot_manage_users():
    owner = _setup_owner()
    _create_staff(owner["token"], "staff1", "staffpass", "Mahagun")
    staff_login = client.post("/api/auth/login", json={"username": "staff1", "password": "staffpass"}).json()

    res = client.get("/api/auth/users", headers=_auth_headers(staff_login["token"]))
    assert res.status_code == 403


def test_create_staff_rejects_unknown_store():
    owner = _setup_owner()
    res = client.post(
        "/api/auth/users",
        json={"name": "Staff", "username": "staff2", "password": "staffpass", "store_name": "Nonexistent"},
        headers=_auth_headers(owner["token"]),
    )
    assert res.status_code == 400


def test_create_staff_rejects_duplicate_username():
    owner = _setup_owner()
    _create_staff(owner["token"], "staff1", "staffpass", "Mahagun")
    res = client.post(
        "/api/auth/users",
        json={"name": "Someone Else", "username": "staff1", "password": "otherpass", "store_name": "Gulshan"},
        headers=_auth_headers(owner["token"]),
    )
    assert res.status_code == 400


def test_owner_can_remove_staff_and_their_session_dies():
    owner = _setup_owner()
    staff = _create_staff(owner["token"])
    staff_login = client.post("/api/auth/login", json={"username": "staff1", "password": "staffpass"}).json()
    assert client.get("/api/auth/me", headers=_auth_headers(staff_login["token"])).status_code == 200

    res = client.delete(f"/api/auth/users/{staff['id']}", headers=_auth_headers(owner["token"]))
    assert res.status_code == 200

    assert client.get("/api/auth/me", headers=_auth_headers(staff_login["token"])).status_code == 401


def test_owner_cannot_remove_own_account():
    owner = _setup_owner()
    res = client.delete(f"/api/auth/users/{owner['user']['id']}", headers=_auth_headers(owner["token"]))
    assert res.status_code == 400


# --- Store-scoped posting (Sales) ---

def test_staff_can_post_sales_for_their_own_store():
    owner = _setup_owner()
    _create_staff(owner["token"], "staff1", "staffpass", "Mahagun")
    staff_login = client.post("/api/auth/login", json={"username": "staff1", "password": "staffpass"}).json()

    res = client.post(
        "/api/sales/post",
        json={"ledger": "Cash Mahagun", "amount": 100.0, "tally_date": "20260101", "narration": "test", "store": "Mahagun"},
        headers=_auth_headers(staff_login["token"]),
    )
    assert res.status_code == 200


def test_staff_cannot_post_sales_for_a_different_store():
    owner = _setup_owner()
    _create_staff(owner["token"], "staff1", "staffpass", "Mahagun")
    staff_login = client.post("/api/auth/login", json={"username": "staff1", "password": "staffpass"}).json()

    res = client.post(
        "/api/sales/post",
        json={"ledger": "Cash Gulshan", "amount": 100.0, "tally_date": "20260101", "narration": "test", "store": "Gulshan"},
        headers=_auth_headers(staff_login["token"]),
    )
    assert res.status_code == 403


def test_owner_can_post_sales_for_any_store():
    owner = _setup_owner()
    res = client.post(
        "/api/sales/post",
        json={"ledger": "Cash Gulshan", "amount": 100.0, "tally_date": "20260101", "narration": "test", "store": "Gulshan"},
        headers=_auth_headers(owner["token"]),
    )
    assert res.status_code == 200


def test_sales_queue_payload_records_who_created_it():
    import json as _json
    owner = _setup_owner("owner", "ownerpass")
    client.post(
        "/api/sales/post",
        json={"ledger": "Cash Mahagun", "amount": 100.0, "tally_date": "20260101", "narration": "test", "store": "Mahagun"},
        headers=_auth_headers(owner["token"]),
    )
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT payload FROM offline_queue WHERE description LIKE 'Sales:%' ORDER BY id DESC LIMIT 1")
        payload = _json.loads(cursor.fetchone()["payload"])
    assert payload["created_by"] == "Owner"
