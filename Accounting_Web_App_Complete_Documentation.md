# Mom's Pride: Complete Project Documentation

This is the exhaustive, granular technical documentation for the "Mom's Pride" offline-first Accounting Web App (Tally middleware). It covers the architecture, the complete mobile-first frontend overhaul, the FastAPI backend, the AI integrations, and the background synchronization worker. 

This document serves as the absolute source of truth for the codebase as of this moment.

---

## 1. High-Level Architecture

The system is designed to allow non-technical retail staff to record transactions on their mobile phones without directly interacting with Tally ERP 9/Prime. 

It uses an **Offline-First Queue** architecture:
1. **Frontend (Next.js):** Users submit data via a mobile-optimized PWA-style web app.
2. **Backend (FastAPI):** Receives the data, instantly translates it into Tally-compatible XML, and saves it to a local SQLite `offline_queue` database. It immediately returns a success response to the user.
3. **Background Worker (Python Thread):** Constantly loops in the background. It pops `PENDING` items from the queue and attempts to send them to the `TALLY_URL` (usually `http://127.0.0.1:9000`). If Tally is closed or disconnected, the queue items remain pending until Tally is reopened.

---

## 2. Frontend Structure (Next.js 15, Tailwind v4)

The frontend has been completely redesigned from a desktop-centric table layout to a sleek, mobile-first, app-like experience.

### 2.1 Configuration & Core
- `next.config.mjs`: Standard Next.js configuration.
- `src/app/globals.css`: Global styles, Tailwind v4 injection, custom root variables, custom scrollbar styling, and `next-themes` dark mode class setup.
- `src/app/layout.js`: The root document. Injects the Google Geist font, `suppressHydrationWarning` (to prevent browser extension clashes), and wraps the application in `<Providers>`.
- `src/app/Providers.js`: Wraps the app in the `next-themes` `ThemeProvider` (for light/dark mode) and our custom `UIProvider`.
- `src/context/UIContext.js`: **Crucial state management.** Exposes a global hook `useUI()` to trigger `showToast`, `showActionSheet`, and `showConfirmDialog` from anywhere in the app without rendering local modals on every page.

### 2.2 Global Layout Components (`src/components/layout/`)
- `TopBar.js`: Fixed header. Displays the page title, a back button (if requested via props), and a WiFi icon indicating real-time Tally connectivity. Supports a custom `onBack` prop for overriding default `router.back()` behavior (used heavily in wizard flows).
- `BottomNav.js`: Fixed mobile tab bar at the bottom of the screen. Features an elevated, centered "+" (FAB) button. Tapping the FAB opens an `ActionSheet` to quickly jump to any voucher creation screen (Sales, Purchase, Payment, etc.). 

### 2.3 Reusable UI Component Library (`src/components/ui/`)
- `Button.js`: Standardized button with `primary`, `secondary`, and `danger` variants. Handles hover/active states and dark mode inversion.
- `Input.js`: A floating-label style or distinctly labeled input field. Handles HTML5 types (`text`, `number`, `date`, `password`).
- `Select.js`: Standardized dropdown wrapper matching the `Input` aesthetic.
- `TextArea.js`: Multi-line text editor with focus rings and error states. Used in Queue Management.
- `Card.js`: The foundational layout building block. A rounded, bordered, shadow-having container that seamlessly adapts to dark mode.
- `StatusBadge.js`: Renders small pill-shaped badges (Green = Synced, Amber = Waiting, Red = Failed) based on a passed status string.
- `EmptyState.js`: A placeholder component for when lists (like Recent Activity or Mappings) are empty.

### 2.4 Application Pages (`src/app/`)

#### 1. Dashboard (`/dashboard`)
- **Stats Card:** Displays real-time counts of Waiting queue items, Failed items, and successfully synced items for the day.
- **Recent Activity Feed:** Fetches the last 10 entries from the `offline_queue` table. Displays them as tappable cards with icons representing the transaction type (derived dynamically from `operation_type`) and live `StatusBadge`s.
- **Queue Management Modal:** Tapping an activity card opens a detailed view. It displays the Tally Error message (if it failed). It provides `TextArea` components to manually edit the **JSON Payload** and **Tally XML Data**. Includes buttons to **Save & Queue**, **Retry**, and **Delete**.

#### 2. Purchase (`/purchase`)
- Features a **Segmented Control** at the top to toggle between "Accounting Mode" and "Item Wise Mode".
- **AccountingMode.js:** A simple form for booking basic expense purchases without line-item inventory. Requires Supplier, Amount, Invoice Number, and Date.
- **ItemWiseMode.js (Complex):**
  - Starts with an Upload component asking for a photo or PDF of a supplier invoice.
  - Sends the file to the backend, where Google Gemini extracts it into a structured JSON array of line items.
  - Renders the invoice header (Supplier, Date, Inv No) as editable inputs.
  - Renders each extracted line item as a collapsed `Card`. Tapping a card expands it into an "Edit Mode" allowing the user to map the raw invoice item name to a standard **Tally Item Mapping**, edit Qty, Rate, UOM, and Amount. 
  - Validates unknown items and warns the user if they haven't mapped an item before pushing.
  - Footer provides an "Edit" toggle to manually override the AI-extracted CGST, SGST, IGST, and Rounding amounts.

#### 3. Sales (`/sales`)
- A streamlined form for recording sales. Inputs: Ledger (Customer), Amount, Cost Center, and Narration. 
- Features an "Add Sale" action that pushes the generated Tally XML to the queue.

#### 4. Payment (`/payment`)
- Features a **Segmented Control** for "Expense" vs "Party/Other".
- **Expense Mode:** Pre-fills the debit ledger list with PnL expense accounts and mandates Cost Center selection.
- **Party/Other Mode:** Allows selecting any ledger.
- The UI handles the double-entry logic (Debit Ledger vs Credit/Bank Ledger) via clean dropdowns.

#### 5. Bank Statement (`/bank-statement`)
- A highly advanced 3-step wizard.
- **Step 1 (Upload):** User selects a Bank Ledger, uploads a PDF/Excel statement, and optionally provides a password. Contains a "Manage Rules" button.
- **Rules Management Toggle:** Clicking "Manage Rules" replaces the view with a list of all saved auto-mapping rules for the selected bank. Allows deletion of rules.
- **Step 2 (Review):** Displays the extracted transactions as vertical cards. 
  - Matched items display a green checkmark.
  - Unmapped items prompt the user to select a target ledger.
  - Selecting a ledger triggers a `ConfirmDialog` asking "Always send transactions containing 'X' to this ledger?". Confirming fires an API call to save this rule for future AI processing.
  - Validates cost center requirements natively based on cached Tally Master data.

#### 6. Transfers & Stock Transfers (`/transfer`, `/stock-transfer`)
- **Money Transfer:** Simple From Account -> To Account -> Amount form.
- **Stock Transfer:** Source Godown -> Destination Godown -> Select Item -> Qty form.

---

## 3. Backend Structure (FastAPI, Python)

The backend is built with FastAPI and runs on `uvicorn`.

### 3.1 Core & Config
- `backend/main.py`: Bootstraps FastAPI, configures aggressive CORS (allowing all origins/methods for local dev), includes all routers, and starts the `tally_sync_worker` in a daemon thread on startup.
- `backend/config.py`: Loads `.env` variables (`GEMINI_API_KEY`, `TALLY_URL`).

### 3.2 Database Layer (`backend/database.py`)
- Uses `sqlite3`. 
- **Schema:**
  - `offline_queue`: Stores `operation_type`, `payload`, `xml_data`, `status` (PENDING/SUCCESS/ERROR), `error_message`, and timestamps.
  - `stock_items`, `ledgers`, `uoms`: Caches of Tally Master Data to provide fast dropdowns on the frontend without querying Tally in real-time.
  - `purchase_item_rates`: Historic tracking of item prices.
  - `bank_mappings`: Stores `bank_account_name`, `keyword`, `target_ledger`, and `cost_center` for the auto-categorization engine.
- **Key Functions:** `queue_operation()` wraps XML payloads and JSON dumps into the `offline_queue` table.

### 3.3 AI Extraction Service (`backend/services/ai_extraction.py`)
- Configures the `google.generativeai` SDK using `gemini-1.5-pro` (or flash).
- Contains massive, highly-engineered system prompts for:
  - **Invoice Parsing:** Extracts Supplier Name, Invoice Number, Date, GST Rates, Tax Types, CGST, SGST, IGST, Rounding, and an array of line items from images/PDFs.
  - **Bank Statement Parsing:** Extracts Date, Narration, Withdrawal, and Deposit columns from messy bank PDF statements, handling encrypted PDFs via `PyPDF2`.

### 3.4 Background Sync Worker (`backend/services/tally_sync_worker.py`)
- The heartbeat of the application.
- **`fetch_and_cache_masters()`:** Sends XML requests to Tally to export all Ledgers (checking `IsCostCentresOn`), Stock Items, and UOMs. Parses the XML and bulk-inserts them into the SQLite cache tables.
- **`flush_offline_queue()`:** 
  1. Queries SQLite for `status = 'PENDING'`.
  2. Iterates over them, sending the `xml_data` to Tally via HTTP POST.
  3. Parses Tally's XML response.
  4. If Tally returns `<LINEERROR>`, it catches it, sets status to `ERROR`, and saves the error message back to SQLite.
  5. If successful, updates status to `SUCCESS`.
- Runs in an infinite loop (`while True`) with a sleep interval.

### 3.5 API Routers (`backend/routers/`)
- **`dashboard.py`**:
  - `GET /stats`: Returns counts for waiting/failed queue items and tally connectivity.
  - `GET /activity`, `GET /activity/{id}`, `PUT /activity/{id}`, `DELETE /activity/{id}`, `POST /activity/{id}/retry`: Complete CRUD for the new frontend Queue Management modal.
- **`purchase_item.py`**:
  - `POST /extract`: Calls Gemini to read the uploaded invoice.
  - `POST /post`: Generates an extremely complex nested XML payload (`<TALLYMESSAGE>`) for Item-wise purchase vouchers, handling Cost Centers, IGST vs CGST/SGST ledger mapping, and rounding. Calls `queue_operation`.
- **`bank_statement.py`**:
  - `POST /upload`: Extracts statement, applies regex/fuzzy matching against the `bank_mappings` table, and returns mapped/unmapped transactions to the frontend.
  - `POST /mappings`, `PUT /mappings/{id}`, `DELETE /mappings/{id}`: CRUD for the rules engine.
  - `POST /create-ledger`: Dynamically generates Tally XML to create a new Ledger in Tally on the fly if an accountant needs a new category.
- **`sales.py`, `payment.py`, `transfer.py`, `stock_transfer.py`**:
  - Receive simplified JSON payloads from the frontend.
  - Inject variables into hardcoded Tally XML templates.
  - Send the generated XML to the `offline_queue`.

---

## 4. Tally XML Integration Details

Tally ERP 9 / Prime accepts data via HTTP POST to its configured port (usually 9000). The app communicates exclusively in Tally XML format.

### Key XML Concepts Used:
- **Exporting Data (Master Sync):** We send `<ENVELOPE><HEADER><TALLYREQUEST>Export...</HEADER>` targeting `<ID>LedgerCollection</ID>` with custom `<TDLMESSAGE>` blocks to fetch specific fields like `IsCostCentresOn`.
- **Importing Data (Voucher Creation):** We send `<ENVELOPE><HEADER><TALLYREQUEST>Import</TALLYREQUEST>` with `<VOUCHER VCHTYPE="Purchase" ACTION="Create">`.
- **Cost Centers:** If a ledger requires a cost center, the XML must include `<CATEGORYALLOCATIONS.LIST>` inside the ledger entry, specifying the `<COSTCENTREALLOCATIONS.LIST>`. If omitted, Tally throws a "Cost Centre missing" error (which our queue management catches).
- **Sanitization:** All user inputs (names, narrations) are passed through `escape()` to prevent XML injection errors (`&`, `<`, `>` breaking the payload).

---

## 5. Deployment & Execution
- **Database Initialization:** SQLite database (`app.db`) is automatically created on startup by `database.py`.
- **Backend Run:** `uvicorn backend.main:app --reload --port 8000`
- **Frontend Run:** `npm run dev` (running Next.js 15 Turbopack on port 3000).
- **Network Architecture:** The frontend is designed to be accessed by mobile phones on the local network (e.g., hitting `http://192.168.1.100:3000`). The frontend API calls hit `127.0.0.1:8000` (Note: if exposing to external devices, frontend `fetch` calls currently hardcoded to `127.0.0.1` should be updated to a dynamic environment variable or relative paths if proxying). 

---
*End of Documentation.*
