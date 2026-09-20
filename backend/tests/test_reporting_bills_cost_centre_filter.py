import pytest
from backend.database import get_db
from backend.services.reporting_sales_service import get_sales_bills
from backend.services.reporting_purchase_service import get_supplier_purchase_analysis, get_purchase_bills

# Regression coverage for the cost_centre SQL-parameterization fix in
# get_sales_bills / get_supplier_purchase_analysis / get_purchase_bills --
# these used to string-interpolate cost_centre directly into the query text;
# this locks in that Combined / named-store / Unallocated filtering still
# behaves correctly now that it's bound as an ordinary parameter.


@pytest.fixture(autouse=True)
def setup_db():
    with get_db() as conn:
        conn.execute("DELETE FROM reporting_ledger_entries")
        conn.execute("DELETE FROM reporting_cost_centre_allocations")
        conn.execute("DELETE FROM reporting_vouchers")
        conn.execute("DELETE FROM ledgers WHERE name IN ('Sales Account 1', 'Purchase Account 1')")
        conn.execute("INSERT INTO ledgers (name, parent) VALUES ('Sales Account 1', 'Sales Accounts')")
        conn.execute("INSERT INTO ledgers (name, parent) VALUES ('Purchase Account 1', 'Purchase Accounts')")

        # Sales voucher: allocated to Store A, 1000 credited to Sales Account 1
        conn.execute("INSERT INTO reporting_vouchers (id, tally_guid, date, voucher_number, voucher_type, party_ledger_name) VALUES (1, 'g1', '20260115', 'S1', 'Sales', 'Customer X')")
        conn.execute("INSERT INTO reporting_ledger_entries (id, voucher_id, ledger_name, amount, is_deemed_positive) VALUES (1, 1, 'Sales Account 1', 1000.0, 0)")
        conn.execute("INSERT INTO reporting_cost_centre_allocations (ledger_entry_id, cost_centre_name, amount) VALUES (1, 'Store A', 1000.0)")

        # Sales voucher: unallocated, 500 credited
        conn.execute("INSERT INTO reporting_vouchers (id, tally_guid, date, voucher_number, voucher_type, party_ledger_name) VALUES (2, 'g2', '20260116', 'S2', 'Sales', 'Customer Y')")
        conn.execute("INSERT INTO reporting_ledger_entries (id, voucher_id, ledger_name, amount, is_deemed_positive) VALUES (2, 2, 'Sales Account 1', 500.0, 0)")

        # Purchase voucher: allocated to Store A, 800 debited (positive raw amount per Tally XML sign convention for a Debit)
        conn.execute("INSERT INTO reporting_vouchers (id, tally_guid, date, voucher_number, voucher_type, party_ledger_name) VALUES (3, 'g3', '20260117', 'P1', 'Purchase', 'Supplier X')")
        conn.execute("INSERT INTO reporting_ledger_entries (id, voucher_id, ledger_name, amount, is_deemed_positive) VALUES (3, 3, 'Purchase Account 1', -800.0, 1)")
        conn.execute("INSERT INTO reporting_cost_centre_allocations (ledger_entry_id, cost_centre_name, amount) VALUES (3, 'Store A', -800.0)")

        conn.commit()
    yield
    with get_db() as conn:
        conn.execute("DELETE FROM reporting_ledger_entries")
        conn.execute("DELETE FROM reporting_cost_centre_allocations")
        conn.execute("DELETE FROM reporting_vouchers")
        conn.execute("DELETE FROM ledgers WHERE name IN ('Sales Account 1', 'Purchase Account 1')")
        conn.commit()


def test_sales_bills_combined_includes_all():
    result = get_sales_bills("20260101", "20260131")
    assert result["total_count"] == 2
    assert {b["net_sales"] for b in result["bills"]} == {1000.0, 500.0}


def test_sales_bills_named_store_filter():
    result = get_sales_bills("20260101", "20260131", cost_centre="Store A")
    assert result["total_count"] == 1
    assert result["bills"][0]["net_sales"] == 1000.0
    assert result["bills"][0]["store_name"] == "Store A"


def test_sales_bills_unallocated_filter():
    result = get_sales_bills("20260101", "20260131", cost_centre="Unallocated")
    assert result["total_count"] == 1
    assert result["bills"][0]["net_sales"] == 500.0


def test_supplier_purchase_analysis_named_store_filter():
    result = get_supplier_purchase_analysis("20260101", "20260131", cost_centre="Store A")
    assert result["total_count"] == 1
    assert result["suppliers"][0]["supplier_name"] == "Supplier X"
    assert result["suppliers"][0]["net_purchases"] == 800.0


def test_purchase_bills_combined_and_store_filter():
    combined = get_purchase_bills("20260101", "20260131")
    assert combined["total_count"] == 1
    assert combined["bills"][0]["store_name"] == "Combined"

    store_a = get_purchase_bills("20260101", "20260131", cost_centre="Store A")
    assert store_a["total_count"] == 1
    assert store_a["bills"][0]["net_purchases"] == 800.0
    assert store_a["bills"][0]["store_name"] == "Store A"
