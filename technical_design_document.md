# Complete Technical Design Document

Here is the extremely detailed, exhaustive technical design document analyzing the current state of the project.

---

## SECTION 1: PROJECT OVERVIEW

### What this application does
This application is an AI-powered, offline-first accounting and inventory management middleware designed specifically to sit in front of Tally ERP. It allows users to manage daily business operations (purchases, sales, fund transfers, stock transfers, bank statement reconciliation) through a modern web interface without needing direct access to the Tally desktop application. 

### Main purpose
The primary purpose is to simplify data entry for accounting and inventory, automate invoice data extraction using AI (Gemini OCR), and safely synchronize all financial vouchers and master data with a local Tally server via XML. 

### Intended users
Business owners, warehouse managers, and accounting staff who need to enter or reconcile financial data (like purchase invoices, bank statements, and stock transfers) without needing a Tally license on their machine or needing to understand complex accounting software interfaces.

### Current workflow
1. **Data Entry/Upload**: Users interact with the frontend to create vouchers manually or upload documents (PDF/Images/Excel).
2. **AI Processing**: Uploaded invoices and statements are sent to the backend, passed through Gemini AI for data extraction, and mapped to Tally ledgers using fuzzy matching (RapidFuzz) and SQLite mapping rules.
3. **Queueing**: The finalized voucher data is saved to a local SQLite database (`offline_queue`).
4. **Synchronization**: A background worker continuously polls the queue and attempts to push the data to Tally via an XML HTTP POST request. If Tally is offline, it remains queued.

### Overall architecture
A monolithic client-server architecture.
- **Frontend**: A Next.js (App Router) React application serving as the UI.
- **Backend**: A Python FastAPI server handling business logic, AI orchestration, and database operations.
- **Database**: A local SQLite database (`tally_sync.db`) acting as the single source of truth for offline operations and caching Tally master data.
- **Integration Layer**: A background asynchronous worker (`tally_sync_worker.py`) handling XML communication with the Tally HTTP server.

---

## SECTION 2: TECH STACK

### Frontend
- **Framework**: Next.js 16.3.0 (App Router)
- **Library**: React 19.2.8
- **Language**: JavaScript (No TypeScript)
- **CSS Framework**: Tailwind CSS v4 (via PostCSS)
- **UI Library**: Vanilla HTML/Tailwind classes (No component libraries like Shadcn or MUI).
- **Icons**: `lucide-react`
- **State Management**: React `useState` and `useEffect` (Local component state; no Redux/Zustand).

### Backend
- **Framework**: FastAPI (Python)
- **Server**: Uvicorn
- **Language**: Python 3.x
- **Data Parsing**: Pandas, PyPDF, pdfminer, openpyxl, xlrd (for parsing Bank Statements in CSV/Excel/PDF).
- **HTTP Client**: `requests`, `httpx` (for Tally communication and API calls).

### Database
- **Engine**: SQLite3 natively via Python `sqlite3` module.

### Authentication
- None implemented. The application is completely open and relies on local network security. Forms have an optional "password" field for encrypted PDFs, but no app-level user auth exists.

### OCR & AI
- **LLM**: Google Gemini (`google-genai`, `google-generativeai`).
- **Prompting**: JSON-mode forced prompts for structured data extraction.
- **Matching**: `RapidFuzz` for fuzzy string matching (e.g., matching scanned item names to Tally stock items).
- **Legacy OCR**: `pytesseract` and `opencv-python-headless` are installed as dependencies but primarily superseded by Gemini.

### Tally Integration
- **Protocol**: HTTP POST using XML payloads.
- **Background Workers**: `asyncio.create_task` running an infinite loop (`tally_sync_worker.py`) attached to the FastAPI lifespan.

---

## SECTION 3: PROJECT STRUCTURE

```text
/INV Scanner copy
├── .env                  # Environment variables (Tally URL, Gemini API keys)
├── requirements.txt      # Python dependencies
├── backend/              # Python FastAPI Application
│   ├── main.py           # Application entry point, router inclusion, lifespan
│   ├── config.py         # Centralized configuration (loads .env)
│   ├── database.py       # SQLite connection manager and CRUD operations
│   ├── tally_sync.db     # SQLite Database file
│   ├── routers/          # API Route Controllers
│   │   ├── bank_statement.py
│   │   ├── dashboard.py
│   │   ├── payment.py
│   │   ├── purchase.py
│   │   ├── purchase_item.py
│   │   ├── sales.py
│   │   ├── stock_transfer.py
│   │   ├── sync.py
│   │   └── transfer.py
│   └── services/         # Business Logic & Workers
│       ├── extraction_engine.py
│       ├── tally_sync_service.py
│       └── tally_sync_worker.py
└── frontend/             # Next.js Application
    ├── package.json      
    └── src/
        └── app/          # Next.js App Router Pages
            ├── layout.js
            ├── globals.css
            ├── page.js   # Main index (redirects or displays nav)
            ├── bank-statement/
            │   └── page.js
            ├── dashboard/
            │   └── page.js
            ├── payment/
            │   └── page.js
            ├── purchase/
            │   └── page.js
            ├── purchase-item/
            │   └── page.js
            ├── sales/
            │   └── page.js
            └── transfer/
                └── page.js
```

### Purpose of Important Files
- `backend/database.py`: The critical data layer. Contains raw SQL queries for managing the `offline_queue`, master caches, and mapping rules.
- `backend/main.py`: Bootstraps FastAPI, configures CORS, mounts routers, and starts the background sync worker.
- `backend/services/tally_sync_worker.py`: The background daemon that polls Tally for master data updates and pushes pending XML vouchers from the queue.

---

## SECTION 4: BACKEND

### Routes & Controllers
Located in `backend/routers/`, these modules handle HTTP requests, validate payloads (using Pydantic `BaseModel`), and return JSON responses.
- **bank_statement.py**: Handles file uploads (PDF/Excel), parses transaction rows, queries SQLite for mappings, and generates Tally Bank XML. Also houses the CRUD for Bank Mappings.
- **stock_transfer.py**: Handles internal inventory movement between Tally Godowns.
- **transfer.py**: Handles fund transfers between bank/cash ledgers.
- **sales/payment/purchase.py**: Handle manual voucher creation for their respective accounting types.
- **dashboard.py**: Serves analytics and queue status for the frontend.

### Services & Business Logic
- **extraction_engine.py**: Wraps the Gemini API. Takes images/PDFs, constructs prompts, enforces JSON schema formatting, and utilizes `json_repair` to fix broken LLM outputs.
- **tally_sync_service.py**: Contains the logic to formulate raw XML queries (e.g., `<ENVELOPE><HEADER><TALLYREQUEST>Export...`) to fetch Ledgers, Stock Items, and UOMs from Tally.

### Database Layer
`backend/database.py` operates without an ORM. It uses native `sqlite3` with `contextlib.contextmanager` (`get_db()`).

### Background Services & Queue
- **Offline Sync**: Instead of posting directly to Tally, routers serialize voucher XML and call `queue_operation()`. 
- **Queue**: The `offline_queue` table acts as a message broker. `tally_sync_worker.py` loops every 10 seconds, picks up `PENDING` operations, POSTs them to Tally, and marks them `COMPLETED` or `FAILED` based on the Tally XML response.
- **Master Sync**: The worker also periodically polls Tally for Ledgers, Stock Items, and UOMs, caching them in SQLite so the web app functions instantly without querying Tally.

---

## SECTION 5: DATABASE

### Current Database
SQLite3 (`tally_sync.db`) located in the `backend/` directory.

### Tables & Fields
1. **offline_queue**
   - `id` (INTEGER PRIMARY KEY)
   - `operation_type` (TEXT): e.g., 'Stock Transfer', 'Bank Statement'
   - `payload` (TEXT): Stringified JSON of the original request
   - `xml_data` (TEXT): The raw Tally XML to be pushed
   - `status` (TEXT): 'PENDING', 'COMPLETED', 'FAILED'
   - `error_message` (TEXT)
   - `description` (TEXT)
   - `created_at` (TEXT)
   - `updated_at` (TEXT)
2. **ledgers**
   - `id` (INTEGER PRIMARY KEY)
   - `name` (TEXT UNIQUE): Ledger name exactly as in Tally
   - `parent` (TEXT): Ledger group
   - `cost_centre` (BOOLEAN): 1 if cost centers are enabled, 0 otherwise
3. **stock_items**
   - `id` (INTEGER PRIMARY KEY)
   - `name` (TEXT UNIQUE)
   - `unit` (TEXT): Base Unit of Measure (UOM)
4. **uoms**
   - `id` (INTEGER PRIMARY KEY)
   - `name` (TEXT UNIQUE)
5. **purchase_rates**
   - `id` (INTEGER PRIMARY KEY)
   - `item_name` (TEXT UNIQUE)
   - `last_purchase_rate` (REAL)
6. **bank_mappings**
   - `id` (INTEGER PRIMARY KEY)
   - `bank_account_name` (TEXT)
   - `keyword` (TEXT)
   - `target_ledger` (TEXT)
   - `cost_center` (TEXT)

### Data Flow
1. Master data flows **One-Way**: Tally -> XML -> `tally_sync_worker.py` -> SQLite (ledgers, stock_items).
2. Transaction data flows **One-Way**: Frontend -> API Router -> SQLite (`offline_queue`) -> `tally_sync_worker.py` -> XML -> Tally.

---

## SECTION 6: API DOCUMENTATION

*Note: All endpoints are prefixed with `/api` and expect/return `application/json` unless otherwise noted.*

### Stock Transfer
- **POST `/stock-transfer/submit`**: Creates a stock journal XML. Body requires source godown, destination godown, and items array.

### Fund Transfer
- **POST `/transfer/submit`**: Creates a contra/journal XML for bank-to-bank or bank-to-cash.

### Sales / Payment / Purchase
- **POST `/{module}/submit`**: Creates standard accounting vouchers.

### Bank Statement
- **POST `/bank-statement/upload`**: (Multipart/Form-Data). Accepts file, parses it, queries `bank_mappings` DB, returns array of categorized transactions.
- **POST `/bank-statement/post`**: Queues the finalized bank transactions to the `offline_queue`.
- **GET `/bank-statement/mappings/banks`**: Returns list of unique mapped bank accounts.
- **GET `/bank-statement/mappings?bank={name}`**: Fetches mappings.
- **POST `/bank-statement/mappings`**: Creates a new mapping.
- **PUT `/bank-statement/mappings/{id}`**: Updates a mapping.
- **DELETE `/bank-statement/mappings/{id}`**: Deletes a mapping.
- **POST `/bank-statement/create-ledger`**: Instantly queues a new ledger creation XML to Tally and optimistically saves to SQLite.

### Dashboard & Sync
- **GET `/dashboard/stats`**: Returns counts of pending/failed/completed queue items.
- **GET `/sync/status`**: Returns boolean indicating if Tally is currently reachable.

---

## SECTION 7: CURRENT FRONTEND

### Pages
1. **`/dashboard`**: 
   - *Purpose*: Landing page/Overview.
   - *Components*: Stat cards.
   - *Missing Features*: No detailed view of the `offline_queue`. Failed transactions cannot be retried from the UI.
2. **`/bank-statement`**: 
   - *Purpose*: Upload bank PDFs/Excel, AI categorize, and post to Tally.
   - *Components*: File uploader, Tabbed Mappings Sidebar, Review Transactions Table, Create Ledger Modal.
   - *UI*: Complex 2-column layout. 
3. **`/purchase-item`**: 
   - *Purpose*: Upload purchase invoices (images) for Gemini to extract line items.
4. **`/sales`, `/purchase`, `/payment`, `/transfer`, `/stock-transfer`**: 
   - *Purpose*: Standard form-based manual data entry pages.
   - *UI*: Basic vertical forms with native HTML inputs.

*General UI Assessment*: Built using raw Tailwind CSS. No cohesive design system. Highly fragmented styling between different pages. 

---

## SECTION 8: COMPONENTS

There are **zero** reusable components extracted in this project (no `src/components` folder exists). 
Every single page (`page.js`) hardcodes its own UI elements:
- Buttons (`<button className="bg-teal-600...">`)
- Tables (`<table className="min-w-full...">`)
- Modals (Built inline with fixed positioning and z-indexes).
- Sidebars

Because of this, if the styling of a primary button needs to change, it must be manually updated in every single `page.js` file.

---

## SECTION 9: FEATURES

### 1. Bank Statement Import & Mapping
Users upload a bank PDF. The backend uses PyPDF/Pandas to extract dates, narrations, and amounts. It checks the `bank_mappings` table for keyword matches in the narration to assign a Tally Ledger and Cost Center. Unmapped transactions are sent to the frontend for manual mapping.

### 2. Invoice OCR (Purchase Item)
Users upload an image of a purchase invoice. The backend sends the image to Gemini 1.5 with a strict prompt to extract items, quantities, and rates. The extracted items are fuzzy-matched (`RapidFuzz`) against the cached SQLite `stock_items` to find the exact Tally item name.

### 3. Offline Queue System
Transactions are never blocked by Tally being offline. Vouchers are saved to the SQLite `offline_queue`.

### 4. Background Master Sync
The application maintains its own copy of Tally Ledgers, Items, and UOMs, polling Tally via XML to keep them updated. This allows dropdowns on the frontend to populate instantly without network latency.

---

## SECTION 10: TALLY

### XML Generation
The backend builds raw XML strings using Python f-strings rather than a library like `xml.etree`.
Example: `<ENVELOPE><HEADER><TALLYREQUEST>Import Data...`

### Voucher Creation
Vouchers are sent as `Import Data` requests. 
- Bank statements are sent as `Receipt` or `Payment` vouchers.
- Fund transfers are sent as `Contra`.
- Stock transfers are sent as `Stock Journal`.

### Queue & Sync
Handled by `tally_sync_worker.py`. 
- **Conflict handling**: Almost non-existent. If a voucher fails in Tally (e.g., invalid ledger), Tally returns a failure XML. The worker updates the status to `FAILED` in SQLite and stores the error message.
- **Failure recovery**: No UI exists to retry or edit a failed transaction. It requires direct database intervention.

---

## SECTION 11: AI

### Gemini
- Used exclusively for visual document extraction (Invoices).
- Relies on `json_repair` library because Gemini sometimes outputs markdown code blocks (````json ... ````) instead of raw JSON strings.

### RapidFuzz
- Applied in the `purchase_item` pipeline.
- `fuzz.token_sort_ratio` is used to compare the Gemini-extracted item name against the thousands of cached Tally stock items to find the best match.

---

## SECTION 12: USER FLOW

### Bank Statement Flow
1. User selects PDF and Bank Ledger -> Clicks Analyze.
2. Frontend `POST /upload` (multipart/form-data).
3. Backend reads file into memory -> Parses via Pandas/PyPDF.
4. Backend queries SQLite `bank_mappings`.
5. Backend matches rows -> Returns JSON array of transactions.
6. Frontend displays table. User maps unmapped items (optionally saving new rules to SQLite via API).
7. User clicks "Push to Tally".
8. Frontend checks validation (Missing Cost Centers).
9. Frontend `POST /post`.
10. Backend generates XML -> Inserts to `offline_queue`.
11. Async Worker picks up queue item -> POSTs to Tally HTTP server.

---

## SECTION 13: CODE QUALITY

### Architecture & Design Patterns
- **Strengths**: The Offline-first SQLite architecture is highly resilient. Moving Tally XML logic into a background worker prevents the frontend from hanging.
- **Weaknesses**: The frontend is a monolithic mess of duplicated code. No React components are abstracted.
- **Duplicate Code**: Every page handles its own fetch logic, error handling, toast notifications, and UI rendering.
- **Technical Debt**: 
  - Raw XML string concatenation in Python is brittle.
  - Lack of TypeScript means API responses and state payloads are completely untyped, leading to runtime errors if schema changes.

---

## SECTION 14: PERFORMANCE

- **Bottlenecks**: Uploading large PDFs to the backend blocks the Python thread while Pandas processes it.
- **Database Efficiency**: Excellent. SQLite handles the minimal concurrency of this app perfectly.
- **Caching**: Excellent. By storing Tally masters in SQLite, the app avoids the massive latency (often 2-5 seconds) of querying Tally directly.

---

## SECTION 15: SECURITY

- **Authentication / Authorization**: Non-existent. Anyone with the URL can post financial vouchers to Tally.
- **Secrets**: Handled via `.env` files (Gemini API keys).
- **Validation**: Minimal. Heavily relies on Tally to reject bad XML rather than validating strict accounting rules before queueing.

---

## SECTION 16: RESPONSIVENESS

- **Mobile/Tablet**: Very poor. While Tailwind CSS is used, the tables (like Bank Statement review) use fixed widths or `min-w-full` without proper overflow wrappers, causing horizontal scrolling breaking the layout on mobile. Forms are stacked but not optimized for touch.
- **Desktop**: Functional, but lacks standard max-width containers, making it look stretched on ultra-wide monitors.

---

## SECTION 17: FUTURE FEATURES

Based on the current offline-queue and SQLite architecture, the following can be naturally added:
1. **Queue Management Dashboard**: A UI to view, edit, and retry `FAILED` transactions from the `offline_queue`.
2. **Authentication**: Adding JWT-based login (e.g., NextAuth/Auth.js) to secure the routes.
3. **Multi-Company Support**: Currently hardcoded to whatever company is open in Tally. The architecture could support querying Tally for open companies and passing a `Company` header in the XML.
4. **Webhooks/Notifications**: Alerting a Slack/Telegram channel when the background worker encounters a Tally failure.

---

## SECTION 18: SUMMARY

### Executive Summary
The application is highly capable in its business logic but immature in its frontend engineering. 

The backend architecture is **highly robust**. The decision to use SQLite as a caching and queuing layer insulates the user from Tally's notoriously slow and synchronous HTTP server. The background worker design is a massive strength, ensuring data integrity and offline capabilities.

However, the frontend requires a total rewrite. The absence of a component-based architecture in Next.js indicates it was built rapidly as a proof-of-concept. The lack of TypeScript, reusable UI components, and state management makes the frontend fragile and difficult to scale. 

**Overall Assessment**: The backend is deployment-ready and structurally sound. The frontend is functional but technically indebted. A complete UI/UX redesign using modern React paradigms (abstracted components, strict typing, centralized API fetching) will transform this from a functional prototype into a robust enterprise application.
