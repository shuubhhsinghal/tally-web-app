import asyncio
from backend.services.extraction_engine import process_invoice

with open("dummy.pdf", "rb") as f:
    pdf_bytes = f.read()

# Wait, we don't have the invoice image.
