import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db, queue_operation, update_queue_status
from backend.services.reporting_sales_service import get_unsynced_sales, get_pending_sales_trend

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        conn.commit()
    yield
    with get_db() as conn:
        conn.execute("DELETE FROM offline_queue")
        conn.commit()


def _queue_sale(amount, tally_date, ledger="Cash Mahagun", cost_center=None, status=None):
    payload = {"ledger": ledger, "amount": amount, "tally_date": tally_date, "narration": ""}
    if cost_center:
        payload["cost_center"] = cost_center
    qid = queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", payload, f"Sales: {amount} from {ledger}")
    if status:
        update_queue_status(qid, status)
    return qid


def test_pending_sale_within_range_is_counted():
    _queue_sale(500.0, "20260115")
    result = get_unsynced_sales("20260101", "20260131")
    assert result["pending_count"] == 1
    assert result["pending_amount"] == 500.0
    assert result["failed_count"] == 0


def test_pending_sale_outside_range_is_excluded():
    _queue_sale(500.0, "20260215")
    result = get_unsynced_sales("20260101", "20260131")
    assert result["pending_count"] == 0
    assert result["pending_amount"] == 0.0


def test_synced_sale_is_excluded():
    qid = _queue_sale(500.0, "20260115")
    update_queue_status(qid, "SYNCED")
    result = get_unsynced_sales("20260101", "20260131")
    assert result["pending_count"] == 0


def test_failed_sale_is_counted_separately():
    _queue_sale(300.0, "20260115", status="FAILED")
    result = get_unsynced_sales("20260101", "20260131")
    assert result["pending_count"] == 0
    assert result["failed_count"] == 1
    assert result["failed_amount"] == 300.0


def test_cost_centre_filter_named_store():
    _queue_sale(500.0, "20260115", cost_center="Mahagun")
    _queue_sale(700.0, "20260116", cost_center="Gulshan")
    result = get_unsynced_sales("20260101", "20260131", cost_centre="Mahagun")
    assert result["pending_count"] == 1
    assert result["pending_amount"] == 500.0


def test_cost_centre_filter_unallocated():
    _queue_sale(500.0, "20260115", cost_center="Mahagun")
    _queue_sale(200.0, "20260116", cost_center=None)
    result = get_unsynced_sales("20260101", "20260131", cost_centre="Unallocated")
    assert result["pending_count"] == 1
    assert result["pending_amount"] == 200.0


def test_no_cost_centre_filter_includes_all_stores_combined():
    _queue_sale(500.0, "20260115", cost_center="Mahagun")
    _queue_sale(200.0, "20260116", cost_center=None)
    result = get_unsynced_sales("20260101", "20260131")
    assert result["pending_count"] == 2
    assert result["pending_amount"] == 700.0


def test_non_sales_queue_row_is_ignored():
    # A Payment/Transfer voucher must never be mistaken for a pending sale.
    queue_operation("POST_VOUCHER", "<ENVELOPE></ENVELOPE>", {"amount": 999.0}, "Transfer: 999.0 from Cash")
    result = get_unsynced_sales("20260101", "20260131")
    assert result["pending_count"] == 0
    assert result["pending_amount"] == 0.0


def test_sales_endpoint_includes_unsynced_sales_field():
    _queue_sale(500.0, "20260115")
    resp = client.get("/api/reporting/sales", params={"start_date": "20260101", "end_date": "20260131"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["unsynced_sales"]["pending_count"] == 1
    assert data["unsynced_sales"]["pending_amount"] == 500.0


def test_pending_sales_trend_excludes_delivery_uncertain():
    from backend.database import set_delivery_uncertain
    qid = _queue_sale(500.0, "20260115")
    set_delivery_uncertain(qid, True)

    result = get_pending_sales_trend("20260101", "20260131")
    assert result == []


def test_pending_sales_trend_groups_by_date_and_store():
    _queue_sale(500.0, "20260115", cost_center="Mahagun")
    _queue_sale(300.0, "20260115", cost_center="Mahagun")
    _queue_sale(200.0, "20260116", cost_center="Gulshan")

    result = get_pending_sales_trend("20260101", "20260131")
    by_date = {r["date"]: r for r in result}
    assert by_date["20260115"]["Mahagun"] == 800.0
    assert by_date["20260115"]["Combined"] == 800.0
    assert by_date["20260115"]["Combined_invoices"] == 2
    assert by_date["20260116"]["Gulshan"] == 200.0


def test_sales_endpoint_blends_pending_into_net_sales_total():
    _queue_sale(500.0, "20260115")
    resp = client.get("/api/reporting/sales", params={"start_date": "20260101", "end_date": "20260131"})
    data = resp.json()
    # No Tally-confirmed sales exist in this test DB, so confirmed=0 and the
    # blended total should equal exactly the pending amount.
    assert data["summary"]["net_sales_confirmed"] == 0.0
    assert data["summary"]["pending_amount"] == 500.0
    assert data["summary"]["net_sales"] == 500.0


def test_sales_endpoint_delivery_uncertain_sale_not_blended_into_total():
    from backend.database import set_delivery_uncertain
    qid = _queue_sale(500.0, "20260115")
    set_delivery_uncertain(qid, True)

    resp = client.get("/api/reporting/sales", params={"start_date": "20260101", "end_date": "20260131"})
    data = resp.json()
    assert data["summary"]["net_sales"] == 0.0
    # Still surfaced in the warning banner, just not added to the total.
    assert data["unsynced_sales"]["pending_count"] == 1


def test_sales_endpoint_trend_and_store_comparison_include_pending():
    _queue_sale(500.0, "20260115", cost_center="Mahagun")
    resp = client.get("/api/reporting/sales", params={"start_date": "20260101", "end_date": "20260131"})
    data = resp.json()

    trend_row = next(r for r in data["trend"] if r["date"] == "20260115")
    assert trend_row["Combined"] == 500.0
    assert trend_row["Mahagun"] == 500.0

    store_row = next(r for r in data["store_comparison"] if r["store_name"] == "Mahagun")
    assert store_row["net_sales"] == 500.0


def test_sales_endpoint_cost_centre_filter_applies_to_blended_total():
    _queue_sale(500.0, "20260115", cost_center="Mahagun")
    _queue_sale(700.0, "20260116", cost_center="Gulshan")

    resp = client.get("/api/reporting/sales", params={"start_date": "20260101", "end_date": "20260131", "cost_centre": "Mahagun"})
    data = resp.json()
    assert data["summary"]["pending_amount"] == 500.0
    assert data["summary"]["net_sales"] == 500.0
