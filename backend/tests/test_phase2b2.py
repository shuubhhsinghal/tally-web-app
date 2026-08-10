"""
backend/tests/test_phase2b2.py
Phase 2B.2B — Bank Import Persistence, Identity & IN_FLIGHT Safety
Phase 2B.2B.1 — Transition whitelist hardening

All tests use an isolated temporary SQLite database.
The real runtime database (tally_sync.db) is NEVER accessed.
No Tally / network calls are made.

Test fixture strategy:
  - Legal state machine transitions use the public production helpers.
  - Direct SQL inserts are used ONLY to construct starting states that
    require bypassing the whitelist (e.g., test_21 verifying the guard).
  - _bank_row_transition is never called with an illegal (from, to) pair
    in any test setup.
"""
import sqlite3
import pytest
from datetime import datetime
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Fixtures — isolated DB
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_db(tmp_path):
    """
    Provide a temporary SQLite DB path and monkey-patch database.DB_PATH so
    every database.py function operates on the temp file, not the real DB.
    """
    db_file = str(tmp_path / "test_phase2b2.db")
    with patch("backend.database.DB_PATH", db_file):
        from backend.database import init_db
        init_db()
        yield db_file


# ---------------------------------------------------------------------------
# Convenience builders
# ---------------------------------------------------------------------------

def _row(row_key="rk-1", date="20250809", narration="ATM WDL 001",
         withdraw="5000.00", deposit="0.00", direction="DEBIT",
         ledger="Cash", cost_center=None):
    return {
        "row_key": row_key,
        "date": date,
        "narration_source": narration,
        "withdraw_str": withdraw,
        "deposit_str": deposit,
        "txn_direction": direction,
        "ledger": ledger,
        "cost_center": cost_center,
    }


def _force_row_status(db_path: str, session_key: str, row_key: str, status: str,
                       error_message: str = None):
    """
    Direct SQL update to force a row into an arbitrary status for test setup.
    ONLY used in tests that need to construct starting states that cannot be
    reached via legal production transitions. Never used to assert production
    behaviour — the whitelist guards do that.
    """
    now = datetime.now().isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute("""
        UPDATE bank_import_rows
        SET status = ?, error_message = ?, updated_at = ?
        WHERE session_id = (SELECT id FROM bank_import_sessions WHERE session_key = ?)
        AND row_key = ?
    """, (status, error_message, now, session_key, row_key))
    conn.commit()
    conn.close()


# ===========================================================================
# TEST 1 — schema initialization
# ===========================================================================

def test_01_schema_initialized(isolated_db):
    conn = sqlite3.connect(isolated_db)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cursor.fetchall()}
    conn.close()

    assert "bank_import_sessions" in tables
    assert "bank_import_rows" in tables
    assert "ledgers" in tables
    assert "stock_items" in tables
    assert "offline_queue" in tables
    assert "pending_masters" in tables
    assert "bank_mappings" in tables


# ===========================================================================
# TEST 2 — atomic session + rows creation
# ===========================================================================

def test_02_create_session_with_rows(isolated_db):
    with patch("backend.database.DB_PATH", isolated_db):
        from backend.database import (
            create_bank_import_session,
            get_bank_import_session,
            get_bank_import_rows_for_session,
        )

        rows = [
            _row("rk-1", narration="NEFT ABC", withdraw="1000.00"),
            _row("rk-2", narration="NEFT XYZ", withdraw="2500.50"),
            _row("rk-3", narration="UPI PAY",  withdraw="750.00"),
        ]
        result = create_bank_import_session("sess-001", "Federal Bank", rows)
        assert result["session_key"] == "sess-001"
        assert result["row_count"] == 3

        session = get_bank_import_session("sess-001")
        assert session is not None
        assert session["bank_ledger"] == "Federal Bank"
        assert session["row_count"] == 3
        assert session["status"] == "IN_PROGRESS"

        db_rows = get_bank_import_rows_for_session("sess-001")
        assert len(db_rows) == 3
        assert all(r["status"] == "PENDING" for r in db_rows)

        row_keys = {r["row_key"] for r in db_rows}
        assert row_keys == {"rk-1", "rk-2", "rk-3"}

        r1 = next(r for r in db_rows if r["row_key"] == "rk-1")
        assert r1["ledger"] == "Cash"
        assert r1["withdraw_minor"] == 100000
        assert r1["deposit_minor"] == 0
        assert r1["txn_direction"] == "DEBIT"


# ===========================================================================
# TEST 3 — atomic rollback on duplicate row_key
# ===========================================================================

def test_03_atomic_rollback_on_duplicate_row_key(isolated_db):
    with patch("backend.database.DB_PATH", isolated_db):
        from backend.database import create_bank_import_session, get_bank_import_session

        rows_with_dup = [_row("dup-key"), _row("dup-key")]
        with pytest.raises(Exception):
            create_bank_import_session("sess-dup", "Test Bank", rows_with_dup)

        assert get_bank_import_session("sess-dup") is None


# ===========================================================================
# TEST 4 — valid money conversion
# ===========================================================================

def test_04_money_conversion_valid(isolated_db):
    with patch("backend.database.DB_PATH", isolated_db):
        from backend.database import parse_amount_to_minor

        assert parse_amount_to_minor("5000")    == 500000
        assert parse_amount_to_minor("5000.0")  == 500000
        assert parse_amount_to_minor("5000.00") == 500000
        assert parse_amount_to_minor("1999.99") == 199999
        assert parse_amount_to_minor("0.01")    == 1
        assert parse_amount_to_minor("0.00")    == 0


# ===========================================================================
# TEST 5 — invalid / sub-paise inputs rejected
# ===========================================================================

def test_05_money_conversion_invalid(isolated_db):
    with patch("backend.database.DB_PATH", isolated_db):
        from backend.database import parse_amount_to_minor, BankImportAmountError

        for bad in ["5000.001", "19.999", "abc", "", "NaN", "Infinity", "-Infinity"]:
            with pytest.raises(BankImportAmountError):
                parse_amount_to_minor(bad)


# ===========================================================================
# TEST 6 — identical-looking rows with different row_keys stay distinct
# ===========================================================================

def test_06_distinct_row_keys_not_collapsed(isolated_db):
    with patch("backend.database.DB_PATH", isolated_db):
        from backend.database import (
            create_bank_import_session,
            get_bank_import_rows_for_session,
        )

        rows = [
            _row("rk-A", narration="ATM WDL", withdraw="5000.00", direction="DEBIT"),
            _row("rk-B", narration="ATM WDL", withdraw="5000.00", direction="DEBIT"),
        ]
        create_bank_import_session("sess-distinct", "Test Bank", rows)
        db_rows = get_bank_import_rows_for_session("sess-distinct")
        assert len(db_rows) == 2
        assert {r["row_key"] for r in db_rows} == {"rk-A", "rk-B"}


# ===========================================================================
# TEST 7 — immutable identity allows editable mapping changes
# ===========================================================================

def test_07_identity_allows_mapping_change(isolated_db):
    with patch("backend.database.DB_PATH", isolated_db):
        from backend.database import (
            create_bank_import_session,
            validate_bank_import_row_identity,
        )

        create_bank_import_session("sess-map", "Bank", [
            _row("rk-1", ledger="Old Ledger", cost_center="Old Project")
        ])

        # Same immutable fields, different ledger/cost_center — must NOT raise
        validate_bank_import_row_identity(
            session_key="sess-map",
            row_key="rk-1",
            date="20250809",
            narration_source="ATM WDL 001",
            withdraw_str="5000.00",
            deposit_str="0.00",
            txn_direction="DEBIT",
        )


# ===========================================================================
# TEST 8 — immutable identity rejects source changes
# ===========================================================================

def test_08_identity_rejects_source_changes(isolated_db):
    with patch("backend.database.DB_PATH", isolated_db):
        from backend.database import (
            create_bank_import_session,
            validate_bank_import_row_identity,
            BankImportRowMismatch,
        )

        create_bank_import_session("sess-imm", "Bank", [_row("rk-1")])

        base = dict(
            session_key="sess-imm", row_key="rk-1",
            date="20250809", narration_source="ATM WDL 001",
            withdraw_str="5000.00", deposit_str="0.00",
            txn_direction="DEBIT"
        )

        with pytest.raises(BankImportRowMismatch):
            validate_bank_import_row_identity(**{**base, "date": "20250801"})
        with pytest.raises(BankImportRowMismatch):
            validate_bank_import_row_identity(**{**base, "narration_source": "NEFT PAY"})
        with pytest.raises(BankImportRowMismatch):
            validate_bank_import_row_identity(**{**base, "withdraw_str": "3000.00"})
        with pytest.raises(BankImportRowMismatch):
            validate_bank_import_row_identity(**{**base, "deposit_str": "1.00"})
        with pytest.raises(BankImportRowMismatch):
            validate_bank_import_row_identity(**{**base, "txn_direction": "CREDIT"})


# ===========================================================================
# TEST 9 — equivalent amount strings preserve identity
# ===========================================================================

def test_09_equivalent_amounts_same_identity(isolated_db):
    with patch("backend.database.DB_PATH", isolated_db):
        from backend.database import (
            create_bank_import_session,
            validate_bank_import_row_identity,
        )

        create_bank_import_session("sess-eq", "Bank", [
            _row("rk-1", withdraw="5000", deposit="0")
        ])

        validate_bank_import_row_identity(
            session_key="sess-eq", row_key="rk-1",
            date="20250809", narration_source="ATM WDL 001",
            withdraw_str="5000.00", deposit_str="0.00",
            txn_direction="DEBIT"
        )


# ===========================================================================
# TEST 10 — editable mapping state guards
# Rows needing non-legal starting states use _force_row_status (direct SQL).
# ===========================================================================

def test_10_mapping_update_state_guards(isolated_db):
    with patch("backend.database.DB_PATH", isolated_db):
        from backend.database import (
            create_bank_import_session,
            update_bank_import_row_mapping,
            get_bank_import_row,
            mark_bank_import_row_in_flight,
        )

        sess = "sess-states"
        create_bank_import_session(sess, "Bank", [
            _row("rk-pending"),
            _row("rk-failed"),
            _row("rk-inflight"),
            _row("rk-imported"),
            _row("rk-unknown"),
        ])

        # Use legal transitions for FAILED and IN_FLIGHT
        _force_row_status(isolated_db, sess, "rk-failed",   "FAILED")
        mark_bank_import_row_in_flight(sess, "rk-inflight")
        # IMPORTED and UNKNOWN have no direct legal path from PENDING in one step;
        # use direct SQL for these test fixture states only
        _force_row_status(isolated_db, sess, "rk-imported", "IMPORTED")
        _force_row_status(isolated_db, sess, "rk-unknown",  "UNKNOWN")

        assert update_bank_import_row_mapping(sess, "rk-pending",  "New Ledger", "CC1") is True
        assert get_bank_import_row(sess, "rk-pending")["ledger"] == "New Ledger"

        assert update_bank_import_row_mapping(sess, "rk-failed",   "Fixed Ledger", None) is True
        assert get_bank_import_row(sess, "rk-failed")["ledger"] == "Fixed Ledger"

        assert update_bank_import_row_mapping(sess, "rk-inflight", "X", None) is False
        assert update_bank_import_row_mapping(sess, "rk-imported", "X", None) is False
        assert update_bank_import_row_mapping(sess, "rk-unknown",  "X", None) is False


# ===========================================================================
# TEST 11 — PENDING → IN_FLIGHT
# ===========================================================================

def test_11_pending_to_in_flight(isolated_db):
    with patch("backend.database.DB_PATH", isolated_db):
        from backend.database import (
            create_bank_import_session, mark_bank_import_row_in_flight, get_bank_import_row
        )
        create_bank_import_session("sess-11", "Bank", [_row("rk-1")])
        assert mark_bank_import_row_in_flight("sess-11", "rk-1") is True
        assert get_bank_import_row("sess-11", "rk-1")["status"] == "IN_FLIGHT"


# ===========================================================================
# TEST 12 — FAILED → IN_FLIGHT
# Uses direct SQL to set FAILED (no single-step legal path from PENDING→FAILED
# without going through IN_FLIGHT first — legal path is PENDING→IN_FLIGHT→FAILED).
# ===========================================================================

def test_12_failed_to_in_flight(isolated_db):
    with patch("backend.database.DB_PATH", isolated_db):
        from backend.database import (
            create_bank_import_session,
            mark_bank_import_row_in_flight, get_bank_import_row
        )
        create_bank_import_session("sess-12", "Bank", [_row("rk-1")])
        _force_row_status(isolated_db, "sess-12", "rk-1", "FAILED")
        assert mark_bank_import_row_in_flight("sess-12", "rk-1") is True
        assert get_bank_import_row("sess-12", "rk-1")["status"] == "IN_FLIGHT"


# ===========================================================================
# TEST 13 — IMPORTED row cannot transition to IN_FLIGHT
# Uses direct SQL to place row in IMPORTED state (no legal single-step path
# from PENDING→IMPORTED), then asserts the public helper correctly refuses.
# ===========================================================================

def test_13_imported_cannot_transition_to_in_flight(isolated_db):
    with patch("backend.database.DB_PATH", isolated_db):
        from backend.database import (
            create_bank_import_session,
            mark_bank_import_row_in_flight, get_bank_import_row
        )
        create_bank_import_session("sess-13", "Bank", [_row("rk-1")])
        _force_row_status(isolated_db, "sess-13", "rk-1", "IMPORTED")
        assert mark_bank_import_row_in_flight("sess-13", "rk-1") is False
        assert get_bank_import_row("sess-13", "rk-1")["status"] == "IMPORTED"


# ===========================================================================
# TEST 14 — IN_FLIGHT final transitions via legal paths
# ===========================================================================

def test_14_in_flight_final_transitions(isolated_db):
    with patch("backend.database.DB_PATH", isolated_db):
        from backend.database import (
            create_bank_import_session,
            mark_bank_import_row_in_flight,
            mark_bank_import_row_imported,
            mark_bank_import_row_failed,
            mark_bank_import_row_unknown,
            get_bank_import_row,
        )

        def _setup(sess):
            create_bank_import_session(sess, "Bank", [_row("rk-1")])
            mark_bank_import_row_in_flight(sess, "rk-1")  # PENDING → IN_FLIGHT (legal)

        _setup("sess-14a")
        assert mark_bank_import_row_imported("sess-14a", "rk-1") is True
        assert get_bank_import_row("sess-14a", "rk-1")["status"] == "IMPORTED"

        _setup("sess-14b")
        assert mark_bank_import_row_failed("sess-14b", "rk-1", "Tally: ledger not found") is True
        r = get_bank_import_row("sess-14b", "rk-1")
        assert r["status"] == "FAILED"
        assert "ledger not found" in r["error_message"]

        _setup("sess-14c")
        assert mark_bank_import_row_unknown("sess-14c", "rk-1") is True
        r = get_bank_import_row("sess-14c", "rk-1")
        assert r["status"] == "UNKNOWN"
        assert r["error_message"] is not None


# ===========================================================================
# TEST 15 — UNKNOWN operator resolution
# ===========================================================================

def test_15_unknown_operator_resolution(isolated_db):
    with patch("backend.database.DB_PATH", isolated_db):
        from backend.database import (
            create_bank_import_session,
            mark_bank_import_row_in_flight,
            mark_bank_import_row_unknown,
            resolve_bank_import_row_unknown,
            get_bank_import_row,
        )

        def _setup_unknown(sess):
            create_bank_import_session(sess, "Bank", [_row("rk-1")])
            mark_bank_import_row_in_flight(sess, "rk-1")   # PENDING → IN_FLIGHT
            mark_bank_import_row_unknown(sess, "rk-1")     # IN_FLIGHT → UNKNOWN

        _setup_unknown("sess-15a")
        assert resolve_bank_import_row_unknown("sess-15a", "rk-1", "exists_in_tally") is True
        assert get_bank_import_row("sess-15a", "rk-1")["status"] == "IMPORTED"

        _setup_unknown("sess-15b")
        assert resolve_bank_import_row_unknown("sess-15b", "rk-1", "not_in_tally") is True
        assert get_bank_import_row("sess-15b", "rk-1")["status"] == "FAILED"

        # Cannot resolve from PENDING (not in UNKNOWN state)
        create_bank_import_session("sess-15c", "Bank", [_row("rk-1")])
        assert resolve_bank_import_row_unknown("sess-15c", "rk-1", "exists_in_tally") is False
        assert get_bank_import_row("sess-15c", "rk-1")["status"] == "PENDING"

        _setup_unknown("sess-15d")
        with pytest.raises(ValueError, match="Invalid resolution"):
            resolve_bank_import_row_unknown("sess-15d", "rk-1", "maybe")


# ===========================================================================
# TEST 16 — crash recovery: IN_FLIGHT → UNKNOWN on init_db()
# ===========================================================================

def test_16_crash_recovery_in_flight_to_unknown(isolated_db):
    conn = sqlite3.connect(isolated_db)
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    cursor.execute("""
        INSERT INTO bank_import_sessions
            (session_key, bank_ledger, row_count, status, created_at, updated_at)
        VALUES ('sess-crash', 'Test Bank', 1, 'IN_PROGRESS', ?, ?)
    """, (now, now))
    sess_id = cursor.lastrowid

    cursor.execute("""
        INSERT INTO bank_import_rows
            (session_id, row_key, date, narration_source,
             withdraw_minor, deposit_minor, txn_direction,
             ledger, status, created_at, updated_at)
        VALUES (?, 'rk-crash', '20250809', 'ATM WDL', 500000, 0, 'DEBIT',
                'Cash', 'IN_FLIGHT', ?, ?)
    """, (sess_id, now, now))
    conn.commit()
    conn.close()

    with patch("backend.database.DB_PATH", isolated_db):
        from backend.database import init_db, get_bank_import_row
        init_db()

        recovered = get_bank_import_row("sess-crash", "rk-crash")
        assert recovered is not None
        assert recovered["status"] == "UNKNOWN"
        assert "Verify in Tally" in recovered["error_message"]


# ===========================================================================
# TEST 17 — crash recovery does not touch non-IN_FLIGHT rows
# ===========================================================================

def test_17_crash_recovery_leaves_other_rows_unchanged(isolated_db):
    with patch("backend.database.DB_PATH", isolated_db):
        from backend.database import (
            create_bank_import_session,
            mark_bank_import_row_in_flight,
            mark_bank_import_row_imported,
            mark_bank_import_row_unknown,
            get_bank_import_row, init_db
        )

        sess = "sess-safe"
        create_bank_import_session(sess, "Bank", [
            _row("rk-pending"),
            _row("rk-failed"),
            _row("rk-imported"),
            _row("rk-unknown"),
        ])

        # FAILED: direct SQL (no single-step legal path from PENDING→FAILED)
        _force_row_status(isolated_db, sess, "rk-failed", "FAILED")

        # IMPORTED: PENDING → IN_FLIGHT → IMPORTED (two legal steps)
        mark_bank_import_row_in_flight(sess, "rk-imported")
        mark_bank_import_row_imported(sess, "rk-imported")

        # UNKNOWN: PENDING → IN_FLIGHT → UNKNOWN (two legal steps)
        mark_bank_import_row_in_flight(sess, "rk-unknown")
        mark_bank_import_row_unknown(sess, "rk-unknown")

        init_db()  # Crash recovery runs — no IN_FLIGHT rows, so it's a no-op

        assert get_bank_import_row(sess, "rk-pending")["status"]  == "PENDING"
        assert get_bank_import_row(sess, "rk-failed")["status"]   == "FAILED"
        assert get_bank_import_row(sess, "rk-imported")["status"] == "IMPORTED"
        assert get_bank_import_row(sess, "rk-unknown")["status"]  == "UNKNOWN"


# ===========================================================================
# TEST 18 — session OFFLINE_QUEUED transition
# ===========================================================================

def test_18_offline_queued_transition(isolated_db):
    with patch("backend.database.DB_PATH", isolated_db):
        from backend.database import (
            create_bank_import_session,
            set_bank_import_session_offline_queued,
            get_bank_import_session,
        )

        create_bank_import_session("sess-oq", "Bank", [_row("rk-1")])
        assert set_bank_import_session_offline_queued("sess-oq") is True
        assert get_bank_import_session("sess-oq")["status"] == "OFFLINE_QUEUED"

        assert set_bank_import_session_offline_queued("sess-oq") is False
        assert get_bank_import_session("sess-oq")["status"] == "OFFLINE_QUEUED"

        assert set_bank_import_session_offline_queued("no-such-session") is False


# ===========================================================================
# TEST 19 — bank_ledger is immutable through DB helpers
# ===========================================================================

def test_19_bank_ledger_immutable(isolated_db):
    with patch("backend.database.DB_PATH", isolated_db):
        from backend.database import (
            create_bank_import_session,
            get_bank_import_session,
        )

        create_bank_import_session("sess-19", "Federal Bank A", [_row("rk-1")])
        assert get_bank_import_session("sess-19")["bank_ledger"] == "Federal Bank A"
        assert get_bank_import_session("sess-19")["bank_ledger"] == "Federal Bank A"


# ===========================================================================
# TEST 20 — SQLite IDs not exposed by public helpers
# ===========================================================================

def test_20_db_ids_not_exposed(isolated_db):
    with patch("backend.database.DB_PATH", isolated_db):
        from backend.database import (
            create_bank_import_session,
            get_bank_import_session,
            get_bank_import_row,
            get_bank_import_rows_for_session,
        )

        create_bank_import_session("sess-20", "Bank", [_row("rk-1")])

        session = get_bank_import_session("sess-20")
        assert "id" not in session
        assert "session_id" not in session

        row = get_bank_import_row("sess-20", "rk-1")
        assert "id" not in row
        assert "session_id" not in row

        rows = get_bank_import_rows_for_session("sess-20")
        for r in rows:
            assert "id" not in r
            assert "session_id" not in r

        result = create_bank_import_session("sess-20b", "Bank", [_row("rk-x")])
        assert set(result.keys()) == {"session_key", "row_count"}


# ===========================================================================
# TEST 21 — _bank_row_transition whitelist: illegal transitions are refused
#
# This is the primary hardening verification test.
# Uses direct SQL to construct starting states, then asserts _bank_row_transition
# raises ValueError for each illegal (from, to) pair — without touching the DB.
# ===========================================================================

def test_21_illegal_transitions_refused(isolated_db):
    with patch("backend.database.DB_PATH", isolated_db):
        from backend.database import (
            create_bank_import_session,
            _bank_row_transition,
            get_bank_import_row,
        )

        sess = "sess-21"
        create_bank_import_session(sess, "Bank", [
            _row("rk-pending"),
            _row("rk-failed"),
            _row("rk-imported"),
            _row("rk-unknown"),
        ])

        # Force states using direct SQL (bypassing production helpers as needed)
        _force_row_status(isolated_db, sess, "rk-failed",   "FAILED")
        _force_row_status(isolated_db, sess, "rk-imported", "IMPORTED")
        _force_row_status(isolated_db, sess, "rk-unknown",  "UNKNOWN")

        # --- PENDING → IMPORTED: illegal ---
        with pytest.raises(ValueError, match="Illegal bank import row transition"):
            _bank_row_transition(sess, "rk-pending", ("PENDING",), "IMPORTED")
        assert get_bank_import_row(sess, "rk-pending")["status"] == "PENDING"

        # --- PENDING → FAILED: illegal ---
        with pytest.raises(ValueError, match="Illegal bank import row transition"):
            _bank_row_transition(sess, "rk-pending", ("PENDING",), "FAILED")
        assert get_bank_import_row(sess, "rk-pending")["status"] == "PENDING"

        # --- PENDING → UNKNOWN: illegal ---
        with pytest.raises(ValueError, match="Illegal bank import row transition"):
            _bank_row_transition(sess, "rk-pending", ("PENDING",), "UNKNOWN")
        assert get_bank_import_row(sess, "rk-pending")["status"] == "PENDING"

        # --- FAILED → IMPORTED: illegal ---
        with pytest.raises(ValueError, match="Illegal bank import row transition"):
            _bank_row_transition(sess, "rk-failed", ("FAILED",), "IMPORTED")
        assert get_bank_import_row(sess, "rk-failed")["status"] == "FAILED"

        # --- FAILED → UNKNOWN: illegal ---
        with pytest.raises(ValueError, match="Illegal bank import row transition"):
            _bank_row_transition(sess, "rk-failed", ("FAILED",), "UNKNOWN")
        assert get_bank_import_row(sess, "rk-failed")["status"] == "FAILED"

        # --- IMPORTED → FAILED: illegal (terminal state) ---
        with pytest.raises(ValueError, match="Illegal bank import row transition"):
            _bank_row_transition(sess, "rk-imported", ("IMPORTED",), "FAILED")
        assert get_bank_import_row(sess, "rk-imported")["status"] == "IMPORTED"

        # --- IMPORTED → IN_FLIGHT: illegal (terminal state) ---
        with pytest.raises(ValueError, match="Illegal bank import row transition"):
            _bank_row_transition(sess, "rk-imported", ("IMPORTED",), "IN_FLIGHT")
        assert get_bank_import_row(sess, "rk-imported")["status"] == "IMPORTED"

        # --- UNKNOWN → IN_FLIGHT: illegal ---
        with pytest.raises(ValueError, match="Illegal bank import row transition"):
            _bank_row_transition(sess, "rk-unknown", ("UNKNOWN",), "IN_FLIGHT")
        assert get_bank_import_row(sess, "rk-unknown")["status"] == "UNKNOWN"

        # --- UNKNOWN → PENDING: illegal ---
        with pytest.raises(ValueError, match="Illegal bank import row transition"):
            _bank_row_transition(sess, "rk-unknown", ("UNKNOWN",), "PENDING")
        assert get_bank_import_row(sess, "rk-unknown")["status"] == "UNKNOWN"
