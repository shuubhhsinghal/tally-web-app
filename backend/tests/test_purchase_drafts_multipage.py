import os
import json
from unittest.mock import patch
from fastapi.testclient import TestClient
from PIL import Image
import io

from backend.main import app
from backend.database import get_db

client = TestClient(app)

def create_test_image(color="red", format="JPEG"):
    img = Image.new('RGB', (100, 100), color=color)
    buf = io.BytesIO()
    img.save(buf, format=format)
    return buf.getvalue()

def create_test_pdf():
    img1 = Image.new('RGB', (100, 100), color="red")
    img2 = Image.new('RGB', (100, 100), color="blue")
    buf = io.BytesIO()
    img1.save(buf, format="PDF", save_all=True, append_images=[img2])
    return buf.getvalue()

@patch("backend.routers.purchase_drafts.process_async_extraction")
def test_upload_single_image(mock_process):
    img_bytes = create_test_image()
    
    response = client.post(
        "/api/purchase-drafts/async-extract",
        files=[("files", ("page1.jpg", img_bytes, "image/jpeg"))]
    )
    
    assert response.status_code == 200
    draft_id = response.json()["id"]
    
    # Check what was saved to DB
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT image_path FROM purchase_drafts WHERE id = ?", (draft_id,))
        row = cursor.fetchone()
        assert row is not None
        image_path = row["image_path"]
        assert image_path.endswith(".jpg")
        
        # Verify file exists on disk
        filepath = os.path.join(os.getcwd(), "backend", image_path.lstrip("/"))
        assert os.path.exists(filepath)
        
    # Verify mock was called with correct data
    assert mock_process.called
    args, _ = mock_process.call_args
    assert args[0] == draft_id
    assert len(args[1]) == 1
    assert args[1][0][1] == "page1.jpg"

@patch("backend.routers.purchase_drafts.process_async_extraction")
def test_upload_single_pdf(mock_process):
    pdf_bytes = create_test_pdf()
    
    response = client.post(
        "/api/purchase-drafts/async-extract",
        files=[("files", ("invoice.pdf", pdf_bytes, "application/pdf"))]
    )
    
    assert response.status_code == 200
    draft_id = response.json()["id"]
    
    # Check DB
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT image_path FROM purchase_drafts WHERE id = ?", (draft_id,))
        image_path = cursor.fetchone()["image_path"]
        assert image_path.endswith(".pdf")
        
        filepath = os.path.join(os.getcwd(), "backend", image_path.lstrip("/"))
        assert os.path.exists(filepath)
        
    # Verify mock
    assert mock_process.called
    args, _ = mock_process.call_args
    assert len(args[1]) == 1
    assert args[1][0][1] == "invoice.pdf"

@patch("backend.routers.purchase_drafts.process_async_extraction")
def test_upload_multiple_images(mock_process):
    img1 = create_test_image("red")
    img2 = create_test_image("blue")
    img3 = create_test_image("green")
    
    response = client.post(
        "/api/purchase-drafts/async-extract",
        files=[
            ("files", ("page1.jpg", img1, "image/jpeg")),
            ("files", ("page2.png", img2, "image/png")),
            ("files", ("page3.jpg", img3, "image/jpeg")),
        ]
    )
    
    assert response.status_code == 200
    draft_id = response.json()["id"]
    
    # Check DB - it should be saved as a single PDF!
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT image_path FROM purchase_drafts WHERE id = ?", (draft_id,))
        image_path = cursor.fetchone()["image_path"]
        assert image_path.endswith(".pdf")
        
        filepath = os.path.join(os.getcwd(), "backend", image_path.lstrip("/"))
        assert os.path.exists(filepath)
        assert os.path.getsize(filepath) > 0
        
    # VERY IMPORTANT: Verify extraction receives ALL 3 original pages, unmodified
    assert mock_process.called
    args, _ = mock_process.call_args
    files_data = args[1]
    assert len(files_data) == 3
    assert files_data[0][1] == "page1.jpg"
    assert files_data[1][1] == "page2.png"
    assert files_data[2][1] == "page3.jpg"
