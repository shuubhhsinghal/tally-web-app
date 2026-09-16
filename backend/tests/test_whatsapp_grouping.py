import os
import sqlite3
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import BackgroundTasks
import time
from datetime import datetime, timedelta
import threading

from backend.main import app
from backend.database import get_db, init_db
from backend.routers.whatsapp import handle_whatsapp_message, flush_whatsapp_queue, startup_whatsapp_recovery

def run_bg_tasks(bt: BackgroundTasks):
    for task in bt.tasks:
        task.func(*task.args, **task.kwargs)
    bt.tasks.clear()

def create_valid_image_bytes():
    from PIL import Image
    import io
    img = Image.new('RGB', (10, 10), color='white')
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    return buf.getvalue()

VALID_IMG_BYTES = create_valid_image_bytes()

def setup_module(module):
    os.environ["TESTING"] = "true"
    init_db()
    # Explicitly clear tables before testing
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM whatsapp_image_queue")
        cursor.execute("DELETE FROM whatsapp_processed_messages")
        cursor.execute("DELETE FROM purchase_drafts")
        conn.commit()

def teardown_function(function):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM whatsapp_image_queue")
        cursor.execute("DELETE FROM whatsapp_processed_messages")
        cursor.execute("DELETE FROM purchase_drafts")
        conn.commit()

def create_mock_message(msg_id, sender, timestamp):
    return {
        "id": msg_id,
        "from": sender,
        "timestamp": timestamp,
        "image": {"id": f"media_{msg_id}"}
    }

@patch("backend.routers.whatsapp.download_meta_media")
@patch("backend.routers.purchase_drafts.process_async_extraction")
@patch("backend.routers.whatsapp.time.sleep")
def test_scenario_1_single_image(mock_sleep, mock_extract, mock_download):
    mock_download.return_value = (VALID_IMG_BYTES, "mock.jpg", "image/jpeg")
    bt = BackgroundTasks()
    
    msg = create_mock_message("msg1", "12345", "1000")
    handle_whatsapp_message(bt, msg)
    
    # Check queue
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM whatsapp_image_queue")
        assert len(cursor.fetchall()) == 1

    flush_whatsapp_queue(bt, "12345")
    run_bg_tasks(bt)
    
    # Check draft created
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM purchase_drafts")
        drafts = cursor.fetchall()
        assert len(drafts) == 1
        assert drafts[0]["id"] == "wa_msg1"
        
        # Check queue cleared
        cursor.execute("SELECT * FROM whatsapp_image_queue")
        assert len(cursor.fetchall()) == 0

@patch("backend.routers.whatsapp.download_meta_media")
@patch("backend.routers.purchase_drafts.process_async_extraction")
@patch("backend.routers.whatsapp.time.sleep")
def test_scenario_2_two_images(mock_sleep, mock_extract, mock_download):
    mock_download.return_value = (VALID_IMG_BYTES, "mock.jpg", "image/jpeg")
    
    msg1 = create_mock_message("msg1", "12345", "1000")
    msg2 = create_mock_message("msg2", "12345", "1005")
    
    bt = BackgroundTasks()
    handle_whatsapp_message(bt, msg1)
    handle_whatsapp_message(bt, msg2)
    
    flush_whatsapp_queue(bt, "12345")
    run_bg_tasks(bt)
    
    # 2 files, 1 draft
    assert mock_extract.call_count == 1
    assert len(mock_extract.call_args[0][1]) == 2
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM purchase_drafts")
        drafts = cursor.fetchall()
        assert len(drafts) == 1
        assert drafts[0]["id"] == "wa_msg1" # First msg ID determines draft_id

@patch("backend.routers.whatsapp.download_meta_media")
@patch("backend.routers.purchase_drafts.process_async_extraction")
@patch("backend.routers.whatsapp.time.sleep")
def test_scenario_3_three_images_ordering(mock_sleep, mock_extract, mock_download):
    mock_download.return_value = (VALID_IMG_BYTES, "mock.jpg", "image/jpeg")
    
    bt = BackgroundTasks()
    handle_whatsapp_message(bt, create_mock_message("msg2", "12345", "1010"))
    handle_whatsapp_message(bt, create_mock_message("msg1", "12345", "1005"))
    handle_whatsapp_message(bt, create_mock_message("msg3", "12345", "1015"))
    
    flush_whatsapp_queue(bt, "12345")
    run_bg_tasks(bt)
    
    assert mock_extract.call_count == 1
    files_data = mock_extract.call_args[0][1]
    assert len(files_data) == 3
    
    # Wait, the mock doesn't record which file is which. 
    # But we can verify draft_id generated from the FIRST chronologically
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM purchase_drafts")
        drafts = cursor.fetchall()
        assert drafts[0]["id"] == "wa_msg1" # msg1 is timestamp 1005

@patch("backend.routers.whatsapp.download_meta_media")
@patch("backend.routers.purchase_drafts.process_async_extraction")
@patch("backend.routers.whatsapp.time.sleep")
def test_scenario_4_same_second_ordering(mock_sleep, mock_extract, mock_download):
    mock_download.return_value = (VALID_IMG_BYTES, "mock.jpg", "image/jpeg")
    
    bt = BackgroundTasks()
    # Same timestamp, tie break on message_id ('msgA' < 'msgB')
    handle_whatsapp_message(bt, create_mock_message("msgB", "12345", "1000"))
    handle_whatsapp_message(bt, create_mock_message("msgA", "12345", "1000"))
    
    flush_whatsapp_queue(bt, "12345")
    run_bg_tasks(bt)
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM purchase_drafts")
        drafts = cursor.fetchall()
        assert drafts[0]["id"] == "wa_msgA"

@patch("backend.routers.whatsapp.download_meta_media")
@patch("backend.routers.purchase_drafts.process_async_extraction")
@patch("backend.routers.whatsapp.time.sleep")
def test_scenario_5_different_senders(mock_sleep, mock_extract, mock_download):
    mock_download.return_value = (VALID_IMG_BYTES, "mock.jpg", "image/jpeg")
    
    btA = BackgroundTasks()
    btB = BackgroundTasks()
    
    handle_whatsapp_message(btA, create_mock_message("msg1", "senderA", "1000"))
    handle_whatsapp_message(btB, create_mock_message("msg2", "senderB", "1005"))
    
    flush_whatsapp_queue(btA, "senderA")
    flush_whatsapp_queue(btB, "senderB")
    run_bg_tasks(btA)
    run_bg_tasks(btB)
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM purchase_drafts ORDER BY id")
        drafts = cursor.fetchall()
        assert len(drafts) == 2
        assert drafts[0]["id"] == "wa_msg1"
        assert drafts[1]["id"] == "wa_msg2"

@patch("backend.routers.whatsapp.download_meta_media")
@patch("backend.routers.purchase_drafts.process_async_extraction")
@patch("backend.routers.whatsapp.time.sleep")
def test_scenario_6_outside_grouping_window(mock_sleep, mock_extract, mock_download):
    mock_download.return_value = (VALID_IMG_BYTES, "mock.jpg", "image/jpeg")
    
    bt = BackgroundTasks()
    handle_whatsapp_message(bt, create_mock_message("msg1", "12345", "1000"))
    flush_whatsapp_queue(bt, "12345")
    run_bg_tasks(bt)
    
    handle_whatsapp_message(bt, create_mock_message("msg2", "12345", "1100"))
    flush_whatsapp_queue(bt, "12345")
    run_bg_tasks(bt)
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM purchase_drafts")
        assert len(cursor.fetchall()) == 2

@patch("backend.routers.whatsapp.download_meta_media")
@patch("backend.routers.purchase_drafts.process_async_extraction")
@patch("backend.routers.whatsapp.time.sleep")
def test_scenario_7_duplicate_webhook(mock_sleep, mock_extract, mock_download):
    mock_download.return_value = (VALID_IMG_BYTES, "mock.jpg", "image/jpeg")
    
    bt = BackgroundTasks()
    msg1 = create_mock_message("msg1", "12345", "1000")
    handle_whatsapp_message(bt, msg1)
    handle_whatsapp_message(bt, msg1) # Retry webhook
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM whatsapp_image_queue")
        assert len(cursor.fetchall()) == 1 # Only one entry queued
        
    flush_whatsapp_queue(bt, "12345")
    run_bg_tasks(bt)
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM purchase_drafts")
        assert len(cursor.fetchall()) == 1

@patch("backend.routers.whatsapp.download_meta_media")
@patch("backend.routers.purchase_drafts.process_async_extraction")
@patch("backend.routers.whatsapp.time.sleep")
def test_scenario_8_concurrent_flushes(mock_sleep, mock_extract, mock_download):
    mock_download.return_value = (VALID_IMG_BYTES, "mock.jpg", "image/jpeg")
    bt = BackgroundTasks()
    handle_whatsapp_message(bt, create_mock_message("msg1", "12345", "1000"))
    
    def run_flush():
        flush_whatsapp_queue(bt, "12345")
        
    t1 = threading.Thread(target=run_flush)
    t2 = threading.Thread(target=run_flush)
    
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    # Despite 2 threads running concurrently, only one draft should be created
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM purchase_drafts")
        assert len(cursor.fetchall()) == 1

@patch("backend.routers.whatsapp.download_meta_media")
@patch("backend.routers.whatsapp.enqueue_draft_extraction")
@patch("backend.routers.whatsapp.time.sleep")
def test_scenario_9_new_message_during_flush(mock_sleep, mock_enqueue, mock_download):
    mock_download.return_value = (VALID_IMG_BYTES, "mock.jpg", "image/jpeg")
    
    bt = BackgroundTasks()
    handle_whatsapp_message(bt, create_mock_message("msg1", "12345", "1000"))
    
    # Mock enqueue to simulate long processing time and insert msg2 DURING the enqueue
    def slow_enqueue(*args, **kwargs):
        handle_whatsapp_message(bt, create_mock_message("msg2", "12345", "1010"))
        return "mock_draft_id"
    
    mock_enqueue.side_effect = slow_enqueue
    
    # This flush claims msg1, but msg2 arrives mid-flush
    flush_whatsapp_queue(bt, "12345")
    
    # Queue should still have msg2 because it wasn't claimed
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM whatsapp_image_queue")
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0]["message_id"] == "msg2"

@patch("backend.routers.whatsapp.download_meta_media")
@patch("backend.routers.whatsapp.enqueue_draft_extraction")
@patch("backend.routers.whatsapp.time.sleep")
def test_scenario_10_enqueue_failure_recovery(mock_sleep, mock_enqueue, mock_download):
    mock_download.return_value = (VALID_IMG_BYTES, "mock.jpg", "image/jpeg")
    
    bt = BackgroundTasks()
    handle_whatsapp_message(bt, create_mock_message("msg1", "12345", "1000"))
    
    mock_enqueue.side_effect = Exception("Simulated DB or network failure during enqueue")
    
    flush_whatsapp_queue(bt, "12345")
    
    # Enqueue failed, queue cleanup should revert batch_id to NULL
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM whatsapp_image_queue")
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0]["batch_id"] is None

@patch("backend.routers.whatsapp.download_meta_media")
@patch("backend.routers.purchase_drafts.process_async_extraction")
@patch("backend.routers.whatsapp.time.sleep")
def test_scenario_11_stale_claim_sweep(mock_sleep, mock_extract, mock_download):
    mock_download.return_value = (VALID_IMG_BYTES, "mock.jpg", "image/jpeg")
    
    # Manually insert a stale batch from 10 minutes ago
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO whatsapp_image_queue (message_id, sender, message_timestamp, filename, content_type, file_bytes, batch_id, batch_claimed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ("stale_msg", "12345", "1000", "stale.jpg", "image/jpeg", b"bytes", "stale_batch_id", (datetime.now() - timedelta(minutes=10)).isoformat()))
        conn.commit()
        
    bt = BackgroundTasks()
    # Another sender triggers a webhook, sweeping ALL stale batches globally
    handle_whatsapp_message(bt, create_mock_message("new_msg", "other_sender", "2000"))
    flush_whatsapp_queue(bt, "other_sender")
    run_bg_tasks(bt)
    
    with get_db() as conn:
        cursor = conn.cursor()
        # The stale batch for '12345' should be reverted to NULL
        cursor.execute("SELECT batch_id FROM whatsapp_image_queue WHERE sender='12345'")
        stale_row = cursor.fetchone()
        assert stale_row["batch_id"] is None
        
        # The other_sender's message should be processed successfully
        cursor.execute("SELECT id FROM purchase_drafts")
        assert len(cursor.fetchall()) == 1

@patch("backend.routers.whatsapp.download_meta_media")
@patch("backend.routers.purchase_drafts.process_async_extraction")
@patch("backend.routers.whatsapp.time.sleep")
def test_scenario_12_crash_between_draft_creation_and_cleanup(mock_sleep, mock_extract, mock_download):
    """
    CRASH BETWEEN DRAFT CREATION AND CLEANUP:
    Demonstrates idempotent draft creation. 
    1. Create the draft directly (simulating successful enqueue but failure to cleanup).
    2. Leave images in the queue (simulating crash).
    3. Run flush again. It should NOT create a duplicate draft, and it SHOULD clean up the queue.
    """
    mock_download.return_value = (VALID_IMG_BYTES, "mock.jpg", "image/jpeg")
    
    bt = BackgroundTasks()
    # 1. Insert message and flush it manually
    handle_whatsapp_message(bt, create_mock_message("msg1", "12345", "1000"))
    
    # We need to simulate the exact behavior: enqueue succeeds but delete fails.
    # We can do this by mocking sqlite3.connect to return a custom connection that raises on DELETE
    from unittest.mock import MagicMock
    import sqlite3
    
    import backend.routers.whatsapp
    real_get_db = backend.routers.whatsapp.get_db
    
    import contextlib
    @contextlib.contextmanager
    def failing_get_db():
        with real_get_db() as conn:
            class FailingCursor:
                def __init__(self, real_cursor):
                    self.real_cursor = real_cursor
                def __getattr__(self, name):
                    return getattr(self.real_cursor, name)
                def execute(self, sql, *args, **kwargs):
                    if "DELETE FROM whatsapp_image_queue" in sql:
                        raise Exception("Simulated crash BEFORE queue cleanup")
                    return self.real_cursor.execute(sql, *args, **kwargs)
                def fetchall(self):
                    return self.real_cursor.fetchall()
                def fetchone(self):
                    return self.real_cursor.fetchone()
            
            class FailingConn:
                def __init__(self, real_conn):
                    self.real_conn = real_conn
                def __getattr__(self, name):
                    return getattr(self.real_conn, name)
                def cursor(self):
                    return FailingCursor(self.real_conn.cursor())
                    
            yield FailingConn(conn)
            
    with patch("backend.routers.whatsapp.get_db", new=failing_get_db):
        try:
            flush_whatsapp_queue(bt, "12345")
        except Exception:
            pass # We expect it to crash here in the mock
            
    # Verify draft was created but images remain in queue
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM purchase_drafts")
        assert len(cursor.fetchall()) == 1
        
        cursor.execute("SELECT * FROM whatsapp_image_queue")
        assert len(cursor.fetchall()) == 1

    # Now simulate recovery: make the stuck batch stale
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE whatsapp_image_queue SET batch_claimed_at = ?", ((datetime.now() - timedelta(minutes=10)).isoformat(),))
        conn.commit()
        
    # Flush again (recovery)
    flush_whatsapp_queue(bt, "12345")
    run_bg_tasks(bt)
    
    # VERIFY IDEMPOTENCY: Still exactly ONE draft (no duplicates)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM purchase_drafts")
        drafts = cursor.fetchall()
        assert len(drafts) == 1
        
        # Verify queue was finally cleaned up
        cursor.execute("SELECT * FROM whatsapp_image_queue")
        assert len(cursor.fetchall()) == 0

def test_scenario_13_startup_recovery_sweep():
    # Insert a stale batch
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO whatsapp_image_queue (message_id, sender, message_timestamp, filename, content_type, file_bytes, batch_id, batch_claimed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ("msg1", "123", "1000", "test.jpg", "image/jpeg", b"bytes", "batch_1", (datetime.now() - timedelta(minutes=10)).isoformat()))
        conn.commit()
        
    # Trigger startup hook
    startup_whatsapp_recovery()
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT batch_id, batch_claimed_at FROM whatsapp_image_queue")
        row = cursor.fetchone()
        assert row["batch_id"] is None
        assert row["batch_claimed_at"] is None
