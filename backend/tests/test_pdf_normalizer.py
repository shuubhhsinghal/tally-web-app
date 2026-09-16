import pytest
import os
import cv2
import numpy as np
from PIL import Image
import io
from backend.services.pdf_normalizer import normalize_to_pdf

def create_mock_raster(format="JPEG", width=100, height=100):
    img = np.full((height, width, 3), (255, 255, 255), dtype=np.uint8)
    cv2.putText(img, 'Test', (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    pil_img = Image.fromarray(img)
    buffer = io.BytesIO()
    pil_img.save(buffer, format=format)
    return buffer.getvalue()

def create_mock_pdf():
    pdf_bytes_io = io.BytesIO()
    img = Image.new('RGB', (100, 100), color = 'white')
    img.save(pdf_bytes_io, format="PDF")
    return pdf_bytes_io.getvalue()

def test_single_jpeg_to_pdf():
    jpeg_bytes = create_mock_raster("JPEG")
    res_bytes, mime, suffix = normalize_to_pdf([jpeg_bytes])
    assert mime == "application/pdf"
    assert suffix == ".pdf"
    assert res_bytes.startswith(b"%PDF")

def test_multiple_rasters_to_single_pdf():
    img1 = create_mock_raster("JPEG")
    img2 = create_mock_raster("PNG")
    res_bytes, mime, suffix = normalize_to_pdf([img1, img2])
    assert mime == "application/pdf"
    assert suffix == ".pdf"
    assert res_bytes.startswith(b"%PDF")

def test_existing_pdf_unchanged():
    pdf_bytes = create_mock_pdf()
    res_bytes, mime, suffix = normalize_to_pdf([pdf_bytes])
    assert mime == "application/pdf"
    assert suffix == ".pdf"
    assert res_bytes == pdf_bytes

def test_empty_images():
    with pytest.raises(ValueError):
        normalize_to_pdf([])
