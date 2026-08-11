import asyncio
from backend.services.extraction_engine import process_invoice

with open("dummy.pdf", "rb") as f:
    pdf_bytes = f.read()

try:
    process_invoice([pdf_bytes])
except Exception as e:
    print("Error:", repr(e))
