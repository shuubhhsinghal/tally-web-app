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
from backend.routers.whatsapp import (
    handle_whatsapp_message, flush_whatsapp_queue, startup_whatsapp_recovery,
    ALBUM_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS, ALBUM_MAX_WAIT_SECONDS,
    _is_album_group_complete, _startup_orphan_recovery, _DONE_BUTTON_ID,
)

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


# ── Sliding debounce, album detection, and the "done" keyword ──────────────

@patch("backend.routers.whatsapp.download_meta_media")
@patch("backend.routers.purchase_drafts.process_async_extraction")
@patch("backend.routers.whatsapp.time.sleep")
def test_scenario_14_debounce_backoff_then_flush_on_latest_message(mock_sleep, mock_extract, mock_download):
    """A debounce check that wakes up to find a newer, still-unclaimed image
    from the same sender must back off rather than flush early -- the newer
    image's own check (scheduled the same delay after it arrived) is what
    should actually do the flush once nothing newer follows it."""
    mock_download.return_value = (VALID_IMG_BYTES, "mock.jpg", "image/jpeg")
    bt = BackgroundTasks()

    handle_whatsapp_message(bt, create_mock_message("msg1", "12345", "1000"))
    handle_whatsapp_message(bt, create_mock_message("msg2", "12345", "1005"))

    # Both are lone messages from the same sender with nothing flushed in
    # between, so they share the same auto-resolved "lone" group.
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT group_id FROM whatsapp_image_queue WHERE message_id = 'msg1'")
        group_id = cursor.fetchone()["group_id"]

    # msg1's own debounce check fires first (in real time it would've woken
    # up first too) -- but msg2 arrived after it, so it must back off.
    flush_whatsapp_queue(bt, "12345", group_id=group_id, message_timestamp="1000", message_id="msg1", delay_seconds=DEFAULT_DEBOUNCE_SECONDS)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM whatsapp_image_queue WHERE batch_id IS NOT NULL")
        assert len(cursor.fetchall()) == 0  # nothing claimed -- it backed off
        cursor.execute("SELECT * FROM purchase_drafts")
        assert len(cursor.fetchall()) == 0  # no draft created yet

    # msg2's own check fires next and finds nothing newer -- it flushes both.
    flush_whatsapp_queue(bt, "12345", group_id=group_id, message_timestamp="1005", message_id="msg2", delay_seconds=DEFAULT_DEBOUNCE_SECONDS)
    run_bg_tasks(bt)

    assert mock_extract.call_count == 1
    assert len(mock_extract.call_args[0][1]) == 2  # both pages went into one draft

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM purchase_drafts")
        drafts = cursor.fetchall()
        assert len(drafts) == 1
        assert drafts[0]["id"] == "wa_msg1"


@patch("backend.routers.whatsapp.download_meta_media")
def test_scenario_15_album_message_gets_shorter_debounce(mock_download):
    """Messages with expected_count > 1 (Meta bundled them in one webhook
    delivery -- a deliberate multi-select send) should schedule the shorter
    ALBUM_DEBOUNCE_SECONDS instead of the default per-message window."""
    mock_download.return_value = (VALID_IMG_BYTES, "mock.jpg", "image/jpeg")
    bt = BackgroundTasks()

    handle_whatsapp_message(bt, create_mock_message("msg_album", "12345", "1000"), group_id="album_test123", expected_count=2)
    handle_whatsapp_message(bt, create_mock_message("msg_lone", "12345", "1010"))

    # task.args = (background_tasks, sender, group_id, timestamp, message_id, delay, expected_count)
    scheduled = {task.args[4]: task.args[5] for task in bt.tasks}  # message_id -> delay_seconds
    assert scheduled["msg_album"] == ALBUM_DEBOUNCE_SECONDS
    assert scheduled["msg_lone"] == DEFAULT_DEBOUNCE_SECONDS
    assert ALBUM_DEBOUNCE_SECONDS < DEFAULT_DEBOUNCE_SECONDS


@patch("backend.routers.whatsapp.download_meta_media")
@patch("backend.routers.purchase_drafts.process_async_extraction")
@patch("backend.routers.whatsapp.time.sleep")
def test_scenario_16_done_keyword_flushes_without_waiting_for_debounce(mock_sleep, mock_extract, mock_download):
    """Texting "done" must process whatever is queued right away, even while
    the image's own natural debounce check is still pending (i.e. before the
    normal window would have elapsed on its own)."""
    mock_download.return_value = (VALID_IMG_BYTES, "mock.jpg", "image/jpeg")
    bt = BackgroundTasks()

    handle_whatsapp_message(bt, create_mock_message("msg1", "12345", "1000"))
    # msg1's own debounce-check task is now sitting in bt.tasks, unexecuted --
    # simulating "still mid-window, nothing has fired yet".

    done_message = {"id": "done1", "from": "12345", "timestamp": "1002", "type": "text", "text": {"body": "Done"}}
    handle_whatsapp_message(bt, done_message)

    # Run only the task the "done" message just scheduled -- not msg1's
    # still-pending natural flush -- to prove "done" alone is sufficient.
    done_task = bt.tasks[-1]
    done_task.func(*done_task.args, **done_task.kwargs)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM purchase_drafts")
        drafts = cursor.fetchall()
        assert len(drafts) == 1
        assert drafts[0]["id"] == "wa_msg1"

        cursor.execute("SELECT * FROM whatsapp_image_queue")
        assert len(cursor.fetchall()) == 0


@patch("backend.routers.whatsapp.send_whatsapp_message")
def test_scenario_17_done_keyword_with_nothing_queued_replies(mock_send):
    """"done" with nothing pending should tell the sender rather than stay
    silent -- silence there would just look like the bot ignored them."""
    bt = BackgroundTasks()
    done_message = {"id": "done1", "from": "99999", "timestamp": "1000", "type": "text", "text": {"body": "done"}}
    handle_whatsapp_message(bt, done_message)
    run_bg_tasks(bt)

    mock_send.assert_called_once()
    args, _ = mock_send.call_args
    assert args[0] == "99999"


@patch("backend.routers.whatsapp.download_meta_media")
def test_scenario_18_irrelevant_text_does_not_trigger_flush(mock_download):
    """A plain text message that isn't a "done"-style keyword should be
    ignored entirely -- no flush task scheduled for it."""
    bt = BackgroundTasks()
    handle_whatsapp_message(bt, {"id": "m1", "from": "12345", "timestamp": "1000", "type": "text", "text": {"body": "hello"}})
    assert len(bt.tasks) == 0


@patch("backend.routers.whatsapp.download_meta_media")
@patch("backend.routers.purchase_drafts.process_async_extraction")
@patch("backend.routers.whatsapp.time.sleep")
def test_scenario_19_bulk_invoices_stay_separated_by_album(mock_sleep, mock_extract, mock_download):
    """The core bulk-upload scenario this was built for: several invoices
    sent from the same number in one burst, some multi-page. As long as each
    invoice's page(s) are selected and sent together (one multi-select album
    per invoice -- the natural WhatsApp gesture), they must end up as
    separate, correctly-paged drafts -- not one merged draft -- no matter how
    close together they arrive."""
    mock_download.return_value = (VALID_IMG_BYTES, "mock.jpg", "image/jpeg")
    bt = BackgroundTasks()

    # Invoice A: a 2-page album sent together.
    handle_whatsapp_message(bt, create_mock_message("a1", "12345", "1000"), group_id="album_A", expected_count=2)
    handle_whatsapp_message(bt, create_mock_message("a2", "12345", "1000"), group_id="album_A", expected_count=2)

    # Invoice B: a separate 1-page album, sent moments later in the same bulk burst.
    handle_whatsapp_message(bt, create_mock_message("b1", "12345", "1001"), group_id="album_B", expected_count=1)

    run_bg_tasks(bt)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM purchase_drafts ORDER BY id")
        drafts = cursor.fetchall()
        assert [d["id"] for d in drafts] == ["wa_a1", "wa_b1"]

    assert mock_extract.call_count == 2
    page_counts = sorted(len(call.args[1]) for call in mock_extract.call_args_list)
    assert page_counts == [1, 2]  # invoice A's 2 pages stayed together; B stayed separate


# ── Robustness: a permanently-missing album sibling, and restart recovery ──

def test_scenario_20_empty_group_check_is_terminal_not_a_retry_loop():
    """Regression guard for a real bug: once every message in an album has
    been flushed (deleted), a redundant/late check for a sibling that
    already got swept up in that flush must treat "nothing left here" as
    done -- not as "still waiting," which previously retried forever since
    an empty group has no timestamp to ever satisfy the max-wait fallback."""
    assert _is_album_group_complete("some_group_with_nothing_in_it", expected_count=2) is True


@patch("backend.routers.whatsapp.download_meta_media")
@patch("backend.routers.purchase_drafts.process_async_extraction")
@patch("backend.routers.whatsapp.time.sleep")
def test_scenario_21_incomplete_album_retries_instead_of_flushing_early(mock_sleep, mock_extract, mock_download):
    """An album still short of its expected page count, and not yet past
    ALBUM_MAX_WAIT_SECONDS, must not flush early -- it should reschedule
    its own check rather than give up permanently (there's no sibling
    message to rely on instead, unlike a lone group)."""
    mock_download.return_value = (VALID_IMG_BYTES, "mock.jpg", "image/jpeg")
    bt = BackgroundTasks()

    # Only 1 of an expected 2 pages has arrived.
    handle_whatsapp_message(bt, create_mock_message("only1", "12345", "1000"), group_id="album_incomplete", expected_count=2)
    bt.tasks[0].func(*bt.tasks[0].args, **bt.tasks[0].kwargs)  # run just this one check, not any reschedule

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM purchase_drafts")
        assert len(cursor.fetchall()) == 0  # not flushed -- still waiting on page 2
        cursor.execute("SELECT * FROM whatsapp_image_queue WHERE group_id = 'album_incomplete'")
        assert len(cursor.fetchall()) == 1  # page 1 is still sitting there, unclaimed

    mock_extract.assert_not_called()


@patch("backend.routers.whatsapp.download_meta_media")
@patch("backend.routers.purchase_drafts.process_async_extraction")
@patch("backend.routers.whatsapp.time.sleep")
def test_scenario_22_incomplete_album_flushes_once_past_max_wait(mock_sleep, mock_extract, mock_download):
    """Once an incomplete album has been sitting past ALBUM_MAX_WAIT_SECONDS
    (a sibling's download permanently failed, or otherwise never arrived),
    the pages that did make it must still get processed -- not lost forever."""
    mock_download.return_value = (VALID_IMG_BYTES, "mock.jpg", "image/jpeg")
    bt = BackgroundTasks()

    handle_whatsapp_message(bt, create_mock_message("only1", "12345", "1000"), group_id="album_stuck", expected_count=2)

    # Backdate this row's created_at past the max-wait ceiling, simulating
    # that enough real time has passed with the 2nd page never showing up.
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE whatsapp_image_queue SET created_at = ? WHERE group_id = 'album_stuck'",
            ((datetime.now() - timedelta(seconds=ALBUM_MAX_WAIT_SECONDS + 5)).isoformat(),)
        )
        conn.commit()

    bt.tasks[0].func(*bt.tasks[0].args, **bt.tasks[0].kwargs)  # run just this one check
    run_bg_tasks(bt)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM purchase_drafts")
        drafts = cursor.fetchall()
        assert len(drafts) == 1
        assert drafts[0]["id"] == "wa_only1"

    mock_extract.assert_called_once()
    assert len(mock_extract.call_args[0][1]) == 1  # just the one page that did arrive


@patch("backend.routers.whatsapp.download_meta_media")
@patch("backend.routers.purchase_drafts.process_async_extraction")
def test_scenario_23_startup_orphan_recovery_flushes_lost_in_memory_tasks(mock_extract, mock_download):
    """A message queued right before a restart has no scheduled task left to
    ever flush it (BackgroundTasks are in-memory only). The startup sweep
    must find anything sitting unclaimed too long and process it anyway."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO whatsapp_image_queue
            (message_id, sender, message_timestamp, filename, content_type, file_bytes, group_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "orphan1", "55555", "1000", "orphan.jpg", "image/jpeg", VALID_IMG_BYTES, "lone_orphan",
            (datetime.now() - timedelta(minutes=5)).isoformat(),
        ))
        conn.commit()

    _startup_orphan_recovery()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM purchase_drafts")
        drafts = cursor.fetchall()
        assert len(drafts) == 1
        assert drafts[0]["id"] == "wa_orphan1"

        cursor.execute("SELECT * FROM whatsapp_image_queue WHERE group_id = 'lone_orphan'")
        assert len(cursor.fetchall()) == 0


def test_scenario_24_startup_orphan_recovery_ignores_recent_rows():
    """A row that's only just arrived (well within any normal debounce
    window) must NOT be swept up as an "orphan" -- its own in-memory check
    is presumably still pending, not lost."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO whatsapp_image_queue
            (message_id, sender, message_timestamp, filename, content_type, file_bytes, group_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "fresh1", "66666", "1000", "fresh.jpg", "image/jpeg", VALID_IMG_BYTES, "lone_fresh",
            datetime.now().isoformat(),
        ))
        conn.commit()

    _startup_orphan_recovery()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM whatsapp_image_queue WHERE group_id = 'lone_fresh'")
        assert len(cursor.fetchall()) == 1  # left alone


@patch("backend.routers.whatsapp.send_whatsapp_done_button")
@patch("backend.routers.whatsapp.download_meta_media")
def test_scenario_25_done_button_sent_once_per_invoice_not_per_page(mock_download, mock_button):
    """The "Done" button prompt should fire once for the FIRST page of a new
    lone (non-album) invoice, and never again for subsequent pages of the
    same still-open invoice -- otherwise every page would re-spam the button."""
    mock_download.return_value = (VALID_IMG_BYTES, "mock.jpg", "image/jpeg")
    bt = BackgroundTasks()

    handle_whatsapp_message(bt, create_mock_message("msg1", "77777", "1000"))
    mock_button.assert_called_once_with("77777")

    mock_button.reset_mock()
    handle_whatsapp_message(bt, create_mock_message("msg2", "77777", "1005"))
    mock_button.assert_not_called()


@patch("backend.routers.whatsapp.send_whatsapp_done_button")
@patch("backend.routers.whatsapp.download_meta_media")
def test_scenario_26_done_button_not_sent_for_album_pages(mock_download, mock_button):
    """Album pages already have a hard signal (expected_count) to auto-flush
    on -- they don't need the button nudge, so it shouldn't be sent for them."""
    mock_download.return_value = (VALID_IMG_BYTES, "mock.jpg", "image/jpeg")
    bt = BackgroundTasks()

    handle_whatsapp_message(bt, create_mock_message("msg_a1", "88888", "1000"), group_id="album_x", expected_count=2)
    handle_whatsapp_message(bt, create_mock_message("msg_a2", "88888", "1000"), group_id="album_x", expected_count=2)

    mock_button.assert_not_called()


@patch("backend.routers.whatsapp.download_meta_media")
@patch("backend.routers.purchase_drafts.process_async_extraction")
@patch("backend.routers.whatsapp.time.sleep")
def test_scenario_27_done_button_tap_flushes_without_waiting_for_debounce(mock_sleep, mock_extract, mock_download):
    """Tapping the Done button (an 'interactive'/'button_reply' message
    carrying _DONE_BUTTON_ID back) must flush immediately, exactly like
    typing a _DONE_KEYWORDS word -- the whole point of offering it."""
    mock_download.return_value = (VALID_IMG_BYTES, "mock.jpg", "image/jpeg")
    bt = BackgroundTasks()

    handle_whatsapp_message(bt, create_mock_message("msg1", "12345", "1000"))
    # msg1's own debounce-check task is still pending, unexecuted.

    button_tap = {
        "id": "tap1", "from": "12345", "timestamp": "1002", "type": "interactive",
        "interactive": {"type": "button_reply", "button_reply": {"id": _DONE_BUTTON_ID, "title": "✅ Done"}},
    }
    handle_whatsapp_message(bt, button_tap)

    # Run only the task the button tap just scheduled -- not msg1's own
    # still-pending natural flush -- to prove the tap alone is sufficient.
    tap_task = bt.tasks[-1]
    tap_task.func(*tap_task.args, **tap_task.kwargs)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM purchase_drafts")
        drafts = cursor.fetchall()
        assert len(drafts) == 1
        assert drafts[0]["id"] == "wa_msg1"

        cursor.execute("SELECT * FROM whatsapp_image_queue")
        assert len(cursor.fetchall()) == 0


@patch("backend.routers.whatsapp.download_meta_media")
def test_scenario_28_button_tap_with_unrecognized_id_does_not_flush(mock_download):
    """An interactive reply carrying some OTHER button id (not the Done
    button we sent) must not be treated as a flush signal."""
    bt = BackgroundTasks()
    other_tap = {
        "id": "tap2", "from": "12345", "timestamp": "1000", "type": "interactive",
        "interactive": {"type": "button_reply", "button_reply": {"id": "some_other_button", "title": "X"}},
    }
    handle_whatsapp_message(bt, other_tap)
    assert len(bt.tasks) == 0
