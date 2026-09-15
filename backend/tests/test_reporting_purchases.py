import os
os.environ["TESTING"] = "true"

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db, init_db
from backend.services.tally_reporting_sync import fetch_and_store_vouchers

client = TestClient(app)

MOCK_PURCHASE_XML = """
<ENVELOPE>
  <BODY>
    <DATA>
      <COLLECTION>
        <!-- 1. Normal Purchase (Allocated) -->
        <VOUCHER VCHKEY="P-1" VCHTYPE="Purchase" ACTION="Create" OBJVIEW="Accounting Voucher View">
          <DATE>20260701</DATE>
          <GUID>P-1</GUID>
          <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
          <VOUCHERNUMBER>1</VOUCHERNUMBER>
          <PARTYLEDGERNAME>Supplier A</PARTYLEDGERNAME>
          
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>Supplier A</LEDGERNAME>
            <AMOUNT>1180.00</AMOUNT>
            <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE> <!-- Credit -->
          </ALLLEDGERENTRIES.LIST>
          
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>Purchase Account 1</LEDGERNAME>
            <AMOUNT>-1000.00</AMOUNT>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE> <!-- Debit -->
            <CATEGORYALLOCATIONS.LIST>
              <COSTCENTREALLOCATIONS.LIST>
                <NAME>Store A</NAME>
                <AMOUNT>-1000.00</AMOUNT>
              </COSTCENTREALLOCATIONS.LIST>
            </CATEGORYALLOCATIONS.LIST>
          </ALLLEDGERENTRIES.LIST>
          
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>Input CGST</LEDGERNAME>
            <AMOUNT>-90.00</AMOUNT>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          </ALLLEDGERENTRIES.LIST>
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>Input SGST</LEDGERNAME>
            <AMOUNT>-90.00</AMOUNT>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          </ALLLEDGERENTRIES.LIST>
          
          <ALLINVENTORYENTRIES.LIST>
            <STOCKITEMNAME>Item X</STOCKITEMNAME>
            <AMOUNT>-1000.00</AMOUNT>
            <BILLEDQTY> 10.00 PCS</BILLEDQTY> <!-- Positive absolute quantity -->
          </ALLINVENTORYENTRIES.LIST>
        </VOUCHER>

        <!-- 2. Unallocated Purchase -->
        <VOUCHER VCHKEY="P-2" VCHTYPE="Purchase" ACTION="Create" OBJVIEW="Accounting Voucher View">
          <DATE>20260702</DATE>
          <GUID>P-2</GUID>
          <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
          <VOUCHERNUMBER>2</VOUCHERNUMBER>
          <PARTYLEDGERNAME>Supplier B</PARTYLEDGERNAME>
          
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>Supplier B</LEDGERNAME>
            <AMOUNT>500.00</AMOUNT>
            <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          </ALLLEDGERENTRIES.LIST>
          
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>Purchase Account 2</LEDGERNAME>
            <AMOUNT>-500.00</AMOUNT>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          </ALLLEDGERENTRIES.LIST>
          
          <ALLINVENTORYENTRIES.LIST>
            <STOCKITEMNAME>Item Y</STOCKITEMNAME>
            <AMOUNT>-500.00</AMOUNT>
            <BILLEDQTY> 5.00 PCS</BILLEDQTY> <!-- Positive absolute quantity -->
          </ALLINVENTORYENTRIES.LIST>
        </VOUCHER>

        <!-- 3. Purchase Return (Debit Note) -->
        <VOUCHER VCHKEY="P-3" VCHTYPE="Debit Note" ACTION="Create" OBJVIEW="Accounting Voucher View">
          <DATE>20260703</DATE>
          <GUID>P-3</GUID>
          <VOUCHERTYPENAME>Debit Note</VOUCHERTYPENAME>
          <VOUCHERNUMBER>3</VOUCHERNUMBER>
          <PARTYLEDGERNAME>Supplier A</PARTYLEDGERNAME>
          
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>Supplier A</LEDGERNAME>
            <AMOUNT>-236.00</AMOUNT>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE> <!-- Debit Supplier -->
          </ALLLEDGERENTRIES.LIST>
          
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>Purchase Account 1</LEDGERNAME>
            <AMOUNT>200.00</AMOUNT>
            <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE> <!-- Credit Purchase -->
            <CATEGORYALLOCATIONS.LIST>
              <COSTCENTREALLOCATIONS.LIST>
                <NAME>Store A</NAME>
                <AMOUNT>200.00</AMOUNT>
              </COSTCENTREALLOCATIONS.LIST>
            </CATEGORYALLOCATIONS.LIST>
          </ALLLEDGERENTRIES.LIST>
          
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>Input CGST</LEDGERNAME>
            <AMOUNT>18.00</AMOUNT>
            <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          </ALLLEDGERENTRIES.LIST>
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>Input SGST</LEDGERNAME>
            <AMOUNT>18.00</AMOUNT>
            <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          </ALLLEDGERENTRIES.LIST>
          
          <ALLINVENTORYENTRIES.LIST>
            <STOCKITEMNAME>Item X</STOCKITEMNAME>
            <AMOUNT>200.00</AMOUNT>
            <BILLEDQTY> 2.00 PCS</BILLEDQTY> <!-- Positive absolute quantity -->
          </ALLINVENTORYENTRIES.LIST>
        </VOUCHER>

      </COLLECTION>
    </DATA>
  </BODY>
</ENVELOPE>
"""

@pytest.fixture(autouse=True)
def setup_teardown_db():
    init_db()
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Insert mock ledgers
        cursor.execute("INSERT OR REPLACE INTO ledgers (name, parent, cost_centre) VALUES ('Supplier A', 'Sundry Creditors', 0)")
        cursor.execute("INSERT OR REPLACE INTO ledgers (name, parent, cost_centre) VALUES ('Supplier B', 'Sundry Creditors', 0)")
        cursor.execute("INSERT OR REPLACE INTO ledgers (name, parent, cost_centre) VALUES ('Purchase Account 1', 'Purchase Accounts', 1)")
        cursor.execute("INSERT OR REPLACE INTO ledgers (name, parent, cost_centre) VALUES ('Purchase Account 2', 'Purchase Accounts', 1)")
        cursor.execute("INSERT OR REPLACE INTO ledgers (name, parent, cost_centre) VALUES ('Input CGST', 'Duties & Taxes', 0)")
        cursor.execute("INSERT OR REPLACE INTO ledgers (name, parent, cost_centre) VALUES ('Input SGST', 'Duties & Taxes', 0)")
        
        # Insert sync history covering July 2026
        cursor.execute("INSERT OR REPLACE INTO reporting_sync_history (start_date, end_date, synced_at) VALUES ('20260701', '20260731', '2026-07-31T23:59:59')")
        
        # Insert Cost Centres
        cursor.execute("INSERT OR REPLACE INTO cost_centres (name) VALUES ('Store A')")
        
        # Clear existing vouchers for clean state
        cursor.execute("DELETE FROM reporting_vouchers WHERE tally_guid IN ('P-1', 'P-2', 'P-3')")
        conn.commit()
        
    with patch('requests.post') as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = MOCK_PURCHASE_XML
        mock_post.return_value = mock_resp
        fetch_and_store_vouchers('20260701', '20260731')
    
    yield
    
    # Teardown
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM reporting_vouchers WHERE tally_guid IN ('P-1', 'P-2', 'P-3')")
        cursor.execute("DELETE FROM reporting_sync_history WHERE start_date='20260701'")

def test_purchases_report_combined():
    # Store A: 1000 - 200 = 800
    # Unallocated: 500
    # Combined: 1300
    response = client.get("/api/reporting/purchases?start_date=20260701&end_date=20260731")
    assert response.status_code == 200
    data = response.json()
    
    assert data['is_data_complete'] is True
    assert data['summary']['net_purchases'] == 1300.0
    
    store_comp = {s['store_name']: s['net_purchases'] for s in data['store_comparison']}
    assert store_comp['Store A'] == 800.0
    assert store_comp['Unallocated'] == 500.0

def test_purchases_report_store_a():
    response = client.get("/api/reporting/purchases?start_date=20260701&end_date=20260731&cost_centre=Store A")
    assert response.status_code == 200
    data = response.json()
    assert data['summary']['net_purchases'] == 800.0

def test_purchases_report_unallocated():
    response = client.get("/api/reporting/purchases?start_date=20260701&end_date=20260731&cost_centre=Unallocated")
    assert response.status_code == 200
    data = response.json()
    assert data['summary']['net_purchases'] == 500.0

def test_supplier_purchase_analysis():
    response = client.get("/api/reporting/purchases/suppliers?start_date=20260701&end_date=20260731")
    assert response.status_code == 200
    data = response.json()
    assert data['total_count'] == 2
    
    suppliers = {s['supplier_name']: s for s in data['suppliers']}
    assert 'Supplier A' in suppliers
    assert suppliers['Supplier A']['vouchers_count'] == 2
    assert suppliers['Supplier A']['net_purchases'] == 800.0
    
    assert 'Supplier B' in suppliers
    assert suppliers['Supplier B']['vouchers_count'] == 1
    assert suppliers['Supplier B']['net_purchases'] == 500.0

def test_purchase_bills():
    response = client.get("/api/reporting/purchases/bills?start_date=20260701&end_date=20260731&supplier_name=Supplier A")
    assert response.status_code == 200
    data = response.json()
    assert data['total_count'] == 2
    assert len(data['bills']) == 2
    
    bills = {b['voucher_number']: b for b in data['bills']}
    assert '1' in bills
    assert bills['1']['net_purchases'] == 1000.0
    
    assert '3' in bills
    assert bills['3']['net_purchases'] == -200.0 # Return reduces purchase value

def test_purchase_bill_details():
    response = client.get("/api/reporting/purchases/bills?start_date=20260701&end_date=20260731&supplier_name=Supplier A")
    bills = response.json()['bills']
    vch_3_id = [b['voucher_id'] for b in bills if b['voucher_number'] == '3'][0]
    
    response2 = client.get(f"/api/reporting/purchases/bills/{vch_3_id}")
    assert response2.status_code == 200
    data = response2.json()
    
    assert data['voucher']['voucher_type'] == 'Debit Note'
    assert data['purchase_value'] == -200.0
    assert data['supplier_payable'] == -236.0
    assert data['taxes']['cgst'] == -18.0
    
    items = {i['item_name']: i for i in data['items']}
    assert items['Item X']['quantity'] == -2.0

def test_purchase_trend_reconciliation():
    response = client.get("/api/reporting/purchases?start_date=20260701&end_date=20260731")
    assert response.status_code == 200
    data = response.json()
    
    trend = data['trend']
    assert len(trend) > 0
    
    # 20260701: Supplier A - Purchase Account 1 (1000)
    # 20260702: Supplier B - Unallocated (500)
    # 20260703: Supplier A - Purchase Account 1 (-200, Return)
    
    day_1 = [d for d in trend if d['date'] == '20260701'][0]
    assert day_1['Store A'] == 1000.0
    assert day_1['Combined'] == 1000.0
    
    day_2 = [d for d in trend if d['date'] == '20260702'][0]
    assert day_2['Unallocated'] == 500.0
    assert day_2['Combined'] == 500.0
    
    day_3 = [d for d in trend if d['date'] == '20260703'][0]
    assert day_3['Store A'] == -200.0
    assert day_3['Combined'] == -200.0
    
    # Reconciliation test
    total_trend_combined = sum(d['Combined'] for d in trend)
    assert total_trend_combined == 1300.0
    assert data['summary']['net_purchases'] == total_trend_combined


def test_purchase_trend_store_filter():
    # Test specific store filter (Store A)
    response = client.get("/api/reporting/purchases?start_date=20260701&end_date=20260731&cost_centre=Store A")
    assert response.status_code == 200
    data = response.json()
    
    trend = data['trend']
    # 20260701 and 20260703 should be present, but not 20260702 because 20260702 is Unallocated
    assert len(trend) == 2
    
    day_1 = [d for d in trend if d['date'] == '20260701'][0]
    assert day_1['Store A'] == 1000.0
    assert day_1['Combined'] == 1000.0
    assert 'Unallocated' not in day_1
    
    day_3 = [d for d in trend if d['date'] == '20260703'][0]
    assert day_3['Store A'] == -200.0
    assert day_3['Combined'] == -200.0
    assert 'Unallocated' not in day_3

def test_purchase_trend_unallocated_filter():
    response = client.get("/api/reporting/purchases?start_date=20260701&end_date=20260731&cost_centre=Unallocated")
    assert response.status_code == 200
    data = response.json()
    
    trend = data['trend']
    assert len(trend) == 1
    
    day_2 = [d for d in trend if d['date'] == '20260702'][0]
    assert day_2['Unallocated'] == 500.0
    assert day_2['Combined'] == 500.0
    assert 'Purchase Account 1' not in day_2

