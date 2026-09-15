import os
import asyncio
import time
import requests
from contextlib import asynccontextmanager
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from backend.routers import stock_transfer, transfer, sales, payment, purchase, purchase_item, bank_statement, sync, dashboard, masters, settings, purchase_drafts, reporting, reporting_pl, repack
from backend.database import init_db
from backend.services.tally_sync_worker import sync_worker_loop

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize SQLite database schema
    init_db()
    
    # Start the robust background worker for offline queue & master sync
    task = asyncio.create_task(sync_worker_loop())
    yield
    task.cancel()

app = FastAPI(title="Accounting Web App API", version="1.0.0", lifespan=lifespan)

# Mount static files for uploads
uploads_dir = os.path.join(os.getcwd(), "backend", "uploads")
os.makedirs(uploads_dir, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")

frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3000")
allow_origins = [url.strip() for url in frontend_url.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stock_transfer.router, prefix="/api/stock-transfer", tags=["Stock Transfer"])
app.include_router(transfer.router, prefix="/api/transfer", tags=["Fund Transfer"])
app.include_router(sales.router, prefix="/api/sales", tags=["Sales"])
app.include_router(payment.router, prefix="/api/payment", tags=["Payment"])
app.include_router(purchase.router, prefix="/api/purchase", tags=["Purchase"])
app.include_router(purchase_item.router, prefix="/api/purchase-item", tags=["Purchase Item"])
app.include_router(purchase_drafts.router, prefix="/api/purchase-drafts", tags=["Purchase Drafts"])
app.include_router(bank_statement.router, prefix="/api/bank-statement", tags=["Bank Statement"])
app.include_router(sync.router, prefix="/api/sync", tags=["Sync Worker"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(masters.router)
app.include_router(settings.router, prefix="/api/settings", tags=["Settings"])
app.include_router(repack.router, prefix="/api/repack", tags=["Repack"])
app.include_router(reporting.router, prefix="/api")
app.include_router(reporting_pl.router)

from backend.routers import whatsapp
app.include_router(whatsapp.router, prefix="/api/whatsapp", tags=["WhatsApp"])

@app.get("/api/health")
async def health_check():
    return {"status": "ok", "message": "Backend API is running!"}
