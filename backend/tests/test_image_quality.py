import pytest
import os
import cv2
import numpy as np
from unittest.mock import patch
from backend.services.image_quality import assess_and_enhance_image

def create_mock_image_bytes(width, height, is_blurry=False, color=(255, 255, 255)):
    """Helper to create synthetic image bytes in memory."""
    img = np.full((height, width, 3), color, dtype=np.uint8)
    
    # Add some text/edges so Laplacian variance isn't exactly zero
    cv2.putText(img, 'Invoice', (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2, cv2.LINE_AA)
    
    if is_blurry:
        img = cv2.GaussianBlur(img, (25, 25), 0)
        
    _, buffer = cv2.imencode('.jpg', img)
    return buffer.tobytes()

def test_assess_good_image():
    # Large, sharp image (2000x2000)
    image_bytes = create_mock_image_bytes(2000, 2000, is_blurry=False)
    
    with patch("cv2.Laplacian") as mock_laplacian:
        mock_var = mock_laplacian.return_value
        mock_var.var.return_value = 200.0  # Sharp
        
        out_bytes, status, metrics = assess_and_enhance_image(image_bytes)
        
        assert status == "GOOD"
        assert not metrics["preprocessed"]
        assert out_bytes == image_bytes

def test_assess_unusable_image_tiny():
    # Impossibly tiny image (100x100)
    image_bytes = create_mock_image_bytes(100, 100)
    
    out_bytes, status, metrics = assess_and_enhance_image(image_bytes)
    
    assert status == "UNUSABLE"
    assert out_bytes == image_bytes

def test_assess_borderline_image_blurry():
    # Large but very blurry image
    image_bytes = create_mock_image_bytes(1500, 1500, is_blurry=True)
    
    # Force the laplacian to be exactly within BORDERLINE range
    with patch("cv2.Laplacian") as mock_laplacian:
        mock_var = mock_laplacian.return_value
        mock_var.var.return_value = 50.0  # 15 < 50 < 85 (BORDERLINE)
        
        out_bytes, status, metrics = assess_and_enhance_image(image_bytes)
        
        assert status == "BORDERLINE"
        assert metrics["preprocessed"] == True
        assert out_bytes != image_bytes
        assert "clahe_lightness" in metrics["operations"]
        assert "unsharp_mask" in metrics["operations"]

def test_assess_borderline_image_small():
    # Small but sharp image (500x500) -> will be upscaled
    image_bytes = create_mock_image_bytes(500, 500, is_blurry=False)
    
    with patch("cv2.Laplacian") as mock_laplacian:
        mock_var = mock_laplacian.return_value
        mock_var.var.return_value = 200.0  # Sharp, but size makes it borderline
        
        out_bytes, status, metrics = assess_and_enhance_image(image_bytes)
        
        assert status == "BORDERLINE"
        assert metrics["preprocessed"] == True
        assert out_bytes != image_bytes
        
        # Should have upscaled to 1200 (scale=2.4)
        assert any(op.startswith("upscale_") for op in metrics["operations"])
        assert metrics["final_width"] == 1200 or metrics["final_height"] == 1200

def test_assess_unusable_image_blurry_and_small():
    # Small AND blurry (var=10.0) -> UNUSABLE
    image_bytes = create_mock_image_bytes(500, 500, is_blurry=True)
    
    with patch("cv2.Laplacian") as mock_laplacian:
        mock_var = mock_laplacian.return_value
        mock_var.var.return_value = 10.0
        
        out_bytes, status, metrics = assess_and_enhance_image(image_bytes)
        
        assert status == "UNUSABLE"
        assert out_bytes == image_bytes

def test_assess_pdf_bypass():
    # Non-image bytes should return None from cv2.imdecode
    fake_pdf_bytes = b"%PDF-1.4...fake...pdf..."
    
    out_bytes, status, metrics = assess_and_enhance_image(fake_pdf_bytes)
    
    assert status == "GOOD"
    assert metrics.get("bypassed") == True
    assert out_bytes == fake_pdf_bytes
