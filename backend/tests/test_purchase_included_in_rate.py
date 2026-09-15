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

def test_v4_gst_combinations():
    from backend.services.extraction_v4.reconciliation_v4 import calculate_and_reconcile_v4
    
    # Base extraction data mock
    extracted_data = {
        "tax_type": "local",
        "items": [
            {
                "name": "Test Item",
                "qty": 18,
                "line_amount": 4320.0
            }
        ]
    }
    
    # Combination 1: Exclusive + Included in Rate (The exact bug case)
    res_c = calculate_and_reconcile_v4(extracted_data, "included_in_rate", "exclusive", 5.0)
    calc_c = res_c["calculated_data"]["items"][0]
    # GST = 4320 * 5% = 216. Final = 4536.
    assert calc_c["ex_gst_amount"] == 4320.0
    assert calc_c["gst_amount_calculated"] == 216.0
    assert calc_c["final_amount"] == 4536.0
    assert calc_c["rate"] == 252.0
    
    # Verify XML for Combination 1
    client.post("/api/purchase-item/post", json={
        "supplier": "Test Supplier",
        "invoice_number": "INV-C",
        "tally_date": "2023-10-10",
        "cost_center": "Mahagun",
        "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "rounding_off": 0.0,
        "items": [{
            "name": "Test Item", "qty": 18, "uom": "PCS",
            "rate": 252.0, "amount": 4536.0, "final_amount": 4536.0, "final_rate": 252.0,
            "mapped_name": "Item A", "mapped_unit": "PCS", "is_mapped": True
        }]
    })
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT xml_data, xml_payload FROM offline_queue WHERE description LIKE '%INV-C%' ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        xml = row["xml_payload"] if "xml_payload" in dict(row).keys() and row["xml_payload"] else row["xml_data"]
        # Included in Rate -> Amount = -4536 (Purchase), Rate = 252
        assert "<AMOUNT>-4536.00</AMOUNT>" in xml
        assert "<RATE>252.0/PCS</RATE>" in xml
        assert "<LEDGERNAME>Input CGST</LEDGERNAME>" not in xml
        
    # Combination 2: Exclusive + Record GST Separately
    res_a = calculate_and_reconcile_v4(extracted_data, "separate_ledger", "exclusive", 5.0)
    calc_a = res_a["calculated_data"]["items"][0]
    # taxable = 4320. GST = 216. Final = 4536. Rate = 240.
    assert calc_a["ex_gst_amount"] == 4320.0
    assert calc_a["gst_amount_calculated"] == 216.0
    assert calc_a["final_amount"] == 4536.0
    assert calc_a["rate"] == 240.0
    
    client.post("/api/purchase-item/post", json={
        "supplier": "Test Supplier",
        "invoice_number": "INV-A",
        "tally_date": "2023-10-10",
        "cost_center": "Mahagun",
        "cgst": 108.0, "sgst": 108.0, "igst": 0.0, "rounding_off": 0.0,
        "items": [{
            "name": "Test Item", "qty": 18, "uom": "PCS",
            "rate": 240.0, "amount": 4320.0, "final_amount": 4536.0, "final_rate": 252.0,
            "mapped_name": "Item A", "mapped_unit": "PCS", "is_mapped": True
        }]
    })
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT xml_data, xml_payload FROM offline_queue WHERE description LIKE '%INV-A%' ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        xml = row["xml_payload"] if "xml_payload" in dict(row).keys() and row["xml_payload"] else row["xml_data"]
        assert "<AMOUNT>-4320.00</AMOUNT>" in xml
        assert "<RATE>240.0/PCS</RATE>" in xml
        assert "<LEDGERNAME>Input CGST</LEDGERNAME>" in xml
        
    # Combination 3: Inclusive + Record GST Separately
    res_b = calculate_and_reconcile_v4(extracted_data, "separate_ledger", "inclusive", 5.0)
    calc_b = res_b["calculated_data"]["items"][0]
    # final = 4320. taxable = 4320 / 1.05 = 4114.29
    assert abs(calc_b["ex_gst_amount"] - 4114.29) < 0.02
    assert abs(calc_b["gst_amount_calculated"] - 205.71) < 0.02
    assert calc_b["final_amount"] == 4320.0
    
    client.post("/api/purchase-item/post", json={
        "supplier": "Test Supplier",
        "invoice_number": "INV-B",
        "tally_date": "2023-10-10",
        "cost_center": "Mahagun",
        "cgst": 102.86, "sgst": 102.86, "igst": 0.0, "rounding_off": 0.0,
        "items": [{
            "name": "Test Item", "qty": 18, "uom": "PCS",
            "rate": calc_b["rate"], "amount": calc_b["ex_gst_amount"], 
            "final_amount": 4320.0, "final_rate": 240.0,
            "mapped_name": "Item A", "mapped_unit": "PCS", "is_mapped": True
        }]
    })
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT xml_data, xml_payload FROM offline_queue WHERE description LIKE '%INV-B%' ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        xml = row["xml_payload"] if "xml_payload" in dict(row).keys() and row["xml_payload"] else row["xml_data"]
        assert "<AMOUNT>-4114.29</AMOUNT>" in xml
        assert "<LEDGERNAME>Input CGST</LEDGERNAME>" in xml

    # Combination 4: Inclusive + Included in Rate
    res_d = calculate_and_reconcile_v4(extracted_data, "included_in_rate", "inclusive", 5.0)
    calc_d = res_d["calculated_data"]["items"][0]
    assert abs(calc_d["ex_gst_amount"] - 4114.29) < 0.02
    assert abs(calc_d["gst_amount_calculated"] - 205.71) < 0.02
    assert calc_d["final_amount"] == 4320.0
    assert calc_d["rate"] == 240.0
    
    client.post("/api/purchase-item/post", json={
        "supplier": "Test Supplier",
        "invoice_number": "INV-D",
        "tally_date": "2023-10-10",
        "cost_center": "Mahagun",
        "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "rounding_off": 0.0,
        "items": [{
            "name": "Test Item", "qty": 18, "uom": "PCS",
            "rate": 240.0, "amount": 4320.0, "final_amount": 4320.0, "final_rate": 240.0,
            "mapped_name": "Item A", "mapped_unit": "PCS", "is_mapped": True
        }]
    })
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT xml_data, xml_payload FROM offline_queue WHERE description LIKE '%INV-D%' ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        xml = row["xml_payload"] if "xml_payload" in dict(row).keys() and row["xml_payload"] else row["xml_data"]
        assert "<AMOUNT>-4320.00</AMOUNT>" in xml
        assert "<RATE>240.0/PCS</RATE>" in xml
        assert "<LEDGERNAME>Input CGST</LEDGERNAME>" not in xml

