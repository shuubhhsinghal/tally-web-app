import os
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock, AsyncMock

os.environ["TESTING"] = "true"

from backend.database import init_db, get_db
from backend.services.reporting_sales_service import (
    check_data_completeness, 
    get_previous_period, 
    calculate_sales,
    get_sales_trend,
    get_store_comparisons
)
from backend.services.tally_reporting_sync import fetch_and_store_vouchers

# Dummy XML with comprehensive cases. VCHTYPE is deliberately NOT "Sales" for
# V1/V3 -- calculate_sales excludes that voucher type (it's always sourced
# from this app's own offline queue instead, counted the moment it's SYNCED
# rather than waiting on this same reporting sync -- see
# reporting_sales_service.get_queue_sales_trend). "Journal" here simulates a
# manual adjustment touching a Sales Accounts ledger some other way, which
# this report should still pick up normally.
# VCH 1: Journal, 1000 to Store A, 500 to Store B, 150 CGST (GST should be ignored).
# VCH 2: Sales Return (Credit Note), 200 from Store A (Reduction).
# VCH 3: Journal, 300 to no store.
MOCK_XML = """<ENVELOPE>
  <BODY>
    <DATA>
      <COLLECTION>
        <!-- Voucher 1: Journal with GST and Allocations -->
        <VOUCHER VCHKEY="v1">
          <GUID>v1</GUID>
          <DATE>20230915</DATE>
          <VOUCHERTYPENAME>Journal</VOUCHERTYPENAME>
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>Sales Account 1</LEDGERNAME>
            <AMOUNT>1500.00</AMOUNT>
            <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
            <CATEGORYALLOCATIONS.LIST>
              <COSTCENTREALLOCATIONS.LIST>
                <NAME>Store A</NAME>
                <AMOUNT>1000.00</AMOUNT>
              </COSTCENTREALLOCATIONS.LIST>
              <COSTCENTREALLOCATIONS.LIST>
                <NAME>Store B</NAME>
                <AMOUNT>500.00</AMOUNT>
              </COSTCENTREALLOCATIONS.LIST>
            </CATEGORYALLOCATIONS.LIST>
          </ALLLEDGERENTRIES.LIST>
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>Output CGST</LEDGERNAME>
            <AMOUNT>-150.00</AMOUNT>
            <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          </ALLLEDGERENTRIES.LIST>
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>Customer X</LEDGERNAME>
            <AMOUNT>1650.00</AMOUNT>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          </ALLLEDGERENTRIES.LIST>
        </VOUCHER>

        <!-- Voucher 2: Sales Return (Credit Note) -->
        <VOUCHER VCHKEY="v2">
          <GUID>v2</GUID>
          <DATE>20230916</DATE>
          <VOUCHERTYPENAME>Credit Note</VOUCHERTYPENAME>
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>Sales Return Account</LEDGERNAME>
            <AMOUNT>200.00</AMOUNT>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <CATEGORYALLOCATIONS.LIST>
              <COSTCENTREALLOCATIONS.LIST>
                <NAME>Store A</NAME>
                <AMOUNT>200.00</AMOUNT>
              </COSTCENTREALLOCATIONS.LIST>
            </CATEGORYALLOCATIONS.LIST>
          </ALLLEDGERENTRIES.LIST>
        </VOUCHER>

        <!-- Voucher 3: Journal, Unallocated -->
        <VOUCHER VCHKEY="v3">
          <GUID>v3</GUID>
          <DATE>20230917</DATE>
          <VOUCHERTYPENAME>Journal</VOUCHERTYPENAME>
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>Sales Account 1</LEDGERNAME>
            <AMOUNT>300.00</AMOUNT>
            <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          </ALLLEDGERENTRIES.LIST>
        </VOUCHER>
      </COLLECTION>
    </DATA>
  </BODY>
</ENVELOPE>"""

@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM reporting_inventory_entries")
        c.execute("DELETE FROM reporting_cost_centre_allocations")
        c.execute("DELETE FROM reporting_ledger_entries")
        c.execute("DELETE FROM reporting_vouchers")
        c.execute("DELETE FROM reporting_sync_history")
        c.execute("DELETE FROM cost_centres")
        c.execute("DELETE FROM ledgers")
        
        # Setup master ledgers needed for tests
        c.execute("INSERT INTO ledgers (name, parent) VALUES ('Sales Account 1', 'Sales Accounts')")
        c.execute("INSERT INTO ledgers (name, parent) VALUES ('Sales Return Account', 'Sales Accounts')")
        c.execute("INSERT INTO ledgers (name, parent) VALUES ('Output CGST', 'Duties & Taxes')")
        c.execute("INSERT INTO ledgers (name, parent) VALUES ('Customer X', 'Sundry Debtors')")
        conn.commit()
    yield

@pytest.mark.anyio
@patch('backend.services.tally_reporting_sync.tally_transport.post', new_callable=AsyncMock)
async def test_calculate_sales(mock_post):
    mock_resp = MagicMock()
    mock_resp.text = MOCK_XML
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    # Perform Sync
    await fetch_and_store_vouchers("20230901", "20230930")

    # Test Combined Sales
    # V1 (Sales): 1500
    # V2 (Return): -200
    # V3 (Unallocated): 300
    # Total Expected = 1600. Output CGST (150) should be ignored.
    combined = calculate_sales("20230901", "20230930")
    assert combined == 1600.0

    # Test Store A Isolated
    # V1: 1000
    # V2: -200
    # Expected = 800
    store_a = calculate_sales("20230901", "20230930", "Store A")
    assert store_a == 800.0

    # Test Store B Isolated
    # V1: 500
    # Expected = 500
    store_b = calculate_sales("20230901", "20230930", "Store B")
    assert store_b == 500.0

    # Test Unallocated
    # V3: 300
    # Expected = 300
    unallocated = calculate_sales("20230901", "20230930", "Unallocated")
    assert unallocated == 300.0

    # Ensure Store A + Store B + Unallocated = Combined
    assert (store_a + store_b + unallocated) == combined

def test_data_completeness():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO reporting_sync_history (start_date, end_date, synced_at) VALUES ('20230901', '20230930', 'now')")
        conn.commit()

    # Inside synced range
    assert check_data_completeness("20230905", "20230910") is True
    # Exactly matching range
    assert check_data_completeness("20230901", "20230930") is True
    # Outside synced range
    assert check_data_completeness("20230801", "20230831") is False
    # Partially outside synced range
    assert check_data_completeness("20230915", "20231015") is False

def test_previous_period():
    # 7 Days
    prev_s, prev_e = get_previous_period("20230908", "20230914")
    assert prev_s == "20230901"
    assert prev_e == "20230907"

    # Calendar Month
    prev_s, prev_e = get_previous_period("20231001", "20231031")
    assert prev_s == "20230901"
    assert prev_e == "20230930"
    
    # Leap Year Month
    prev_s, prev_e = get_previous_period("20240301", "20240331")
    assert prev_s == "20240201"
    assert prev_e == "20240229"

    # Quarter
    prev_s, prev_e = get_previous_period("20230401", "20230630")
    assert prev_s == "20230101"
    assert prev_e == "20230331"

    # FY
    prev_s, prev_e = get_previous_period("20230401", "20240331")
    assert prev_s == "20220401"
    assert prev_e == "20230331"

    # Custom period (5 days)
    prev_s, prev_e = get_previous_period("20230910", "20230914") # 5 days
    assert prev_s == "20230905"
    assert prev_e == "20230909"
