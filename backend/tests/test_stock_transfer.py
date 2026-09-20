import json
import pytest
import requests
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db
from backend.connector.manager import connector_manager

client = TestClient(app)

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
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM offline_queue")
        cursor.execute("INSERT OR REPLACE INTO stores (store_name, cost_center_name, godown_name, active) VALUES ('Store A', 'CC A', 'Godown A', 1)")
        cursor.execute("INSERT OR REPLACE INTO stores (store_name, cost_center_name, godown_name, active) VALUES ('Store B', 'CC B', 'Godown B', 1)")
        cursor.execute("INSERT OR REPLACE INTO stores (store_name, cost_center_name, godown_name, active) VALUES ('No Godown Store', 'CC C', '', 1)")
        conn.commit()
    yield


def _post_payload(**overrides):
    payload = {
        "item_name": "Test Item",
        "qty": 10.0,
        "rate": 50.0,
        "total_amount": 500.0,
        "from_store": "Store A",
        "to_store": "Store B",
        "tally_date": "20260901",
    }
    payload.update(overrides)
    return payload


def _queued_xmls():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT operation_type, xml_data, description FROM offline_queue ORDER BY id")
        return [dict(r) for r in cursor.fetchall()]


def test_metadata_returns_stores_from_local_table():
    response = client.get("/api/stock-transfer/metadata")
    assert response.status_code == 200
    data = response.json()
    assert "Store A" in data["stores"]
    assert "Store B" in data["stores"]


def test_post_transfer_missing_from_godown_returns_400(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ConnectionError()))
    response = client.post("/api/stock-transfer/post", json=_post_payload(from_store="No Godown Store"))
    assert response.status_code == 400
    assert "No Godown Store" in response.json()["detail"]


def test_post_transfer_missing_to_godown_returns_400(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ConnectionError()))
    response = client.post("/api/stock-transfer/post", json=_post_payload(to_store="No Godown Store"))
    assert response.status_code == 400
    assert "No Godown Store" in response.json()["detail"]


def test_post_transfer_queues_both_vouchers_when_tally_offline(monkeypatch):
    # No connector registered (the default in tests) -- is_tally_reachable()
    # is naturally False and tally_transport.post() naturally raises
    # ConnectTimeout, exactly like a real "nothing connected" state.
    response = client.post("/api/stock-transfer/post", json=_post_payload())
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "queued"
    assert data["accounting"]["status"] == "queued"
    assert data["physical"]["status"] == "queued"

    queued = _queued_xmls()
    assert len(queued) == 2

    accounting = next(q for q in queued if "(Accounting)" in q["description"])
    physical = next(q for q in queued if "(Physical)" in q["description"])

    assert 'VCHTYPE="Journal"' in accounting["xml_data"]
    assert "inter store transfer" in accounting["xml_data"]

    assert 'VCHTYPE="Stock Journal"' in physical["xml_data"]
    assert "<STOCKITEMNAME>Test Item</STOCKITEMNAME>" in physical["xml_data"]
    assert "<GODOWNNAME>Godown A</GODOWNNAME>" in physical["xml_data"]
    assert "<GODOWNNAME>Godown B</GODOWNNAME>" in physical["xml_data"]
    # OUT leg (source godown) must be deemed negative with positive amount,
    # IN leg (destination godown) deemed positive with negative amount --
    # mirrors repack.py's already Tally-verified sign convention.
    out_idx = physical["xml_data"].index("INVENTORYENTRIESOUT.LIST")
    in_idx = physical["xml_data"].index("INVENTORYENTRIESIN.LIST")
    out_section = physical["xml_data"][out_idx:in_idx]
    in_section = physical["xml_data"][in_idx:]
    assert "<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>" in out_section
    assert "<AMOUNT>500.00</AMOUNT>" in out_section
    assert "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>" in in_section
    assert "<AMOUNT>-500.00</AMOUNT>" in in_section


def test_post_transfer_blocks_on_insufficient_stock_when_tally_reachable(monkeypatch):
    monkeypatch.setattr(connector_manager, "is_connected", lambda: True)

    import backend.services.tally_godown_stock as godown_module
    async def mock_get_godown_stock(item_name, godown_name):
        return {"qty": 2.0, "rate": 50.0, "amount": 100.0}
    monkeypatch.setattr(godown_module, "get_godown_stock", mock_get_godown_stock)

    response = client.post("/api/stock-transfer/post", json=_post_payload(qty=10.0))
    assert response.status_code == 400
    assert "Insufficient stock" in response.json()["detail"]
    assert len(_queued_xmls()) == 0


def test_post_transfer_success_when_reachable_and_sufficient(monkeypatch):
    monkeypatch.setattr(connector_manager, "is_connected", lambda: True)

    import backend.services.tally_godown_stock as godown_module
    async def mock_get_godown_stock(item_name, godown_name):
        return {"qty": 100.0, "rate": 50.0, "amount": 5000.0}
    monkeypatch.setattr(godown_module, "get_godown_stock", mock_get_godown_stock)

    mock_resp = MagicMock()
    mock_resp.text = SUCCESS_XML
    monkeypatch.setattr("backend.routers.stock_transfer.tally_transport.post", AsyncMock(return_value=mock_resp))

    response = client.post("/api/stock-transfer/post", json=_post_payload())
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["accounting"]["status"] == "success"
    assert data["physical"]["status"] == "success"


def test_post_transfer_reports_failed_when_tally_rejects(monkeypatch):
    monkeypatch.setattr(connector_manager, "is_connected", lambda: True)

    import backend.services.tally_godown_stock as godown_module
    async def mock_get_godown_stock(item_name, godown_name):
        return {"qty": 100.0, "rate": 50.0, "amount": 5000.0}
    monkeypatch.setattr(godown_module, "get_godown_stock", mock_get_godown_stock)

    mock_resp = MagicMock()
    mock_resp.text = REJECTED_XML
    monkeypatch.setattr("backend.routers.stock_transfer.tally_transport.post", AsyncMock(return_value=mock_resp))

    response = client.post("/api/stock-transfer/post", json=_post_payload())
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "failed"
    assert data["accounting"]["status"] == "failed"
    assert data["physical"]["status"] == "failed"
