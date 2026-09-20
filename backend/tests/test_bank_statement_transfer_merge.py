import json
import tempfile
import pandas as pd
import pytest
import requests
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from backend.main import app
from backend.database import get_db, queue_operation, update_queue_status, set_delivery_uncertain

client = TestClient(app)

BANK_A = "Bank A"
BANK_B = "Bank B"


@pytest.fixture(autouse=True)
def setup_db():
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        conn.execute("DELETE FROM bank_mappings")
        conn.execute("DELETE FROM ledgers WHERE name IN (?, ?, 'Bank Suspense Account')", (BANK_A, BANK_B))
        conn.execute("INSERT INTO ledgers (name, parent, cost_centre) VALUES (?, 'Bank Accounts', 0)", (BANK_A,))
        conn.execute("INSERT INTO ledgers (name, parent, cost_centre) VALUES (?, 'Bank Accounts', 0)", (BANK_B,))
        conn.execute("INSERT INTO ledgers (name, parent, cost_centre) VALUES ('Bank Suspense Account', 'Suspense Accounts', 0)")
        conn.commit()
    yield
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        conn.execute("DELETE FROM bank_mappings")
        conn.execute("DELETE FROM ledgers WHERE name IN (?, ?, 'Bank Suspense Account')", (BANK_A, BANK_B))
        conn.commit()


def _seed_pending_bank_a_leg(date="2026-01-15", amount=5000.0):
    payload = {
        "bank_ledger_name": BANK_A,
        "debit_ledger": BANK_A,
        "credit_ledger": "Bank Suspense Account",
        "amount": amount,
        "date": date.replace("-", ""),
        "narration": "MOBFT to: Bank B/123456",
    }
    return queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Bank Stmt: {date} - {amount} - {BANK_A}")


def _get_queue_row(qid):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM offline_queue WHERE id = ?", (qid,))
        row = cursor.fetchone()
        return dict(row) if row else None


def test_upload_flags_transfer_match_against_pending_other_bank_leg():
    # Day values kept > 12 (15th/16th) to sidestep a separate, pre-existing
    # pandas dayfirst=True day/month-swap quirk in the xlsx date parser
    # (e.g. "2026-01-02" parses as Feb 1) -- unrelated to this feature,
    # flagged separately rather than worked around here.
    qid = _seed_pending_bank_a_leg(date="2026-01-15", amount=5000.0)

    df = pd.DataFrame([
        {"Date": "2026-01-16", "Narration": "MOBFT from: Bank A/123456", "Withdrawal": 0, "Deposit": 5000.0},
    ])
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as f:
        df.to_excel(f.name, index=False)
        f.seek(0)
        resp = client.post(
            "/api/bank-statement/upload",
            data={"bank_ledger_name": BANK_B},
            files={"file": ("test.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        )

    assert resp.status_code == 200
    txns = resp.json()["transactions"]
    assert len(txns) == 1
    match = txns[0]["transfer_match"]
    assert match is not None
    assert match["source"] == "pending"
    assert match["queue_id"] == qid
    assert match["bank_ledger_name"] == BANK_A


def test_merge_transfer_cancels_original_and_posts_single_contra(monkeypatch):
    qid = _seed_pending_bank_a_leg(date="2026-01-01", amount=5000.0)
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ConnectionError()))

    resp = client.post("/api/bank-statement/merge-transfer", json={
        "queue_id": qid,
        "bank_ledger_name": BANK_B,
        "date": "20260102",
        "withdraw": 0.0,
        "deposit": 5000.0,
        "raw_narration": "MOBFT from: Bank A/123456",
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "merged"

    # Original Bank A leg is gone.
    assert _get_queue_row(qid) is None

    # A single new Contra voucher was queued in its place.
    new_row = _get_queue_row(data["queue_id"])
    assert new_row is not None
    assert 'VCHTYPE="Contra"' in new_row["xml_data"]
    # Money left Bank A (money arrived at Bank B, per the deposit) -> from=A, to=B
    assert f"<LEDGERNAME>{BANK_A}</LEDGERNAME>" in new_row["xml_data"]
    assert f"<LEDGERNAME>{BANK_B}</LEDGERNAME>" in new_row["xml_data"]
    assert "<AMOUNT>5000.0</AMOUNT>" in new_row["xml_data"]
    assert "<AMOUNT>-5000.0</AMOUNT>" in new_row["xml_data"]


def test_merge_transfer_rejects_if_target_already_synced():
    qid = _seed_pending_bank_a_leg()
    update_queue_status(qid, "SYNCED")

    resp = client.post("/api/bank-statement/merge-transfer", json={
        "queue_id": qid,
        "bank_ledger_name": BANK_B,
        "date": "20260102",
        "withdraw": 0.0,
        "deposit": 5000.0,
        "raw_narration": "x",
    })

    assert resp.status_code == 409
    # The synced row must be untouched.
    assert _get_queue_row(qid)["status"] == "SYNCED"


def test_merge_transfer_rejects_if_delivery_uncertain():
    qid = _seed_pending_bank_a_leg()
    set_delivery_uncertain(qid, True)

    resp = client.post("/api/bank-statement/merge-transfer", json={
        "queue_id": qid,
        "bank_ledger_name": BANK_B,
        "date": "20260102",
        "withdraw": 0.0,
        "deposit": 5000.0,
        "raw_narration": "x",
    })

    assert resp.status_code == 409
    assert _get_queue_row(qid) is not None


def test_merge_transfer_rejects_unknown_bank_ledger():
    qid = _seed_pending_bank_a_leg()

    resp = client.post("/api/bank-statement/merge-transfer", json={
        "queue_id": qid,
        "bank_ledger_name": "Not A Real Ledger",
        "date": "20260102",
        "withdraw": 0.0,
        "deposit": 5000.0,
        "raw_narration": "x",
    })

    assert resp.status_code == 400
    assert _get_queue_row(qid) is not None
