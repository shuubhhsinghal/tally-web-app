import pytest
from unittest.mock import patch, MagicMock
from backend.services.extraction_v4.extraction_engine import process_invoice_v4

# Dummy extracted data generator to easily simulate validation passing/failing
def make_mock_data(row_count=2, gt=200, items_val=200, is_pdf=False, subtotal=None, gst=None, items_count=None):
    return {
        "physical_row_count": row_count,
        "grand_total_printed": gt,
        "subtotal_printed": subtotal,
        "cgst_printed": gst / 2 if gst else None,
        "sgst_printed": gst / 2 if gst else None,
        "igst_printed": None,
        "tax_type": "local",
        "subtotal_basis": "exclusive" if subtotal else "unknown",
        "items": [
            {
                "name": "Item",
                "qty": 1,
                "line_amount": items_val / row_count,
                "amount_candidates": [{"header": "Total", "value": items_val / row_count}]
            }
        ] * (items_count if items_count is not None else row_count),
        "_is_pdf_mock": is_pdf
    }

@pytest.fixture
def mock_core():
    with patch("backend.services.extraction_v4.extraction_engine._run_core_extraction") as mock:
        yield mock

@pytest.fixture
def mock_pdf():
    with patch("backend.services.extraction_v4.extraction_engine.normalize_to_pdf") as mock:
        yield mock

def test_jpeg_passes_pdf_not_called(mock_core, mock_pdf):
    # JPEG math matches perfectly
    mock_core.return_value = (make_mock_data(row_count=2, gt=200, items_val=200), {})
    
    res = process_invoice_v4([b"fake_jpeg"])
    
    assert mock_core.call_count == 1
    assert mock_pdf.call_count == 0
    assert res["extraction_source"] == "jpeg"

def test_jpeg_row_count_fails_pdf_called(mock_core, mock_pdf):
    # JPEG has wrong row count (expected 3, got 2 items)
    mock_jpeg = make_mock_data(row_count=3, gt=200, items_val=200, items_count=2)
    mock_pdf_result = make_mock_data(row_count=3, gt=200, items_val=200)
    
    mock_core.side_effect = [(mock_jpeg, {}), (mock_pdf_result, {})]
    mock_pdf.return_value = (b"%PDF-fake", None, None)
    
    res = process_invoice_v4([b"fake_jpeg"])
    
    assert mock_core.call_count == 2
    assert mock_pdf.call_count == 1
    assert res["extraction_source"] == "pdf_fallback"

def test_jpeg_grand_total_mismatch_pdf_called(mock_core, mock_pdf):
    # JPEG has math mismatch (printed 300, extracted items sum to 200)
    mock_jpeg = make_mock_data(row_count=2, gt=300, items_val=200)
    mock_pdf_result = make_mock_data(row_count=2, gt=300, items_val=300)
    
    mock_core.side_effect = [(mock_jpeg, {}), (mock_pdf_result, {})]
    mock_pdf.return_value = (b"%PDF-fake", None, None)
    
    res = process_invoice_v4([b"fake_jpeg"])
    
    assert mock_core.call_count == 2
    assert mock_pdf.call_count == 1
    assert res["extraction_source"] == "pdf_fallback"

def test_jpeg_subtotal_mismatch_pdf_called(mock_core, mock_pdf):
    # JPEG has math mismatch on subtotal
    mock_jpeg = make_mock_data(row_count=2, gt=200, items_val=200, subtotal=150)
    mock_pdf_result = make_mock_data(row_count=2, gt=200, items_val=200, subtotal=200)
    
    mock_core.side_effect = [(mock_jpeg, {}), (mock_pdf_result, {})]
    mock_pdf.return_value = (b"%PDF-fake", None, None)
    
    res = process_invoice_v4([b"fake_jpeg"])
    
    assert mock_core.call_count == 2
    assert mock_pdf.call_count == 1
    assert res["extraction_source"] == "pdf_fallback"

def test_jpeg_gst_mismatch_pdf_called(mock_core, mock_pdf):
    mock_jpeg = make_mock_data(row_count=2, gt=220, items_val=200, gst=20)
    mock_pdf_result = make_mock_data(row_count=2, gt=200, items_val=200)
    
    mock_core.side_effect = [(mock_jpeg, {}), (mock_pdf_result, {})]
    mock_pdf.return_value = (b"%PDF-fake", None, None)
    
    res = process_invoice_v4([b"fake_jpeg"])
    
    assert mock_core.call_count == 2
    assert mock_pdf.call_count == 1
    assert res["extraction_source"] == "pdf_fallback"

def test_jpeg_fails_pdf_passes_pdf_selected(mock_core, mock_pdf):
    mock_jpeg = make_mock_data(row_count=2, gt=300, items_val=200)
    mock_pdf_result = make_mock_data(row_count=2, gt=300, items_val=300)
    
    mock_core.side_effect = [(mock_jpeg, {}), (mock_pdf_result, {})]
    mock_pdf.return_value = (b"%PDF-fake", None, None)
    
    res = process_invoice_v4([b"fake_jpeg"])
    assert res["extraction_source"] == "pdf_fallback"

def test_jpeg_fails_pdf_fails_jpeg_selected(mock_core, mock_pdf):
    mock_jpeg = make_mock_data(row_count=2, gt=300, items_val=200)
    mock_pdf_result = make_mock_data(row_count=2, gt=300, items_val=100)
    
    mock_core.side_effect = [(mock_jpeg, {}), (mock_pdf_result, {})]
    mock_pdf.return_value = (b"%PDF-fake", None, None)
    
    res = process_invoice_v4([b"fake_jpeg"])
    assert res["extraction_source"] == "jpeg"

def test_missing_grand_total_does_not_fail(mock_core, mock_pdf):
    mock_jpeg = make_mock_data(row_count=2, gt=None, items_val=200)
    
    mock_core.return_value = (mock_jpeg, {})
    
    res = process_invoice_v4([b"fake_jpeg"])
    
    assert mock_core.call_count == 1
    assert mock_pdf.call_count == 0
    assert res["extraction_source"] == "jpeg"

def test_multipage_fallback_produces_one_pdf(mock_core, mock_pdf):
    mock_jpeg = make_mock_data(row_count=2, gt=300, items_val=200)
    mock_pdf_result = make_mock_data(row_count=2, gt=300, items_val=300)
    
    mock_core.side_effect = [(mock_jpeg, {}), (mock_pdf_result, {})]
    mock_pdf.return_value = (b"%PDF-fake", None, None)
    
    res = process_invoice_v4([b"fake_pg1", b"fake_pg2"])
    
    mock_pdf.assert_called_once_with([b"fake_pg1", b"fake_pg2"])
    
    assert len(mock_core.call_args_list[1][0][0]) == 1
    assert mock_core.call_args_list[1][0][0][0] == b"%PDF-fake"

def test_native_pdf_skips_fallback(mock_core, mock_pdf):
    mock_native_pdf = make_mock_data(row_count=2, gt=300, items_val=200)
    mock_core.return_value = (mock_native_pdf, {})
    
    res = process_invoice_v4([b"%PDF-real-pdf"])
    
    assert mock_pdf.call_count == 0
    assert mock_core.call_count == 1
    assert res["extraction_source"] == "jpeg"
