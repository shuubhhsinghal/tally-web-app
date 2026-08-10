# Phase 1: Queue/Tally Sync Safety — TODO

## Backend
- [x] `backend/routers/dashboard.py`: Remove fake `processed_jobs: 142` from stats
- [x] `backend/routers/sync.py`: Add `GET /status` endpoint using TALLY_URL

## Frontend
- [x] `frontend/src/app/dashboard/page.js`: Fix status conditions (PENDING/SYNCED/FAILED)
- [x] `frontend/src/app/dashboard/page.js`: Fix failed count (FAILED not ERROR)
- [x] `frontend/src/app/dashboard/page.js`: Remove/hide third "Sent today" statistic

## Tests
- [x] Verify PENDING -> waiting
- [x] Verify SYNCED -> synced
- [x] Verify FAILED -> failed
- [x] Verify failed count uses FAILED
- [x] Verify SYNCED detail hides Edit/Retry/Delete
- [x] Verify PENDING behavior intact
- [x] Verify FAILED exposes Retry
- [x] Verify GET /api/sync/status returns online:false when offline
- [x] Verify TopBar consumes endpoint
- [x] Verify no SUCCESS/ERROR comparisons remain
- [x] Verify no hardcoded 142 remains
