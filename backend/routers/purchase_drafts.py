import os
import json
import uuid
import datetime
import shutil
from typing import Optional
from fastapi import APIRouter, File, UploadFile, Form, HTTPException
from backend.database import get_db

router = APIRouter()

UPLOAD_DIR = os.path.join(os.getcwd(), "backend", "uploads", "drafts")
os.makedirs(UPLOAD_DIR, exist_ok=True)

from fastapi import BackgroundTasks
from backend.services.image_normalizer import normalize_uploaded_invoice
import traceback

def process_async_extraction(draft_id: str, files_data: list):
    from backend.services.extraction_v4.extraction_engine import process_invoice_v4
    from backend.services.extraction_v4.text_mapper import map_items_to_tally, map_supplier_to_tally
    
    try:
        f_bytes_list = []
        for file_bytes, filename, content_type in files_data:
            f_bytes_list.append(normalize_uploaded_invoice(file_bytes, filename, content_type))
            
        extracted_data = process_invoice_v4(f_bytes_list)
        
        mapped_items = map_items_to_tally(extracted_data.get("items", []))
        extracted_data["items"] = mapped_items
        
        mapped_supplier = map_supplier_to_tally(extracted_data.get("supplier", ""))
        extracted_data["supplier_mapped"] = mapped_supplier
        
        draft_data = {
            "invoice": extracted_data,
            "items": extracted_data.get("items", []),
            "v4RawData": {"extracted_data": extracted_data},
            "v4Config": {
                "selected_amount_header": None,
                "gst_basis_override": None,
                "gst_recording_method": "included_in_rate"
            }
        }
        
        now = datetime.datetime.now().isoformat()
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE purchase_drafts 
                SET status = 'READY', 
                    draft_data = ?, 
                    updated_at = ?,
                    supplier_name = ?,
                    invoice_number = ?,
                    invoice_date = ?,
                    grand_total = ?,
                    item_count = ?
                WHERE id = ?
            """, (
                json.dumps(draft_data), 
                now, 
                extracted_data.get("supplier"),
                extracted_data.get("invoice_number"),
                extracted_data.get("date"),
                extracted_data.get("grand_total_printed"),
                len(extracted_data.get("items", [])),
                draft_id
            ))
            conn.commit()
            
    except Exception as e:
        print(f"Async extraction failed: {e}")
        traceback.print_exc()
        now = datetime.datetime.now().isoformat()
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE purchase_drafts 
                SET status = 'FAILED', draft_data = ?, updated_at = ?
                WHERE id = ?
            """, (json.dumps({"error": str(e)}), now, draft_id))
            conn.commit()

def enqueue_draft_extraction(background_tasks: BackgroundTasks, files_data: list) -> str:
    """
    Shared logic to create a draft, save the file, and queue background extraction.
    files_data: list of tuples (file_bytes, filename, content_type)
    Returns: draft_id
    """
    draft_id = str(uuid.uuid4())
    first_file_bytes, first_filename, first_content_type = files_data[0]
    
    ext = os.path.splitext(first_filename)[1]
    if not ext:
        ext = ".pdf" if first_content_type == "application/pdf" else ".jpg"

    # Normalize HEIC to JPEG for browser preview
    first_file_bytes = normalize_uploaded_invoice(first_file_bytes, first_filename, first_content_type)
    if ext.lower() in [".heic", ".heif"]:
        ext = ".jpg"
        
    filename = f"{draft_id}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)
    
    # We only save the first page for the preview image_path
    with open(filepath, "wb") as buffer:
        buffer.write(first_file_bytes)

    # Update the files_data with normalized bytes for the first file
    files_data[0] = (first_file_bytes, first_filename, first_content_type)

    image_path = f"/uploads/drafts/{filename}"
    now = datetime.datetime.now().isoformat()
    
    initial_data = {}

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO purchase_drafts 
            (id, supplier_name, invoice_number, invoice_date, grand_total, item_count, status, draft_data, image_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            draft_id, "Unknown", "", "", 0.0, 0,
            'PROCESSING', json.dumps(initial_data), image_path, now, now
        ))
        conn.commit()

    background_tasks.add_task(process_async_extraction, draft_id, files_data)
    
    return draft_id

@router.post("/async-extract")
async def create_purchase_draft_async(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...)
):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
        
    first_file_bytes = await files[0].read()
    files_data = [(first_file_bytes, files[0].filename, files[0].content_type)]
    
    for i in range(1, len(files)):
        fb = await files[i].read()
        files_data.append((fb, files[i].filename, files[i].content_type))

    draft_id = enqueue_draft_extraction(background_tasks, files_data)
    
    return {"id": draft_id, "message": "Draft creation and extraction started"}


@router.post("")
async def create_purchase_draft(
    file: UploadFile = File(...),
    draft_data: str = Form(...) # JSON string
):
    try:
        data = json.loads(draft_data)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid JSON in draft_data")

    # Save the file
    draft_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename)[1]
    if not ext:
        ext = ".pdf" if file.content_type == "application/pdf" else ".jpg"

    file_bytes = await file.read()
    
    # Normalize HEIC to JPEG for browser preview
    file_bytes = normalize_uploaded_invoice(file_bytes, file.filename, file.content_type)
    if ext.lower() in [".heic", ".heif"]:
        ext = ".jpg"
        
    filename = f"{draft_id}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    with open(filepath, "wb") as buffer:
        buffer.write(file_bytes)

    image_path = f"/uploads/drafts/{filename}"

    # Extract summary info for the list view
    invoice_data = data.get("invoice", {})
    supplier_name = invoice_data.get("supplier", "Unknown Supplier")
    invoice_number = invoice_data.get("invoice_number", "")
    invoice_date = invoice_data.get("date", "")
    items = data.get("items", [])
    item_count = len(items)

    # Re-calculate grand total like the frontend does for consistency
    item_subtotal = sum((item.get("final_amount") if item.get("final_amount") is not None else item.get("amount", 0)) for item in items)
    cgst = float(invoice_data.get("cgst", 0))
    sgst = float(invoice_data.get("sgst", 0))
    igst = float(invoice_data.get("igst", 0))
    rounding = float(invoice_data.get("rounding_off", 0))
    
    # Simple total, can be overriden by UI
    grand_total = item_subtotal + cgst + sgst + igst + rounding

    now = datetime.datetime.now().isoformat()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO purchase_drafts 
            (id, supplier_name, invoice_number, invoice_date, grand_total, item_count, status, draft_data, image_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            draft_id, supplier_name, invoice_number, invoice_date, grand_total, item_count,
            'PENDING_REVIEW', draft_data, image_path, now, now
        ))
        conn.commit()

    return {"id": draft_id, "message": "Draft created successfully"}

@router.get("")
def list_purchase_drafts():
    with get_db() as conn:
        conn.row_factory = dict_factory
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, supplier_name, invoice_number, invoice_date, grand_total, item_count, status, image_path, created_at, updated_at
            FROM purchase_drafts
            WHERE status != 'POSTED'
            ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()
    return {"drafts": rows}

@router.get("/{draft_id}")
def get_purchase_draft(draft_id: str):
    with get_db() as conn:
        conn.row_factory = dict_factory
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM purchase_drafts WHERE id = ?", (draft_id,))
        row = cursor.fetchone()
        
    if not row:
        raise HTTPException(status_code=404, detail="Draft not found")
        
    # parse the JSON data so frontend can consume it directly
    try:
        row["draft_data"] = json.loads(row["draft_data"])
    except:
        pass
        
    return row

@router.put("/{draft_id}")
async def update_purchase_draft(draft_id: str, draft_data: str = Form(...)):
    try:
        data = json.loads(draft_data)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid JSON in draft_data")

    # Extract summary info
    invoice_data = data.get("invoice", {})
    supplier_name = invoice_data.get("supplier", "Unknown Supplier")
    invoice_number = invoice_data.get("invoice_number", "")
    invoice_date = invoice_data.get("date", "")
    items = data.get("items", [])
    item_count = len(items)

    item_subtotal = sum((item.get("final_amount") if item.get("final_amount") is not None else item.get("amount", 0)) for item in items)
    cgst = float(invoice_data.get("cgst", 0))
    sgst = float(invoice_data.get("sgst", 0))
    igst = float(invoice_data.get("igst", 0))
    rounding = float(invoice_data.get("rounding_off", 0))
    grand_total = item_subtotal + cgst + sgst + igst + rounding

    now = datetime.datetime.now().isoformat()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE purchase_drafts 
            SET supplier_name = ?, invoice_number = ?, invoice_date = ?, grand_total = ?, item_count = ?, draft_data = ?, updated_at = ?
            WHERE id = ?
        """, (
            supplier_name, invoice_number, invoice_date, grand_total, item_count, draft_data, now, draft_id
        ))
        
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Draft not found")
            
        conn.commit()

    return {"message": "Draft updated successfully"}

@router.delete("/{draft_id}")
def delete_purchase_draft(draft_id: str):
    with get_db() as conn:
        cursor = conn.cursor()
        # Optionally, get the image_path to delete the file
        cursor.execute("SELECT image_path FROM purchase_drafts WHERE id = ?", (draft_id,))
        row = cursor.fetchone()
        
        cursor.execute("DELETE FROM purchase_drafts WHERE id = ?", (draft_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Draft not found")
            
        conn.commit()

    if row and row[0]:
        filepath = os.path.join(os.getcwd(), "backend", row[0].lstrip('/'))
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except:
                pass

    return {"message": "Draft deleted successfully"}

def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d
