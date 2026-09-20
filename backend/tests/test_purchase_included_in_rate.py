import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db, init_db

@pytest.fixture(autouse=True)
def test_db():
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM purchase_rates")
        conn.execute("DELETE FROM pending_masters")
        conn.execute("DELETE FROM offline_queue")
        conn.execute("INSERT OR IGNORE INTO ledgers (name, parent, cost_centre) VALUES (?, ?, ?)", ("Test Supplier", "Sundry Creditors", False))
        conn.execute("INSERT OR IGNORE INTO stock_items (name, unit) VALUES (?, ?)", ("Item A", "PCS"))
        conn.execute("INSERT OR IGNORE INTO uoms (name) VALUES (?)", ("PCS",))
        conn.execute("INSERT OR IGNORE INTO uoms (name) VALUES (?)", ("NOS",))
        conn.commit()

client = TestClient(app)

def test_included_in_rate_vs_separate_ledger():
    # 1. Base transaction data (118 inclusive = 100 exclusive + 18 GST)
    items = [
        {
            "name": "Item A",
            "qty": 1,
            "uom": "PCS",
            "rate": 118.0,  # Frontend tracks inclusive rate for display/history
            "discount": 0.0,
            "amount": 118.0,  # Frontend passes inclusive amount in items payload for Mode 1
            "mapped_name": "Item A",
            "mapped_unit": "PCS",
            "is_mapped": True
        }
    ]
    
    # 2. Included-in-Rate payload sent by frontend 
    # (Frontend sends cgst/sgst as 0, and amount is inclusive)
    included_payload = {
        "supplier": "Test Supplier",
        "invoice_number": "INV-100",
        "tally_date": "2023-10-10", # Test Date parsing YYYY-MM-DD -> 20231010
        "cost_center": "Mahagun",
        "cgst": 0.0,
        "sgst": 0.0,
        "igst": 0.0,
        "rounding_off": 0.0,
        "items": items,
        "adjustment": None
    }
    
    response = client.post("/api/purchase-item/post", json=included_payload)
    assert response.status_code == 200
    
    # 3. Verify Offline Queue / XML Generation
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM offline_queue WHERE description LIKE '%INV-100%' ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        assert row is not None
        
        xml = row["xml_payload"] if "xml_payload" in row.keys() else row["xml_data"]
        
        # Test Criteria:
        # - Date is properly formatted
        assert "<DATE>20231010</DATE>" in xml

        # - Purchase value is correctly represented (amount is 118 inclusive)
        assert "<AMOUNT>-118.00</AMOUNT>" in xml
        
        # - XML rate matches inclusive amount
        assert "<RATE>118.0/PCS</RATE>" in xml
        
        # - Supplier payable equals full inclusive total
        assert "<AMOUNT>118.00</AMOUNT>" in xml
        
        # - GST input ledgers are absent
        assert "<LEDGERNAME>Input CGST</LEDGERNAME>" not in xml
        assert "<LEDGERNAME>Input SGST</LEDGERNAME>" not in xml

    # 4. Verify Separate-GST mode behaves exactly the same as it always did
    separate_payload = {
        "supplier": "Test Supplier",
        "invoice_number": "INV-101",
        "tally_date": "10/10/2023", # Test Date parsing DD/MM/YYYY -> 20231010
        "cost_center": "Mahagun",
        "cgst": 9.0,
        "sgst": 9.0,
        "igst": 0.0,
        "rounding_off": 0.0,
        "items": [
            {
                "name": "Item A",
                "qty": 1,
                "uom": "PCS",
                "rate": 100.0,   # In separate ledger, rate is exclusive
                "amount": 100.0, # exclusive amount
                "mapped_name": "Item A",
                "mapped_unit": "PCS",
                "is_mapped": True
            }
        ]
    }
    
    res2 = client.post("/api/purchase-item/post", json=separate_payload)
    assert res2.status_code == 200
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM offline_queue WHERE description LIKE '%INV-101%' ORDER BY id DESC LIMIT 1")
        row2 = cursor.fetchone()
        xml2 = row2["xml_payload"] if "xml_payload" in row2.keys() else row2["xml_data"]
        
        # Date parsed correctly
        assert "<DATE>20231010</DATE>" in xml2

        # Core XML should represent 100 exclusive + 18 tax
        assert "<AMOUNT>-100.00</AMOUNT>" in xml2
        assert "<RATE>100.0/PCS</RATE>" in xml2
        assert "<AMOUNT>118.00</AMOUNT>" in xml2
        assert "<LEDGERNAME>Input CGST</LEDGERNAME>" in xml2
        assert "<AMOUNT>-9.00</AMOUNT>" in xml2

def test_mapped_item_no_conflict_validation():
    # 1. Base transaction data with mismatched UOM (which should be ignored for matched items)
    items = [
        {
            "name": "ALOO BHUJA 420G/-110",
            "qty": 1,
            "uom": "PCS",
            "rate": 100.0,
            "discount": 0.0,
            "amount": 100.0,
            "mapped_name": "Item A", # "Item A" is in the database with unit "PCS"
            "mapped_unit": "NOS", # Mismatched unit from Tally's "PCS"
            "is_mapped": True
        }
    ]

    included_payload = {
        "supplier": "Test Supplier",
        "invoice_number": "INV-CONFLICT-TEST",
        "tally_date": "20231010",
        "cost_center": "Mahagun",
        "cgst": 0.0,
        "sgst": 0.0,
        "igst": 0.0,
        "rounding_off": 0.0,
        "items": items,
        "adjustment": None
    }

    response = client.post("/api/purchase-item/post", json=included_payload)
    if response.status_code != 200:
        print(response.text)
    # The API should get past the 409 Conflict check and successfully queue it
    assert response.status_code == 200
    
    # Verify the queued XML did NOT contain any STOCKITEM definition alters
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM offline_queue WHERE description LIKE '%INV-CONFLICT-TEST%'")
        row = cursor.fetchone()
        assert row is not None
        
        xml = dict(row).get("xml_data", "")
        assert "STOCKITEM ACTION=\"Alter\"" not in xml
        assert "<INVENTORYENTRIES.LIST>" in xml
        assert "<STOCKITEMNAME>Item A</STOCKITEMNAME>" in xml


def test_item_wise_return_posts_debit_note_with_flipped_signs():
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO stock_items (name, unit) VALUES (?, ?)", ("Item B", "PCS"))
        conn.execute(
            "INSERT INTO purchase_rates (stock_item_name, latest_rate, supplier, purchase_date) VALUES (?, ?, ?, ?)",
            ("Item B", 50.0, "Test Supplier", "2023-10-01")
        )
        conn.commit()

    items = [{
        "name": "Item A", "qty": 1, "uom": "PCS", "rate": 100.0, "discount": 0.0,
        "amount": 100.0, "mapped_name": "Item A", "mapped_unit": "PCS", "is_mapped": True
    }]

    payload = {
        "supplier": "Test Supplier",
        "invoice_number": "INV-200",
        "tally_date": "2023-10-10",
        "cost_center": "Mahagun",
        "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "rounding_off": 0.0,
        "items": items,
        "adjustment": {
            "store": "Mahagun",
            "reason": "Damaged",
            "notes": "2 units returned",
            "items": [{"name": "Item B", "uom": "PCS", "qty": 2, "rate": 0, "amount": 0}]
        }
    }

    # Tally isn't reachable in the test env, so the whole request takes the
    # "queued" (offline) path -- same as every other test in this file. What
    # matters here is that the Debit Note XML was built and queued correctly.
    response = client.post("/api/purchase-item/post", json=payload)
    assert response.status_code == 200

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM offline_queue WHERE description LIKE '%Debit Note%INV-200%' ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        assert row is not None
        xml = dict(row).get("xml_data", "")

    assert 'VCHTYPE="Debit Note"' in xml
    assert "<VOUCHERTYPENAME>Debit Note</VOUCHERTYPENAME>" in xml
    assert "<STOCKITEMNAME>Item B</STOCKITEMNAME>" in xml
    assert "<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>" in xml
    assert "<ACTUALQTY>-2.0 PCS</ACTUALQTY>" in xml
    assert "<AMOUNT>100.00</AMOUNT>" in xml  # 2 * 50 latest rate, positive (flipped vs. Purchase)
    assert "<LEDGERNAME>Purchase Return</LEDGERNAME>" in xml
    assert "<BILLTYPE>Agst Ref</BILLTYPE>" in xml
    assert "<NAME>INV-200</NAME>" in xml
    assert "<NARRATION>Damaged -- 2 units returned</NARRATION>" in xml
    # Supplier ledger entry is debited (reduces payable) by the return total --
    # the mirror image of the credited Purchase Return/inventory legs above,
    # i.e. the voucher's two sides (-100.00 supplier vs. +100.00 item/return) balance.
    assert "<AMOUNT>-100.00</AMOUNT>" in xml


def test_item_wise_return_honors_explicit_non_latest_rate():
    # The 2-year purchase-history picker lets the user select an OLDER rate
    # than the current "latest" -- the backend must post the Debit Note at
    # that selected rate, not silently override it with get_purchase_rate().
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO stock_items (name, unit) VALUES (?, ?)", ("Item C", "PCS"))
        conn.execute(
            "INSERT INTO purchase_rates (stock_item_name, latest_rate, supplier, purchase_date) VALUES (?, ?, ?, ?)",
            ("Item C", 80.0, "Test Supplier", "2023-10-05")
        )
        conn.commit()

    items = [{
        "name": "Item A", "qty": 1, "uom": "PCS", "rate": 100.0, "discount": 0.0,
        "amount": 100.0, "mapped_name": "Item A", "mapped_unit": "PCS", "is_mapped": True
    }]

    payload = {
        "supplier": "Test Supplier",
        "invoice_number": "INV-202",
        "tally_date": "2023-10-10",
        "cost_center": "Mahagun",
        "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "rounding_off": 0.0,
        "items": items,
        "adjustment": {
            "store": "Mahagun",
            # User picked an older rate (60) from history, even though the
            # current "latest" rate for this item is 80.
            "items": [{"name": "Item C", "uom": "PCS", "qty": 2, "rate": 60.0, "amount": 120.0}]
        }
    }

    response = client.post("/api/purchase-item/post", json=payload)
    assert response.status_code == 200

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM offline_queue WHERE description LIKE '%Debit Note%INV-202%' ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        assert row is not None
        xml = dict(row).get("xml_data", "")

    # 2 * 60 (the explicitly selected historical rate) = 120.00 -- must NOT be
    # 2 * 80 (the "latest" rate) = 160.00.
    assert "<RATE>60.0/PCS</RATE>" in xml
    assert "<AMOUNT>120.00</AMOUNT>" in xml
    assert "<AMOUNT>-120.00</AMOUNT>" in xml
    assert "160.00" not in xml


def test_item_wise_return_rejects_item_with_no_known_rate():
    items = [{
        "name": "Item A", "qty": 1, "uom": "PCS", "rate": 100.0, "discount": 0.0,
        "amount": 100.0, "mapped_name": "Item A", "mapped_unit": "PCS", "is_mapped": True
    }]

    payload = {
        "supplier": "Test Supplier",
        "invoice_number": "INV-201",
        "tally_date": "2023-10-10",
        "cost_center": "Mahagun",
        "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "rounding_off": 0.0,
        "items": items,
        "adjustment": {
            "store": "Mahagun",
            "items": [{"name": "Never Purchased Item", "uom": "PCS", "qty": 1, "rate": 0, "amount": 0}]
        }
    }

    response = client.post("/api/purchase-item/post", json=payload)
    assert response.status_code == 400
    assert "purchase rate" in response.json()["detail"].lower()


def test_record_purchase_rate_uses_invoice_date_not_today():
    items = [{
        "name": "Item A", "qty": 1, "uom": "PCS", "rate": 77.0, "discount": 0.0,
        "amount": 77.0, "mapped_name": "Item A", "mapped_unit": "PCS", "is_mapped": True
    }]
    payload = {
        "supplier": "Test Supplier",
        "invoice_number": "INV-BACKDATED",
        "tally_date": "2020-01-15",  # intentionally backdated vs. "today"
        "cost_center": "Mahagun",
        "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "rounding_off": 0.0,
        "items": items,
        "adjustment": None
    }
    response = client.post("/api/purchase-item/post", json=payload)
    assert response.status_code == 200

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT latest_rate, purchase_date FROM purchase_rates WHERE stock_item_name = 'item a'")
        row = cursor.fetchone()
        assert row is not None
        assert row["latest_rate"] == 77.0
        assert row["purchase_date"] == "2020-01-15"  # NOT today's date


def test_rejected_conflict_post_does_not_record_purchase_rate():
    items = [{
        "name": "Item A", "qty": 1, "uom": "NOS", "rate": 999.0, "discount": 0.0,
        "amount": 999.0, "mapped_name": None, "mapped_unit": "NOS", "is_mapped": False
        # is_mapped=False + mismatched UOM "NOS" vs. Item A's stored "PCS"
        # triggers the ITEM/CONFLICT branch in get_master_dependency_state.
    }]
    payload = {
        "supplier": "Test Supplier",
        "invoice_number": "INV-CONFLICT-2",
        "tally_date": "2023-10-10",
        "cost_center": "Mahagun",
        "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "rounding_off": 0.0,
        "items": items,
        "adjustment": None
    }
    response = client.post("/api/purchase-item/post", json=payload)
    assert response.status_code == 409

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM purchase_rates WHERE stock_item_name = 'item a'")
        assert cursor.fetchone() is None  # never poisoned by the rejected post


def test_ampersand_item_name_recorded_and_retrievable():
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO stock_items (name, unit) VALUES (?, ?)", ("M&M's", "PCS"))
        conn.commit()

    items = [{
        "name": "M&M's", "qty": 1, "uom": "PCS", "rate": 42.0, "discount": 0.0,
        "amount": 42.0, "mapped_name": "M&M's", "mapped_unit": "PCS", "is_mapped": True
    }]
    payload = {
        "supplier": "Test Supplier",
        "invoice_number": "INV-AMP",
        "tally_date": "2023-10-10",
        "cost_center": "Mahagun",
        "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "rounding_off": 0.0,
        "items": items,
        "adjustment": None
    }
    response = client.post("/api/purchase-item/post", json=payload)
    assert response.status_code == 200

    with get_db() as conn:
        cursor = conn.cursor()
        # Must be stored unescaped ("m&m's"), not XML-escaped ("m&amp;m's")
        cursor.execute("SELECT latest_rate FROM purchase_rates WHERE stock_item_name = ?", ("m&m's",))
        row = cursor.fetchone()
        assert row is not None
        assert row["latest_rate"] == 42.0

    from backend.database import get_purchase_rate
    assert get_purchase_rate("M&M's") == 42.0  # resolves via COLLATE NOCASE against the real name


def test_deleting_queued_purchase_clears_its_recorded_rate():
    # Give Tally a known, older baseline rate for Item A so we can confirm
    # get_purchase_rate() falls back to it once the local record is cleared.
    with get_db() as conn:
        conn.execute(
            "UPDATE stock_items SET last_purchase_rate = ?, last_purchase_date = ? WHERE name = 'Item A'",
            (100.0, "2023-09-01")
        )
        conn.commit()

    items = [{
        "name": "Item A", "qty": 1, "uom": "PCS", "rate": 120.0, "discount": 0.0,
        "amount": 120.0, "mapped_name": "Item A", "mapped_unit": "PCS", "is_mapped": True
    }]
    payload = {
        "supplier": "Test Supplier",
        "invoice_number": "INV-DELETE-ME",
        "tally_date": "2023-09-05",
        "cost_center": "Mahagun",
        "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "rounding_off": 0.0,
        "items": items,
        "adjustment": None
    }
    response = client.post("/api/purchase-item/post", json=payload)
    assert response.status_code == 200

    from backend.database import get_purchase_rate
    assert get_purchase_rate("Item A") == 120.0  # app's newer record wins over Tally's 100

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, source_queue_id FROM purchase_rates WHERE stock_item_name = 'item a'")
        rate_row = cursor.fetchone()
        assert rate_row is not None
        assert rate_row["source_queue_id"] is not None

        cursor.execute("SELECT id FROM offline_queue WHERE description LIKE '%INV-DELETE-ME%' ORDER BY id DESC LIMIT 1")
        queue_row = cursor.fetchone()
        assert queue_row is not None
        assert rate_row["source_queue_id"] == queue_row["id"]

    del_response = client.delete(f"/api/dashboard/activity/{queue_row['id']}")
    assert del_response.status_code == 200

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM purchase_rates WHERE stock_item_name = 'item a'")
        assert cursor.fetchone() is None  # local record cleared

    # Falls back to Tally's rate now that the app's own record is gone.
    assert get_purchase_rate("Item A") == 100.0


def test_deleting_superseded_purchase_leaves_newer_rate_untouched():
    # Post an old purchase, then a newer one that overwrites purchase_rates.
    items_old = [{
        "name": "Item A", "qty": 1, "uom": "PCS", "rate": 50.0, "discount": 0.0,
        "amount": 50.0, "mapped_name": "Item A", "mapped_unit": "PCS", "is_mapped": True
    }]
    payload_old = {
        "supplier": "Test Supplier", "invoice_number": "INV-OLD", "tally_date": "2023-09-01",
        "cost_center": "Mahagun", "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "rounding_off": 0.0,
        "items": items_old, "adjustment": None
    }
    res_old = client.post("/api/purchase-item/post", json=payload_old)
    assert res_old.status_code == 200

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM offline_queue WHERE description LIKE '%INV-OLD%' ORDER BY id DESC LIMIT 1")
        old_queue_id = cursor.fetchone()["id"]

    items_new = [{
        "name": "Item A", "qty": 1, "uom": "PCS", "rate": 90.0, "discount": 0.0,
        "amount": 90.0, "mapped_name": "Item A", "mapped_unit": "PCS", "is_mapped": True
    }]
    payload_new = {
        "supplier": "Test Supplier", "invoice_number": "INV-NEW", "tally_date": "2023-09-10",
        "cost_center": "Mahagun", "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "rounding_off": 0.0,
        "items": items_new, "adjustment": None
    }
    res_new = client.post("/api/purchase-item/post", json=payload_new)
    assert res_new.status_code == 200

    from backend.database import get_purchase_rate
    assert get_purchase_rate("Item A") == 90.0  # the newer purchase now owns the cached row

    # Deleting the OLD (already-superseded) transaction must not wipe out the
    # newer rate, since purchase_rates.source_queue_id no longer points to it.
    del_response = client.delete(f"/api/dashboard/activity/{old_queue_id}")
    assert del_response.status_code == 200
    assert get_purchase_rate("Item A") == 90.0

