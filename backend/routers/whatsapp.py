import os
import hmac
import hashlib
import requests
from datetime import datetime
from fastapi import APIRouter, Request, BackgroundTasks, HTTPException, Response
from backend.database import get_db
from backend.routers.purchase_drafts import enqueue_draft_extraction

router = APIRouter()

# Environment variables
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
META_APP_SECRET = os.getenv("META_APP_SECRET")
META_API_VERSION = os.getenv("META_API_VERSION", "v19.0")


def verify_signature(payload: bytes, signature: str) -> bool:
    """Verify that the payload came from Meta using the app secret."""
    if not META_APP_SECRET or not signature:
        return False
    
    # Meta signature format: "sha256=<hmac_hash>"
    if not signature.startswith("sha256="):
        return False
    
    signature_hash = signature.split("=")[1]
    
    expected_hash = hmac.new(
        key=META_APP_SECRET.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(expected_hash, signature_hash)


def check_and_mark_message_processed(message_id: str) -> bool:
    """
    Check if message was already processed (deduplication).
    Returns True if this is a new message (successfully inserted).
    Returns False if it's a duplicate.
    """
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


def download_meta_media(media_id: str) -> tuple[bytes, str, str]:
    """
    Downloads media from Meta Graph API.
    Returns (file_bytes, filename, content_type)
    """
    if not META_ACCESS_TOKEN:
        raise ValueError("META_ACCESS_TOKEN is not set")

    headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}"}
    
    # 1. Get media URL
    media_url_req = requests.get(f"https://graph.facebook.com/{META_API_VERSION}/{media_id}", headers=headers, timeout=10)
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


def handle_whatsapp_message(background_tasks: BackgroundTasks, message: dict):
    """Process a single WhatsApp message event."""
    message_id = message.get("id")
    if not message_id:
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
        
        # Call the existing shared pipeline
        files_data = [(file_bytes, filename, content_type)]
        enqueue_draft_extraction(background_tasks, files_data)
        
    except Exception as e:
        print(f"Failed to process WhatsApp media {media_id}: {e}")
        

@router.get("/webhook")
async def verify_webhook(request: Request):
    """Meta webhook verification endpoint."""
    hub_mode = request.query_params.get("hub.mode")
    hub_challenge = request.query_params.get("hub.challenge")
    hub_verify_token = request.query_params.get("hub.verify_token")
    
    if hub_mode == "subscribe" and hub_verify_token == META_VERIFY_TOKEN:
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
