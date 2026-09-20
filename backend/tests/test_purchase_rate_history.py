import xml.etree.ElementTree as ET
import pytest
import requests
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db, init_db, dedup_pending_against_tally, cleanup_purchase_rate_pending_entries


@pytest.fixture(autouse=True)
def test_db():
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM purchase_rates")
        conn.execute("DELETE FROM purchase_rate_pending_entries")
        conn.execute("DELETE FROM pending_masters")
        conn.execute("DELETE FROM offline_queue")
        conn.execute("DELETE FROM reporting_vouchers")
        conn.execute("DELETE FROM reporting_inventory_entries")
        conn.execute("INSERT OR IGNORE INTO ledgers (name, parent, cost_centre) VALUES (?, ?, ?)", ("Test Supplier", "Sundry Creditors", False))
        conn.execute("INSERT OR IGNORE INTO stock_items (name, unit) VALUES (?, ?)", ("Item A", "PCS"))
        conn.execute("INSERT OR IGNORE INTO uoms (name) VALUES (?)", ("PCS",))
        conn.commit()


client = TestClient(app)


def test_posting_purchase_logs_a_pending_rate_history_entry():
    items = [{
        "name": "Item A", "qty": 3, "uom": "PCS", "rate": 55.0, "discount": 0.0,
        "amount": 165.0, "mapped_name": "Item A", "mapped_unit": "PCS", "is_mapped": True
    }]
    payload = {
        "supplier": "Test Supplier",
        "invoice_number": "INV-HISTORY-1",
        "tally_date": "2026-08-10",
        "cost_center": "Mahagun",
        "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "rounding_off": 0.0,
        "items": items,
        "adjustment": None
    }
    response = client.post("/api/purchase-item/post", json=payload)
    assert response.status_code == 200

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM purchase_rate_pending_entries WHERE stock_item_name = 'item a'")
        row = cursor.fetchone()
        assert row is not None
        assert row["rate"] == 55.0
        assert row["purchase_date"] == "2026-08-10"
        assert row["qty"] == 3
        assert row["voucher_number"] == "INV-HISTORY-1"
        assert row["supplier"] == "Test Supplier"


def test_dedup_drops_pending_entry_matching_confirmed_tally_entry():
    tally_entries = [{"date": "2026-08-10", "rate": 55.0, "supplier": "Test Supplier", "voucher_number": "PB/1", "qty": 3, "unit": "PCS", "origin": "tally"}]
    exact_dup = [{"date": "2026-08-10", "rate": 55.0, "supplier": "Test Supplier", "voucher_number": "INV-HISTORY-1", "qty": 3, "unit": "PCS", "origin": "app_post"}]
    assert dedup_pending_against_tally(tally_entries, exact_dup) == []


def test_dedup_keeps_pending_entry_with_different_rate_same_day():
    tally_entries = [{"date": "2026-08-10", "rate": 55.0, "supplier": "Test Supplier", "voucher_number": "PB/1", "qty": 3, "unit": "PCS", "origin": "tally"}]
    different_rate = [{"date": "2026-08-10", "rate": 60.0, "supplier": "Other Supplier", "voucher_number": "INV-2", "qty": 5, "unit": "PCS", "origin": "app_post"}]
    assert dedup_pending_against_tally(tally_entries, different_rate) == different_rate


def test_dedup_keeps_pending_entry_with_no_matching_tally_entry():
    pending = [{"date": "2026-09-01", "rate": 70.0, "supplier": "Test Supplier", "voucher_number": "INV-3", "qty": 1, "unit": "PCS", "origin": "app_post"}]
    assert dedup_pending_against_tally([], pending) == pending


def test_return_item_history_falls_back_to_local_cache_when_tally_unreachable():
    # No real Tally listening in the test env, so the live fetch inside the
    # endpoint naturally raises a connection error and exercises the fallback.
    with get_db() as conn:
        conn.execute(
            "INSERT INTO purchase_rate_pending_entries (stock_item_name, rate, purchase_date, supplier, voucher_number, qty, unit) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("item a", 55.0, "2026-08-10", "Test Supplier", "INV-HISTORY-1", 3, "PCS")
        )
        conn.commit()

    response = client.get("/api/purchase-item/return-item-history", params={"item_name": "Item A"})
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "local_cache"
    assert len(data["entries"]) == 1
    assert data["entries"][0]["rate"] == 55.0
    assert data["entries"][0]["origin"] == "app_post"


def test_return_item_history_ampersand_item_name_round_trips(monkeypatch):
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO stock_items (name, unit) VALUES (?, ?)", ("M&M's", "PCS"))
        conn.execute(
            "INSERT INTO purchase_rate_pending_entries (stock_item_name, rate, purchase_date, supplier, voucher_number, qty, unit) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("m&m's", 42.0, "2026-07-01", "Test Supplier", "INV-AMP", 2, "PCS")
        )
        conn.commit()

    response = client.get("/api/purchase-item/return-item-history", params={"item_name": "M&M's"})
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "local_cache"
    assert len(data["entries"]) == 1
    assert data["entries"][0]["rate"] == 42.0


def test_return_item_history_malformed_tally_response_returns_502(monkeypatch):
    def raise_parse_error(item_name, since_date, tally_url=None):
        raise ET.ParseError("mismatched tag")

    monkeypatch.setattr(
        "backend.services.tally_reporting_sync.fetch_live_purchase_history_from_tally",
        raise_parse_error
    )
    response = client.get("/api/purchase-item/return-item-history", params={"item_name": "Item A"})
    assert response.status_code == 502


def test_cleanup_removes_old_and_superseded_pending_entries():
    with get_db() as conn:
        cursor = conn.cursor()
        # Old row, past the 2-year retention window.
        cursor.execute(
            "INSERT INTO purchase_rate_pending_entries (stock_item_name, rate, purchase_date) VALUES (?, ?, ?)",
            ("item a", 10.0, "2020-01-01")
        )
        # Recent row, superseded by a matching confirmed Tally voucher below.
        cursor.execute(
            "INSERT INTO purchase_rate_pending_entries (stock_item_name, rate, purchase_date) VALUES (?, ?, ?)",
            ("item a", 55.0, "2026-08-10")
        )
        # Recent row with no matching Tally voucher -- must survive.
        cursor.execute(
            "INSERT INTO purchase_rate_pending_entries (stock_item_name, rate, purchase_date) VALUES (?, ?, ?)",
            ("item a", 99.0, "2026-08-11")
        )
        cursor.execute(
            "INSERT INTO reporting_vouchers (tally_guid, date, voucher_number, voucher_type, party_ledger_name) VALUES (?, ?, ?, ?, ?)",
            ("guid-hist-1", "20260810", "PB/1", "Purchase", "Test Supplier")
        )
        vch_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO reporting_inventory_entries (voucher_id, stock_item_name, billed_qty, amount, rate) VALUES (?, ?, ?, ?, ?)",
            (vch_id, "Item A", 3, 165.0, 55.0)
        )
        conn.commit()

    deleted = cleanup_purchase_rate_pending_entries()
    assert deleted == 2

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT rate FROM purchase_rate_pending_entries WHERE stock_item_name = 'item a'")
        remaining = [r["rate"] for r in cursor.fetchall()]
        assert remaining == [99.0]
