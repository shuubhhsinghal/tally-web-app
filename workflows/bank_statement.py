import os
import re
import json
import openpyxl
import datetime
from io import BytesIO
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    CommandHandler,
    filters,
)

# States
WAIT_FOR_EXCEL, RESOLVE_COST_CENTERS, WAITING_FOR_INLINE_RULE, WAITING_FOR_PDF_PASSWORD, WAITING_FOR_VIEWRULES_ACCOUNT = range(10, 15)

COST_CENTERS = [
    "Mahagun",
    "Gulshan",
    "Vvip"
]

BANK_STATEMENT_LEDGERS = [
    "Union Bank Mahagun 133",
    "Federal Bank Gulshan",
    "Union Bank Vvip 2170"
]

RULES_FILE = "rules.json"
BANK_SUSPENSE_LEDGER = "Bank Suspense Account"

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

def requires_cost_centre(ledger_name: str) -> bool:
    if not ledger_name:
        return False
    try:
        with open("tally_ledger_cache.json", 'r') as f:
            cache = json.load(f)
            val = cache.get(ledger_name.strip().lower(), False)
            if isinstance(val, dict):
                return val.get("cost_centre", False)
            return bool(val)
    except (FileNotFoundError, json.JSONDecodeError):
        return False


class PasswordRequiredError(Exception):
    pass

def extract_df_from_pdf(file_bytes, password=None):
    try:
        import pdfplumber
        import pdfminer.pdfdocument
    except ImportError:
        raise ImportError("pdfplumber is not installed. Please run `pip install pdfplumber`.")
        
    all_rows = []
    try:
        with pdfplumber.open(BytesIO(file_bytes), password=password) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        if any(cell is not None and str(cell).strip() != "" for cell in row):
                            all_rows.append([str(c).replace('\n', ' ').strip() if c else "" for c in row])
                            
    except pdfminer.pdfdocument.PDFPasswordIncorrect:
        raise PasswordRequiredError("PDF is password protected.")
    except Exception as e:
        if "password" in str(e).lower() or "encrypted" in str(e).lower():
            raise PasswordRequiredError("PDF is password protected.")
        raise e
        
    if not all_rows:
        raise ValueError("Could not extract any tables from the PDF.")
        
    return pd.DataFrame(all_rows)

def get_real_bank_key(rules, target_bank):
    target_clean = target_bank.strip().lower()
    for k in rules.keys():
        if k.strip().lower() == target_clean:
            return k
    return target_bank.title()

def load_rules():
    if not os.path.exists(RULES_FILE):
        default_rules = {
            "Federal Bank Gulshan": {
                "northarcapital": {"ledger": "PAYTM 845 Loan", "cost_center": None},
                "shubh": {"ledger": "Drawings 3024", "cost_center": None},
                "paytmpayments": {"ledger": "Paytm Gulshan", "cost_center": None},
                "poweraccess": {"ledger": "Paytm Gulshan", "cost_center": None},
                "sanchi": {"ledger": "Drawings 3024", "cost_center": None},
                "shree shyam": {"ledger": "union bank mahagun 133", "cost_center": None},
                "zepto": {"ledger": "Drawings 3024", "cost_center": None},
                "mamta": {"ledger": "Rent", "cost_center": None}
            },
            "Union Bank Mahagun 133": {},
            "Union Bank Vvip 2170": {}
        }
        save_rules(default_rules)
        return default_rules
        
    with open(RULES_FILE, "r") as f:
        try:
            data = json.load(f)
            migrated = False
            
            # Injection logic requested by user
            if "Federal Bank Gulshan" not in data or not data["Federal Bank Gulshan"]:
                data["Federal Bank Gulshan"] = {
                    "northarcapital": {"ledger": "PAYTM 845 Loan", "cost_center": None},
                    "shubh": {"ledger": "Drawings 3024", "cost_center": None},
                    "paytmpayments": {"ledger": "Paytm Gulshan", "cost_center": None},
                    "poweraccess": {"ledger": "Paytm Gulshan", "cost_center": None},
                    "sanchi": {"ledger": "Drawings 3024", "cost_center": None},
                    "shree shyam": {"ledger": "union bank mahagun 133", "cost_center": None},
                    "zepto": {"ledger": "Drawings 3024", "cost_center": None},
                    "mamta": {"ledger": "Rent", "cost_center": None}
                }
                migrated = True
                
            if "Union Bank Mahagun 133" not in data:
                data["Union Bank Mahagun 133"] = {}
                migrated = True
                
            if "Union Bank Vvip 2170" not in data:
                data["Union Bank Vvip 2170"] = {}
                migrated = True
                
            # Purge obsolete banks
            if "Union Bank Mahagun" in data:
                del data["Union Bank Mahagun"]
                migrated = True
                
            if "Union Bank Vvip" in data:
                del data["Union Bank Vvip"]
                migrated = True

            for bank, rules in data.items():
                if isinstance(rules, dict):
                    for k, v in rules.items():
                        if isinstance(v, str):
                            data[bank][k] = {"ledger": v, "cost_center": None}
                            migrated = True
                            
            # Remove "Default" if it somehow exists
            if "Default" in data:
                del data["Default"]
                migrated = True
                            
            if migrated:
                save_rules(data)
            return data
        except json.JSONDecodeError:
            return {
                "Federal Bank Gulshan": {
                    "northarcapital": {"ledger": "PAYTM 845 Loan", "cost_center": None},
                    "shubh": {"ledger": "Drawings 3024", "cost_center": None},
                    "paytmpayments": {"ledger": "Paytm Gulshan", "cost_center": None},
                    "poweraccess": {"ledger": "Paytm Gulshan", "cost_center": None},
                    "sanchi": {"ledger": "Drawings 3024", "cost_center": None},
                    "shree shyam": {"ledger": "union bank mahagun 133", "cost_center": None},
                    "zepto": {"ledger": "Drawings 3024", "cost_center": None},
                    "mamta": {"ledger": "Rent", "cost_center": None}
                },
                "Union Bank Mahagun 133": {},
                "Union Bank Vvip 2170": {}
            }

def save_rules(rules):
    with open(RULES_FILE, "w") as f:
        json.dump(rules, f, indent=4)

def parse_statement(file_bytes, rules, bank_ledger_name, file_extension, password=None):
    if file_extension == '.pdf':
        df = extract_df_from_pdf(file_bytes, password)
    else:
        try:
            df = pd.read_excel(BytesIO(file_bytes), header=None)
        except Exception:
            df = pd.read_html(BytesIO(file_bytes), header=None)[0]
        
    # Scan first 30 rows to find header
    header_idx = -1
    for idx, row in df.head(30).iterrows():
        # strictly convert row values to a single string to avoid Series truth ambiguity
        row_str = ' '.join(str(val).lower() for val in row.values if pd.notna(val))
        if 'date' in row_str and ('narration' in row_str or 'particulars' in row_str or 'description' in row_str or 'remarks' in row_str):
            header_idx = idx
            break
            
    if header_idx != -1:
        df.columns = df.iloc[header_idx]
        df = df.iloc[header_idx+1:].reset_index(drop=True)
        
    # Standardize column names
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
    
    # Remove any duplicated columns that mapped to the same standardized name
    df = df.loc[:, ~df.columns.duplicated()]
    
    required_cols = {'Date', 'Narration', 'Withdrawal', 'Deposit'}
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        raise ValueError(f"Could not identify columns. Missing: {missing_cols}. I found these columns: {list(df.columns)}. Please update the mapping rules.")
        
    df['Deposit'] = df['Deposit'].fillna(0)
    df['Withdrawal'] = df['Withdrawal'].fillna(0)
    
    # Strictly use pandas bitwise operators to drop rows where BOTH Withdrawal and Deposit are NaN/empty/0
    df = df[~(((df['Withdrawal'] == 0) | (df['Withdrawal'].astype(str) == '0.0') | (df['Withdrawal'].astype(str) == '')) & 
              ((df['Deposit'] == 0) | (df['Deposit'].astype(str) == '0.0') | (df['Deposit'].astype(str) == '')))]
    
    # Clean junk rows (drop NaN Date, empty Date, or Opening/Closing Balance rows)
    df = df[df['Date'].notna()]
    df = df[df['Date'].astype(str).str.strip() != '']
    df = df[~df['Date'].astype(str).str.contains('Opening Balance|Closing Balance', case=False, na=False)]
    
    transactions = []
    for _, row in df.iterrows():
        raw_date = row['Date']
        if pd.isna(raw_date): continue
        
        try:
            dt = pd.to_datetime(raw_date, dayfirst=True)
            tally_date = dt.strftime('%Y%m%d')
        except:
            continue
            
        raw_narration = str(row['Narration']).strip() if not pd.isna(row['Narration']) else ""
        clean_narration = sanitize_xml(raw_narration)
        narr_upper = raw_narration.upper()
        
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
            
        ledger = BANK_SUSPENSE_LEDGER
        cost_center = None
        
        real_bank = get_real_bank_key(rules, bank_ledger_name)
        bank_rules = rules.get(real_bank, {})
        
        for keyword, mapped_rule in bank_rules.items():
            if keyword.upper() in narr_upper:
                if isinstance(mapped_rule, dict):
                    ledger = mapped_rule.get("ledger", BANK_SUSPENSE_LEDGER)
                    cost_center = mapped_rule.get("cost_center")
                else:
                    ledger = mapped_rule
                break
                
        if ledger == "IGNORE":
            continue
            
        is_unmapped = (ledger == BANK_SUSPENSE_LEDGER)
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
        
    return transactions

def build_tally_xml(transactions, bank_ledger_name):
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
    
    for t in transactions:
        ledger = t['ledger']
        if ledger == "IGNORE":
            continue
            
        withdraw = t['withdraw']
        deposit = t['deposit']
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
        
        voucher = f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
           <VOUCHER VCHTYPE="{vch_type}" ACTION="Create">
              <DATE>{t['date']}</DATE>
              <EFFECTIVEDATE>{t['date']}</EFFECTIVEDATE>
              <VOUCHERTYPENAME>{vch_type}</VOUCHERTYPENAME>
              <NARRATION>{t['clean_narration']}</NARRATION>{ledgers_xml}
           </VOUCHER>
        </TALLYMESSAGE>"""
        xml_lines.append(voucher)
        
    xml_lines.extend([
        '      </REQUESTDATA>',
        '    </IMPORTDATA>',
        '  </BODY>',
        '</ENVELOPE>'
    ])
    return "\n".join(xml_lines).encode('utf-8')

import pypdf
from google import genai

def map_gemini_transactions_to_rules(json_txns, rules, bank_ledger_name):
    transactions = []
    real_bank = get_real_bank_key(rules, bank_ledger_name)
    bank_rules = rules.get(real_bank, {})
    
    for row in json_txns:
        try:
            dt = pd.to_datetime(row['date'])
            tally_date = dt.strftime('%Y%m%d')
        except:
            continue
            
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
            
        ledger = BANK_SUSPENSE_LEDGER
        cost_center = None
        
        for keyword, mapped_rule in bank_rules.items():
            if keyword.upper() in narr_upper:
                if isinstance(mapped_rule, dict):
                    ledger = mapped_rule.get("ledger", BANK_SUSPENSE_LEDGER)
                    cost_center = mapped_rule.get("cost_center")
                else:
                    ledger = mapped_rule
                break
                
        if ledger == "IGNORE":
            continue
            
        is_unmapped = (ledger == BANK_SUSPENSE_LEDGER)
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
        
    return transactions

def extract_transactions_from_pdf_gemini(encrypted_pdf_path, password=None):
    # 1. Decrypt locally if a password is provided
    reader = pypdf.PdfReader(encrypted_pdf_path)
    if reader.is_encrypted:
        if not password or not reader.decrypt(password):
            raise ValueError("Incorrect password.")
            
    # 2. Save a temporary unlocked copy for Gemini
    unlocked_pdf_path = encrypted_pdf_path.replace(".pdf", "_unlocked_temp.pdf")
    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
        
    with open(unlocked_pdf_path, "wb") as f:
        writer.write(f)
        
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)
    uploaded_file = None
    
    try:
        # 3. Upload native PDF to Gemini File API
        uploaded_file = client.files.upload(file=unlocked_pdf_path)
        
        # 4. Prompt Gemini 3.5 Flash-Lite
        prompt = (
            "You are a strict financial data extractor. Read this bank statement PDF. "
            "Extract every transaction into a JSON array of objects. "
            "Each object MUST have the following exact keys:\n"
            "- 'date': string (format YYYY-MM-DD)\n"
            "- 'narration': string (the transaction description/particulars)\n"
            "- 'amount': float (the absolute numerical value without commas)\n"
            "- 'type': string (strictly 'DEBIT' for money out, or 'CREDIT' for money in)\n\n"
            "Return ONLY a valid JSON array. Do not include markdown formatting or explanations."
        )
        
        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=[uploaded_file, prompt],
        )
        
        # 5. Clean and parse JSON
        res_text = response.text.strip()
        if res_text.startswith("```json"):
            res_text = res_text[7:-3].strip()
        elif res_text.startswith("```"):
            res_text = res_text[3:-3].strip()
            
        return json.loads(res_text)
        
    finally:
        # 6. Secure Cleanup: Delete local and cloud temp files
        if os.path.exists(unlocked_pdf_path):
            os.remove(unlocked_pdf_path)
        if uploaded_file:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass

async def process_and_show_pdf_transactions(update, context, file_path, password=None, processing_msg=None):
    try:
        json_txns = extract_transactions_from_pdf_gemini(file_path, password)
        rules = load_rules()
        bank_ledger_name = context.user_data.get('bank_ledger_name', 'Bank Suspense Account')
        transactions = map_gemini_transactions_to_rules(json_txns, rules, bank_ledger_name)
        
        with open(file_path, "rb") as f:
            file_bytes = f.read()
            
        context.user_data['current_excel_bytes'] = file_bytes
        context.user_data['transactions'] = transactions
        missing_cc_indices = [i for i, t in enumerate(transactions) if t.get('missing_cost_center')]
        context.user_data['missing_cc_indices'] = missing_cc_indices
        
        if processing_msg:
            await processing_msg.delete()
            
        if not missing_cc_indices:
            return await show_bank_summary(update, context)
            
        context.user_data['current_cc_idx'] = 0
        return await prompt_next_cost_center(update, context)
        
    except ValueError as e:
        if "Incorrect password" in str(e):
            msg = "❌ Incorrect password. Please type the password again:"
            if processing_msg:
                await processing_msg.edit_text(msg)
            else:
                await update.message.reply_text(msg)
            return WAITING_FOR_PDF_PASSWORD
        raise e
    except Exception as e:
        msg = f"⚠️ Error parsing PDF: {str(e)}"
        if processing_msg:
            await processing_msg.edit_text(msg)
        else:
            await update.message.reply_text(msg)
        return ConversationHandler.END

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_name = update.message.document.file_name.lower()
    ext = '.' + file_name.split('.')[-1] if '.' in file_name else ''
    if ext not in ['.xlsx', '.xls', '.pdf']:
        await update.message.reply_text("⚠️ Please upload a valid Excel or PDF file (.xlsx, .xls, .pdf).")
        return WAIT_FOR_EXCEL
        
    processing_msg = await update.message.reply_text(f"Downloading and parsing {ext[1:].upper()} file...")
    try:
        doc_file = await update.message.document.get_file()
        file_bytes = await doc_file.download_as_bytearray()
        
        if ext == '.pdf':
            os.makedirs("downloads", exist_ok=True)
            file_path = f"downloads/{file_name}"
            with open(file_path, "wb") as f:
                f.write(file_bytes)
                
            reader = pypdf.PdfReader(file_path)
            if reader.is_encrypted:
                context.user_data['pdf_path'] = file_path
                context.user_data['pending_processing_msg'] = processing_msg.message_id
                await processing_msg.edit_text("🔒 This PDF is password protected. Please type the password to unlock it:")
                return WAITING_FOR_PDF_PASSWORD
            else:
                return await process_and_show_pdf_transactions(update, context, file_path, password=None, processing_msg=processing_msg)
        
        rules = load_rules()
        bank_ledger_name = context.user_data.get('bank_ledger_name', 'Bank Suspense Account')
        transactions = parse_statement(file_bytes, rules, bank_ledger_name, ext)
        
        context.user_data['current_excel_bytes'] = file_bytes
        context.user_data['transactions'] = transactions
        missing_cc_indices = [i for i, t in enumerate(transactions) if t.get('missing_cost_center')]
        context.user_data['missing_cc_indices'] = missing_cc_indices
        
        await processing_msg.delete()
        
        if not missing_cc_indices:
            return await show_bank_summary(update, context)
            
        context.user_data['current_cc_idx'] = 0
        return await prompt_next_cost_center(update, context)
    except Exception as e:
        await processing_msg.edit_text(f"Error processing document: {str(e)}")
        return WAIT_FOR_EXCEL

async def handle_pdf_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    file_path = context.user_data.get('pdf_path')
    
    if not file_path or not os.path.exists(file_path):
        await update.message.reply_text("Session expired. Please upload the file again.")
        return WAIT_FOR_EXCEL
        
    msg_id = context.user_data.get('pending_processing_msg')
    if msg_id:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg_id)
        except:
            pass
            
    processing_msg = await update.message.reply_text("🔓 Decrypting and analyzing PDF with Gemini...")
    return await process_and_show_pdf_transactions(update, context, file_path, password=password, processing_msg=processing_msg)

async def prompt_next_cost_center(update: Update, context: ContextTypes.DEFAULT_TYPE):
    missing_cc_indices = context.user_data['missing_cc_indices']
    current_idx = context.user_data['current_cc_idx']
    
    if current_idx >= len(missing_cc_indices):
        return await show_bank_summary(update, context)
        
    tx_idx = missing_cc_indices[current_idx]
    tx = context.user_data['transactions'][tx_idx]
    amt = tx['withdraw'] if tx['withdraw'] > 0 else tx['deposit']
    
    msg = (
        f"Transaction: {tx['raw_narration']} ({tx['ledger']} - ₹{amt:,.0f}) requires a Cost Centre. Please select one:"
    )
    
    keyboard = []
    for idx, cc in enumerate(COST_CENTERS):
        keyboard.append([InlineKeyboardButton(cc, callback_data=f"cc_{idx}")])
        
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.message.reply_text(msg, reply_markup=reply_markup)
    else:
        await update.message.reply_text(msg, reply_markup=reply_markup)
        
    return RESOLVE_COST_CENTERS

async def resolve_cost_center_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    idx_str = query.data.split("_")[1]
    cost_center = COST_CENTERS[int(idx_str)]
    
    current_idx = context.user_data['current_cc_idx']
    tx_idx = context.user_data['missing_cc_indices'][current_idx]
    
    # Update the transaction
    context.user_data['transactions'][tx_idx]['cost_center'] = cost_center
    context.user_data['transactions'][tx_idx]['missing_cost_center'] = False
    
    ledger_name = context.user_data['transactions'][tx_idx]['ledger']
    await query.message.edit_text(f"✅ Assigned Cost Centre: `{cost_center}` for `{ledger_name}`")
    
    context.user_data['current_cc_idx'] += 1
    return await prompt_next_cost_center(update, context)

async def show_bank_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    transactions = context.user_data.get('transactions', [])
    mapped = [t for t in transactions if not t.get('unmapped')]
    unmapped = [t for t in transactions if t.get('unmapped')]
    
    context.user_data['mapped_txns'] = mapped
    context.user_data['unmapped_txns'] = unmapped
    
    keyboard = [
        [InlineKeyboardButton("👀 Show Mapped", callback_data="preview_mapped"),
         InlineKeyboardButton("❓ Show Unmapped", callback_data="preview_unmapped")],
        [InlineKeyboardButton("✅ Push to Tally", callback_data="push_bank_to_tally")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    summary = (
        f"📊 Statement Review\n"
        f"Total: {len(transactions)}\n"
        f"Auto-Mapped: {len(mapped)}\n"
        f"Suspense A/c: {len(unmapped)}\n\n"
        f"Review your transactions or push to Tally."
    )
    if update.message:
        await update.message.reply_text(summary, reply_markup=reply_markup)
    else:
        await update.callback_query.answer()
        context.user_data['unmapped_page'] = 0
        context.user_data['mapped_page'] = 0
        await update.callback_query.message.edit_text(summary, reply_markup=reply_markup)
    return WAIT_FOR_EXCEL

async def bank_preview_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    is_unmapped_view = False
    if query.data in ["preview_mapped", "mapped_next", "mapped_prev"]:
        txns = context.user_data.get('mapped_txns', [])
        page_key = 'mapped_page'
        title_prefix = "👀 Mapped Transactions"
    else:
        txns = context.user_data.get('unmapped_txns', [])
        page_key = 'unmapped_page'
        title_prefix = "❓ Unmapped Transactions"
        is_unmapped_view = True

    if query.data in ["preview_mapped", "preview_unmapped"]:
        context.user_data[page_key] = 0
    elif query.data in ["mapped_next", "unmapped_next"]:
        context.user_data[page_key] = context.user_data.get(page_key, 0) + 1
    elif query.data in ["mapped_prev", "unmapped_prev"]:
        context.user_data[page_key] = max(0, context.user_data.get(page_key, 0) - 1)
        
    page = context.user_data.get(page_key, 0)
    items_per_page = 15
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    
    current_items = txns[start_idx:end_idx]
    total_pages = (len(txns) + items_per_page - 1) // items_per_page
    if total_pages == 0:
        total_pages = 1
        
    preview_text = f"{title_prefix} (Page {page + 1} of {total_pages})\n\n"
    
    for tx in current_items:
        amt = tx['withdraw'] if tx['withdraw'] > 0 else tx['deposit']
        
        date_str = str(tx['date'])
        if len(date_str) == 8 and date_str.isdigit():
            date_str = f"{date_str[6:8]}/{date_str[4:6]}"
            
        narration = str(tx.get('raw_narration', ''))[:75]
        
        if is_unmapped_view:
            preview_text += f"• {date_str} | ₹{amt:,.0f} ➡️ ⚠️ Suspense\n"
            preview_text += f"  └ 📝 {narration}\n\n"
        else:
            ledger = tx.get('ledger', 'Unknown')
            preview_text += f"• {date_str} | ₹{amt:,.0f} ➡️ {ledger}\n"
            preview_text += f"  └ 📝 {narration}\n\n"
            
    if not current_items:
        preview_text += "No transactions in this category."
        
    nav_buttons = []
    if page > 0:
        prev_data = "unmapped_prev" if is_unmapped_view else "mapped_prev"
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=prev_data))
    if end_idx < len(txns):
        next_data = "unmapped_next" if is_unmapped_view else "mapped_next"
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=next_data))
        
    keyboard = []
    if nav_buttons:
        keyboard.append(nav_buttons)
        
    if is_unmapped_view:
        keyboard.append([InlineKeyboardButton("➕ New Rule", callback_data="inline_new_rule")])
        
    keyboard.append([InlineKeyboardButton("🔙 Back to Summary", callback_data="summary_back")])
    
    await query.edit_message_text(preview_text, reply_markup=InlineKeyboardMarkup(keyboard))
    return WAIT_FOR_EXCEL

async def inline_new_rule_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.edit_text("Type your new rule (e.g., keyword=ledger):")
    return WAITING_FOR_INLINE_RULE

async def inline_rule_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    context.user_data['is_inline_bank_rule'] = True
    
    rule_data = re.sub(r'(?i)^(add|create)\s+rule\s*', '', text).strip()
    if "=" not in rule_data:
        await update.message.reply_text("Usage: KEYWORD = LEDGER_NAME\nMake sure to include the '=' sign.")
        return WAITING_FOR_INLINE_RULE
        
    parts = rule_data.split("=", 1)
    keyword = parts[0].strip()
    ledger = parts[1].strip()
    
    if not keyword or not ledger:
        await update.message.reply_text("Both Keyword and Ledger Name must be provided.")
        return WAITING_FOR_INLINE_RULE
        
    await validate_and_prompt_rule(update, context, "add", keyword, ledger)
    return WAITING_FOR_INLINE_RULE

async def push_bank_to_tally_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await finish_xml_generation(update, context)
    return WAIT_FOR_EXCEL

async def finish_xml_generation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    transactions = context.user_data['transactions']
    xml_bytes = build_tally_xml(transactions, context.user_data.get('bank_ledger_name', 'Bank Suspense Account'))
    
    push_to_tally = context.bot_data.get('push_to_tally')
    status_code, response_text = push_to_tally(
        xml_bytes,
        user_id=update.effective_user.id,
        description=f"Bank Statement Sync: {len(transactions)} entries"
    )
    
    total = len(transactions)
    still_unmapped = sum(1 for t in transactions if t['unmapped'])
    mapped_count = total - still_unmapped
    
    sync_status = "⏸ Offline (Saved to queue)" if status_code == 202 else f"HTTP {status_code}"
    
    summary = (
        f"✅ Processing Complete!\n"
        f"- Total Transactions: {total}\n"
        f"- Auto-Mapped: {mapped_count}\n"
        f"- Sent to Suspense A/c: {still_unmapped}\n\n"
        f"📡 Tally Sync: {sync_status}"
    )
        
    xml_file = BytesIO(xml_bytes)
    user_id = update.effective_user.id
    
    if update.callback_query:
        msg_obj = update.callback_query.message
    else:
        msg_obj = update.message
        
    await context.bot.send_document(
        chat_id=user_id,
        document=xml_file,
        filename="bank_import.xml",
        caption=summary
    )
    
    
    # Reset context
    context.user_data.pop('transactions', None)
    context.user_data.pop('unmapped_indices', None)
    context.user_data.pop('current_unmapped_idx', None)
    
    return ConversationHandler.END
    return ConversationHandler.END

async def bank_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "main_menu":
        return ConversationHandler.END
        
    if data.startswith("bank_"):
        bank_name = data.split("_", 1)[1]
        context.user_data['bank_ledger_name'] = bank_name
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_to_bank_list")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            f"{bank_name} selected.\n\nPlease upload your Bank Statement file (.xlsx, .xls, .pdf).",
            reply_markup=reply_markup
        )
        return WAIT_FOR_EXCEL
        
    return ConversationHandler.END

async def back_to_bank_list_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = []
    for b in BANK_STATEMENT_LEDGERS:
        keyboard.append([InlineKeyboardButton(b, callback_data=f"bank_{b}")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text("Select the Bank Ledger for this statement:", reply_markup=reply_markup)
    
    return ConversationHandler.END

def _get_top_ledger_candidates(raw_name, tally_list, top_n=3):
    def tokenize(text):
        return set(re.sub(r'[^\w\s]', ' ', text.lower()).split())
        
    raw_tokens = tokenize(raw_name)
    scored_items = []
    
    for tally_item in tally_list:
        tally_tokens = tokenize(tally_item)
        overlap = len(raw_tokens.intersection(tally_tokens))
        scored_items.append((overlap, tally_item))
        
    scored_items.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored_items if item[0] > 0][:top_n]

async def validate_and_prompt_rule(update, context, action, keyword, ledger=None, cost_center=None):
    if action == "delete":
        await prompt_bank_for_rule(update, context, action, keyword)
        return
        
    try:
        with open("tally_ledger_cache.json", "r") as f:
            tally_cache = json.load(f)
    except Exception:
        tally_cache = {}
        
    lower_to_real = {k.lower(): k for k in tally_cache.keys()}
    tally_ledgers_lower = list(lower_to_real.keys())
    
    target_lower = ledger.lower()
    if target_lower in lower_to_real:
        real_ledger = lower_to_real[target_lower]
        await prompt_bank_for_rule(update, context, action, keyword, real_ledger, cost_center)
        return
        
    matches = _get_top_ledger_candidates(target_lower, tally_ledgers_lower, top_n=3)
    is_fuzzy = True
        
    if matches:
        keyboard = []
        context.user_data['pending_fuzzy_action'] = action
        context.user_data['pending_fuzzy_keyword'] = keyword
        context.user_data['pending_fuzzy_cc'] = cost_center
        context.user_data['fuzzy_matches'] = {str(i): lower_to_real[m] for i, m in enumerate(matches)}
        
        for i, m in enumerate(matches):
            real_m = lower_to_real[m]
            keyboard.append([InlineKeyboardButton(real_m, callback_data=f"rule_ledger:{i}")])
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="rule_ledger:cancel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        if is_fuzzy:
            msg = f"Ledger '{ledger}' not found exactly. Did you mean:"
        else:
            msg = f"🔍 Found matching ledgers for '{ledger}'. Please select one:"
        
        if update.message:
            await update.message.reply_text(msg, reply_markup=reply_markup)
        elif update.callback_query:
            await update.callback_query.message.reply_text(msg, reply_markup=reply_markup)
    else:
        msg = f"❌ No matching ledger found in Tally for '{ledger}'. Please check your spelling or run /sync_masters."
        if update.message:
            await update.message.reply_text(msg)
        elif update.callback_query:
            await update.callback_query.message.reply_text(msg)

async def handle_rule_ledger_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data.replace("rule_ledger:", "")
    
    if data == "cancel":
        await query.message.edit_text("❌ Action cancelled.")
        context.user_data.pop('pending_fuzzy_action', None)
        context.user_data.pop('pending_fuzzy_keyword', None)
        context.user_data.pop('pending_fuzzy_cc', None)
        context.user_data.pop('fuzzy_matches', None)
        return WAITING_FOR_INLINE_RULE
        
    fuzzy_matches = context.user_data.get('fuzzy_matches', {})
    real_ledger = fuzzy_matches.get(data)
    
    action = context.user_data.get('pending_fuzzy_action')
    keyword = context.user_data.get('pending_fuzzy_keyword')
    cost_center = context.user_data.get('pending_fuzzy_cc')
    
    if not real_ledger or not action or not keyword:
        await query.message.edit_text("Session expired. Please try again.")
        return
        
    await query.message.edit_text(f"Selected ledger: {real_ledger}")
    await prompt_bank_for_rule(update, context, action, keyword, real_ledger, cost_center)

    context.user_data.pop('pending_fuzzy_action', None)
    context.user_data.pop('pending_fuzzy_keyword', None)
    context.user_data.pop('pending_fuzzy_cc', None)
    context.user_data.pop('fuzzy_matches', None)
    return WAITING_FOR_INLINE_RULE

async def prompt_bank_for_rule(update, context, action, keyword, ledger=None, cost_center=None):
    context.user_data['pending_rule_action'] = action
    context.user_data['pending_rule_keyword'] = keyword
    context.user_data['pending_rule_ledger'] = ledger
    context.user_data['pending_rule_cc'] = cost_center
    
    rules = load_rules()
    keyboard = []
    for bank in rules.keys():
        keyboard.append([InlineKeyboardButton(bank, callback_data=f"rulebank_{bank}")])
    keyboard.append([InlineKeyboardButton("ALL Banks", callback_data="rulebank_ALL")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = "Which bank account should this apply to?"
    
    if update.message:
        await update.message.reply_text(msg, reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.reply_text(msg, reply_markup=reply_markup)

async def rule_bank_select_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    bank_selection = query.data.split("rulebank_", 1)[1]
    
    action = context.user_data.get('pending_rule_action')
    keyword = context.user_data.get('pending_rule_keyword')
    ledger = context.user_data.get('pending_rule_ledger')
    cost_center = context.user_data.get('pending_rule_cc')
    
    if not action or not keyword:
        await query.message.edit_text("Session expired. Please try again.")
        return
        
    rules = load_rules()
    target_banks = list(rules.keys()) if bank_selection == "ALL" else [bank_selection]
    
    for bank in target_banks:
        real_bank = get_real_bank_key(rules, bank)
        if real_bank not in rules:
            rules[real_bank] = {}
            
        if action == "add":
            if cost_center:
                rules[real_bank][keyword] = {"ledger": ledger, "cost_center": cost_center}
            else:
                rules[real_bank][keyword] = {"ledger": ledger, "cost_center": None}
        elif action == "delete":
            matched_key = None
            target_clean = keyword.strip().lower()
            for k in rules[real_bank].keys():
                if k.strip().lower() == target_clean:
                    matched_key = k
                    break
            if matched_key:
                del rules[real_bank][matched_key]
                
    save_rules(rules)
    
    context.user_data.pop('pending_rule_action', None)
    context.user_data.pop('pending_rule_keyword', None)
    context.user_data.pop('pending_rule_ledger', None)
    context.user_data.pop('pending_rule_cc', None)
    
    is_inline = context.user_data.pop('is_inline_bank_rule', False)
    
    if action == "add":
        if bank_selection == "ALL":
            msg = f"✅ Rule for '{keyword}' added to ALL banks."
        else:
            msg = f"✅ Rule for '{keyword}' added to {bank_selection}."
    else:
        if bank_selection == "ALL":
            msg = f"✅ Rule for '{keyword}' deleted from ALL banks."
        else:
            msg = f"✅ Rule for '{keyword}' deleted from {bank_selection}."
            
    await query.message.edit_text(msg)
    
    if is_inline and 'current_excel_bytes' in context.user_data:
        file_bytes = context.user_data['current_excel_bytes']
        bank_ledger_name = context.user_data.get('bank_ledger_name', 'Bank Suspense Account')
        transactions = parse_excel(file_bytes, load_rules(), bank_ledger_name)
        
        context.user_data['transactions'] = transactions
        missing_cc_indices = [i for i, t in enumerate(transactions) if t.get('missing_cost_center')]
        context.user_data['missing_cc_indices'] = missing_cc_indices
        
        if not missing_cc_indices:
            return await show_bank_summary(update, context)
        else:
            context.user_data['current_cc_idx'] = 0
            return await prompt_next_cost_center(update, context)

async def addrule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text.startswith('/'):
        rule_data = text.replace('/addrule', '').strip()
    else:
        rule_data = re.sub(r'(?i)^(add|create)\s+rule\s*', '', text).strip()
        
    if not rule_data:
        await update.message.reply_text("Usage: /addrule KEYWORD = LEDGER_NAME or 'add rule KEYWORD = LEDGER_NAME'")
        return
        
    if "=" not in rule_data:
        await update.message.reply_text("Usage: /addrule KEYWORD = LEDGER_NAME\nMake sure to include the '=' sign.")
        return
        
    parts = rule_data.split("=", 1)
    keyword = parts[0].strip()
    ledger = parts[1].strip()
    
    if not keyword or not ledger:
        await update.message.reply_text("Both Keyword and Ledger Name must be provided.")
        return
        
    await validate_and_prompt_rule(update, context, "add", keyword, ledger)

async def viewrules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rules = load_rules()
    keyboard = []
    for bank in rules.keys():
        callback_data = f"viewrules_acct:{bank.lower().strip()}"[:64]
        keyboard.append([InlineKeyboardButton(bank, callback_data=callback_data)])
        
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Select an account to view its rules:", reply_markup=reply_markup)

async def handle_viewrules_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    bank = query.data.replace("viewrules_acct:", "")
    account_key = bank.lower().strip()
    rules = load_rules()
    
    bank_rules = {}
    display_bank = bank
    for k, v in rules.items():
        if k.lower().strip().startswith(account_key):
            bank_rules = v
            display_bank = k
            break
            
    try:
        if not bank_rules:
            await query.message.edit_text(f"🏦 {display_bank}\n\nNo rules found for this account.", reply_markup=query.message.reply_markup)
            return
            
        lines = [f"🏦 {display_bank}\n"]
        for idx, (kw, value) in enumerate(bank_rules.items(), 1):
            if isinstance(value, dict):
                ledger = value.get("ledger")
                cost_center = value.get("cost_center")
            else:
                ledger = value
                cost_center = None
                
            if cost_center:
                lines.append(f"  {idx}. `{kw}` ➡️ `{ledger}` [Branch: {cost_center}]")
            else:
                lines.append(f"  {idx}. `{kw}` ➡️ `{ledger}`")
                
        await query.message.edit_text("\n".join(lines), reply_markup=query.message.reply_markup)
    except Exception as e:
        if "Message is not modified" in str(e):
            pass
        else:
            raise

async def delrule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text.startswith('/'):
        keyword = text.replace('/delrule', '').strip()
    else:
        keyword = re.sub(r'(?i)^(delete|remove)\s+rule\s*', '', text).strip()
        
    if not keyword:
        await update.message.reply_text("Usage: /delrule KEYWORD or 'delete rule KEYWORD'")
        return
        
    await validate_and_prompt_rule(update, context, "delete", keyword)


bank_statement_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(bank_menu_cb, pattern="^bank_"),
        CommandHandler("addrule", addrule_command),
        CommandHandler("delrule", delrule_command),
        CommandHandler("viewrules", viewrules_command),
        MessageHandler(filters.Regex(re.compile(r'(?i)^(add|create)\s+rule', re.IGNORECASE)), addrule_command),
        MessageHandler(filters.Regex(re.compile(r'(?i)^(delete|remove)\s+rule', re.IGNORECASE)), delrule_command),
    ],
    states={
        WAIT_FOR_EXCEL: [
            MessageHandler(filters.Document.ALL, handle_document),
            CallbackQueryHandler(bank_preview_cb, pattern="^(preview_|mapped_next|mapped_prev|unmapped_next|unmapped_prev)"),
            CallbackQueryHandler(show_bank_summary, pattern="^summary_back$"),
            CallbackQueryHandler(inline_new_rule_cb, pattern="^inline_new_rule$"),
            CallbackQueryHandler(push_bank_to_tally_cb, pattern="^push_bank_to_tally$"),
            CallbackQueryHandler(back_to_bank_list_cb, pattern="^back_to_bank_list$")
        ],
        WAITING_FOR_INLINE_RULE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, inline_rule_handler),
            # CallbackQueryHandler(handle_rule_ledger_selection, pattern="^rule_ledger:"),
            CallbackQueryHandler(rule_bank_select_cb, pattern="^rulebank_")
        ],
        WAITING_FOR_PDF_PASSWORD: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_pdf_password)
        ],
        RESOLVE_COST_CENTERS: [
            CallbackQueryHandler(resolve_cost_center_cb, pattern="^cc_")
        ],
        WAITING_FOR_VIEWRULES_ACCOUNT: [
            CallbackQueryHandler(handle_viewrules_account, pattern="^viewrules_acct:")
        ],
    },
    fallbacks=[
        CallbackQueryHandler(show_bank_summary, pattern="^summary_back$"),
        CallbackQueryHandler(handle_viewrules_account, pattern="^viewrules_acct:") 
    ],
    allow_reentry=True
)

bank_statement_handlers = [
    bank_statement_handler,
    # The handle_viewrules_account will be registered globally in group 1 in bot.py
    # The handle_rule_ledger_selection will be registered globally in group 1 in bot.py
    CallbackQueryHandler(rule_bank_select_cb, pattern="^rulebank_"),
]
