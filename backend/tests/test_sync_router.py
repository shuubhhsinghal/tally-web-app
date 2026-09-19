import os
from unittest.mock import patch, AsyncMock

os.environ["TESTING"] = "true"

from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


@patch('backend.routers.sync.async_sync_vouchers', new_callable=AsyncMock)
@patch('backend.routers.sync.async_sync_cost_centres', new_callable=AsyncMock)
@patch('backend.routers.sync.flush_offline_queue')
def test_sync_queue_reports_partial_failure_on_failed_months(mock_flush, mock_cc, mock_vouchers):
    mock_flush.return_value = (3, 0)
    mock_cc.return_value = 2
    mock_vouchers.return_value = {"vouchers_count": 10, "failed_months": ["2026-09"]}

    res = client.post("/api/sync/sync-queue")
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "partial_failure"
    assert data["failed_months"] == ["2026-09"]
    assert data["reporting_sync_error"] is None
    assert "2026-09" in data["message"]


@patch('backend.routers.sync.async_sync_vouchers', new_callable=AsyncMock)
@patch('backend.routers.sync.async_sync_cost_centres', new_callable=AsyncMock)
@patch('backend.routers.sync.flush_offline_queue')
def test_sync_queue_reports_success_when_nothing_failed(mock_flush, mock_cc, mock_vouchers):
    mock_flush.return_value = (5, 0)
    mock_cc.return_value = 2
    mock_vouchers.return_value = {"vouchers_count": 10, "failed_months": []}

    res = client.post("/api/sync/sync-queue")
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "success"
    assert data["failed_months"] == []
    assert data["reporting_sync_error"] is None


@patch('backend.routers.sync.async_sync_vouchers', new_callable=AsyncMock)
@patch('backend.routers.sync.async_sync_cost_centres', new_callable=AsyncMock)
@patch('backend.routers.sync.flush_offline_queue')
def test_sync_queue_reports_partial_failure_on_reporting_exception(mock_flush, mock_cc, mock_vouchers):
    mock_flush.return_value = (1, 0)
    mock_cc.side_effect = Exception("Tally disconnected mid-sync")

    res = client.post("/api/sync/sync-queue")
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "partial_failure"
    assert data["reporting_sync_error"] == "Tally disconnected mid-sync"
    assert data["failed_months"] == []
