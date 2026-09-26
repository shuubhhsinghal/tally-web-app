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
def get_meta_phone_number_id(): return os.getenv("META_PHONE_NUMBER_ID")


def send_whatsapp_message(to: str, text: str) -> None:
    """Sends a plain-text WhatsApp reply to `to` via the Graph API. Best-effort:
    a failed reply (missing config, network error, Meta rejecting the number)
    is logged and swallowed rather than raised, so it can never take down the
    extraction pipeline that triggered it -- the draft itself is the source
    of truth either way."""
    if os.environ.get("TESTING") == "true":
        # Same gate as database.py/main.py -- .env commonly holds a real
        # META_ACCESS_TOKEN for live testing (see project memory on the live
        # Tally connector setup), so this must be hard-blocked here rather
        # than relying on tests happening not to reach this call.
        print(f"[TESTING] Skipping real WhatsApp reply to {to}: {text[:60]!r}")
        return

    access_token = get_meta_access_token()
    phone_number_id = get_meta_phone_number_id()
    if not access_token or not phone_number_id:
        print(f"Skipping WhatsApp reply to {to}: META_ACCESS_TOKEN or META_PHONE_NUMBER_ID not configured")
        return

    api_version = get_meta_api_version()
    url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"Failed to send WhatsApp reply to {to}: {e}")


def send_whatsapp_done_button(to: str) -> None:
    """Sends a WhatsApp 'reply button' message -- the Cloud API's equivalent
    of a Telegram inline keyboard -- so a sender can tap to close out an
    invoice instead of having to type the "done" keyword. Purely a UX layer
    on top of the existing debounce/text-keyword flush path: same best-effort
    semantics as send_whatsapp_message (a failed send is logged and swallowed,
    never raised), since the button is never the only way to flush a group."""
    if os.environ.get("TESTING") == "true":
        print(f"[TESTING] Skipping real WhatsApp done-button to {to}")
        return

    access_token = get_meta_access_token()
    phone_number_id = get_meta_phone_number_id()
    if not access_token or not phone_number_id:
        print(f"Skipping WhatsApp done-button to {to}: META_ACCESS_TOKEN or META_PHONE_NUMBER_ID not configured")
        return

    api_version = get_meta_api_version()
    url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": "Got it! Send more pages if this invoice continues, or tap Done once you've sent them all."},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": _DONE_BUTTON_ID, "title": "✅ Done"}},
                ],
            },
        },
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"Failed to send WhatsApp done-button to {to}: {e}")


def _format_extraction_reply(success: bool, extracted_data: dict = None, error: str = None) -> str:
    if not success:
        return (
            "Sorry, we couldn't process that invoice automatically. "
            "Please make sure the photo is clear and shows the full invoice, "
            "or open the app to review/re-upload it manually."
        )

    supplier = (extracted_data or {}).get("supplier") or "Unknown supplier"
    item_count = len((extracted_data or {}).get("items") or [])
    total = (extracted_data or {}).get("grand_total_printed")
    try:
        total_str = f"Rs. {float(total):,.2f}"
    except (TypeError, ValueError):
        total_str = "not detected"

    return (
        f"Invoice processed: {supplier}\n"
        f"Items: {item_count}\n"
        f"Total: {total_str}\n\n"
        "Open the app to review before it's posted to Tally."
    )


def _make_whatsapp_extraction_callback(sender: str):
    """Builds the on_complete callback passed into enqueue_draft_extraction so
    the WhatsApp sender gets told what happened, once extraction actually
    finishes (not just once the image was queued)."""
    def _callback(success: bool, extracted_data: dict = None, error: str = None):
        send_whatsapp_message(sender, _format_extraction_reply(success, extracted_data, error))
    return _callback


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

    _startup_orphan_recovery()


def _startup_orphan_recovery():
    """A message whose debounce/completion check was still pending in memory
    when the process last stopped (e.g. a deploy) has no scheduled task left
    to ever flush it -- BackgroundTasks are in-memory only and don't survive
    a restart. Find anything that's been sitting unclaimed for longer than
    any normal debounce window could explain, and flush it now, synchronously,
    since there's no live request here to hand a real BackgroundTasks to."""
    orphan_cutoff = (datetime.now() - timedelta(minutes=2)).isoformat()
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT sender, group_id FROM whatsapp_image_queue
                WHERE batch_id IS NULL AND created_at < ? AND group_id IS NOT NULL
            """, (orphan_cutoff,))
            orphans = cursor.fetchall()
    except Exception as e:
        print(f"WhatsApp startup orphan sweep failed (table might not exist yet): {e}")
        return

    for row in orphans:
        bt = BackgroundTasks()
        try:
            if _flush_group(bt, row["sender"], row["group_id"]):
                print(f"Recovered orphaned WhatsApp image(s) for {row['sender']} (group {row['group_id']}) left over from a restart.")
            for task in bt.tasks:
                task.func(*task.args, **task.kwargs)
        except Exception as e:
            print(f"WhatsApp startup orphan recovery failed for {row['sender']}/{row['group_id']}: {e}")


# How long to wait, after one image arrives with nothing newer following it,
# before treating a lone (non-album) group as "done" and processing it. Kept
# short because it's a *sliding* window (see flush_whatsapp_queue below) --
# every new image in the same group resets it, so this is the steady-state
# latency for a single-page invoice, not a batch-wide cap.
DEFAULT_DEBOUNCE_SECONDS = 10

# Meta delivers photos picked and sent together (one multi-select share) as
# several messages inside the SAME webhook POST -- a real signal they're one
# invoice's pages, sent deliberately as a set, not a guess based on timing.
# Still debounce them briefly (media downloads for the other pages are still
# in flight as background tasks) but far less than a lone photo's window.
ALBUM_DEBOUNCE_SECONDS = 3

# If an album never reaches its expected message count (a sibling's download
# failed, or it otherwise never arrived), don't wait on it forever -- once a
# group has been sitting this long, process whatever pages did make it
# rather than silently losing the whole invoice.
ALBUM_MAX_WAIT_SECONDS = 30

STALE_BATCH_MINUTES = 5

# Text a sender can send to force-flush their queue immediately instead of
# waiting out the debounce window -- the escape hatch for "I sent everything,
# don't wait ~10s to hear back", and for cleanly closing off one invoice
# before starting the next when sending pages one at a time rather than as
# a multi-select album.
_DONE_KEYWORDS = {"done", "finish", "finished", "end", "complete"}

# The reply-button id sent by send_whatsapp_done_button -- a tap on it arrives
# as an "interactive"/"button_reply" message carrying this same id back, and
# is treated identically to typing a _DONE_KEYWORDS word.
_DONE_BUTTON_ID = "wa_done_confirm"


def _new_album_group_id() -> str:
    return f"album_{uuid.uuid4()}"


def _new_lone_group_id() -> str:
    return f"lone_{uuid.uuid4()}"


def _get_or_create_lone_group_id(sender: str) -> tuple[str, bool]:
    """Every image that did NOT arrive as part of a multi-select album needs
    to land in the sender's currently-open "lone" group, if one exists, so
    that pages photographed and sent one tap at a time still merge into a
    single invoice -- exactly like before this file supported albums at all.
    A lone group is "open" as long as it still has an unclaimed row; once it's
    flushed there's nothing left to find, so the next lone image starts a
    fresh group. The 'lone_' prefix (see _new_lone_group_id) is what lets this
    query skip over any pending album rows for the same sender -- those must
    never be merged into just because they happen to still be unclaimed.

    Returns (group_id, is_new) -- is_new is True only when no open group
    existed and one had to be created, i.e. this is the FIRST page of a
    potential invoice. handle_whatsapp_message uses that to send the "Done"
    button prompt once per invoice rather than once per page."""
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT group_id FROM whatsapp_image_queue
                WHERE sender = ? AND batch_id IS NULL AND group_id LIKE 'lone_%'
                ORDER BY message_timestamp DESC, message_id DESC LIMIT 1
            """, (sender,))
            row = cursor.fetchone()
        except sqlite3.OperationalError as e:
            if "no such table" in str(e).lower() or "no such column" in str(e).lower():
                row = None
            else:
                raise
    if row:
        return row["group_id"], False
    return _new_lone_group_id(), True


def _has_newer_unclaimed_message(group_id: str, after_timestamp: str, after_message_id: str) -> bool:
    """True if some still-unclaimed image in `group_id` arrived strictly after
    (after_timestamp, after_message_id). WhatsApp timestamps are whole seconds
    -- a fast burst can share one -- so message_id (always unique) breaks ties.
    Used by flush_whatsapp_queue for LONE groups to back off: if a newer image
    is still sitting there, THAT image's own debounce check (scheduled the
    same delay after it arrived) will fire later and do the real flush once
    things actually go quiet."""
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT message_id, message_timestamp FROM whatsapp_image_queue
                WHERE group_id = ? AND batch_id IS NULL
            """, (group_id,))
            rows = cursor.fetchall()
        except sqlite3.OperationalError as e:
            if "no such table" in str(e).lower():
                return False
            raise

    after = (int(after_timestamp), after_message_id)
    return any((int(r["message_timestamp"]), r["message_id"]) > after for r in rows)


def _is_album_group_complete(group_id: str, expected_count: int) -> bool:
    """True once every message Meta said belonged to this album has actually
    finished downloading and landed in the queue -- OR once the group has
    been waiting long enough that a missing sibling (a failed download, or
    any other reason one message never made it) should no longer hold the
    rest hostage. Unlike a lone group, an album's membership is known exactly
    at receipt time (Meta told us how many messages were bundled in that one
    webhook delivery), so instead of guessing from timestamps, this compares
    against that count -- with ALBUM_MAX_WAIT_SECONDS as the escape hatch so
    one permanently-missing page doesn't strand the whole invoice forever."""
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT COUNT(*) as c, MIN(created_at) as first_seen FROM whatsapp_image_queue
                WHERE group_id = ? AND batch_id IS NULL
            """, (group_id,))
            row = cursor.fetchone()
        except sqlite3.OperationalError as e:
            if "no such table" in str(e).lower():
                return True
            raise

    count = row["c"] if row else 0
    if count == 0:
        # Nothing left in this group -- either a sibling's check already
        # flushed it, or (redundantly) another check is racing this one.
        # Either way there's nothing to wait for: treat as "done" so this
        # terminates instead of retrying forever (there's no timestamp
        # here to ever satisfy the max-wait check below).
        return True
    if count >= expected_count:
        return True

    first_seen = datetime.fromisoformat(row["first_seen"])
    return (datetime.now() - first_seen).total_seconds() > ALBUM_MAX_WAIT_SECONDS


def _reclaim_stale_batches():
    """Safety net: if a process crashed mid-flush after claiming a batch but
    before cleaning it up, its rows would otherwise sit claimed forever.
    Run once per flush attempt (regardless of which sender/group triggered
    it), same as before this file partitioned by group_id."""
    five_mins_ago = (datetime.now() - timedelta(minutes=STALE_BATCH_MINUTES)).isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                UPDATE whatsapp_image_queue
                SET batch_id = NULL, batch_claimed_at = NULL
                WHERE batch_id IS NOT NULL AND batch_claimed_at < ?
            """, (five_mins_ago,))
            conn.commit()
        except sqlite3.OperationalError as e:
            if "no such table" in str(e).lower():
                return
            raise


def _flush_one_group(background_tasks: BackgroundTasks, sender: str, group_id: str) -> bool:
    """Claims and processes just the rows belonging to `group_id` -- one
    invoice's worth -- into its own draft. Returns True if there was
    anything to process. Scoping the claim to (sender, group_id) rather than
    "everything for sender" is what lets a bulk send of several invoices turn
    into several correctly-separated drafts instead of one merged one."""
    batch_id = str(uuid.uuid4())
    now_iso = datetime.now().isoformat()

    # PHASE 1 - CLAIM
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                UPDATE whatsapp_image_queue
                SET batch_id = ?, batch_claimed_at = ?
                WHERE sender = ? AND group_id = ? AND batch_id IS NULL
            """, (batch_id, now_iso, sender, group_id))

            cursor.execute("SELECT * FROM whatsapp_image_queue WHERE batch_id = ?", (batch_id,))
            rows = cursor.fetchall()
            conn.commit()
        except sqlite3.OperationalError as e:
            if "no such table" in str(e).lower():
                print(f"Warning: whatsapp_image_queue table missing. Cannot flush group {group_id} for {sender}.")
                return False
            raise

    if not rows:
        return False  # Another task already flushed this group, or it's empty

    # Sort in memory: primary by timestamp, tie-breaker by message_id
    sorted_rows = sorted(rows, key=lambda r: (int(r["message_timestamp"]), r["message_id"]))
    files_data = [(r["file_bytes"], r["filename"], r["content_type"]) for r in sorted_rows]

    deterministic_draft_id = f"wa_{sorted_rows[0]['message_id']}"

    # PHASE 2 - PROCESS (outside DB transaction)
    try:
        # Call the existing shared pipeline with deterministic draft_id.
        # WhatsApp invoices always use Qwen 3.5 Flash, regardless of the
        # global Settings toggle used for manual uploads -- this also skips
        # the scanner-style enhancement pipeline (see enqueue_draft_extraction),
        # so the raw photo is sent to the model as-is. on_complete fires once
        # extraction itself finishes (success or failure), so the sender
        # hears back with a real result rather than just "we got it".
        enqueue_draft_extraction(
            background_tasks, files_data, draft_id=deterministic_draft_id,
            provider_override="qwen", on_complete=_make_whatsapp_extraction_callback(sender)
        )
        success = True
    except Exception as e:
        print(f"Failed to enqueue extraction for group {group_id} (batch {batch_id}): {e}")
        success = False
        send_whatsapp_message(sender, _format_extraction_reply(success=False))

    # PHASE 3 - CLEANUP
    with get_db() as conn:
        cursor = conn.cursor()
        if success:
            cursor.execute("DELETE FROM whatsapp_image_queue WHERE batch_id = ?", (batch_id,))
        else:
            # Revert so they can be tried again safely
            cursor.execute("UPDATE whatsapp_image_queue SET batch_id = NULL, batch_claimed_at = NULL WHERE batch_id = ?", (batch_id,))
        conn.commit()

    return True


def _flush_group(background_tasks: BackgroundTasks, sender: str, group_id: str = None) -> bool:
    """If group_id is given, claims+processes just that one invoice's group.
    If group_id is None, flushes EVERY group currently pending for `sender`
    -- each still becomes its own separate draft. Used by the "done" command
    (finish everything queued, whatever invoices it spans) and by direct/
    manual force-flush calls. Always reclaims stale batches first, exactly
    as before this file supported per-group flushing. Returns True if
    anything was processed."""
    _reclaim_stale_batches()

    if group_id is not None:
        return _flush_one_group(background_tasks, sender, group_id)

    with get_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT DISTINCT group_id FROM whatsapp_image_queue
                WHERE sender = ? AND batch_id IS NULL
            """, (sender,))
            pending_group_ids = [r["group_id"] for r in cursor.fetchall()]
        except sqlite3.OperationalError as e:
            if "no such table" in str(e).lower():
                return False
            raise

    return any([_flush_one_group(background_tasks, sender, gid) for gid in pending_group_ids])


def flush_whatsapp_queue(
    background_tasks: BackgroundTasks,
    sender: str,
    group_id: str = None,
    message_timestamp: str = None,
    message_id: str = None,
    delay_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
    expected_count: int = None,
):
    """
    Debounce check for one group. Sleeps `delay_seconds`, then flushes --
    but only once that group is confirmed to actually be done:
      - an album group (expected_count given) is done once every message
        Meta said belonged to it has finished downloading (_is_album_group_complete)
      - a lone group (message_timestamp/message_id given) is done once
        nothing newer has arrived in it since (_has_newer_unclaimed_message) --
        a sliding window, since every new image in the group reschedules its
        own check with a fresh (timestamp, message_id)

    If a lone group isn't done yet, this backs off silently: a newer image's
    own check (scheduled the same delay after IT arrived) will retry later.
    If an album isn't done yet, THIS check reschedules itself (there's no
    newer sibling message to rely on instead, since album membership is
    fixed at receipt) -- until either it completes or ALBUM_MAX_WAIT_SECONDS
    passes, at which point _is_album_group_complete gives up waiting and
    this flushes whatever pages did arrive.

    group_id=None (no debounce context at all) skips every check and forces
    an immediate flush of EVERY group pending for `sender` -- used for
    direct/manual force-flush calls.
    """
    time.sleep(delay_seconds)

    if group_id is not None:
        if expected_count is not None:
            if not _is_album_group_complete(group_id, expected_count):
                # A sibling's download is still in flight and nothing else
                # will check on this group -- keep checking ourselves.
                background_tasks.add_task(
                    flush_whatsapp_queue, background_tasks, sender, group_id,
                    None, None, ALBUM_DEBOUNCE_SECONDS, expected_count,
                )
                return
        elif message_timestamp is not None and message_id is not None:
            if _has_newer_unclaimed_message(group_id, message_timestamp, message_id):
                return  # a later message in this group will handle the flush

    _flush_group(background_tasks, sender, group_id)


def _handle_done_command(background_tasks: BackgroundTasks, sender: str):
    """Immediately flushes every group queued for `sender`, bypassing the
    debounce entirely -- triggered by the sender texting a keyword like
    "done". Each pending invoice still becomes its own draft. Replies
    directly if there was nothing queued, since silence here would just
    look like the bot ignored them."""
    processed = _flush_group(background_tasks, sender, group_id=None)
    if not processed:
        send_whatsapp_message(sender, "No pending invoice photos found to process.")


def handle_whatsapp_message(
    background_tasks: BackgroundTasks,
    message: dict,
    group_id: str = None,
    expected_count: int = 1,
):
    """Process a single WhatsApp message event.
    group_id/expected_count describe the "send action" this message was part
    of, as determined by receive_webhook from the raw webhook payload:
      - expected_count > 1 means Meta bundled this message with others from
        the same sender in one webhook delivery (a deliberate multi-select
        share) -- group_id is shared by all of them, fixed, and never grows.
      - expected_count == 1 (the default) means this message arrived alone.
        group_id is then resolved at insert time to whichever "lone" group is
        currently open for this sender (see _get_or_create_lone_group_id), so
        pages sent one tap at a time still merge into one invoice."""
    message_id = message.get("id")
    sender = message.get("from")
    timestamp = message.get("timestamp", "0")

    if not message_id or not sender:
        return

    # Deduplication
    if not check_and_mark_message_processed(message_id):
        return  # Already processed

    # A "done" text forces an immediate flush of whatever's queued for this
    # sender, bypassing the debounce -- the explicit escape hatch for a fast
    # reply, or for cleanly separating invoices sent one photo at a time.
    if message.get("type") == "text":
        body = (message.get("text") or {}).get("body", "")
        if body.strip().lower().rstrip(".!") in _DONE_KEYWORDS:
            background_tasks.add_task(_handle_done_command, background_tasks, sender)
        return  # No media on a text message either way

    # A tap on the "Done" button sent by send_whatsapp_done_button arrives as
    # this message type, carrying the same button id back -- treated exactly
    # like typing a _DONE_KEYWORDS word.
    if message.get("type") == "interactive":
        interactive = message.get("interactive") or {}
        if interactive.get("type") == "button_reply":
            button_id = (interactive.get("button_reply") or {}).get("id")
            if button_id == _DONE_BUTTON_ID:
                background_tasks.add_task(_handle_done_command, background_tasks, sender)
        return  # No media on an interactive reply either

    # Check for media we care about
    media_id = None
    if "image" in message:
        media_id = message["image"].get("id")
    elif "document" in message:
        media_id = message["document"].get("id")

    if not media_id:
        return # Ignore other message types (audio, location, reactions, ...)

    try:
        file_bytes, filename, content_type = download_meta_media(media_id)

        is_album = expected_count > 1
        # group_id should always be set by receive_webhook when is_album is
        # true -- these final fallbacks only guard against a direct/manual
        # call passing an inconsistent combination (e.g. expected_count>1
        # with no group_id), which would otherwise insert a row no query
        # could ever match again.
        is_new_lone_group = False
        if is_album:
            resolved_group_id = group_id or _new_album_group_id()
        elif group_id:
            resolved_group_id = group_id
        else:
            resolved_group_id, is_new_lone_group = _get_or_create_lone_group_id(sender)

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO whatsapp_image_queue
                (message_id, sender, message_timestamp, filename, content_type, file_bytes, group_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                message_id, sender, timestamp, filename, content_type, file_bytes, resolved_group_id,
                # Set explicitly (rather than relying on the column's SQL-level
                # CURRENT_TIMESTAMP default, which is UTC) so it's directly
                # comparable to the datetime.now() (local time) used
                # everywhere else in this file -- e.g. _is_album_group_complete's
                # max-wait check and the startup orphan sweep.
                datetime.now().isoformat(),
            ))
            conn.commit()

        # Spawn this message's own debounce check -- see flush_whatsapp_queue.
        delay = ALBUM_DEBOUNCE_SECONDS if is_album else DEFAULT_DEBOUNCE_SECONDS
        background_tasks.add_task(
            flush_whatsapp_queue, background_tasks, sender, resolved_group_id,
            timestamp, message_id, delay, expected_count if is_album else None,
        )

        # Only on the FIRST page of a new lone (non-album) invoice -- once per
        # invoice, not once per page -- so the sender can tap instead of
        # typing "done" to close it out without waiting for the debounce.
        if is_new_lone_group:
            send_whatsapp_done_button(sender)

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

            # Messages from the same sender delivered in this one webhook
            # call were picked and sent together as a single WhatsApp action
            # (a multi-select share) -- a real "these are one invoice" signal,
            # not a timing guess. They share one fixed group_id and a known
            # expected_count; a lone message (count 1) gets neither here --
            # handle_whatsapp_message resolves its group at insert time.
            sender_counts = {}
            for message in messages:
                sender_counts[message.get("from")] = sender_counts.get(message.get("from"), 0) + 1
            album_group_ids = {}

            for message in messages:
                sender = message.get("from")
                count = sender_counts.get(sender, 1)
                if count > 1:
                    if sender not in album_group_ids:
                        album_group_ids[sender] = _new_album_group_id()
                    group_id = album_group_ids[sender]
                else:
                    group_id = None
                # We do the heavy lifting in background to ack Meta quickly
                background_tasks.add_task(handle_whatsapp_message, background_tasks, message, group_id, count)

    # 4. Acknowledge Meta quickly
    return {"status": "ok"}
