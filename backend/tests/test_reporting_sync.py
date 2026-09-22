import os
import asyncio
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock, AsyncMock

# Set testing environment so database.py uses test_tally_sync.db
os.environ["TESTING"] = "true"

from backend.database import init_db, get_db
from backend.services.tally_reporting_sync import (
    fetch_and_store_cost_centres, fetch_and_store_vouchers,
    _get_default_sync_end_date, _get_current_fy_end, async_sync_vouchers,
)

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

@pytest.mark.anyio
@patch('backend.services.tally_reporting_sync.tally_transport.post', new_callable=AsyncMock)
async def test_sync_cost_centres(mock_post):
    mock_resp = MagicMock()
    mock_resp.text = COST_CENTRE_XML
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    count = await fetch_and_store_cost_centres()
    assert count == 2

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM cost_centres ORDER BY name")
        rows = c.fetchall()
        assert len(rows) == 2
        assert rows[0]['name'] == 'Store A'
        assert rows[1]['name'] == 'Store B'

@pytest.mark.anyio
@patch('backend.services.tally_reporting_sync.tally_transport.post', new_callable=AsyncMock)
async def test_sync_vouchers(mock_post):
    mock_resp = MagicMock()
    mock_resp.text = VOUCHER_XML
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    count = await fetch_and_store_vouchers("20230401", "20230430")
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
    count2 = await fetch_and_store_vouchers("20230401", "20230430")
    assert count2 == 1

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as cnt FROM reporting_vouchers")
        assert c.fetchone()['cnt'] == 1
        c.execute("SELECT COUNT(*) as cnt FROM reporting_ledger_entries")
        assert c.fetchone()['cnt'] == 2
        c.execute("SELECT COUNT(*) as cnt FROM reporting_inventory_entries")
        assert c.fetchone()['cnt'] == 1


def test_default_sync_end_date_caps_to_today_mid_fiscal_year():
    # "Today" is mid-fiscal-year (well before March 31) -> default end date
    # must be today, NOT the fiscal year end.
    with patch('backend.services.tally_reporting_sync.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 9, 19)
        result = _get_default_sync_end_date()
        assert result == "20260919"


def test_default_sync_end_date_never_exceeds_fiscal_year_end():
    result = _get_default_sync_end_date()
    assert result <= _get_current_fy_end()


def test_async_sync_vouchers_default_end_date_is_today():
    # Called with no args, async_sync_vouchers must pass TODAY as end_date
    # to fetch_and_store_vouchers, not the (possibly future) fiscal year end.
    with patch('backend.services.tally_reporting_sync.fetch_and_store_vouchers') as mock_fetch, \
         patch('backend.services.tally_reporting_sync.async_sync_monthly_stock', new_callable=AsyncMock) as mock_stock:
        mock_fetch.return_value = 0
        mock_stock.return_value = []

        asyncio.run(async_sync_vouchers())

        called_start, called_end = mock_fetch.call_args[0]
        assert called_end == datetime.now().strftime("%Y%m%d")


def test_async_sync_vouchers_explicit_end_date_not_capped():
    # An explicitly-provided end_date (even one in the future) must pass
    # through unchanged -- capping only applies to the default/no-args case.
    with patch('backend.services.tally_reporting_sync.fetch_and_store_vouchers') as mock_fetch, \
         patch('backend.services.tally_reporting_sync.async_sync_monthly_stock', new_callable=AsyncMock) as mock_stock:
        mock_fetch.return_value = 0
        mock_stock.return_value = []

        future_end = "20270331"
        asyncio.run(async_sync_vouchers(start_date="20260401", end_date=future_end))

        called_start, called_end = mock_fetch.call_args[0]
        assert called_start == "20260401"
        assert called_end == future_end
