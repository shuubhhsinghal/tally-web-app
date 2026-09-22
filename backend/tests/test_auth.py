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


def _setup_owner(name="Owner", password="ownerpass"):
    res = client.post("/api/auth/setup", json={"name": name, "password": password})
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
    res = client.post("/api/auth/setup", json={"name": "Second", "password": "whatever"})
    assert res.status_code == 400


def test_setup_rejects_short_password():
    res = client.post("/api/auth/setup", json={"name": "Owner", "password": "abc"})
    assert res.status_code == 400


# --- Login ---

def test_login_with_correct_credentials():
    _setup_owner("Owner", "ownerpass")
    res = client.post("/api/auth/login", json={"name": "Owner", "password": "ownerpass"})
    assert res.status_code == 200
    assert res.json()["token"]


def test_login_with_wrong_password_is_rejected():
    _setup_owner("Owner", "ownerpass")
    res = client.post("/api/auth/login", json={"name": "Owner", "password": "nope"})
    assert res.status_code == 401


def test_login_with_unknown_name_is_rejected():
    _setup_owner("Owner", "ownerpass")
    res = client.post("/api/auth/login", json={"name": "Nobody", "password": "whatever"})
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
    data = _setup_owner("Owner", "ownerpass")
    res = client.get("/api/auth/me", headers=_auth_headers(data["token"]))
    assert res.status_code == 200
    assert res.json()["name"] == "Owner"


def test_logout_invalidates_the_session():
    data = _setup_owner()
    token = data["token"]
    assert client.get("/api/auth/me", headers=_auth_headers(token)).status_code == 200

    res = client.post("/api/auth/logout", headers=_auth_headers(token))
    assert res.status_code == 200

    assert client.get("/api/auth/me", headers=_auth_headers(token)).status_code == 401


# --- Staff accounts ---

def _create_staff(owner_token, name="Staff One", password="staffpass", store="Mahagun"):
    res = client.post(
        "/api/auth/users",
        json={"name": name, "password": password, "store_name": store},
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
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")

    res = client.post("/api/auth/login", json={"name": "Staff One", "password": "staffpass"})
    assert res.status_code == 200
    assert res.json()["user"]["store_name"] == "Mahagun"


def test_staff_cannot_manage_users():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff_login = client.post("/api/auth/login", json={"name": "Staff One", "password": "staffpass"}).json()

    res = client.get("/api/auth/users", headers=_auth_headers(staff_login["token"]))
    assert res.status_code == 403


def test_create_staff_rejects_unknown_store():
    owner = _setup_owner()
    res = client.post(
        "/api/auth/users",
        json={"name": "Staff Two", "password": "staffpass", "store_name": "Nonexistent"},
        headers=_auth_headers(owner["token"]),
    )
    assert res.status_code == 400


def test_create_staff_rejects_duplicate_name():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    res = client.post(
        "/api/auth/users",
        json={"name": "Staff One", "password": "otherpass", "store_name": "Gulshan"},
        headers=_auth_headers(owner["token"]),
    )
    assert res.status_code == 400


def test_owner_can_remove_staff_and_their_session_dies():
    owner = _setup_owner()
    staff = _create_staff(owner["token"])
    staff_login = client.post("/api/auth/login", json={"name": "Staff One", "password": "staffpass"}).json()
    assert client.get("/api/auth/me", headers=_auth_headers(staff_login["token"])).status_code == 200

    res = client.delete(f"/api/auth/users/{staff['id']}", headers=_auth_headers(owner["token"]))
    assert res.status_code == 200

    assert client.get("/api/auth/me", headers=_auth_headers(staff_login["token"])).status_code == 401


def test_removed_staff_drops_out_of_the_team_list():
    owner = _setup_owner()
    staff = _create_staff(owner["token"])
    client.delete(f"/api/auth/users/{staff['id']}", headers=_auth_headers(owner["token"]))

    res = client.get("/api/auth/users", headers=_auth_headers(owner["token"]))
    ids = [u["id"] for u in res.json()]
    assert staff["id"] not in ids


def test_removed_users_name_can_be_reused():
    owner = _setup_owner()
    staff = _create_staff(owner["token"], "Kushal", "staffpass", "Mahagun")
    client.delete(f"/api/auth/users/{staff['id']}", headers=_auth_headers(owner["token"]))

    res = client.post(
        "/api/auth/users",
        json={"name": "Kushal", "password": "newpass", "store_name": "Gulshan"},
        headers=_auth_headers(owner["token"]),
    )
    assert res.status_code == 200, res.text
    assert res.json()["store_name"] == "Gulshan"

    # The new account logs in fine; the old session/credentials are dead.
    assert client.post("/api/auth/login", json={"name": "Kushal", "password": "newpass"}).status_code == 200
    assert client.post("/api/auth/login", json={"name": "Kushal", "password": "staffpass"}).status_code == 401


def test_owner_cannot_remove_own_account():
    owner = _setup_owner()
    res = client.delete(f"/api/auth/users/{owner['user']['id']}", headers=_auth_headers(owner["token"]))
    assert res.status_code == 400


# --- Co-owners (full access) ---

def test_owner_can_create_a_co_owner():
    owner = _setup_owner()
    res = client.post(
        "/api/auth/users",
        json={"name": "Second Owner", "password": "secondpass", "is_owner": True},
        headers=_auth_headers(owner["token"]),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["is_owner"] is True
    assert data["store_name"] is None


def test_co_owner_can_manage_staff():
    owner = _setup_owner()
    client.post(
        "/api/auth/users",
        json={"name": "Second Owner", "password": "secondpass", "is_owner": True},
        headers=_auth_headers(owner["token"]),
    )
    second_owner_login = client.post("/api/auth/login", json={"name": "Second Owner", "password": "secondpass"}).json()

    res = client.post(
        "/api/auth/users",
        json={"name": "Staff One", "password": "staffpass", "store_name": "Mahagun"},
        headers=_auth_headers(second_owner_login["token"]),
    )
    assert res.status_code == 200


def test_co_owner_can_post_sales_for_any_store():
    owner = _setup_owner()
    client.post(
        "/api/auth/users",
        json={"name": "Second Owner", "password": "secondpass", "is_owner": True},
        headers=_auth_headers(owner["token"]),
    )
    second_owner_login = client.post("/api/auth/login", json={"name": "Second Owner", "password": "secondpass"}).json()

    res = client.post(
        "/api/sales/post",
        json={"ledger": "Cash Gulshan", "amount": 100.0, "tally_date": "20260101", "narration": "test", "store": "Gulshan"},
        headers=_auth_headers(second_owner_login["token"]),
    )
    assert res.status_code == 200


def test_owner_can_remove_a_co_owner():
    owner = _setup_owner()
    created = client.post(
        "/api/auth/users",
        json={"name": "Second Owner", "password": "secondpass", "is_owner": True},
        headers=_auth_headers(owner["token"]),
    ).json()

    res = client.delete(f"/api/auth/users/{created['id']}", headers=_auth_headers(owner["token"]))
    assert res.status_code == 200

    assert client.post("/api/auth/login", json={"name": "Second Owner", "password": "secondpass"}).status_code == 401


# --- Store-scoped posting (Sales) ---

def test_staff_can_post_sales_for_their_own_store():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff_login = client.post("/api/auth/login", json={"name": "Staff One", "password": "staffpass"}).json()

    res = client.post(
        "/api/sales/post",
        json={"ledger": "Cash Mahagun", "amount": 100.0, "tally_date": "20260101", "narration": "test", "store": "Mahagun"},
        headers=_auth_headers(staff_login["token"]),
    )
    assert res.status_code == 200


def test_staff_cannot_post_sales_for_a_different_store():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff_login = client.post("/api/auth/login", json={"name": "Staff One", "password": "staffpass"}).json()

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
    owner = _setup_owner("Owner", "ownerpass")
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


# --- Change password (self-service) ---

def test_change_password_with_correct_current_password():
    owner = _setup_owner("Owner", "ownerpass")
    res = client.put(
        "/api/auth/change-password",
        json={"current_password": "ownerpass", "new_password": "newpass123"},
        headers=_auth_headers(owner["token"]),
    )
    assert res.status_code == 200

    assert client.post("/api/auth/login", json={"name": "Owner", "password": "newpass123"}).status_code == 200
    assert client.post("/api/auth/login", json={"name": "Owner", "password": "ownerpass"}).status_code == 401


def test_change_password_rejects_wrong_current_password():
    owner = _setup_owner("Owner", "ownerpass")
    res = client.put(
        "/api/auth/change-password",
        json={"current_password": "wrongpass", "new_password": "newpass123"},
        headers=_auth_headers(owner["token"]),
    )
    assert res.status_code == 401
    assert client.post("/api/auth/login", json={"name": "Owner", "password": "ownerpass"}).status_code == 200


def test_change_password_rejects_short_new_password():
    owner = _setup_owner("Owner", "ownerpass")
    res = client.put(
        "/api/auth/change-password",
        json={"current_password": "ownerpass", "new_password": "abc"},
        headers=_auth_headers(owner["token"]),
    )
    assert res.status_code == 400


def test_change_password_signs_out_other_sessions_but_keeps_this_one():
    owner = _setup_owner("Owner", "ownerpass")
    token_a = owner["token"]
    token_b = client.post("/api/auth/login", json={"name": "Owner", "password": "ownerpass"}).json()["token"]
    assert token_a != token_b

    res = client.put(
        "/api/auth/change-password",
        json={"current_password": "ownerpass", "new_password": "newpass123"},
        headers=_auth_headers(token_a),
    )
    assert res.status_code == 200

    assert client.get("/api/auth/me", headers=_auth_headers(token_a)).status_code == 200
    assert client.get("/api/auth/me", headers=_auth_headers(token_b)).status_code == 401


def test_staff_can_change_own_password():
    owner = _setup_owner()
    staff = _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff_login = client.post("/api/auth/login", json={"name": "Staff One", "password": "staffpass"}).json()

    res = client.put(
        "/api/auth/change-password",
        json={"current_password": "staffpass", "new_password": "newstaffpass"},
        headers=_auth_headers(staff_login["token"]),
    )
    assert res.status_code == 200
    assert client.post("/api/auth/login", json={"name": "Staff One", "password": "newstaffpass"}).status_code == 200


# --- Reset password (owner resetting a staff/co-owner account) ---

def test_owner_can_reset_staff_password():
    owner = _setup_owner()
    staff = _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")

    res = client.post(
        f"/api/auth/users/{staff['id']}/reset-password",
        json={"new_password": "resetpass123"},
        headers=_auth_headers(owner["token"]),
    )
    assert res.status_code == 200
    assert client.post("/api/auth/login", json={"name": "Staff One", "password": "resetpass123"}).status_code == 200
    assert client.post("/api/auth/login", json={"name": "Staff One", "password": "staffpass"}).status_code == 401


def test_reset_password_signs_the_account_out_everywhere():
    owner = _setup_owner()
    _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    staff_login = client.post("/api/auth/login", json={"name": "Staff One", "password": "staffpass"}).json()
    staff_id = staff_login["user"]["id"]

    assert client.get("/api/auth/me", headers=_auth_headers(staff_login["token"])).status_code == 200

    res = client.post(
        f"/api/auth/users/{staff_id}/reset-password",
        json={"new_password": "resetpass123"},
        headers=_auth_headers(owner["token"]),
    )
    assert res.status_code == 200
    assert client.get("/api/auth/me", headers=_auth_headers(staff_login["token"])).status_code == 401


def test_staff_cannot_reset_others_passwords():
    owner = _setup_owner()
    staff_a = _create_staff(owner["token"], "Staff One", "staffpass", "Mahagun")
    _create_staff(owner["token"], "Staff Two", "staffpass2", "Gulshan")
    staff_a_login = client.post("/api/auth/login", json={"name": "Staff One", "password": "staffpass"}).json()

    res = client.post(
        f"/api/auth/users/{staff_a['id']}/reset-password",
        json={"new_password": "hijacked123"},
        headers=_auth_headers(staff_a_login["token"]),
    )
    assert res.status_code == 403


def test_reset_password_rejects_unknown_user():
    owner = _setup_owner()
    res = client.post(
        "/api/auth/users/999999/reset-password",
        json={"new_password": "resetpass123"},
        headers=_auth_headers(owner["token"]),
    )
    assert res.status_code == 404
