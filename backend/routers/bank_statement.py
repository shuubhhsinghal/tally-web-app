import os
import pandas as pd
import pypdf
import requests
from io import BytesIO
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from typing import Optional, List, Dict, Any
from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError
from json_repair import repair_json

from backend.database import (
    get_db, get_all_ledgers, queue_operation, queue_operations_bulk, update_queue_status,
    get_all_bank_mappings, get_mappings_by_bank,
    create_bank_mapping, update_bank_mapping, delete_bank_mapping, set_delivery_uncertain
)
from backend.services.tally_response import parse_tally_response
from backend.connector.transport import tally_transport

router = APIRouter()

class TransactionPayload(BaseModel):
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

        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=[uploaded_file, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=BankStatementExtractionResponseV1,
            ),
        )

        res_text = response.text.strip()
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
    finally:
        try:
            client.files.delete(name=uploaded_file.name)
        except Exception:
            pass

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

@router.post("/create-ledger")
async def create_tally_ledger(payload: dict):
    name = payload.get("name")
    parent = payload.get("parent", "Indirect Expenses")
    cc_flag = "Yes" if payload.get("cost_center") else "No"
    
    xml_data = f"""<ENVELOPE>
      <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
      <BODY>
        <IMPORTDATA>
          <REQUESTDESC><REPORTNAME>All Masters</REPORTNAME></REQUESTDESC>
          <REQUESTDATA>
            <TALLYMESSAGE xmlns:UDF="TallyUDF">
              <LEDGER ACTION="Create" NAME="{sanitize_xml(name)}">
                <NAME>{sanitize_xml(name)}</NAME>
                <PARENT>{sanitize_xml(parent)}</PARENT>
                <ISCOSTCAPP>{cc_flag}</ISCOSTCAPP>
              </LEDGER>
            </TALLYMESSAGE>
          </REQUESTDATA>
        </IMPORTDATA>
      </BODY>
    </ENVELOPE>"""
    
    xml_bytes = xml_data.encode('utf-8')
    
    # 1. Pre-send cache check
    from backend.database import check_master_exists_locally, normalize_master_name, MasterConflictException
    try:
        norm, _ = normalize_master_name(name)
        if check_master_exists_locally('LEDGER', norm, {"parent": parent}):
            return {"status": "success", "message": f"Ledger '{name}' already exists (idempotent)."}
    except MasterConflictException as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    try:
        response = await tally_transport.post(xml_bytes, timeout=4)
        parsed = parse_tally_response(response.text, "CREATE_LEDGER")
        if not parsed["is_success"]:
            raise HTTPException(status_code=400, detail=f"Tally rejected the ledger creation: {parsed['error_message']}")
            
        from backend.services.tally_response import is_already_exists_success
        from backend.services.tally_verification import verify_tally_master_definition, VerificationResult
        
        insert_name = name
        if is_already_exists_success(parsed, "CREATE_LEDGER"):
            ver_res, canonical = await verify_tally_master_definition("LEDGER", name, {"parent": parent})
            if ver_res == VerificationResult.UNVERIFIABLE:
                raise HTTPException(status_code=400, detail="Master already exists in Tally, but its definition could not be verified. Refresh masters and try again.")
            elif ver_res == VerificationResult.CONFLICT:
                raise HTTPException(status_code=409, detail="Master already exists in Tally with a conflicting definition.")
            insert_name = canonical['name']
            
        # 1. LOCAL INJECTION: Update cache ONLY after confirmation
        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO ledgers (name, parent, cost_centre) VALUES (?, ?, ?)",
                    (insert_name, parent, payload.get("cost_center", False))
                )
                conn.commit()
        except Exception as e:
            print(f"Local ledger insert failed: {e}")
            
        return {"status": "success", "message": f"Ledger {insert_name} created in Tally."}
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        from backend.database import queue_master_operation, MasterFailedException
        try:
            res = queue_master_operation("LEDGER", name, "CREATE_LEDGER", xml_data, payload)
            if res.get("status") == "exists_confirmed":
                return {"status": "success", "message": f"Ledger '{name}' already confirmed."}
            elif res.get("status") in ("exists_pending", "exists_pending_concurrent"):
                return {"status": "success", "message": f"Ledger '{name}' already pending."}
        except MasterConflictException as e:
            raise HTTPException(status_code=409, detail=str(e))
        except MasterFailedException as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            print(f"Unexpected error: {e}")
            raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")
            
        return {"status": "queued", "message": f"Ledger {name} saved to offline queue."}
    except HTTPException:
        raise

@router.post("/upload")
def upload_bank_statement(
    file: UploadFile = File(...),
    bank_ledger_name: str = Form(...),
    password: Optional[str] = Form(None)
):
    contents = file.file.read()
    ext = os.path.splitext(file.filename)[1].lower()
    
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
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"temp_{file.filename}")
        with open(temp_path, "wb") as f:
            f.write(contents)
            
        try:
            reader = pypdf.PdfReader(temp_path)
            if reader.is_encrypted:
                if not password or not reader.decrypt(password):
                    os.remove(temp_path)
                    raise HTTPException(status_code=403, detail="PDF is encrypted. Password required.")
                    
            unlocked_pdf_path = os.path.join(temp_dir, f"unlocked_{file.filename}")
            writer = pypdf.PdfWriter()
            for page in reader.pages:
                writer.add_page(page)
            with open(unlocked_pdf_path, "wb") as f:
                writer.write(f)
                
            api_key = os.environ.get("GEMINI_API_KEY")
            client = genai.Client(api_key=api_key)

            json_txns = _call_gemini_bank_extraction(client, unlocked_pdf_path, is_retry=False)
            if not json_txns:
                json_txns = _call_gemini_bank_extraction(client, unlocked_pdf_path, is_retry=True)
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
            if 'unlocked_pdf_path' in locals() and os.path.exists(unlocked_pdf_path):
                os.remove(unlocked_pdf_path)
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

    # Queue-First Architecture: Insert individually first
    queue_ids = []
    xml_lines = [
        '<ENVELOPE>',
        '  <HEADER>',
        '    <TALLYREQUEST>Import Data</TALLYREQUEST>',
        '  </HEADER>',
        '  <BODY>',
        '    <IMPORTDATA>',
        '      <REQUESTDESC>',
        '        <REPORTNAME>Vouchers</REPORTNAME>',
        '      </REQUESTDESC>',
        '      <REQUESTDATA>'
    ]
    
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
        # Save as PENDING
        qid = queue_operation("POST_VOUCHER", single_envelope, pv["payload"], pv["description"])
        queue_ids.append(qid)
        
        # Prepare combined for online send
        xml_lines.append(pv['xml_block'])

    xml_lines.extend([
        '      </REQUESTDATA>',
        '    </IMPORTDATA>',
        '  </BODY>',
        '</ENVELOPE>'
    ])
    
    xml_data = "\n".join(xml_lines).encode('utf-8')
    
    try:
        for qid in queue_ids:
            set_delivery_uncertain(qid, True)
        response = await tally_transport.post(xml_data, timeout=5)
        if "<LINEERROR>" in response.text:
            for qid in queue_ids:
                set_delivery_uncertain(qid, False)
                update_queue_status(qid, "FAILED", "Tally rejected the vouchers. See response for details.")
            raise HTTPException(status_code=400, detail="Tally rejected the vouchers.")

        for qid in queue_ids:
            set_delivery_uncertain(qid, False)
            update_queue_status(qid, "SYNCED")
        return {"status": "success", "message": "Successfully posted to Tally"}

    except requests.exceptions.ConnectTimeout:
        # The connection itself never established (Tally's address is unreachable)
        # -- as safe as ConnectionError, nothing was ever sent.
        for qid in queue_ids:
            set_delivery_uncertain(qid, False)
        return {"status": "queued", "message": f"{len(queue_ids)} individual vouchers saved to offline queue."}
    except requests.exceptions.Timeout:
        # Ambiguous: connection was established and the request was sent, but no
        # response came back in time -- Tally may have processed this batch before
        # the response was lost. Leave delivery_uncertain set on every row in this
        # batch (already persisted above) so a manual retry is blocked until
        # someone verifies in Tally directly.
        return {"status": "queued", "message": f"{len(queue_ids)} individual vouchers saved to offline queue."}
    except requests.exceptions.ConnectionError:
        # Request never reached Tally at all -- safe to clear and leave PENDING.
        for qid in queue_ids:
            set_delivery_uncertain(qid, False)
        return {"status": "queued", "message": f"{len(queue_ids)} individual vouchers saved to offline queue."}
    except HTTPException:
        raise
    except Exception as e:
        for qid in queue_ids:
            update_queue_status(qid, "FAILED", str(e))
        raise HTTPException(status_code=500, detail=str(e))


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
