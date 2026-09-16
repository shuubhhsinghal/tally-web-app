import os
import json
import hmac
import hashlib
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Set env vars before importing main to ensure app initializes correctly
os.environ["META_VERIFY_TOKEN"] = "test_verify_token"
os.environ["META_ACCESS_TOKEN"] = "test_access_token"
os.environ["META_APP_SECRET"] = "test_app_secret"

from backend.main import app
from backend.routers import whatsapp

client = TestClient(app)

def generate_signature(payload_bytes: bytes, secret: str = "test_app_secret") -> str:
    expected_hash = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload_bytes,
        digestmod=hashlib.sha256
    ).hexdigest()
    return f"sha256={expected_hash}"

def test_verify_webhook_success():
    response = client.get("/api/whatsapp/webhook?hub.mode=subscribe&hub.challenge=12345&hub.verify_token=test_verify_token")
    assert response.status_code == 200
    assert response.text == "12345"

def test_verify_webhook_failure():
    response = client.get("/api/whatsapp/webhook?hub.mode=subscribe&hub.challenge=12345&hub.verify_token=wrong_token")
    assert response.status_code == 403

def test_receive_webhook_invalid_signature():
    payload = {"test": "data"}
    payload_bytes = json.dumps(payload).encode("utf-8")
    
    response = client.post(
        "/api/whatsapp/webhook",
        content=payload_bytes,
        headers={"X-Hub-Signature-256": "sha256=invalidhash"}
    )
    assert response.status_code == 403

def test_receive_webhook_missing_signature():
    payload = {"test": "data"}
    response = client.post(
        "/api/whatsapp/webhook",
        json=payload
    )
    assert response.status_code == 403

@patch("backend.routers.whatsapp.time.sleep")
@patch("backend.routers.whatsapp.enqueue_draft_extraction")
@patch("backend.routers.whatsapp.requests.get")
@patch("backend.routers.whatsapp.check_and_mark_message_processed")
def test_receive_webhook_valid_image(mock_check_processed, mock_get, mock_enqueue, mock_sleep):
    mock_check_processed.return_value = True # Treat as new message
    
    # Mock media URL response
    mock_response_1 = MagicMock()
    mock_response_1.json.return_value = {"url": "https://download.url", "mime_type": "image/jpeg"}
    mock_response_1.raise_for_status = MagicMock()
    
    # Mock media content response
    mock_response_2 = MagicMock()
    mock_response_2.content = b"fake_image_bytes"
    mock_response_2.raise_for_status = MagicMock()
    
    mock_get.side_effect = [mock_response_1, mock_response_2]
    
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "msg123",
                                    "from": "12345",
                                    "timestamp": "1000",
                                    "image": {"id": "media123"}
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    payload_bytes = json.dumps(payload).encode("utf-8")
    signature = generate_signature(payload_bytes)
    
    response = client.post(
        "/api/whatsapp/webhook",
        content=payload_bytes,
        headers={"X-Hub-Signature-256": signature}
    )
    
    assert response.status_code == 200
    
    # We must await background tasks in tests by running them manually, or use a customized TestClient.
    # FastAPI TestClient doesn't automatically run BackgroundTasks if we don't return them in a specific way, 
    # but let's check if the router properly parses it. 
    # Wait, Starlette's TestClient DOES run background tasks automatically after returning the response!
    
    mock_check_processed.assert_called_once_with("msg123")
    mock_get.assert_any_call(f"https://graph.facebook.com/{whatsapp.get_meta_api_version()}/media123", headers={"Authorization": "Bearer test_access_token"}, timeout=10)
    mock_get.assert_any_call("https://download.url", headers={"Authorization": "Bearer test_access_token"}, timeout=20)
    mock_enqueue.assert_called_once()
    args, _ = mock_enqueue.call_args
    files_data = args[1]
    assert files_data[0][0] == b"fake_image_bytes"
    assert files_data[0][1] == "whatsapp_media123.jpg"
    assert files_data[0][2] == "image/jpeg"

@patch("backend.routers.whatsapp.time.sleep")
@patch("backend.routers.whatsapp.enqueue_draft_extraction")
@patch("backend.routers.whatsapp.requests.get")
@patch("backend.routers.whatsapp.check_and_mark_message_processed")
def test_receive_webhook_valid_document(mock_check_processed, mock_get, mock_enqueue, mock_sleep):
    mock_check_processed.return_value = True # Treat as new message
    
    # Mock media URL response
    mock_response_1 = MagicMock()
    mock_response_1.json.return_value = {"url": "https://download.url", "mime_type": "application/pdf"}
    mock_response_1.raise_for_status = MagicMock()
    
    # Mock media content response
    mock_response_2 = MagicMock()
    mock_response_2.content = b"fake_pdf_bytes"
    mock_response_2.raise_for_status = MagicMock()
    
    mock_get.side_effect = [mock_response_1, mock_response_2]
    
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "msg124",
                                    "from": "12345",
                                    "timestamp": "1000",
                                    "document": {"id": "media124"}
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    payload_bytes = json.dumps(payload).encode("utf-8")
    signature = generate_signature(payload_bytes)
    
    response = client.post(
        "/api/whatsapp/webhook",
        content=payload_bytes,
        headers={"X-Hub-Signature-256": signature}
    )
    
    assert response.status_code == 200
    mock_enqueue.assert_called_once()
    args, _ = mock_enqueue.call_args
    files_data = args[1]
    assert files_data[0][0] == b"fake_pdf_bytes"
    assert files_data[0][1] == "whatsapp_media124.pdf"
    assert files_data[0][2] == "application/pdf"

@patch("backend.routers.whatsapp.time.sleep")
@patch("backend.routers.whatsapp.enqueue_draft_extraction")
@patch("backend.routers.whatsapp.check_and_mark_message_processed")
def test_receive_webhook_irrelevant_message(mock_check_processed, mock_enqueue, mock_sleep):
    mock_check_processed.return_value = True
    
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "msg125",
                                    "from": "12345",
                                    "timestamp": "1000",
                                    "text": {"body": "hello"}
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    payload_bytes = json.dumps(payload).encode("utf-8")
    signature = generate_signature(payload_bytes)
    
    response = client.post(
        "/api/whatsapp/webhook",
        content=payload_bytes,
        headers={"X-Hub-Signature-256": signature}
    )
    
    assert response.status_code == 200
    mock_enqueue.assert_not_called()

@patch("backend.routers.whatsapp.time.sleep")
@patch("backend.routers.whatsapp.enqueue_draft_extraction")
@patch("backend.routers.whatsapp.check_and_mark_message_processed")
def test_receive_webhook_duplicate_message(mock_check_processed, mock_enqueue, mock_sleep):
    mock_check_processed.return_value = False # Duplicate!
    
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "msg126",
                                    "from": "12345",
                                    "timestamp": "1000",
                                    "image": {"id": "media126"}
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    payload_bytes = json.dumps(payload).encode("utf-8")
    signature = generate_signature(payload_bytes)
    
    response = client.post(
        "/api/whatsapp/webhook",
        content=payload_bytes,
        headers={"X-Hub-Signature-256": signature}
    )
    
    assert response.status_code == 200
    mock_check_processed.assert_called_once_with("msg126")
    mock_enqueue.assert_not_called()

@patch("backend.routers.whatsapp.time.sleep")
@patch("backend.routers.whatsapp.requests.get")
@patch("backend.routers.whatsapp.check_and_mark_message_processed")
def test_receive_webhook_download_failure(mock_check_processed, mock_get, mock_sleep):
    mock_check_processed.return_value = True
    
    # Mock failure
    mock_get.side_effect = Exception("Graph API Error")
    
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "msg127",
                                    "from": "12345",
                                    "timestamp": "1000",
                                    "image": {"id": "media127"}
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    payload_bytes = json.dumps(payload).encode("utf-8")
    signature = generate_signature(payload_bytes)
    
    response = client.post(
        "/api/whatsapp/webhook",
        content=payload_bytes,
        headers={"X-Hub-Signature-256": signature}
    )
    
    # Still returns 200 to Meta so it stops retrying immediately, 
    # but logs the error (caught in the handle_whatsapp_message try-except)
    assert response.status_code == 200

def test_receive_webhook_malformed_payload():
    payload_bytes = b"not valid json"
    signature = generate_signature(payload_bytes)
    
    response = client.post(
        "/api/whatsapp/webhook",
        content=payload_bytes,
        headers={"X-Hub-Signature-256": signature}
    )
    
    assert response.status_code == 400
