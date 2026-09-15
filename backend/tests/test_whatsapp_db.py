import os
import sqlite3
import pytest

# Ensure testing environment is set
os.environ["TESTING"] = "true"

from backend.database import DB_PATH, init_db, get_db
from backend.routers.whatsapp import check_and_mark_message_processed

@pytest.fixture(autouse=True)
def clean_db():
    """Ensure a fresh test database for each test."""
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    yield
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

def test_database_initialization_creates_whatsapp_table():
    """Test that init_db properly creates the whatsapp_processed_messages table."""
    # Run the normal database initialization
    init_db()
    
    # Confirm the table exists
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='whatsapp_processed_messages'")
        result = cursor.fetchone()
        assert result is not None
        
    # Confirm check_and_mark_message_processed works normally
    msg_id = "test-message-id"
    assert check_and_mark_message_processed(msg_id) is True
    
    # Call it again with the same ID and confirm duplicate handling works
    assert check_and_mark_message_processed(msg_id) is False

def test_webhook_handler_does_not_fail_missing_table(capsys):
    """
    Regression test proving the webhook background handler does not fail 
    because the WhatsApp processed-message table is missing.
    """
    # Create the DB file but DO NOT run init_db(), so the table is missing
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE dummy (id INTEGER PRIMARY KEY)")
        
    # Call the check function which should gracefully handle the missing table
    msg_id = "test-message-missing-table"
    result = check_and_mark_message_processed(msg_id)
    
    # Should return True (fallback behavior to process without deduplication)
    assert result is True
    
    # Confirm it logged the warning
    captured = capsys.readouterr()
    assert "whatsapp_processed_messages table missing" in captured.out
