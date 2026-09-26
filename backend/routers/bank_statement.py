import os
import io
import json
import uuid
import datetime
import base64
import traceback
import pandas as pd
import pypdf
import pypdfium2 as pdfium
import requests
from io import BytesIO
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks
from starlette.concurrency import run_in_threadpool
from typing import Optional, List, Dict, Any
from google import genai
from google.genai import types
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError
from json_repair import repair_json

from backend.database import (
    get_db, get_all_ledgers, queue_operation, queue_operations_bulk, update_queue_status,
    get_all_bank_mappings, get_mappings_by_bank,
    create_bank_mapping, update_bank_mapping, delete_bank_mapping, set_delivery_uncertain
)
from backend.services.tally_response import parse_tally_response
from backend.connector.transport import tally_transport
from backend.services.extraction_v4.extraction_engine import OPENROUTER_BASE_URL
from backend.services.extraction_v4.retry import call_with_retry, get_extraction_provider

router = APIRouter()

class TransactionPayload(BaseModel):
    transactions: List[Dict[str, Any]]
    bank_ledger_name: str
    draft_id: Optional[str] = None

class BankStatementDraftPayload(BaseModel):
    transactions: List[Dict[str, Any]]
    bank_ledger_name: str

class BankTransactionRowV1(BaseModel):
    date: str = Field(description="Transaction date, any common format (e.g. YYYY-MM-DD or DD-MM-YYYY).")
    narration: str = Field(description="The transaction description/particulars, as printed.")
    amount: float = Field(description="The absolute numerical value of the transaction, without commas.")
    type: str = Field(description="Either 'DEBIT' (money out) or 'CREDIT' (money in).")

class BankStatementExtractionResponseV1(BaseModel):
    # Gemini's response_schema doesn't accept a bare List[Model] -- it needs a
    # wrapping object, matching the pattern extraction_v4/extraction_engine.py
    # already uses successfully (PrintTranscriptionResponseV4.items).
    transactions: List[BankTransactionRowV1]

def _parse_txn_json(raw_text: str) -> Optional[list]:
    """Shared response parsing for both providers: strips markdown fences,
    repairs near-valid JSON, and returns the 'transactions' list or None if
    the response can't be read as one -- lets the caller retry once with a
    stricter prompt."""
    res_text = raw_text.strip()
    if res_text.startswith("```json"):
        res_text = res_text[7:-3].strip()
    elif res_text.startswith("```"):
        res_text = res_text[3:-3].strip()

    try:
        data = repair_json(res_text, return_objects=True)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None
    transactions = data.get("transactions")
    return transactions if isinstance(transactions, list) else None


def _call_gemini_bank_extraction(client, pdf_path: str, is_retry: bool = False) -> Optional[list]:
    """Uploads the PDF and asks Gemini for a transaction list, returning the
    parsed list on success or None if the response can't be read as a valid
    list -- mirrors extraction_v4/extraction_engine.py's is_retry retry-once
    pattern, so the caller can try once more with a stricter prompt."""
    uploaded_file = client.files.upload(file=pdf_path)
    try:
        prompt = (
            "You are a strict financial data extractor. Read this bank statement PDF. "
            "Extract every transaction into a 'transactions' array of objects. "
            "Each object MUST have the following exact keys:\n"
            "- 'date': string (format YYYY-MM-DD)\n"
            "- 'narration': string (the transaction description/particulars)\n"
            "- 'amount': float (the absolute numerical value without commas)\n"
            "- 'type': string (strictly 'DEBIT' for money out, or 'CREDIT' for money in)\n\n"
            "Return ONLY valid JSON matching the enforced response schema."
        )
        if is_retry:
            prompt += (
                "\n\nWARNING: The previous response could not be read as a valid transaction list. "
                "Re-read the statement carefully and return ONLY valid JSON matching the schema above."
            )

        response = call_with_retry(lambda: client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=[uploaded_file, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=BankStatementExtractionResponseV1,
            ),
        ))
        u = response.usage_metadata
        if u:
            print(f"[GEMINI TOKENS] bank statement extraction: prompt={u.prompt_token_count} output={u.candidates_token_count} total={u.total_token_count}", flush=True)

        return _parse_txn_json(response.text)
    finally:
        try:
            client.files.delete(name=uploaded_file.name)
        except Exception:
            pass


def _render_pdf_pages_to_images(pdf_path: str, dpi: int = 200) -> list[bytes]:
    """Rasterizes every page of a PDF to a JPEG. Used for the Qwen path only
    -- Gemini reads the PDF natively, but Qwen's vision input is images, and
    sending page images (rather than a raw text-layer dump) preserves the
    statement's actual table layout, so the model reads each amount off its
    real Date/Narration/Withdrawal/Deposit column instead of guessing from
    text-extraction reading order. Mirrors how extraction_v4's item pipeline
    reads invoice images for the same reason."""
    pdf = pdfium.PdfDocument(pdf_path)
    scale = dpi / 72
    images = []
    try:
        for page in pdf:
            bitmap = page.render(scale=scale)
            buf = io.BytesIO()
            bitmap.to_pil().convert("RGB").save(buf, format="JPEG", quality=90)
            images.append(buf.getvalue())
            page.close()
    finally:
        pdf.close()
    return images


def _build_qwen_bank_prompt(is_retry: bool = False) -> str:
    prompt = (
        "You are a precise OCR transcription assistant reading a bank statement. "
        "You are receiving one or more page image(s) of the statement, which may span multiple "
        "pages -- treat page 2 and beyond as a continuation of page 1, covering every page.\n\n"
        "Extract every real transaction row from the table into a 'transactions' array of objects. "
        "Each object MUST have the following exact keys:\n"
        "- 'date': string (format YYYY-MM-DD)\n"
        "- 'narration': string (the transaction description/particulars column, as printed)\n"
        "- 'amount': a pure JSON number, the absolute value without commas or currency symbols "
        "(correct: 1234.56, incorrect: \"1,234.56\")\n"
        "- 'type': string, strictly 'DEBIT' if the amount is printed in the Withdrawal/Debit "
        "column, or 'CREDIT' if printed in the Deposit/Credit column\n\n"
        "DO NOT extract as a transaction: the running/closing 'Balance' column value, 'Opening "
        "Balance', 'Closing Balance', 'B/F' (brought forward), 'C/F' (carried forward), or any "
        "page/statement summary or total row. Only extract actual dated transaction rows.\n\n"
        "EXAMPLE -- a row with Date=05-04-2024, Narration=\"UPI/mmt/123/Zomato\", Withdrawal=1,234.50, "
        "Deposit=(blank), Balance=45,210.00 (the Balance column is ignored):\n"
        "{\"date\": \"2024-04-05\", \"narration\": \"UPI/mmt/123/Zomato\", \"amount\": 1234.50, \"type\": \"DEBIT\"}\n\n"
        "Return ONLY a valid JSON object of the exact shape "
        "{\"transactions\": [{\"date\": \"...\", \"narration\": \"...\", \"amount\": 0.0, \"type\": \"...\"}]}. "
        "Do not include markdown formatting like ```json."
    )
    if is_retry:
        prompt += (
            "\n\nWARNING: The previous response could not be read as a valid transaction list. "
            "Re-read the statement carefully and return ONLY valid JSON matching the schema above."
        )
    return prompt


def _call_qwen_bank_extraction(images: list[bytes], is_retry: bool = False) -> Optional[list]:
    """Qwen equivalent of _call_gemini_bank_extraction, via OpenRouter. Sends
    rasterized page images (see _render_pdf_pages_to_images) rather than the
    raw PDF, matching the vision-based approach extraction_v4 already uses
    for Qwen item extraction."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not configured")

    model = os.getenv("QWEN_MODEL", "qwen/qwen3.5-flash-02-23")
    client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL, timeout=90.0)

    prompt = _build_qwen_bank_prompt(is_retry)
    image_parts = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{base64.b64encode(img).decode()}"},
        }
        for img in images
    ]

    response = call_with_retry(lambda: client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": image_parts + [{"type": "text", "text": prompt}]}],
        response_format={"type": "json_object"},
        extra_body={"reasoning": {"enabled": False}},
        # No max_tokens cap here (unlike the item-extraction calls) -- a
        # month's bank statement can run into hundreds of transactions,
        # and truncating mid-JSON would silently fail parsing and burn
        # the one retry. Item tables stay capped since they're bounded
        # to a single invoice's row count.
    ))
    u = response.usage
    if u:
        print(f"[QWEN TOKENS] bank statement extraction: prompt={u.prompt_tokens} output={u.completion_tokens} total={u.total_tokens}", flush=True)

    return _parse_txn_json(response.choices[0].message.content)


def _call_bank_extraction(pdf_path: str, is_retry: bool = False) -> Optional[list]:
    """Provider dispatch, mirroring extraction_v4/extraction_engine.py's
    call_item_extraction_v4 -- reads the same shared "extraction_provider"
    Settings toggle so switching providers there also switches bank
    statement extraction, with no separate toggle for this feature."""
    if get_extraction_provider() == "qwen":
        images = _render_pdf_pages_to_images(pdf_path)
        return _call_qwen_bank_extraction(images, is_retry)

    api_key = os.environ.get("GEMINI_API_KEY")
    # Explicit timeout -- without one, a stalled request can hang the
    # background extraction thread indefinitely instead of failing.
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=60000))
    return _call_gemini_bank_extraction(client, pdf_path, is_retry)

@router.get("/mappings/banks")
def get_mapped_banks():
    mappings = get_all_bank_mappings()
    banks = list(set(m["bank_account_name"] for m in mappings if m["bank_account_name"] != "ALL"))
    return banks

@router.get("/mappings")
def get_all_mappings(bank: Optional[str] = None):
    if bank:
        return get_mappings_by_bank(bank)
    return get_all_bank_mappings()

@router.post("/mappings")
def add_mapping(payload: dict):
    bank = payload.get("bank")
    keyword = payload.get("keyword", "").strip()
    ledger = payload.get("target_ledger") or payload.get("ledger")
    if ledger in ["IGNORE", "IGNORE (Do not push)"]:
        raise HTTPException(status_code=400, detail="IGNORE is no longer a valid mapping destination.")
    cost_center = payload.get("cost_center")
    
    mapping_id = create_bank_mapping(bank, keyword, ledger, cost_center)
    return {"status": "success", "id": mapping_id}

@router.put("/mappings/{mapping_id}")
def update_mapping(mapping_id: int, payload: dict):
    ledger = payload.get("target_ledger") or payload.get("ledger")
    if ledger in ["IGNORE", "IGNORE (Do not push)"]:
        raise HTTPException(status_code=400, detail="IGNORE is no longer a valid mapping destination.")
        
    success = update_bank_mapping(
        mapping_id,
        payload.get("bank"),
        payload.get("keyword", "").strip(),
        ledger,
        payload.get("cost_center")
    )
    if not success:
        raise HTTPException(status_code=404, detail="Mapping not found")
    return {"status": "success"}

@router.delete("/mappings/{mapping_id}")
def delete_mapping(mapping_id: int):
    success = delete_bank_mapping(mapping_id)
    if not success:
        raise HTTPException(status_code=404, detail="Mapping not found")
    return {"status": "success"}

def requires_cost_centre(ledger_name: str) -> bool:
    if not ledger_name:
        return False
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT cost_centre FROM ledgers WHERE name = ? COLLATE NOCASE", (ledger_name.strip(),))
            row = cursor.fetchone()
            if row:
                return bool(row['cost_centre'])
            return False
    except Exception:
        return False

def sanitize_xml(text):
    if not text:
        return ""
    text = str(text)
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace('"', "&quot;")
    text = text.replace("'", "&apos;")
    return text

@router.get("/ledgers")
def get_tally_ledgers():
    try:
        ledgers = get_all_ledgers()
        result = {}
        for l in ledgers:
            result[l['name'].lower()] = {
                "name": l['name'],
                "parent": l['parent'],
                "cost_centre": bool(l['cost_centre'])
            }
        return result
    except Exception:
        return {}

@router.get("/accounts")
def get_bank_accounts():
    from backend.services.reporting_bank_service import list_bank_accounts
    return {"accounts": list_bank_accounts()}

@router.get("/transactions")
def get_bank_transactions(ledger: str, start_date: str, end_date: str):
    from backend.services.reporting_bank_service import get_bank_ledger_movements
    return get_bank_ledger_movements(ledger, start_date, end_date)

def _extract_bank_statement_transactions(contents: bytes, filename: str, bank_ledger_name: str) -> dict:
    """The actual parsing work -- for a PDF this includes the slow Gemini/Qwen
    call -- split out of upload_bank_statement so it can run in a background
    task instead of blocking the request. `contents` for a PDF is expected to
    already be decrypted (upload_bank_statement's synchronous password check
    below handles that before ever getting here)."""
    ext = os.path.splitext(filename)[1].lower()

    json_txns = []

    if ext in ['.xlsx', '.xls']:
        try:
            df = pd.read_excel(BytesIO(contents), header=None)
        except Exception:
            df = pd.read_html(BytesIO(contents), header=None)[0]
            
        header_idx = -1
        for idx, row in df.head(30).iterrows():
            row_str = ' '.join(str(val).lower() for val in row.values if pd.notna(val))
            if 'date' in row_str and ('narration' in row_str or 'particulars' in row_str or 'description' in row_str or 'remarks' in row_str):
                header_idx = idx
                break
                
        if header_idx != -1:
            df.columns = df.iloc[header_idx]
            df = df.iloc[header_idx+1:].reset_index(drop=True)
            
        col_map = {}
        for col in df.columns:
            c_lower = str(col).lower().replace('\n', ' ').strip()
            if c_lower in ['date', 'txn date', 'tran date', 'transaction date', 'value date']:
                col_map[col] = 'Date'
            elif c_lower in ['narration', 'particulars', 'description', 'remarks']:
                col_map[col] = 'Narration'
            elif c_lower in ['withdrawal', 'withdrawals', 'debit', 'debit amount', 'dr', 'dr.', 'withdrawal amount', 'paid out', 'withdrawal (dr)']:
                col_map[col] = 'Withdrawal'
            elif c_lower in ['deposit', 'deposits', 'credit', 'credit amount', 'cr', 'cr.', 'deposit amount', 'paid in', 'deposit (cr)']:
                col_map[col] = 'Deposit'
                
        df.rename(columns=col_map, inplace=True)
        df = df.loc[:, ~df.columns.duplicated()]
        
        required_cols = {'Date', 'Narration', 'Withdrawal', 'Deposit'}
        missing_cols = required_cols - set(df.columns)
        if missing_cols:
            raise HTTPException(status_code=400, detail=f"Could not identify columns. Missing: {missing_cols}.")
            
        df['Deposit'] = df['Deposit'].fillna(0)
        df['Withdrawal'] = df['Withdrawal'].fillna(0)
        
        df = df[~(((df['Withdrawal'] == 0) | (df['Withdrawal'].astype(str) == '0.0') | (df['Withdrawal'].astype(str) == '')) & 
                  ((df['Deposit'] == 0) | (df['Deposit'].astype(str) == '0.0') | (df['Deposit'].astype(str) == '')))]
        
        df = df[df['Date'].notna()]
        df = df[df['Date'].astype(str).str.strip() != '']
        df = df[~df['Date'].astype(str).str.contains('Opening Balance|Closing Balance', case=False, na=False)]
        
        for _, row in df.iterrows():
            raw_date = row['Date']
            if pd.isna(raw_date): continue
            
            try:
                dt = pd.to_datetime(raw_date, dayfirst=True)
                tally_date = dt.strftime('%Y-%m-%d')
            except:
                continue
                
            raw_narration = str(row['Narration']).strip() if not pd.isna(row['Narration']) else ""
            
            try:
                w_str = str(row['Withdrawal']).replace(',', '').strip()
                withdraw = abs(float(w_str)) if w_str and w_str.lower() != 'nan' else 0.0
            except ValueError:
                withdraw = 0.0
                
            try:
                d_str = str(row['Deposit']).replace(',', '').strip()
                deposit = abs(float(d_str)) if d_str and d_str.lower() != 'nan' else 0.0
            except ValueError:
                deposit = 0.0
            
            if withdraw == 0 and deposit == 0: continue
            
            amt = withdraw if withdraw > 0 else deposit
            txn_type = "DEBIT" if withdraw > 0 else "CREDIT"
            
            json_txns.append({
                "date": tally_date,
                "narration": raw_narration,
                "amount": float(amt),
                "type": txn_type
            })

    elif ext == '.pdf':
        import tempfile
        temp_path = os.path.join(tempfile.gettempdir(), f"temp_{uuid.uuid4().hex}_{filename}")
        with open(temp_path, "wb") as f:
            f.write(contents)

        try:
            json_txns = _call_bank_extraction(temp_path, is_retry=False)
            if not json_txns:
                json_txns = _call_bank_extraction(temp_path, is_retry=True)
            if not json_txns:
                raise HTTPException(status_code=400, detail="Could not read transactions from this PDF. Try a clearer scan or a different format.")
        except HTTPException:
            raise
        except Exception as e:
            print(f"Unexpected error: {e}")
            raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    else:
        raise HTTPException(status_code=400, detail="Invalid file extension. Please upload .xlsx, .xls, or .pdf")

    # Validate every row (from either the Excel parser or Gemini's PDF extraction)
    # against a strict shape before any of it reaches the accounting logic below --
    # a row missing/mistyping a field gets dropped and counted instead of crashing
    # deep in the shared loop or silently building a wrong transaction.
    validated_txns = []
    skipped_count = 0
    for row in json_txns:
        try:
            validated_txns.append(BankTransactionRowV1.model_validate(row).model_dump())
        except ValidationError:
            skipped_count += 1

    if json_txns and not validated_txns:
        raise HTTPException(status_code=400, detail="Could not extract readable transactions from this statement.")

    json_txns = validated_txns

    bank_specific_mappings = get_mappings_by_bank(bank_ledger_name)
    all_bank_mappings = get_mappings_by_bank("ALL")
    active_mappings = bank_specific_mappings + all_bank_mappings
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM ledgers WHERE name = 'Bank Suspense Account'")
        if not cursor.fetchone():
            raise HTTPException(status_code=400, detail="'Bank Suspense Account' ledger is missing. Please create it in Tally and sync masters before processing statements.")
            
    transactions = []
    
    for row in json_txns:
        try:
            dt = pd.to_datetime(row['date'])
            tally_date = dt.strftime('%Y%m%d')
        except:
            tally_date = str(row['date']).replace("-", "")
            
        raw_narration = str(row['narration']).strip()
        clean_narration = sanitize_xml(raw_narration)
        narr_upper = raw_narration.upper()
        
        amount = abs(float(row['amount']))
        if row.get('type', '').upper() == 'CREDIT':
            deposit = amount
            withdraw = 0.0
        else:
            deposit = 0.0
            withdraw = amount
            
        if withdraw == 0 and deposit == 0: continue
            
        ledger = "Bank Suspense Account"
        cost_center = None
        
        for mapping in active_mappings:
            if mapping['keyword'].upper() in narr_upper:
                target = mapping.get("target_ledger", "Bank Suspense Account")
                if target in ["IGNORE", "IGNORE (Do not push)"]:
                    break
                ledger = target
                cost_center = mapping.get("cost_center")
                break
                
        is_unmapped = (ledger == "Bank Suspense Account")
        missing_cost_center = False
        
        if not is_unmapped:
            cc_required = requires_cost_centre(ledger)
            if not cc_required:
                cost_center = None
            if cc_required and not cost_center:
                missing_cost_center = True
        
        transactions.append({
            'date': tally_date,
            'raw_narration': raw_narration,
            'clean_narration': clean_narration,
            'withdraw': withdraw,
            'deposit': deposit,
            'ledger': ledger,
            'cost_center': cost_center,
            'unmapped': is_unmapped,
            'missing_cost_center': missing_cost_center
        })

    # Inter-account transfer detection: a transfer between two of the user's
    # own bank accounts shows up as a debit in one statement and a matching
    # credit in the other -- flag likely pairs so the user can merge them
    # into one Contra voucher instead of double-posting the same movement.
    from backend.services.bank_transfer_matching import get_own_bank_ledger_names, find_transfer_match
    own_bank_ledgers = get_own_bank_ledger_names()
    for txn in transactions:
        txn['transfer_match'] = find_transfer_match(txn, bank_ledger_name, own_bank_ledgers)

    return {"transactions": transactions, "skipped_count": skipped_count}


def _process_bank_statement_draft(draft_id: str, contents: bytes, filename: str, bank_ledger_name: str):
    """Runs in the background (kicked off by upload_bank_statement) so a long
    PDF's Gemini/Qwen extraction doesn't block the request -- fills in the
    PROCESSING draft it already created, mirroring purchase_drafts.py's
    process_async_extraction."""
    now = datetime.datetime.now().isoformat()
    try:
        result = _extract_bank_statement_transactions(contents, filename, bank_ledger_name)
        transactions = result["transactions"]
        if not transactions:
            raise HTTPException(status_code=400, detail="No transactions were found in this statement.")

        draft_data = json.dumps({"transactions": transactions})
        with get_db() as conn:
            conn.cursor().execute("""
                UPDATE bank_statement_drafts
                SET status = 'READY', transaction_count = ?, draft_data = ?, updated_at = ?
                WHERE id = ?
            """, (len(transactions), draft_data, now, draft_id))
            conn.commit()
    except HTTPException as e:
        with get_db() as conn:
            conn.cursor().execute("""
                UPDATE bank_statement_drafts
                SET status = 'FAILED', error_message = ?, updated_at = ?
                WHERE id = ?
            """, (str(e.detail), now, draft_id))
            conn.commit()
    except Exception as e:
        print(f"Bank statement async extraction failed: {e}")
        traceback.print_exc()
        with get_db() as conn:
            conn.cursor().execute("""
                UPDATE bank_statement_drafts
                SET status = 'FAILED', error_message = ?, updated_at = ?
                WHERE id = ?
            """, (str(e), now, draft_id))
            conn.commit()


@router.post("/upload")
def upload_bank_statement(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    bank_ledger_name: str = Form(...),
    password: Optional[str] = Form(None)
):
    """Creates a PROCESSING draft and returns immediately -- the actual
    parsing (slow for a PDF) runs in the background and fills the draft in
    once done. The one thing that must stay synchronous is the password
    check: the user needs to be prompted for it right away, not after
    waiting on a background job that was doomed to fail without it."""
    contents = file.file.read()
    ext = os.path.splitext(file.filename)[1].lower()

    if ext not in ['.xlsx', '.xls', '.pdf']:
        raise HTTPException(status_code=400, detail="Invalid file extension. Please upload .xlsx, .xls, or .pdf")

    if ext == '.pdf':
        import tempfile
        temp_path = os.path.join(tempfile.gettempdir(), f"temp_{uuid.uuid4().hex}_{file.filename}")
        with open(temp_path, "wb") as f:
            f.write(contents)
        try:
            reader = pypdf.PdfReader(temp_path)
            if reader.is_encrypted:
                if not password or not reader.decrypt(password):
                    raise HTTPException(status_code=403, detail="PDF is encrypted. Password required.")
                # Re-save decrypted so the background task never needs the
                # password again.
                writer = pypdf.PdfWriter()
                for page in reader.pages:
                    writer.add_page(page)
                buf = BytesIO()
                writer.write(buf)
                contents = buf.getvalue()
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    draft_id = str(uuid.uuid4())
    now = datetime.datetime.now().isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO bank_statement_drafts
            (id, bank_ledger_name, transaction_count, status, draft_data, created_at, updated_at)
            VALUES (?, ?, 0, 'PROCESSING', '{}', ?, ?)
        """, (draft_id, bank_ledger_name, now, now))
        conn.commit()

    background_tasks.add_task(_process_bank_statement_draft, draft_id, contents, file.filename, bank_ledger_name)

    return {"id": draft_id, "status": "PROCESSING"}

@router.get("/drafts")
def list_bank_statement_drafts():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, bank_ledger_name, transaction_count, status, error_message, draft_data, created_at, updated_at
            FROM bank_statement_drafts
            ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()

    drafts = []
    for row in rows:
        draft = dict(row)
        raw = draft.pop('draft_data', None)
        unmapped_count = 0
        if raw:
            try:
                txns = json.loads(raw).get("transactions") or []
                unmapped_count = sum(1 for t in txns if t.get("unmapped") or t.get("missing_cost_center"))
            except Exception:
                pass
        draft['unmapped_count'] = unmapped_count
        drafts.append(draft)

    return {"drafts": drafts}

@router.get("/drafts/{draft_id}")
def get_bank_statement_draft(draft_id: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM bank_statement_drafts WHERE id = ?", (draft_id,))
        row = cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Draft not found")

    draft = dict(row)
    try:
        draft['draft_data'] = json.loads(draft['draft_data'])
    except Exception:
        draft['draft_data'] = {"transactions": []}

    return draft

@router.put("/drafts/{draft_id}")
def update_bank_statement_draft(draft_id: str, payload: BankStatementDraftPayload):
    now = datetime.datetime.now().isoformat()
    draft_data = json.dumps({"transactions": payload.transactions})

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE bank_statement_drafts
            SET bank_ledger_name = ?, transaction_count = ?, draft_data = ?, updated_at = ?
            WHERE id = ?
        """, (payload.bank_ledger_name, len(payload.transactions), draft_data, now, draft_id))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Draft not found")
        conn.commit()

    return {"message": "Draft updated"}

@router.delete("/drafts/{draft_id}")
def delete_bank_statement_draft(draft_id: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bank_statement_drafts WHERE id = ?", (draft_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Draft not found")
        conn.commit()

    return {"message": "Draft deleted"}

@router.post("/post")
async def post_to_tally(payload: TransactionPayload):
    transactions = payload.transactions
    bank_ledger_name = payload.bank_ledger_name
    
    prepared_vouchers = []
    
    for t in transactions:
        ledger = t.get('ledger', "Bank Suspense Account")
            
        withdraw = float(t.get('withdraw', 0))
        deposit = float(t.get('deposit', 0))
        cost_center = t.get('cost_center')
        
        cc_xml_payment = ""
        cc_xml_receipt = ""
        if cost_center:
            cc_xml_payment = f"""
              <CATEGORYALLOCATIONS.LIST>
                <CATEGORYNAME>Primary Cost Category</CATEGORYNAME>
                <COSTCENTREALLOCATIONS.LIST>
                  <NAME>{sanitize_xml(cost_center)}</NAME>
                  <AMOUNT>-{withdraw}</AMOUNT>
                </COSTCENTREALLOCATIONS.LIST>
              </CATEGORYALLOCATIONS.LIST>"""
            cc_xml_receipt = f"""
              <CATEGORYALLOCATIONS.LIST>
                <CATEGORYNAME>Primary Cost Category</CATEGORYNAME>
                <COSTCENTREALLOCATIONS.LIST>
                  <NAME>{sanitize_xml(cost_center)}</NAME>
                  <AMOUNT>{deposit}</AMOUNT>
                </COSTCENTREALLOCATIONS.LIST>
              </CATEGORYALLOCATIONS.LIST>"""
        
        date = t.get('date', '')
        clean_narration = sanitize_xml(t.get('raw_narration', ''))
        
        if withdraw > 0:
            vch_type = "Payment"
            ledgers_xml = f"""
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{sanitize_xml(ledger)}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{withdraw}</AMOUNT>{cc_xml_payment}
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{sanitize_xml(bank_ledger_name)}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{withdraw}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""
            target_debit_ledger = ledger
            target_credit_ledger = bank_ledger_name
            amount = withdraw
        else:
            vch_type = "Receipt"
            ledgers_xml = f"""
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{sanitize_xml(ledger)}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{deposit}</AMOUNT>{cc_xml_receipt}
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{sanitize_xml(bank_ledger_name)}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{deposit}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""
            target_debit_ledger = bank_ledger_name
            target_credit_ledger = ledger
            amount = deposit
            
        voucher_xml = f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
           <VOUCHER VCHTYPE="{vch_type}" ACTION="Create">
              <DATE>{date}</DATE>
              <EFFECTIVEDATE>{date}</EFFECTIVEDATE>
              <VOUCHERTYPENAME>{vch_type}</VOUCHERTYPENAME>
              <NARRATION>{clean_narration}</NARRATION>{ledgers_xml}
           </VOUCHER>
        </TALLYMESSAGE>"""
        
        desc = f"Bank Stmt: {date} - {amount} - {ledger}"
        
        single_payload = {
            "original_transaction": t,
            "bank_ledger_name": bank_ledger_name,
            "debit_ledger": target_debit_ledger,
            "credit_ledger": target_credit_ledger,
            "amount": amount,
            "date": date,
            "narration": t.get('raw_narration', ''),
            "cost_center": cost_center
        }
        
        prepared_vouchers.append({
            "xml_block": voucher_xml,
            "payload": single_payload,
            "description": desc
        })

    # Queue-First Architecture: each transaction is its own independent
    # voucher, saved as its own PENDING row up front.
    queued_items = []  # (queue_id, single_envelope, description)
    for pv in prepared_vouchers:
        single_envelope = f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
      </REQUESTDESC>
      <REQUESTDATA>{pv['xml_block']}
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""
        qid = queue_operation("POST_VOUCHER", single_envelope, pv["payload"], pv["description"])
        queued_items.append((qid, single_envelope, pv["description"]))

    # The vouchers are now durably in the offline queue regardless of whether
    # the Tally send below succeeds -- this statement no longer needs review,
    # so its draft (if it came from one) can come off the Review inbox now.
    if payload.draft_id:
        with get_db() as conn:
            conn.cursor().execute("DELETE FROM bank_statement_drafts WHERE id = ?", (payload.draft_id,))
            conn.commit()

    # Attempted one voucher at a time (not one combined multi-TALLYMESSAGE
    # import) and checked with the same parse_tally_response every other
    # feature uses -- previously this batched all N vouchers into a single
    # request and did a crude `"<LINEERROR>" in response.text` check that,
    # on ANY single line error, blanket-marked every queued row (including
    # ones Tally had already actually CREATED) as FAILED. Since FAILED rows
    # are never retried by the background worker, a user who then retried or
    # re-uploaded the statement would duplicate every voucher that had
    # actually succeeded. Attempting individually gives each transaction its
    # own accurate outcome, exactly like every other multi-voucher feature.
    synced_count = 0
    queued_count = 0
    failed_descriptions = []

    for idx, (qid, envelope, desc) in enumerate(queued_items):
        try:
            set_delivery_uncertain(qid, True)
            response = await tally_transport.post(envelope.encode('utf-8'), timeout=10)
            parsed = parse_tally_response(response.text, "POST_VOUCHER")

            if not parsed["is_success"]:
                set_delivery_uncertain(qid, False)
                update_queue_status(qid, "FAILED", parsed["error_message"])
                failed_descriptions.append(f"{desc}: {parsed['error_message']}")
                continue

            set_delivery_uncertain(qid, False)
            update_queue_status(qid, "SYNCED")
            synced_count += 1
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError):
            # Tally is unreachable -- this item and every remaining one stay
            # PENDING for the background worker, rather than each paying a
            # separate connection timeout.
            set_delivery_uncertain(qid, False)
            queued_count += len(queued_items) - idx
            break
        except requests.exceptions.Timeout:
            # Genuinely ambiguous for this one voucher only -- leave
            # delivery_uncertain set (already persisted above) so a manual
            # retry is blocked until someone verifies it in Tally directly.
            # Other transactions in this batch are unaffected and keep going.
            update_queue_status(qid, "FAILED", "Delivery status is unknown from an earlier Tally submission. Verify this voucher in Tally before attempting any manual retry.")
            failed_descriptions.append(f"{desc}: delivery uncertain, verify in Tally")
        except Exception as e:
            update_queue_status(qid, "FAILED", str(e))
            failed_descriptions.append(f"{desc}: {e}")

    if failed_descriptions and synced_count == 0 and queued_count == 0:
        overall_status = "failed"
    elif failed_descriptions or queued_count:
        overall_status = "partial"
    else:
        overall_status = "success"

    message_parts = []
    if synced_count:
        message_parts.append(f"{synced_count} posted to Tally")
    if queued_count:
        message_parts.append(f"{queued_count} saved to offline queue")
    if failed_descriptions:
        message_parts.append(f"{len(failed_descriptions)} rejected by Tally")
    message = ", ".join(message_parts) or "Nothing to post."

    return {
        "status": overall_status,
        "message": message,
        "synced_count": synced_count,
        "queued_count": queued_count,
        "failed": failed_descriptions,
    }


class MergeTransferRequest(BaseModel):
    queue_id: int          # the OTHER (earlier) leg's offline_queue row to cancel
    bank_ledger_name: str  # the bank ledger currently being uploaded/reviewed
    date: str               # YYYYMMDD
    withdraw: float = 0.0
    deposit: float = 0.0
    raw_narration: str = ""


@router.post("/merge-transfer")
async def merge_transfer(payload: MergeTransferRequest):
    """Merges a bank-statement transaction with the matching other side of an
    inter-account transfer (already queued from a different bank's statement)
    into a single Contra voucher, instead of posting both sides as separate,
    double-counted Payment/Receipt vouchers. Only ever acts on a still-PENDING,
    delivery-confirmed queue row -- never touches a Tally-synced voucher."""
    import json as json_module
    from backend.database import cancel_pending_queue_item
    from backend.routers.transfer import build_contra_voucher_xml
    from backend.services.bank_transfer_matching import get_own_bank_ledger_names

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status, payload FROM offline_queue WHERE id = ?", (payload.queue_id,))
        row = cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="The matched entry no longer exists.")
    if row['status'] != 'PENDING':
        raise HTTPException(status_code=409, detail="The matched entry is no longer pending -- it may have already synced to Tally or failed. Refresh and try again.")

    try:
        other_payload = json_module.loads(row['payload']) if row['payload'] else {}
    except (json_module.JSONDecodeError, TypeError):
        other_payload = {}

    if other_payload.get('delivery_uncertain') is True:
        raise HTTPException(status_code=409, detail="Delivery to Tally is unconfirmed for the matched entry. Verify manually in Tally before merging.")

    other_bank_ledger = other_payload.get('bank_ledger_name')
    own_bank_ledgers = get_own_bank_ledger_names()
    if payload.bank_ledger_name not in own_bank_ledgers or other_bank_ledger not in own_bank_ledgers:
        raise HTTPException(status_code=400, detail="Both accounts must be your own bank ledgers.")
    if other_bank_ledger == payload.bank_ledger_name:
        raise HTTPException(status_code=400, detail="Cannot merge a transaction with itself.")

    success, error_message = cancel_pending_queue_item(payload.queue_id)
    if not success:
        raise HTTPException(status_code=409, detail=error_message)

    if payload.withdraw and payload.withdraw > 0:
        from_account, to_account, amount = payload.bank_ledger_name, other_bank_ledger, payload.withdraw
    else:
        from_account, to_account, amount = other_bank_ledger, payload.bank_ledger_name, payload.deposit

    contra_xml = build_contra_voucher_xml(amount, from_account, to_account, payload.date, payload.raw_narration)
    new_queue_id = queue_operation(
        "POST_VOUCHER", contra_xml,
        {"amount": amount, "from_account": from_account, "to_account": to_account, "date": payload.date, "narration": payload.raw_narration},
        f"Bank Stmt Transfer: {from_account} -> {to_account}"
    )

    try:
        set_delivery_uncertain(new_queue_id, True)
        response = await tally_transport.post(contra_xml.encode('utf-8'), timeout=10)
        parsed = parse_tally_response(response.text, "POST_VOUCHER")

        if not parsed["is_success"]:
            set_delivery_uncertain(new_queue_id, False)
            update_queue_status(new_queue_id, "FAILED", parsed['error_message'])
            raise HTTPException(status_code=400, detail=f"Tally rejected the merged transfer entry: {parsed['error_message']}")

        set_delivery_uncertain(new_queue_id, False)
        update_queue_status(new_queue_id, "SYNCED")
        return {"status": "merged", "queue_id": new_queue_id, "message": "Merged into a single Contra entry and posted to Tally."}
    except requests.exceptions.ConnectTimeout:
        set_delivery_uncertain(new_queue_id, False)
        return {"status": "merged", "queue_id": new_queue_id, "message": "Merged into a single Contra entry, saved to offline queue."}
    except requests.exceptions.Timeout:
        return {"status": "merged", "queue_id": new_queue_id, "message": "Merged into a single Contra entry, saved to offline queue."}
    except requests.exceptions.ConnectionError:
        set_delivery_uncertain(new_queue_id, False)
        return {"status": "merged", "queue_id": new_queue_id, "message": "Merged into a single Contra entry, saved to offline queue."}
    except HTTPException:
        raise
    except Exception as e:
        update_queue_status(new_queue_id, "FAILED", str(e))
        raise HTTPException(status_code=500, detail=str(e))
