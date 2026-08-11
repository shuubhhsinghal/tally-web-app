import sqlite3
import json
import os
import re
import time
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, List

DB_PATH = os.path.join(os.path.dirname(__file__), "tally_sync.db")

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ledgers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                parent TEXT,
                cost_centre BOOLEAN DEFAULT 0
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stock_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                unit TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS uoms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS item_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_name TEXT UNIQUE NOT NULL,
                mapped_name TEXT NOT NULL
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS purchase_rates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_item_name TEXT UNIQUE NOT NULL,
                latest_rate REAL NOT NULL,
                supplier TEXT,
                purchase_date TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS offline_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_type TEXT NOT NULL,
                payload TEXT,
                xml_data TEXT NOT NULL,
                status TEXT DEFAULT 'PENDING',
                error_message TEXT,
                description TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bank_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bank_account_name TEXT NOT NULL,
                keyword TEXT NOT NULL,
                target_ledger TEXT NOT NULL,
                cost_center TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_masters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL CHECK (entity_type IN ('LEDGER', 'ITEM', 'UOM')),
                normalized_name TEXT NOT NULL,
                original_name TEXT NOT NULL,
                queue_id INTEGER UNIQUE NOT NULL,
                status TEXT DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'SYNCED_WAITING_CONFIRMATION', 'FAILED')),
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(entity_type, normalized_name)
            )
        """)
        
        # --- Phase 2B.2B: Bank Import Session Tables ---
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bank_import_sessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_key TEXT UNIQUE NOT NULL,
                bank_ledger TEXT NOT NULL,
                row_count   INTEGER NOT NULL,
                status      TEXT NOT NULL DEFAULT 'IN_PROGRESS'
                            CHECK(status IN ('IN_PROGRESS', 'OFFLINE_QUEUED')),
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bank_import_rows (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id       INTEGER NOT NULL REFERENCES bank_import_sessions(id),
                row_key          TEXT NOT NULL,
                date             TEXT NOT NULL,
                narration_source TEXT NOT NULL,
                withdraw_minor   INTEGER NOT NULL DEFAULT 0,
                deposit_minor    INTEGER NOT NULL DEFAULT 0,
                txn_direction    TEXT NOT NULL CHECK(txn_direction IN ('DEBIT', 'CREDIT')),
                ledger           TEXT NOT NULL DEFAULT 'Bank Suspense Account',
                cost_center      TEXT,
                status           TEXT NOT NULL DEFAULT 'PENDING'
                                 CHECK(status IN ('PENDING', 'IN_FLIGHT', 'IMPORTED', 'FAILED', 'UNKNOWN')),
                error_message    TEXT,
                created_at       TEXT NOT NULL,
                updated_at       TEXT NOT NULL,
                UNIQUE(session_id, row_key)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_bank_import_rows_session_status
                ON bank_import_rows(session_id, status)
        """)

        # IN_FLIGHT crash recovery: any row left IN_FLIGHT from a prior crashed process
        # is unresolvable without Tally verification. Recover to UNKNOWN so it cannot be
        # automatically retried. This is safe here because init_db() is called exactly once
        # at application startup (lifespan handler in main.py) before any request is served.
        now = datetime.now().isoformat()
        cursor.execute("""
            UPDATE bank_import_rows
            SET status = 'UNKNOWN',
                error_message = 'Process interrupted while this voucher was being sent. Verify in Tally before retrying.',
                updated_at = ?
            WHERE status = 'IN_FLIGHT'
        """, (now,))

        # --- Supplier Column Mappings ---
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS supplier_column_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                supplier_key TEXT UNIQUE NOT NULL,
                supplier_name TEXT NOT NULL,
                mapping_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        # Migrations
        try:
            cursor.execute("ALTER TABLE stock_items ADD COLUMN last_purchase_rate FLOAT DEFAULT 0.0")
        except Exception:
            pass # Column exists
            
        try:
            cursor.execute("ALTER TABLE stock_items ADD COLUMN last_purchase_date TEXT")
        except Exception:
            pass # Column exists
            
        try:
            cursor.execute("ALTER TABLE offline_queue ADD COLUMN updated_at TEXT")
        except sqlite3.OperationalError:
            pass # Column exists
            
        try:
            cursor.execute("ALTER TABLE offline_queue ADD COLUMN is_hidden BOOLEAN DEFAULT 0")
        except sqlite3.OperationalError:
            pass # Column exists

        conn.commit()

# --- Helper Functions for Offline Queue ---
def queue_operation(operation_type: str, xml_data: str, payload: dict = None, description: str = ""):
    with get_db() as conn:
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        payload_str = json.dumps(payload) if payload else None
        cursor.execute("""
            INSERT INTO offline_queue (operation_type, payload, xml_data, status, description, created_at, updated_at)
            VALUES (?, ?, ?, 'PENDING', ?, ?, ?)
        """, (operation_type, payload_str, xml_data, description, now, now))
        conn.commit()
        return cursor.lastrowid

def queue_operations_bulk(operations: list):
    with get_db() as conn:
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        
        rows = []
        for op in operations:
            payload_str = json.dumps(op.get("payload")) if op.get("payload") else None
            rows.append((
                op["operation_type"], 
                payload_str, 
                op["xml_data"], 
                op.get("description", ""), 
                now, 
                now
            ))
            
        cursor.executemany("""
            INSERT INTO offline_queue (operation_type, payload, xml_data, status, description, created_at, updated_at)
            VALUES (?, ?, ?, 'PENDING', ?, ?, ?)
        """, rows)
        conn.commit()


def get_pending_queue():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM offline_queue WHERE status = 'PENDING' ORDER BY id ASC")
        return [dict(row) for row in cursor.fetchall()]

def update_queue_status(queue_id: int, status: str, error_message: str = None):
    with get_db() as conn:
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute("""
            UPDATE offline_queue 
            SET status = ?, error_message = ?, updated_at = ?
            WHERE id = ?
        """, (status, error_message, now, queue_id))
        conn.commit()

# --- Pending Masters Helpers ---
class MasterConflictException(Exception):
    pass

class MasterFailedException(Exception):
    pass

def normalize_master_name(name: str) -> str:
    display_name = re.sub(r'\s+', ' ', (name or "").strip())
    if not display_name:
        raise ValueError("Master name cannot be empty")
    return display_name.casefold(), display_name

def _check_definitions_conflict(entity_type: str, pending_payload: dict, new_payload: dict) -> bool:
    if entity_type == 'UOM':
        return False # name is the full definition
    elif entity_type == 'LEDGER':
        p1 = normalize_master_name(pending_payload.get('parent', ''))[0] if pending_payload.get('parent') else ''
        p2 = normalize_master_name(new_payload.get('parent', ''))[0] if new_payload.get('parent') else ''
        return p1 != p2
    elif entity_type == 'ITEM':
        u1 = normalize_master_name(pending_payload.get('uom', ''))[0] if pending_payload.get('uom') else ''
        u2 = normalize_master_name(new_payload.get('uom', ''))[0] if new_payload.get('uom') else ''
        return u1 != u2
    return False

def _check_confirmed_master_exists(cursor, entity_type: str, normalized_name: str, new_payload: dict) -> bool:
    if entity_type == 'LEDGER':
        cursor.execute("SELECT parent FROM ledgers WHERE LOWER(name) = ?", (normalized_name,))
        row = cursor.fetchone()
        if row:
            p1 = normalize_master_name(row['parent'])[0] if row['parent'] else ''
            p2 = normalize_master_name(new_payload.get('parent', ''))[0] if new_payload.get('parent') else ''
            if p1 != p2:
                raise MasterConflictException("A master with this name already exists in Tally with a conflicting definition.")
            return True
    elif entity_type == 'ITEM':
        cursor.execute("SELECT unit FROM stock_items WHERE LOWER(name) = ?", (normalized_name,))
        row = cursor.fetchone()
        if row:
            u1 = normalize_master_name(row['unit'])[0] if row['unit'] else ''
            u2 = normalize_master_name(new_payload.get('uom', ''))[0] if new_payload.get('uom') else ''
            if u1 != u2:
                raise MasterConflictException("A master with this name already exists in Tally with a conflicting definition.")
            return True
    elif entity_type == 'UOM':
        cursor.execute("SELECT name FROM uoms WHERE LOWER(name) = ?", (normalized_name,))
        if cursor.fetchone():
            return True
    return False

def is_master_confirmed_locally(entity_type: str, normalized_name: str, payload: dict) -> bool:
    with get_db() as conn:
        cursor = conn.cursor()
        return _check_confirmed_master_exists(cursor, entity_type, normalized_name, payload)

def check_master_exists_locally(entity_type: str, normalized_name: str, new_payload: dict) -> bool:
    with get_db() as conn:
        cursor = conn.cursor()
        if _check_confirmed_master_exists(cursor, entity_type, normalized_name, new_payload):
            return True
            
        cursor.execute("SELECT * FROM pending_masters WHERE entity_type = ? AND normalized_name = ?", (entity_type, normalized_name))
        pending_row = cursor.fetchone()
        if pending_row:
            if pending_row["status"] == "FAILED":
                return False
                
            cursor.execute("SELECT payload FROM offline_queue WHERE id = ?", (pending_row['queue_id'],))
            queue_row = cursor.fetchone()
            pending_payload = json.loads(queue_row['payload']) if queue_row and queue_row['payload'] else {}
            
            if _check_definitions_conflict(entity_type, pending_payload, new_payload):
                raise MasterConflictException("Materially conflicting master definition already pending.")
            return True
            
        return False

def get_master_states() -> dict:
    """
    Returns a dictionary grouped by entity type (ledgers, stock_items, uoms)
    containing objects for masters that are pending, syncing, or failed.
    """
    states = {
        "ledgers": [],
        "stock_items": [],
        "uoms": []
    }
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT entity_type, normalized_name, original_name, status, error_message FROM pending_masters")
        rows = cursor.fetchall()
        for row in rows:
            group = None
            if row['entity_type'] == 'LEDGER':
                group = "ledgers"
            elif row['entity_type'] == 'ITEM':
                group = "stock_items"
            elif row['entity_type'] == 'UOM':
                group = "uoms"
                
            if group:
                state = row['status'].lower()
                if state == "synced_waiting_confirmation":
                    state = "syncing"
                    
                states[group].append({
                    "name": row['original_name'],
                    "normalized_name": row['normalized_name'],
                    "state": state,
                    "error": row['error_message']
                })
                
    return states

def get_master_dependency_state(entity_type: str, raw_name: str, definition_payload: dict = None):
    """
    Returns (state_string, error_message).
    States: CONFIRMED, SYNCED_WAITING_CONFIRMATION, PENDING, FAILED, MISSING, CONFLICT
    """
    import json
    norm_name, _ = normalize_master_name(raw_name)
    with get_db() as conn:
        cursor = conn.cursor()
        
        cursor.execute("SELECT queue_id, status, error_message FROM pending_masters WHERE entity_type = ? AND normalized_name = ?", (entity_type, norm_name))
        row = cursor.fetchone()
        if row:
            if row['status'] == 'FAILED':
                return "FAILED", row['error_message']
                
            if definition_payload and entity_type == 'ITEM':
                cursor.execute("SELECT payload FROM offline_queue WHERE id = ?", (row['queue_id'],))
                qrow = cursor.fetchone()
                pending_payload = json.loads(qrow['payload']) if qrow and qrow['payload'] else {}
                if _check_definitions_conflict(entity_type, pending_payload, definition_payload):
                    u1 = pending_payload.get('uom', 'Unknown')
                    u2 = definition_payload.get('uom', 'Unknown')
                    return "CONFLICT", f"Item '{raw_name}' already pending with unit {u1}. This invoice is using {u2}."
            
            return row['status'], None
            
        if entity_type == 'LEDGER':
            cursor.execute("SELECT 1 FROM ledgers WHERE LOWER(name) = ?", (norm_name,))
            if cursor.fetchone(): return "CONFIRMED", None
        elif entity_type == 'ITEM':
            cursor.execute("SELECT unit FROM stock_items WHERE LOWER(name) = ?", (norm_name,))
            irow = cursor.fetchone()
            if irow:
                if definition_payload:
                    u1 = normalize_master_name(irow['unit'])[0] if irow['unit'] else ''
                    u2 = normalize_master_name(definition_payload.get('uom', ''))[0] if definition_payload.get('uom') else ''
                    if u1 != u2:
                        existing_uom = irow['unit'] or 'None'
                        attempted_uom = definition_payload.get('uom') or 'None'
                        return "CONFLICT", f"Item '{raw_name}' already exists with unit {existing_uom}. This invoice is using {attempted_uom}."
                return "CONFIRMED", None
        elif entity_type == 'UOM':
            cursor.execute("SELECT 1 FROM uoms WHERE LOWER(name) = ?", (norm_name,))
            if cursor.fetchone(): return "CONFIRMED", None
            
        return "MISSING", None


def is_master_pending_sync(state: str) -> bool:
    """
    Returns True if the state indicates the master exists locally and is waiting to sync with Tally.
    """
    if not state:
        return False
    return state.upper() in ("PENDING", "SYNCED_WAITING_CONFIRMATION", "QUEUED", "IN_PROGRESS", "OFFLINE_QUEUED")


def queue_master_operation(entity_type: str, name: str, operation_type: str, xml_data: str, payload: dict):
    normalized_name, display_name = normalize_master_name(name)
    
    for attempt in range(3):
        with get_db() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                
                # 1. Verify Confirmed
                if _check_confirmed_master_exists(cursor, entity_type, normalized_name, payload):
                    conn.commit()
                    return {"status": "exists_confirmed"}
                
                # 2. Check Pending (for conflict resolution/idempotency)
                cursor.execute("SELECT * FROM pending_masters WHERE entity_type = ? AND normalized_name = ?", (entity_type, normalized_name))
                pending_row = cursor.fetchone()
                if pending_row:
                    cursor.execute("SELECT payload FROM offline_queue WHERE id = ?", (pending_row['queue_id'],))
                    queue_row = cursor.fetchone()
                    pending_payload = json.loads(queue_row['payload']) if queue_row and queue_row['payload'] else {}
                    
                    if _check_definitions_conflict(entity_type, pending_payload, payload):
                        raise MasterConflictException("Materially conflicting master definition already pending.")
                    if pending_row["status"] == "FAILED":
                        raise MasterFailedException("Master creation previously failed. Resolve before creating.")
                    
                    conn.commit()
                    return {"status": "exists_pending", "queue_id": pending_row["queue_id"]}

                # 3. Insert Queue Row
                now = datetime.now().isoformat()
                payload_str = json.dumps(payload) if payload else None
                cursor.execute("""
                    INSERT INTO offline_queue (operation_type, payload, xml_data, status, description, created_at, updated_at)
                    VALUES (?, ?, ?, 'PENDING', ?, ?, ?)
                """, (operation_type, payload_str, xml_data, f"Create {entity_type.capitalize()}: {display_name}", now, now))
                queue_id = cursor.lastrowid
                
                # 4. Insert Pending Identity
                cursor.execute("""
                    INSERT INTO pending_masters (entity_type, normalized_name, original_name, queue_id, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'PENDING', ?, ?)
                """, (entity_type, normalized_name, display_name, queue_id, now, now))
                
                conn.commit()
                return {"status": "queued", "queue_id": queue_id}
                
            except sqlite3.IntegrityError:
                conn.rollback()
                # UNIQUE Race Loser Re-read Algorithm
                cursor.execute("SELECT * FROM pending_masters WHERE entity_type = ? AND normalized_name = ?", (entity_type, normalized_name))
                pending_row = cursor.fetchone()
                if pending_row:
                    cursor.execute("SELECT payload FROM offline_queue WHERE id = ?", (pending_row['queue_id'],))
                    queue_row = cursor.fetchone()
                    pending_payload = json.loads(queue_row['payload']) if queue_row and queue_row['payload'] else {}
                    
                    if _check_definitions_conflict(entity_type, pending_payload, payload):
                        raise MasterConflictException("Materially conflicting master definition already pending.")
                    if pending_row["status"] == "FAILED":
                        raise MasterFailedException("Master creation previously failed. Resolve before creating.")
                    return {"status": "exists_pending_concurrent", "queue_id": pending_row["queue_id"]}
                else:
                    # Winner disappeared between rollback and reread, retry
                    time.sleep(0.1)
                    continue
            except sqlite3.OperationalError as e:
                conn.rollback()
                if "database is locked" in str(e).lower():
                    time.sleep(0.2 * (attempt + 1))
                    continue
                raise
            except Exception:
                conn.rollback()
                raise
    raise Exception("Failed to queue master operation after retries due to database locks.")

def mark_master_synced(queue_id: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        try:
            now = datetime.now().isoformat()
            cursor.execute("UPDATE offline_queue SET status = 'SYNCED', updated_at = ? WHERE id = ?", (now, queue_id))
            cursor.execute("UPDATE pending_masters SET status = 'SYNCED_WAITING_CONFIRMATION', updated_at = ? WHERE queue_id = ?", (now, queue_id))
            conn.commit()
        except:
            conn.rollback()
            raise

def mark_master_failed(queue_id: int, error: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        try:
            now = datetime.now().isoformat()
            cursor.execute("UPDATE offline_queue SET status = 'FAILED', error_message = ?, updated_at = ? WHERE id = ?", (error, now, queue_id))
            cursor.execute("UPDATE pending_masters SET status = 'FAILED', error_message = ?, updated_at = ? WHERE queue_id = ?", (error, now, queue_id))
            conn.commit()
        except:
            conn.rollback()
            raise

def retry_master(queue_id: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        try:
            now = datetime.now().isoformat()
            cursor.execute("UPDATE offline_queue SET status = 'PENDING', error_message = NULL, updated_at = ? WHERE id = ?", (now, queue_id))
            cursor.execute("UPDATE pending_masters SET status = 'PENDING', error_message = NULL, updated_at = ? WHERE queue_id = ?", (now, queue_id))
            conn.commit()
        except:
            conn.rollback()
            raise

def delete_master_queue(queue_id: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        try:
            cursor.execute("DELETE FROM pending_masters WHERE queue_id = ?", (queue_id,))
            cursor.execute("DELETE FROM offline_queue WHERE id = ?", (queue_id,))
            conn.commit()
        except:
            conn.rollback()
            raise

def resolve_master_externally(queue_id: int, description: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        try:
            now = datetime.now().isoformat()
            cursor.execute("DELETE FROM pending_masters WHERE queue_id = ?", (queue_id,))
            # Do NOT overwrite original description, just append or rely on status
            cursor.execute("UPDATE offline_queue SET status = 'SYNCED', updated_at = ? WHERE id = ?", (now, queue_id))
            conn.commit()
        except:
            conn.rollback()
            raise

def run_migration_preflight():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, operation_type, status 
            FROM offline_queue oq
            WHERE oq.operation_type IN ('CREATE_LEDGER','CREATE_ITEM','CREATE_UOM')
              AND oq.status IN ('PENDING','FAILED')
              AND NOT EXISTS (
                  SELECT 1 FROM pending_masters pm WHERE pm.queue_id = oq.id
              )
        """)
        rows = cursor.fetchall()
        if rows:
            raise Exception(f"Migration preflight failed: Found {len(rows)} unlinked legacy master queue rows (IDs: {[r['id'] for r in rows]}). These must be resolved before startup.")

# --- Helper Functions for Masters ---
def get_all_ledgers():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM ledgers")
        return [dict(row) for row in cursor.fetchall()]

def get_all_stock_items():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM stock_items")
        return [dict(row) for row in cursor.fetchall()]

def get_all_uoms():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM uoms")
        return [row['name'] for row in cursor.fetchall()]

def get_all_aliases():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM item_aliases")
        return {row['original_name']: row['mapped_name'] for row in cursor.fetchall()}

def save_alias(original_name: str, mapped_name: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO item_aliases (original_name, mapped_name)
            VALUES (?, ?)
            ON CONFLICT(original_name) DO UPDATE SET mapped_name=excluded.mapped_name
        """, (original_name, mapped_name))
        conn.commit()

def record_purchase_rate(item_name: str, rate: float, supplier: str, purchase_date: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO purchase_rates (stock_item_name, latest_rate, supplier, purchase_date)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(stock_item_name) DO UPDATE SET 
                latest_rate=excluded.latest_rate,
                supplier=excluded.supplier,
                purchase_date=excluded.purchase_date
        """, (item_name, rate, supplier, purchase_date))
        conn.commit()

def get_purchase_rates():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM purchase_rates")
        return {row['stock_item_name']: dict(row) for row in cursor.fetchall()}

def get_purchase_rate(item_name: str) -> float:
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Get from purchase_rates (App offline queue / memory)
        cursor.execute("SELECT latest_rate, purchase_date FROM purchase_rates WHERE stock_item_name = ? COLLATE NOCASE", (item_name,))
        app_row = cursor.fetchone()
        app_rate = app_row['latest_rate'] if app_row else 0.0
        app_date = app_row['purchase_date'] if app_row and app_row['purchase_date'] else "0000-00-00"
        
        # Get from stock_items (Tally sync memory)
        cursor.execute("SELECT last_purchase_rate, last_purchase_date FROM stock_items WHERE name = ? COLLATE NOCASE", (item_name,))
        tally_row = cursor.fetchone()
        tally_rate = tally_row['last_purchase_rate'] if tally_row and tally_row['last_purchase_rate'] else 0.0
        tally_date = tally_row['last_purchase_date'] if tally_row and tally_row['last_purchase_date'] else "0000-00-00"
        
        if tally_date > app_date:
            return tally_rate
        else:
            return app_rate

def clear_and_bulk_insert_ledgers(ledgers: list):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM ledgers")
        if ledgers:
            cursor.executemany("""
                INSERT INTO ledgers (name, parent, cost_centre)
                VALUES (?, ?, ?)
            """, [(l['name'], l.get('parent'), l.get('cost_centre', False)) for l in ledgers])
        conn.commit()

def clear_and_bulk_insert_stock_items(items: list):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM stock_items")
        if items:
            cursor.executemany("""
                INSERT INTO stock_items (name, unit, last_purchase_rate, last_purchase_date)
                VALUES (?, ?, ?, ?)
            """, [(i['name'], i.get('unit'), i.get('last_purchase_rate', 0.0), i.get('last_purchase_date', '')) for i in items])
        conn.commit()

def clear_and_bulk_insert_uoms(uoms: list):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM uoms")
        if uoms:
            cursor.executemany("INSERT INTO uoms (name) VALUES (?)", [(u,) for u in uoms])
        conn.commit()

# --- Helper Functions for Bank Mappings ---
def get_all_bank_mappings():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM bank_mappings")
        return [dict(row) for row in cursor.fetchall()]

def get_mappings_by_bank(bank_name: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM bank_mappings WHERE bank_account_name = ? COLLATE NOCASE", (bank_name,))
        return [dict(row) for row in cursor.fetchall()]

def create_bank_mapping(bank_name: str, keyword: str, ledger: str, cost_center: str = None):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO bank_mappings (bank_account_name, keyword, target_ledger, cost_center)
            VALUES (?, ?, ?, ?)
        """, (bank_name, keyword, ledger, cost_center))
        conn.commit()
        return cursor.lastrowid

def update_bank_mapping(mapping_id: int, bank_name: str, keyword: str, ledger: str, cost_center: str = None):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE bank_mappings
            SET bank_account_name = ?, keyword = ?, target_ledger = ?, cost_center = ?
            WHERE id = ?
        """, (bank_name, keyword, ledger, cost_center, mapping_id))
        conn.commit()
        return cursor.rowcount > 0

def delete_bank_mapping(mapping_id: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bank_mappings WHERE id = ?", (mapping_id,))
        conn.commit()
        return cursor.rowcount > 0


# --- Helper Functions for Supplier Column Mappings ---
def _normalize_supplier_key(name: str) -> str:
    # lowercase, trim spaces, collapse repeated spaces, remove obvious punctuation
    name = (name or "").lower()
    name = re.sub(r'[^a-z0-9\s]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name

def get_supplier_column_mapping(supplier_name: str) -> Optional[dict]:
    supplier_key = _normalize_supplier_key(supplier_name)
    if not supplier_key:
        return None
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT mapping_json FROM supplier_column_mappings WHERE supplier_key = ?", (supplier_key,))
        row = cursor.fetchone()
        if row:
            try:
                return json.loads(row['mapping_json'])
            except json.JSONDecodeError:
                pass
    return None

def save_supplier_column_mapping(supplier_name: str, mapping: dict):
    supplier_key = _normalize_supplier_key(supplier_name)
    if not supplier_key:
        return
    now = datetime.now().isoformat()
    mapping_str = json.dumps(mapping)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO supplier_column_mappings (supplier_key, supplier_name, mapping_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(supplier_key) DO UPDATE SET 
                supplier_name=excluded.supplier_name,
                mapping_json=excluded.mapping_json,
                updated_at=excluded.updated_at
        """, (supplier_key, supplier_name, mapping_str, now, now))
        conn.commit()


# =============================================================================
# Phase 2B.2B — Bank Import Session Persistence
# =============================================================================


# ---------------------------------------------------------------------------
# Money conversion helper
# ---------------------------------------------------------------------------

class BankImportAmountError(ValueError):
    """Raised when an amount string cannot be converted to an exact paise integer."""


def parse_amount_to_minor(value_str: str) -> int:
    """
    Convert a decimal string representing a bank transaction amount to an exact
    integer count of paise (i.e., multiply by 100).

    Rules:
    - Input MUST be a string. Never pass a float — binary float drift is not
      acceptable for accounting identity comparison.
    - Uses decimal.Decimal for exact arithmetic throughout.
    - Rejects any value that is not a finite, non-negative number.
    - Rejects any value whose paise representation is not an exact integer
      (i.e., sub-paise precision is forbidden for bank amounts).
    - Does NOT silently round.

    Examples:
        "5000"    -> 500000
        "5000.0"  -> 500000
        "5000.00" -> 500000
        "1999.99" -> 199999
        "0.01"    -> 1
        "0.00"    -> 0
        "5000.001" -> raises BankImportAmountError (sub-paise)
        "abc"      -> raises BankImportAmountError (not a number)
        ""         -> raises BankImportAmountError
    """
    from decimal import Decimal, InvalidOperation

    if not isinstance(value_str, str) or not value_str.strip():
        raise BankImportAmountError(
            f"Amount must be a non-empty string; got {value_str!r}."
        )

    try:
        d = Decimal(value_str)
    except InvalidOperation:
        raise BankImportAmountError(
            f"Invalid amount '{value_str}': not a valid decimal number."
        )

    if not d.is_finite():
        raise BankImportAmountError(
            f"Invalid amount '{value_str}': NaN and Infinity are not permitted."
        )

    if d < 0:
        raise BankImportAmountError(
            f"Invalid amount '{value_str}': negative amounts are not permitted in this context."
        )

    minor = d * Decimal("100")

    # Verify exact integer — compare to its integral value without rounding
    if minor != minor.to_integral_value():
        raise BankImportAmountError(
            f"Invalid amount precision '{value_str}': sub-paise values are not permitted "
            f"in bank statement imports. Use at most 2 decimal places."
        )

    return int(minor)


# ---------------------------------------------------------------------------
# Domain exception for immutable identity mismatches
# ---------------------------------------------------------------------------

class BankImportRowMismatch(Exception):
    """
    Raised when a retry request provides the same (session_key, row_key) pair
    but different immutable source fields (date, narration, amount, direction).
    Phase 2B.2C translates this to HTTP 409.
    """


# ---------------------------------------------------------------------------
# Session creation — atomic
# ---------------------------------------------------------------------------

def create_bank_import_session(session_key: str, bank_ledger: str, rows: list) -> dict:
    """
    Atomically create one bank_import_sessions row and all initial bank_import_rows.

    `rows` is a list of dicts with keys:
        row_key        : str   — opaque frontend UUID
        date           : str   — YYYYMMDD
        narration_source : str — raw_narration verbatim
        withdraw_str   : str   — decimal string, e.g. "5000.00"
        deposit_str    : str   — decimal string, e.g. "0.00"
        txn_direction  : str   — "DEBIT" or "CREDIT"
        ledger         : str   — current editable mapping
        cost_center    : str or None

    All amounts are validated via parse_amount_to_minor() before any DB write.
    If any row fails validation or insertion, the entire session is rolled back.

    Returns a dict with session_key and row_count (no SQLite IDs exposed).
    """
    # Pre-validate all amounts before touching the DB
    prepared = []
    for r in rows:
        withdraw_minor = parse_amount_to_minor(r["withdraw_str"])
        deposit_minor = parse_amount_to_minor(r["deposit_str"])
        prepared.append({
            "row_key": r["row_key"],
            "date": r["date"],
            "narration_source": r["narration_source"],
            "withdraw_minor": withdraw_minor,
            "deposit_minor": deposit_minor,
            "txn_direction": r["txn_direction"],
            "ledger": r.get("ledger", "Bank Suspense Account"),
            "cost_center": r.get("cost_center"),
        })

    now = datetime.now().isoformat()

    with get_db() as conn:
        try:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO bank_import_sessions
                    (session_key, bank_ledger, row_count, status, created_at, updated_at)
                VALUES (?, ?, ?, 'IN_PROGRESS', ?, ?)
            """, (session_key, bank_ledger, len(prepared), now, now))

            session_id = cursor.lastrowid

            cursor.executemany("""
                INSERT INTO bank_import_rows
                    (session_id, row_key, date, narration_source,
                     withdraw_minor, deposit_minor, txn_direction,
                     ledger, cost_center, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
            """, [
                (session_id, p["row_key"], p["date"], p["narration_source"],
                 p["withdraw_minor"], p["deposit_minor"], p["txn_direction"],
                 p["ledger"], p["cost_center"], now, now)
                for p in prepared
            ])

            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {"session_key": session_key, "row_count": len(prepared)}


# ---------------------------------------------------------------------------
# Session read helpers
# ---------------------------------------------------------------------------

def get_bank_import_session(session_key: str) -> Optional[dict]:
    """
    Return session metadata for the given session_key, or None if not found.
    Does not expose the SQLite primary key.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT session_key, bank_ledger, row_count, status, created_at, updated_at
            FROM bank_import_sessions
            WHERE session_key = ?
        """, (session_key,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_bank_import_row(session_key: str, row_key: str) -> Optional[dict]:
    """
    Return one bank_import_rows record, or None if not found.
    Uses session_key (not session_id) as the public identifier.
    Does not expose SQLite IDs.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.row_key, r.date, r.narration_source,
                   r.withdraw_minor, r.deposit_minor, r.txn_direction,
                   r.ledger, r.cost_center,
                   r.status, r.error_message, r.created_at, r.updated_at
            FROM bank_import_rows r
            JOIN bank_import_sessions s ON s.id = r.session_id
            WHERE s.session_key = ? AND r.row_key = ?
        """, (session_key, row_key))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_bank_import_rows_for_session(session_key: str) -> List[dict]:
    """
    Return all rows for a session, ordered by creation time.
    Does not expose SQLite IDs.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.row_key, r.date, r.narration_source,
                   r.withdraw_minor, r.deposit_minor, r.txn_direction,
                   r.ledger, r.cost_center,
                   r.status, r.error_message, r.created_at, r.updated_at
            FROM bank_import_rows r
            JOIN bank_import_sessions s ON s.id = r.session_id
            WHERE s.session_key = ?
            ORDER BY r.id ASC
        """, (session_key,))
        return [dict(row) for row in cursor.fetchall()]


# ---------------------------------------------------------------------------
# Immutable identity validation
# ---------------------------------------------------------------------------

def validate_bank_import_row_identity(session_key: str, row_key: str,
                                       date: str, narration_source: str,
                                       withdraw_str: str, deposit_str: str,
                                       txn_direction: str) -> None:
    """
    Compare incoming immutable source fields against the already-persisted row.

    Raises BankImportRowMismatch if any immutable field differs.
    Raises ValueError if the row is not found (caller should treat as new row).

    Editable fields (ledger, cost_center) are NOT checked here.
    Amount strings are converted to paise integers for exact comparison.
    """
    existing = get_bank_import_row(session_key, row_key)
    if existing is None:
        raise ValueError(f"Row '{row_key}' not found in session '{session_key}'.")

    withdraw_minor = parse_amount_to_minor(withdraw_str)
    deposit_minor = parse_amount_to_minor(deposit_str)

    mismatches = []
    if existing["date"] != date:
        mismatches.append(f"date: persisted={existing['date']!r} incoming={date!r}")
    if existing["narration_source"] != narration_source:
        mismatches.append("narration_source differs")
    if existing["withdraw_minor"] != withdraw_minor:
        mismatches.append(
            f"withdraw: persisted={existing['withdraw_minor']} incoming={withdraw_minor}"
        )
    if existing["deposit_minor"] != deposit_minor:
        mismatches.append(
            f"deposit: persisted={existing['deposit_minor']} incoming={deposit_minor}"
        )
    if existing["txn_direction"] != txn_direction:
        mismatches.append(
            f"txn_direction: persisted={existing['txn_direction']!r} incoming={txn_direction!r}"
        )

    if mismatches:
        raise BankImportRowMismatch(
            f"Immutable source fields changed for row_key={row_key!r}: " +
            "; ".join(mismatches) +
            ". A row_key cannot be reused for a different bank transaction."
        )


# ---------------------------------------------------------------------------
# Editable mapping update (state-guarded)
# ---------------------------------------------------------------------------

def update_bank_import_row_mapping(session_key: str, row_key: str,
                                    ledger: str, cost_center: Optional[str]) -> bool:
    """
    Update the editable ledger and cost_center for a row, but only if the row
    is currently PENDING or FAILED.

    Returns True if the update succeeded (row existed and was in an editable state).
    Returns False if the row was not found, or was in a non-editable state
    (IN_FLIGHT, IMPORTED, UNKNOWN).

    Phase 2B.2C must call this before each Tally attempt so recovery always
    reflects the user's latest mapping choices.
    """
    now = datetime.now().isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE bank_import_rows
            SET ledger = ?, cost_center = ?, updated_at = ?
            WHERE session_id = (
                SELECT id FROM bank_import_sessions WHERE session_key = ?
            )
            AND row_key = ?
            AND status IN ('PENDING', 'FAILED')
        """, (ledger, cost_center, now, session_key, row_key))
        conn.commit()
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# State transition helpers — narrow and state-guarded
# ---------------------------------------------------------------------------

# Frozen whitelist of every legal bank_import_rows (from_status, to_status) pair.
# _bank_row_transition validates all callers against this set before touching the DB.
_BANK_ROW_LEGAL_TRANSITIONS = frozenset({
    # Starting a Tally POST attempt
    ("PENDING",   "IN_FLIGHT"),
    ("FAILED",    "IN_FLIGHT"),
    # Outcomes after Tally POST
    ("IN_FLIGHT", "IMPORTED"),
    ("IN_FLIGHT", "FAILED"),
    ("IN_FLIGHT", "UNKNOWN"),
    # Operator resolution of ambiguous delivery
    ("UNKNOWN",   "IMPORTED"),
    ("UNKNOWN",   "FAILED"),
})


def _bank_row_transition(session_key: str, row_key: str,
                          from_statuses: tuple, to_status: str,
                          error_message: Optional[str] = None) -> bool:
    """
    Internal compare-and-set helper. Updates the row status only when the
    current status is one of `from_statuses`.

    Every (from_status, to_status) pair is validated against the frozen
    _BANK_ROW_LEGAL_TRANSITIONS whitelist BEFORE any DB access.
    Any pair not in the whitelist raises ValueError immediately —
    this is a hard production guard, not merely a test assertion.

    Returns True if the row was found in one of `from_statuses` and updated.
    Returns False if the row was in a different state (no change made).
    """
    for from_status in from_statuses:
        if (from_status, to_status) not in _BANK_ROW_LEGAL_TRANSITIONS:
            raise ValueError(
                f"Illegal bank import row transition: {from_status!r} -> {to_status!r}. "
                f"This transition is not in the approved state machine."
            )

    now = datetime.now().isoformat()
    placeholders = ",".join("?" * len(from_statuses))
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            UPDATE bank_import_rows
            SET status = ?, error_message = ?, updated_at = ?
            WHERE session_id = (
                SELECT id FROM bank_import_sessions WHERE session_key = ?
            )
            AND row_key = ?
            AND status IN ({placeholders})
        """, (to_status, error_message, now, session_key, row_key, *from_statuses))
        conn.commit()
        return cursor.rowcount > 0


def mark_bank_import_row_in_flight(session_key: str, row_key: str) -> bool:
    """
    PENDING or FAILED → IN_FLIGHT.

    Must be committed to DB before Phase 2B.2C calls requests.post().
    Returns True if the transition succeeded.
    """
    return _bank_row_transition(
        session_key, row_key,
        from_statuses=("PENDING", "FAILED"),
        to_status="IN_FLIGHT",
        error_message=None
    )


def mark_bank_import_row_imported(session_key: str, row_key: str) -> bool:
    """
    IN_FLIGHT → IMPORTED.

    Called after Tally returns authoritative success (CREATED=1, ERRORS=0).
    Returns True if the transition succeeded.
    """
    return _bank_row_transition(
        session_key, row_key,
        from_statuses=("IN_FLIGHT",),
        to_status="IMPORTED",
        error_message=None
    )


def mark_bank_import_row_failed(session_key: str, row_key: str,
                                  error_message: Optional[str] = None) -> bool:
    """
    IN_FLIGHT → FAILED.

    Called after Tally returns an authoritative deterministic rejection
    (CREATED=0, ERRORS>0 or LINEERROR present).
    Returns True if the transition succeeded.
    """
    return _bank_row_transition(
        session_key, row_key,
        from_statuses=("IN_FLIGHT",),
        to_status="FAILED",
        error_message=error_message
    )


def mark_bank_import_row_unknown(session_key: str, row_key: str,
                                   error_message: Optional[str] = None) -> bool:
    """
    IN_FLIGHT → UNKNOWN.

    Called when the delivery result is ambiguous (Timeout, ConnectionError after
    requests.post(), malformed response, contradictory counts, empty response).
    Returns True if the transition succeeded.
    """
    if error_message is None:
        error_message = (
            "Connection lost while sending this voucher to Tally. "
            "Verify its status in Tally before retrying."
        )
    return _bank_row_transition(
        session_key, row_key,
        from_statuses=("IN_FLIGHT",),
        to_status="UNKNOWN",
        error_message=error_message
    )


def resolve_bank_import_row_unknown(session_key: str, row_key: str,
                                     resolution: str) -> bool:
    """
    UNKNOWN → IMPORTED  (resolution="exists_in_tally")
    UNKNOWN → FAILED    (resolution="not_in_tally")

    Operator-asserted resolution. No automatic Tally verification is performed.
    Returns True if the row was in UNKNOWN state and the transition succeeded.
    Raises ValueError for invalid resolution strings.
    """
    if resolution == "exists_in_tally":
        return _bank_row_transition(
            session_key, row_key,
            from_statuses=("UNKNOWN",),
            to_status="IMPORTED",
            error_message="Resolved by operator: confirmed present in Tally."
        )
    elif resolution == "not_in_tally":
        return _bank_row_transition(
            session_key, row_key,
            from_statuses=("UNKNOWN",),
            to_status="FAILED",
            error_message="Resolved by operator: confirmed absent from Tally. Ready to retry."
        )
    else:
        raise ValueError(
            f"Invalid resolution '{resolution}'. "
            "Must be 'exists_in_tally' or 'not_in_tally'."
        )


# ---------------------------------------------------------------------------
# Session-level transition helpers
# ---------------------------------------------------------------------------

def set_bank_import_session_offline_queued(session_key: str) -> bool:
    """
    IN_PROGRESS → OFFLINE_QUEUED.

    Marks the session as handed off to the generic offline_queue for delivery.
    After this transition, Phase 2B.2C must refuse direct bank-session retry.
    The transition is one-way: OFFLINE_QUEUED cannot revert to IN_PROGRESS.

    Returns True if the session existed and was IN_PROGRESS.
    """
    now = datetime.now().isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE bank_import_sessions
            SET status = 'OFFLINE_QUEUED', updated_at = ?
            WHERE session_key = ? AND status = 'IN_PROGRESS'
        """, (now, session_key))
        conn.commit()
        return cursor.rowcount > 0
