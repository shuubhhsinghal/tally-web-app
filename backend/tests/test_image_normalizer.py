import sys
import os
import io
import pytest
from PIL import Image
from fastapi import HTTPException
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from backend.services.image_normalizer import normalize_uploaded_invoice

def test_bypass_jpg():
    b = b"fake_jpg_bytes"
    res = normalize_uploaded_invoice(b, "invoice.jpg", "image/jpeg")
    assert res == b

def test_bypass_png():
    b = b"fake_png_bytes"
    res = normalize_uploaded_invoice(b, "INVOICE.PNG", "image/png")
    assert res == b

def test_heic_detection_by_extension_corrupt():
    b = b"fake_corrupt_heic"
    with pytest.raises(HTTPException) as exc:
        normalize_uploaded_invoice(b, "IMG_123.HEIC", "application/octet-stream")
    assert exc.value.status_code == 400
    assert "Unable to read this HEIC/HEIF image" in exc.value.detail

def test_heic_detection_by_mime_corrupt():
    b = b"fake_corrupt_heic"
    with pytest.raises(HTTPException) as exc:
        normalize_uploaded_invoice(b, "blob", "IMAGE/HEIF")
    assert exc.value.status_code == 400
    assert "Unable to read this HEIC/HEIF image" in exc.value.detail

@patch("backend.services.image_normalizer.Image.open")
def test_successful_heic_conversion(mock_open):
    # Mock Image.open returning a valid PIL image
    mock_img = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
    
    # We must patch ImageOps.exif_transpose as well
    with patch("backend.services.image_normalizer.ImageOps.exif_transpose", return_value=mock_img):
        mock_open.return_value = mock_img
        
        b = b"valid_heic_bytes_mock"
        res = normalize_uploaded_invoice(b, "invoice.heic", "image/heic")
        
        # It should not return the original bytes
        assert res != b
        
        # Resulting bytes should be a valid JPEG (magic number FF D8 FF)
        assert res.startswith(b'\xff\xd8\xff')
