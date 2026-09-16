import os
import hmac
import hashlib
import requests
import time
import uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, BackgroundTasks, HTTPException, Response
from backend.database import get_db
from backend.routers.purchase_drafts import enqueue_draft_extraction

router = APIRouter()

# Environment variables
# We fetch these dynamically in functions to prevent test import-order issues
def get_meta_verify_token(): return os.getenv("META_VERIFY_TOKEN")
def get_meta_access_token(): return os.getenv("META_ACCESS_TOKEN")
def get_meta_app_secret(): return os.getenv("META_APP_SECRET")
def get_meta_api_version(): return os.getenv("META_API_VERSION", "v26.0")


def verify_signature(payload: bytes, signature: str) -> bool:
    """Verify that the payload came from Meta using the app secret."""
    app_secret = get_meta_app_secret()
    if not app_secret or not signature:
        return False
    
    # Meta signature format: "sha256=<hmac_hash>"
    if not signature.startswith("sha256="):
        return False
    
    signature_hash = signature.split("=")[1]
    
    expected_hash = hmac.new(
        key=app_secret.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(expected_hash, signature_hash)


import sqlite3

def check_and_mark_message_processed(message_id: str) -> bool:
    """
    Check if message was already processed (deduplication).
    Returns True if this is a new message (successfully inserted).
    Returns False if it's a duplicate.
    """
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT message_id FROM whatsapp_processed_messages WHERE message_id = ?", (message_id,))
            if cursor.fetchone():
                return False
                
            cursor.execute(
                "INSERT INTO whatsapp_processed_messages (message_id, created_at) VALUES (?, ?)", 
                (message_id, datetime.now().isoformat())
            )
            conn.commit()
        return True
    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            print(f"Warning: whatsapp_processed_messages table missing. Skipping deduplication for {message_id}.")
            return True
        raise


def download_meta_media(media_id: str) -> tuple[bytes, str, str]:
    """
    Downloads media from Meta Graph API.
    Returns (file_bytes, filename, content_type)
    """
    access_token = get_meta_access_token()
    if not access_token:
        raise ValueError("META_ACCESS_TOKEN is not set")

    headers = {"Authorization": f"Bearer {access_token}"}
    
    # 1. Get media URL
    api_version = get_meta_api_version()
    media_url_req = requests.get(f"https://graph.facebook.com/{api_version}/{media_id}", headers=headers, timeout=10)
    media_url_req.raise_for_status()
    media_data = media_url_req.json()
    
    download_url = media_data.get("url")
    mime_type = media_data.get("mime_type", "application/octet-stream")
    
    if not download_url:
        raise ValueError(f"Could not find download URL for media {media_id}")
        
    # 2. Download actual bytes
    media_req = requests.get(download_url, headers=headers, timeout=20)
    media_req.raise_for_status()
    
    # Determine extension
    ext = ".bin"
    if mime_type == "image/jpeg":
        ext = ".jpg"
    elif mime_type == "image/png":
        ext = ".png"
    elif mime_type == "image/webp":
        ext = ".webp"
    elif mime_type == "application/pdf":
        ext = ".pdf"
        
    filename = f"whatsapp_{media_id}{ext}"
    
    return media_req.content, filename, mime_type


@router.on_event("startup")
def startup_whatsapp_recovery():
    """Sweep for any stale whatsapp batches across all senders that failed to cleanup."""
    five_mins_ago = (datetime.now() - timedelta(minutes=5)).isoformat()
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE whatsapp_image_queue 
                SET batch_id = NULL, batch_claimed_at = NULL 
                WHERE batch_id IS NOT NULL AND batch_claimed_at < ?
            """, (five_mins_ago,))
            conn.commit()
    except Exception as e:
        print(f"WhatsApp startup recovery sweep failed (table might not exist yet): {e}")


def flush_whatsapp_queue(background_tasks: BackgroundTasks, sender: str):
    """
    Sleeps for the grouping window, then flushes all queued images for the sender 
    into a single extraction draft.
    """
    # 45 second grouping window
    time.sleep(45)
    
    batch_id = str(uuid.uuid4())
    now_iso = datetime.now().isoformat()
    five_mins_ago = (datetime.now() - timedelta(minutes=5)).isoformat()
    
    # PHASE 1 - CLAIM
    with get_db() as conn:
        cursor = conn.cursor()
        
        try:
            # Safely reclaim stale batches ACROSS ALL SENDERS
            cursor.execute("""
                UPDATE whatsapp_image_queue 
                SET batch_id = NULL, batch_claimed_at = NULL 
                WHERE batch_id IS NOT NULL AND batch_claimed_at < ?
            """, (five_mins_ago,))
            
            # Atomically claim all currently unclaimed rows for this sender
            cursor.execute("""
                UPDATE whatsapp_image_queue 
                SET batch_id = ?, batch_claimed_at = ?
                WHERE sender = ? AND batch_id IS NULL
            """, (batch_id, now_iso, sender))
            
            cursor.execute("SELECT * FROM whatsapp_image_queue WHERE batch_id = ?", (batch_id,))
            rows = cursor.fetchall()
            conn.commit()
        except sqlite3.OperationalError as e:
            if "no such table" in str(e).lower():
                print(f"Warning: whatsapp_image_queue table missing. Cannot flush for {sender}.")
                return
            raise
            
    if not rows:
        return  # Another task already flushed them or queue is empty
        
    # Sort in memory: primary by timestamp, tie-breaker by message_id
    sorted_rows = sorted(rows, key=lambda r: (int(r["message_timestamp"]), r["message_id"]))
    files_data = [(r["file_bytes"], r["filename"], r["content_type"]) for r in sorted_rows]
    
    deterministic_draft_id = f"wa_{sorted_rows[0]['message_id']}"
    
    # PHASE 2 - PROCESS (outside DB transaction)
    try:
        # Call the existing shared pipeline with deterministic draft_id
        result_id = enqueue_draft_extraction(background_tasks, files_data, draft_id=deterministic_draft_id)
        # Check if the result was successful (it will return the draft_id)
        success = True
    except Exception as e:
        print(f"Failed to enqueue extraction for batch {batch_id}: {e}")
        success = False
        
    # PHASE 3 - CLEANUP
    with get_db() as conn:
        cursor = conn.cursor()
        if success:
            cursor.execute("DELETE FROM whatsapp_image_queue WHERE batch_id = ?", (batch_id,))
        else:
            # Revert so they can be tried again safely
            cursor.execute("UPDATE whatsapp_image_queue SET batch_id = NULL, batch_claimed_at = NULL WHERE batch_id = ?", (batch_id,))
        conn.commit()


def handle_whatsapp_message(background_tasks: BackgroundTasks, message: dict):
    """Process a single WhatsApp message event."""
    message_id = message.get("id")
    sender = message.get("from")
    timestamp = message.get("timestamp", "0")
    
    if not message_id or not sender:
        return
        
    # Deduplication
    if not check_and_mark_message_processed(message_id):
        return  # Already processed

    # Check for media we care about
    media_id = None
    if "image" in message:
        media_id = message["image"].get("id")
    elif "document" in message:
        media_id = message["document"].get("id")
        
    if not media_id:
        return # Ignore text messages or other types
        
    try:
        file_bytes, filename, content_type = download_meta_media(media_id)
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO whatsapp_image_queue 
                (message_id, sender, message_timestamp, filename, content_type, file_bytes)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (message_id, sender, timestamp, filename, content_type, file_bytes))
            conn.commit()
            
        # Spawn the flusher task for this sender
        background_tasks.add_task(flush_whatsapp_queue, background_tasks, sender)
        
    except Exception as e:
        print(f"Failed to process WhatsApp media {media_id}: {e}")
        

@router.get("/webhook")
async def verify_webhook(request: Request):
    """Meta webhook verification endpoint."""
    hub_mode = request.query_params.get("hub.mode")
    hub_challenge = request.query_params.get("hub.challenge")
    hub_verify_token = request.query_params.get("hub.verify_token")
    
    if hub_mode == "subscribe" and hub_verify_token == get_meta_verify_token():
        # Must return the challenge directly (not as JSON)
        return Response(content=hub_challenge, media_type="text/plain")
        
    raise HTTPException(status_code=403, detail="Invalid verification token")


@router.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receive incoming WhatsApp messages from Meta."""
    
    # 1. Verify Signature
    payload = await request.body()
    signature = request.headers.get("x-hub-signature-256")
    
    if not verify_signature(payload, signature):
        raise HTTPException(status_code=403, detail="Invalid signature")

    # 2. Parse Payload
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # 3. Process Events
    # The payload structure is data["entry"][0]["changes"][0]["value"]["messages"]
    entries = data.get("entry", [])
    for entry in entries:
        changes = entry.get("changes", [])
        for change in changes:
            value = change.get("value", {})
            messages = value.get("messages", [])
            for message in messages:
                # We do the heavy lifting in background to ack Meta quickly
                background_tasks.add_task(handle_whatsapp_message, background_tasks, message)
                
    # 4. Acknowledge Meta quickly
    return {"status": "ok"}
