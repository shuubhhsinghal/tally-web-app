# CRITICAL PROJECT RULE — NEVER DESTROY EXISTING APPLICATION DATA

This INV Scanner application contains persistent operational data including:
- Offline Tally queue
- Pending vouchers
- Pending masters
- Synced/master state
- Ledger mappings
- Stock-item mappings
- Bank/import state
- Other SQLite application data

**THIS DATA IS SACRED.**

When implementing ANY future task, bug fix, refactor, dependency update, or feature:
- Do NOT delete `tally_sync.db`, recreate it, or replace it with an empty DB.
- Do NOT clear tables, truncate tables, reset IDs, reset queues, or reset pending masters.
- Do NOT modify database initialization so existing rows disappear.
- Do NOT use the real application database for destructive automated tests.

Database initialization must be NON-DESTRUCTIVE (e.g. `CREATE TABLE IF NOT EXISTS`). Schema modifications must be non-destructive migrations.

## Relative SQLite Paths
Verify what directory the SQLite path resolves against. Do not accidentally start the backend from another directory and silently create a second empty `tally_sync.db`. DO NOT move or replace the existing database until its current location and data have been identified.

## Test Isolation
Automated tests MUST NOT use the live/development `tally_sync.db`. Tests requiring writes must use a temporary SQLite database, in-memory DB, or dedicated test DB. Test teardown may delete TEST DATA only.

## Pre-Change Safety Check
1. Identify the active DB path.
2. Confirm whether the existing DB exists.
3. Inspect schema/table names WITHOUT modifying data.
4. Do not perform destructive operations. Preserve existing records.

## Required Final Reporting
At the end of every task, explicitly report:
1. Whether any database schema was changed.
2. Whether any database file was modified.
3. Whether any existing records were deleted.
4. Whether queue state was preserved.
5. Whether master state was preserved.
