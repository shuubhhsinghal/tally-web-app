import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

os.environ["TESTING"] = "true"

from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db, init_db

client = TestClient(app)

SUCCESS_XML = """<ENVELOPE>
  <HEADER><STATUS>1</STATUS></HEADER>
  <BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></IMPORTRESULT></DATA></BODY>
</ENVELOPE>"""


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM users")
        conn.execute("INSERT OR IGNORE INTO ledgers (name, parent, cost_centre) VALUES ('Test Supplier', 'Sundry Creditors', 0)")
        conn.execute("INSERT OR IGNORE INTO ledgers (name, parent, cost_centre) VALUES ('Purchase', 'Purchase Accounts', 0)")
        conn.commit()
    yield


def _post_payload(**overrides):
    payload = {
        "supplier": "Test Supplier",
        "invoice_number": "INV-1",
        "amount": 500.0,
        "tally_date": "20261001",
        "cost_center": "Mahagun",
        "narration": "test",
    }
    payload.update(overrides)
    return payload


def _latest_queue_row(description_like="Purchase Invoice:%"):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM offline_queue WHERE description LIKE ? ORDER BY id DESC LIMIT 1",
            (description_like,)
        )
        return dict(cursor.fetchone())


@patch('backend.routers.purchase.tally_transport.post', new_callable=AsyncMock)
def test_post_purchase_credits_the_real_purchase_ledger_name(mock_post):
    # Regression test: this used to hardcode <LEDGERNAME>Purchases</LEDGERNAME>
    # (plural), which doesn't exist in this app's actual Tally company (whose
    # Purchase Accounts ledger is named "Purchase", singular -- matching
    # purchase_item.py's item-wise mode). Every Accounting-mode purchase was
    # silently rejected by Tally with "Ledger 'Purchases' does not exist!"
    # until this was fixed, with zero test coverage catching it.
    mock_resp = MagicMock()
    mock_resp.text = SUCCESS_XML
    mock_post.return_value = mock_resp

    res = client.post("/api/purchase/post", json=_post_payload())
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    row = _latest_queue_row()
    assert "<LEDGERNAME>Purchase</LEDGERNAME>" in row["xml_data"]
    assert "<LEDGERNAME>Purchases</LEDGERNAME>" not in row["xml_data"]
    assert row["status"] == "SYNCED"
