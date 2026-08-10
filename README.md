# Mom's Pride: Accounting Web App

An offline-first Accounting Web App built as a mobile-friendly middleware for Tally ERP 9 / Prime. The application enables non-technical retail staff to easily record transactions (Sales, Purchase, Payment, Bank Statements) directly from their mobile devices, without needing direct access to Tally.

The app uses an **Offline-First Queue** architecture, allowing it to continue working even when Tally is offline. Data is securely saved to a local queue and automatically synced in the background once the Tally connection is restored.

## Key Features

- **Mobile-First UI**: Built with Next.js 15 and Tailwind v4, offering a sleek, app-like experience optimized for mobile browsers.
- **Offline-First Queue**: Transactions are stored in a local SQLite database and synchronized to Tally automatically by a background worker when available.
- **AI-Powered Extraction**: Uses Google Gemini to extract data from supplier invoices and bank statements for intelligent, auto-mapped entries.
- **Wizard & Form Interfaces**: Simplified, step-by-step entry forms for Sales, Purchases (Accounting & Item-wise), Payments, Bank Statements, and Stock Transfers.

## Architecture

1. **Frontend (Next.js)**: The Progressive Web App (PWA) interface used by the retail staff.
2. **Backend (FastAPI)**: A Python API that processes the frontend data, converts it into Tally-compatible XML, and manages the local `sqlite3` database queue.
3. **Background Worker**: A Python daemon thread in the backend that continuously attempts to sync queued items with the local Tally instance (`http://127.0.0.1:9000`).

## Setup Instructions

The project is split into `backend` and `frontend`.

### 1. Backend Setup (FastAPI)

Requires Python 3.9 or newer. Run this from the root directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Environment Variables:**
Create a `.env` file in the root with the following configurations:
```env
GEMINI_API_KEY=your_google_ai_studio_key
TALLY_URL=http://127.0.0.1:9000
```

**Run the Backend (Development/Local):**
```bash
cd backend
uvicorn main:app --reload --port 8000
```
*(This starts the FastAPI server and the Tally background synchronization worker on `127.0.0.1:8000`.)*

### 2. Frontend Setup (Next.js)

Requires Node.js.

```bash
cd frontend
npm install
npm run dev
```

The frontend will start at `http://localhost:3000`. 
**Note:** The Next.js frontend is configured to automatically proxy all API requests (`/api/*`) to the backend running locally at `http://127.0.0.1:8000`. When deploying to a VM, you only need to expose the Next.js frontend port (3000) to the internet; the backend can safely remain bound to `127.0.0.1`.

## Detailed Documentation

For an exhaustive technical breakdown of the architecture, components, and workflows, please refer to the extensive documentation provided in the repository:
- [Complete Web App Documentation](./Accounting_Web_App_Complete_Documentation.md)
- [Item-Wise Purchase Mode Documentation](./ITEM_WISE_PURCHASE_MODE_DOCUMENTATION.md)
- [Technical Design Document](./technical_design_document.md)
