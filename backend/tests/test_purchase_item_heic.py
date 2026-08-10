import sys
import os
import io
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from backend.main import app

client = TestClient(app)

@patch("backend.services.extraction_engine.process_invoice")
@patch("backend.routers.purchase_item.normalize_uploaded_invoice")
def test_heic_upload_calls_normalizer(mock_normalizer, mock_process_invoice):
    mock_process_invoice.return_value = {
        "supplier_name": "Test Supplier",
        "invoice_number": "123",
        "items": []
    }
    
    mock_normalizer.return_value = b"fake_jpeg_bytes"
    
    with patch("backend.routers.purchase_item.get_all_ledgers", return_value=[]), \
         patch("backend.routers.purchase_item.get_all_stock_items", return_value=[]), \
         patch("backend.routers.purchase_item.get_all_uoms", return_value=[]), \
         patch("backend.routers.purchase_item.get_all_aliases", return_value={}), \
         patch("backend.routers.purchase_item.get_db"):
         
        # We need to mock genai.Client as well since it tries to initialize inside extract
        with patch("backend.routers.purchase_item.genai.Client"):
            os.environ["GEMINI_API_KEY"] = "fake_key"
            
            response = client.post(
                "/api/purchase-item/extract",
                files={"file": ("test.HEIC", b"fake_heic", "image/heic")}
            )
            
    assert response.status_code == 200
    
    # Check that normalizer was called with expected arguments
    mock_normalizer.assert_called_once()
    args, kwargs = mock_normalizer.call_args
    assert args[0] == b"fake_heic"
    assert args[1] == "test.HEIC"
    assert args[2] == "image/heic"
    
    # Check that process_invoice was called with the result of the normalizer
    assert mock_process_invoice.called
    args, kwargs = mock_process_invoice.call_args
    assert args[0] == b"fake_jpeg_bytes"
