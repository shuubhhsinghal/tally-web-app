import pytest
import requests
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db
from backend.connector.manager import connector_manager

SUCCESS_XML = """<ENVELOPE>
  <HEADER><STATUS>1</STATUS></HEADER>
  <BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></IMPORTRESULT></DATA></BODY>
</ENVELOPE>"""

REJECTED_XML = """<ENVELOPE>
  <HEADER><STATUS>1</STATUS></HEADER>
  <BODY><DATA><IMPORTRESULT><CREATED>0</CREATED><ALTERED>0</ALTERED><ERRORS>1</ERRORS></IMPORTRESULT>
  <LINEERROR>Could not find Godown 'Bad Godown'</LINEERROR></DATA></BODY>
</ENVELOPE>"""

@pytest.fixture(autouse=True)
def setup_db():
    # Setup test DB tables
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Insert a store
        cursor.execute("INSERT OR IGNORE INTO stores (store_name, cost_center_name, godown_name) VALUES ('Test Store', 'Test Cost Center', 'Test Godown')")
        
        # Insert stock items
        cursor.execute("INSERT OR IGNORE INTO stock_items (name, unit) VALUES ('Bulk Chips', 'Kgs')")
        cursor.execute("INSERT OR IGNORE INTO stock_items (name, unit) VALUES ('300G Box', 'Pcs')")
        cursor.execute("INSERT OR IGNORE INTO stock_items (name, unit) VALUES ('Label', 'Pcs')")
        
        conn.commit()

client = TestClient(app)

def test_create_recipe():
    payload = {
        "new_product_name": "Test Beetroot Chips 300G",
        "output_unit": "PCS",
        "components": [
            {"item_name": "Bulk Chips", "unit": "KGS", "quantity": 0.3},
            {"item_name": "300G Box", "unit": "PCS", "quantity": 1.0},
            {"item_name": "Label", "unit": "PCS", "quantity": 1.0}
        ]
    }
    
    response = client.post("/api/repack/product", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    
    conv_id = data["conversion_id"]
    
    # Verify in DB
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM repack_recipe_components WHERE conversion_id = ?", (conv_id,))
        comps = cursor.fetchall()
        assert len(comps) == 3

def test_execute_repack(monkeypatch):
    # Component costs now come from get_purchase_rate (this app's own latest-
    # rate tracking), not a live Tally Godown valuation -- seed it directly.
    # Explicitly simulate Tally being unreachable (rather than relying on the
    # real dev Tally's ambient state, which is inconsistent) so the
    # stock-sufficiency check is deterministically skipped by the upfront
    # reachability ping in execute_repack.
    from backend.database import record_purchase_rate
    record_purchase_rate("bulk chips", 100.0, "Test Supplier", "2026-09-01")
    record_purchase_rate("300g box", 5.0, "Test Supplier", "2026-09-01")
    record_purchase_rate("label", 0.50, "Test Supplier", "2026-09-01")

    import requests
    def _simulate_offline(*a, **k):
        raise requests.exceptions.ConnectionError("simulated offline")
    monkeypatch.setattr(requests, "get", _simulate_offline)

    # First create recipe
    payload = {
        "new_product_name": "Test Assembly Item",
        "output_unit": "PCS",
        "components": [
            {"item_name": "Bulk Chips", "unit": "KGS", "quantity": 0.3},
            {"item_name": "300G Box", "unit": "PCS", "quantity": 1.0},
            {"item_name": "Label", "unit": "PCS", "quantity": 1.0}
        ]
    }
    create_res = client.post("/api/repack/product", json=payload).json()
    conv_id = create_res["conversion_id"]
    
    # Execute repack (produce 100 PCS)
    exec_payload = {
        "date": "2026-09-15",
        "store_name": "Test Store",
        "conversion_id": conv_id,
        "dest_qty": 100.0
    }
    
    response = client.post("/api/repack/execute", json=exec_payload)
    assert response.status_code == 200
    data = response.json()
    
    # Total cost should be:
    # 100 * 0.3 = 30 KGS * 100 = 3000
    # 100 * 1 = 100 Boxes * 5 = 500
    # 100 * 1 = 100 Labels * 0.5 = 50
    # Total = 3550
    
    assert "₹3550.00" in data["message"]
    
    repack_id = data["repack_id"]
    
    # Check operations snapshot
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM repack_operations WHERE id = ?", (repack_id,))
        op = cursor.fetchone()
        assert op["source_amount"] == 3550.0
        
        cursor.execute("SELECT * FROM repack_operation_components WHERE operation_id = ?", (repack_id,))
        comps = cursor.fetchall()
        assert len(comps) == 3


def test_execute_repack_records_rate_history(monkeypatch):
    from backend.database import record_purchase_rate
    record_purchase_rate("bulk chips", 100.0, "Test Supplier", "2026-09-01")
    record_purchase_rate("300g box", 5.0, "Test Supplier", "2026-09-01")

    import requests
    def _simulate_offline(*a, **k):
        raise requests.exceptions.ConnectionError("simulated offline")
    monkeypatch.setattr(requests, "get", _simulate_offline)

    payload = {
        "new_product_name": "Test Rate History Item",
        "output_unit": "PCS",
        "components": [
            {"item_name": "Bulk Chips", "unit": "KGS", "quantity": 0.3},
            {"item_name": "300G Box", "unit": "PCS", "quantity": 1.0},
        ]
    }
    conv_id = client.post("/api/repack/product", json=payload).json()["conversion_id"]

    exec_payload = {
        "date": "2026-09-15",
        "store_name": "Test Store",
        "conversion_id": conv_id,
        "dest_qty": 10.0
    }
    response = client.post("/api/repack/execute", json=exec_payload)
    assert response.status_code == 200

    # 10 * 0.3 * 100 + 10 * 1 * 5 = 300 + 50 = 350; rate = 350 / 10 = 35.0
    from backend.database import get_purchase_rate
    assert get_purchase_rate("Test Rate History Item") == 35.0

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM purchase_rate_pending_entries WHERE stock_item_name = 'test rate history item'")
        row = cursor.fetchone()
        assert row is not None
        assert row["rate"] == 35.0
        assert row["source_type"] == "repack"
        assert row["qty"] == 10.0
        assert row["supplier"] is None

    hist_res = client.get("/api/purchase-item/return-item-history", params={"item_name": "Test Rate History Item"})
    assert hist_res.status_code == 200
    repack_entries = [e for e in hist_res.json()["entries"] if e["origin"] == "repack"]
    assert len(repack_entries) == 1
    assert repack_entries[0]["rate"] == 35.0


def test_execute_repack_ignores_godown_valuation_rate_when_reachable(monkeypatch):
    # The repack's cost must be computed from get_purchase_rate -- Tally's
    # live Godown valuation (a weighted-average blending old and new stock)
    # is never consulted for costing, and isn't queried for anything else
    # here either (see the no-app-side-stock-check comment in repack.py).
    from backend.database import record_purchase_rate
    record_purchase_rate("bulk chips", 100.0, "Test Supplier", "2026-09-01")
    record_purchase_rate("300g box", 5.0, "Test Supplier", "2026-09-01")

    monkeypatch.setattr(connector_manager, "is_tally_reachable", lambda: True)

    payload = {
        "new_product_name": "Test Ignores Valuation Item",
        "output_unit": "PCS",
        "components": [
            {"item_name": "Bulk Chips", "unit": "KGS", "quantity": 0.3},
            {"item_name": "300G Box", "unit": "PCS", "quantity": 1.0},
        ]
    }
    conv_id = client.post("/api/repack/product", json=payload).json()["conversion_id"]

    exec_payload = {
        "date": "2026-09-15",
        "store_name": "Test Store",
        "conversion_id": conv_id,
        "dest_qty": 10.0
    }
    response = client.post("/api/repack/execute", json=exec_payload)
    assert response.status_code == 200

    # 10*0.3*100 + 10*1*5 = 300 + 50 = 350 -- from get_purchase_rate, not the
    # mocked Godown "rate": 999 a weighted-average valuation would have given.
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT source_amount FROM repack_operations WHERE dest_item_name = ?", ("Test Ignores Valuation Item",))
        row = cursor.fetchone()
        assert row["source_amount"] == 350.0


def test_execute_repack_does_not_check_stock_sufficiency(monkeypatch):
    """get_godown_stock's underlying Tally query doesn't actually filter by
    item (it returns identical numbers for any item requested), so it can
    never be trusted to block a repack -- the app no longer calls it at all
    here. Tally's own "Allow Negative Stock" setting is the real source of
    truth for whether an overdraw is accepted or rejected."""
    from backend.database import record_purchase_rate
    record_purchase_rate("bulk chips", 100.0, "Test Supplier", "2026-09-01")

    monkeypatch.setattr(connector_manager, "is_tally_reachable", lambda: True)

    payload = {
        "new_product_name": "Test No Stock Check Item",
        "output_unit": "PCS",
        "components": [
            {"item_name": "Bulk Chips", "unit": "KGS", "quantity": 0.3},
        ]
    }
    conv_id = client.post("/api/repack/product", json=payload).json()["conversion_id"]

    exec_payload = {
        "date": "2026-09-15",
        "store_name": "Test Store",
        "conversion_id": conv_id,
        "dest_qty": 100.0  # would need 30 KGS -- no longer checked against anything
    }
    response = client.post("/api/repack/execute", json=exec_payload)
    assert response.status_code == 200


def _setup_reachable_with_stock(monkeypatch, rate=100.0, stock_qty=1000.0):
    """Common setup for the three tests below: Tally reachable, so
    execute_repack reaches the immediate live-post attempt this test file
    is verifying. stock_qty is unused now (no app-side stock check calls
    get_godown_stock any more) but kept as a param so callers don't need
    updating."""
    from backend.database import record_purchase_rate
    record_purchase_rate("bulk chips", rate, "Test Supplier", "2026-09-01")

    monkeypatch.setattr(connector_manager, "is_tally_reachable", lambda: True)

    payload = {
        "new_product_name": "Test Live Post Item " + str(id(monkeypatch)),
        "output_unit": "PCS",
        "components": [
            {"item_name": "Bulk Chips", "unit": "KGS", "quantity": 0.3},
        ]
    }
    conv_id = client.post("/api/repack/product", json=payload).json()["conversion_id"]
    return conv_id


def test_execute_repack_attempts_immediate_live_post_and_reports_success(monkeypatch):
    # Previously execute_repack only ever queued the voucher and returned a
    # blanket "success" without ever trying to actually reach Tally within
    # the request -- delivery only happened up to 60s later on the
    # background worker's next tick, with no way for the caller to find out.
    # This verifies the fix: an immediate live-post attempt happens inline,
    # and a genuine Tally success is reflected in both the response and the
    # repack_operations row (not left PENDING for the background worker).
    conv_id = _setup_reachable_with_stock(monkeypatch)

    mock_resp = MagicMock()
    mock_resp.text = SUCCESS_XML
    monkeypatch.setattr("backend.routers.repack.tally_transport.post", AsyncMock(return_value=mock_resp))

    exec_payload = {"date": "2026-09-15", "store_name": "Test Store", "conversion_id": conv_id, "dest_qty": 10.0}
    response = client.post("/api/repack/execute", json=exec_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"

    with get_db() as conn:
        op = conn.execute("SELECT status FROM repack_operations WHERE id = ?", (data["repack_id"],)).fetchone()
        assert op["status"] == "COMPLETED"
        q = conn.execute(
            "SELECT status FROM offline_queue WHERE description LIKE 'Repack:%' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert q["status"] == "SYNCED"


def test_execute_repack_reports_failed_when_tally_rejects(monkeypatch):
    conv_id = _setup_reachable_with_stock(monkeypatch)

    mock_resp = MagicMock()
    mock_resp.text = REJECTED_XML
    monkeypatch.setattr("backend.routers.repack.tally_transport.post", AsyncMock(return_value=mock_resp))

    exec_payload = {"date": "2026-09-15", "store_name": "Test Store", "conversion_id": conv_id, "dest_qty": 10.0}
    response = client.post("/api/repack/execute", json=exec_payload)
    # Reported as a 200 with status "failed" (mirrors stock_transfer.py's
    # pattern) rather than an HTTPException, since the voucher is already
    # durably queued and its failure is visible/actionable from the queue.
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "failed"
    assert "Bad Godown" in data["message"]

    with get_db() as conn:
        op = conn.execute("SELECT status FROM repack_operations WHERE id = ?", (data["repack_id"],)).fetchone()
        assert op["status"] == "FAILED"
        q = conn.execute(
            "SELECT status FROM offline_queue WHERE description LIKE 'Repack:%' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert q["status"] == "FAILED"


def test_execute_repack_queues_when_tally_unreachable_at_post_time(monkeypatch):
    # Reachable at the upfront stock-check ping, but the connector drops
    # before the actual post -- must leave the row PENDING for the
    # background worker, not mark it FAILED.
    conv_id = _setup_reachable_with_stock(monkeypatch)

    async def _raise_connection_error(*a, **k):
        raise requests.exceptions.ConnectionError("simulated drop")
    monkeypatch.setattr("backend.routers.repack.tally_transport.post", _raise_connection_error)

    exec_payload = {"date": "2026-09-15", "store_name": "Test Store", "conversion_id": conv_id, "dest_qty": 10.0}
    response = client.post("/api/repack/execute", json=exec_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "queued"

    with get_db() as conn:
        q = conn.execute(
            "SELECT status FROM offline_queue WHERE description LIKE 'Repack:%' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert q["status"] == "PENDING"
