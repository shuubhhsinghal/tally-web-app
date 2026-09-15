import os
import pytest
from unittest.mock import patch, MagicMock

# Set testing environment so database.py uses test_tally_sync.db
os.environ["TESTING"] = "true"

from backend.database import init_db, get_db
from backend.services.tally_reporting_sync import fetch_and_store_cost_centres, fetch_and_store_vouchers

# Dummy XMLs
COST_CENTRE_XML = """<ENVELOPE>
  <BODY>
    <DATA>
      <COLLECTION>
        <COSTCENTRE>
          <NAME>Store A</NAME>
          <PARENT>Primary</PARENT>
        </COSTCENTRE>
        <COSTCENTRE>
          <NAME>Store B</NAME>
          <PARENT>Primary</PARENT>
        </COSTCENTRE>
      </COLLECTION>
    </DATA>
  </BODY>
</ENVELOPE>"""

VOUCHER_XML = """<ENVELOPE>
  <BODY>
    <DATA>
      <COLLECTION>
        <VOUCHER VCHKEY="vch-guid-123">
          <GUID>vch-guid-123</GUID>
          <DATE>20230401</DATE>
          <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
          <VOUCHERNUMBER>SAL/001</VOUCHERNUMBER>
          <PARTYLEDGERNAME>Cash</PARTYLEDGERNAME>
          <NARRATION>Test Sale</NARRATION>
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>Sales Account</LEDGERNAME>
            <AMOUNT>-100.00</AMOUNT>
            <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
            <CATEGORYALLOCATIONS.LIST>
              <COSTCENTREALLOCATIONS.LIST>
                <NAME>Store A</NAME>
                <AMOUNT>-100.00</AMOUNT>
              </COSTCENTREALLOCATIONS.LIST>
            </CATEGORYALLOCATIONS.LIST>
          </ALLLEDGERENTRIES.LIST>
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>Cash</LEDGERNAME>
            <AMOUNT>100.00</AMOUNT>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          </ALLLEDGERENTRIES.LIST>
          <ALLINVENTORYENTRIES.LIST>
            <STOCKITEMNAME>Item 1</STOCKITEMNAME>
            <BILLEDQTY>10.00 Nos</BILLEDQTY>
            <AMOUNT>-100.00</AMOUNT>
            <RATE>10.00</RATE>
            <BATCHALLOCATIONS.LIST>
              <GODOWNNAME>Main Location</GODOWNNAME>
            </BATCHALLOCATIONS.LIST>
          </ALLINVENTORYENTRIES.LIST>
        </VOUCHER>
      </COLLECTION>
    </DATA>
  </BODY>
</ENVELOPE>"""

@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    yield
    # Cleanup after test
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM reporting_inventory_entries")
        c.execute("DELETE FROM reporting_cost_centre_allocations")
        c.execute("DELETE FROM reporting_ledger_entries")
        c.execute("DELETE FROM reporting_vouchers")
        c.execute("DELETE FROM cost_centres")
        conn.commit()

@patch('backend.services.tally_reporting_sync.requests.post')
def test_sync_cost_centres(mock_post):
    mock_resp = MagicMock()
    mock_resp.text = COST_CENTRE_XML
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    count = fetch_and_store_cost_centres("http://localhost:9000")
    assert count == 2

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM cost_centres ORDER BY name")
        rows = c.fetchall()
        assert len(rows) == 2
        assert rows[0]['name'] == 'Store A'
        assert rows[1]['name'] == 'Store B'

@patch('backend.services.tally_reporting_sync.requests.post')
def test_sync_vouchers(mock_post):
    mock_resp = MagicMock()
    mock_resp.text = VOUCHER_XML
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    count = fetch_and_store_vouchers("20230401", "20230430", "http://localhost:9000")
    assert count == 1

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM reporting_vouchers")
        vouchers = c.fetchall()
        assert len(vouchers) == 1
        assert vouchers[0]['tally_guid'] == 'vch-guid-123'
        assert vouchers[0]['voucher_type'] == 'Sales'
        
        c.execute("SELECT * FROM reporting_ledger_entries ORDER BY id")
        ledgers = c.fetchall()
        assert len(ledgers) == 2
        assert ledgers[0]['ledger_name'] == 'Sales Account'
        assert ledgers[0]['amount'] == -100.0
        assert ledgers[0]['is_deemed_positive'] == False
        
        c.execute("SELECT * FROM reporting_cost_centre_allocations")
        cc_alloc = c.fetchall()
        assert len(cc_alloc) == 1
        assert cc_alloc[0]['cost_centre_name'] == 'Store A'
        
        c.execute("SELECT * FROM reporting_inventory_entries")
        inv = c.fetchall()
        assert len(inv) == 1
        assert inv[0]['stock_item_name'] == 'Item 1'
        assert inv[0]['billed_qty'] == 10.0
        assert inv[0]['godown_name'] == 'Main Location'
        assert inv[0]['amount'] == -100.0

    # Test Idempotency (Syncing the same XML again)
    count2 = fetch_and_store_vouchers("20230401", "20230430", "http://localhost:9000")
    assert count2 == 1

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as cnt FROM reporting_vouchers")
        assert c.fetchone()['cnt'] == 1
        c.execute("SELECT COUNT(*) as cnt FROM reporting_ledger_entries")
        assert c.fetchone()['cnt'] == 2
        c.execute("SELECT COUNT(*) as cnt FROM reporting_inventory_entries")
        assert c.fetchone()['cnt'] == 1
