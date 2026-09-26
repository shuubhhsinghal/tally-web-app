import json
from datetime import timedelta
from backend.database import get_db
from backend.utils.reporting_utils import get_financial_year, parse_date, format_date
from backend.services.reporting_creditors_service import get_creditor_ledger_movements

# Mirrors reporting_creditors_service._fetch_pending_supplier_movements, but
# scoped to the queue description patterns that can move a BANK ledger's
# balance instead of a supplier's -- a bank account can be credited by a sale
# or a loan, or debited by a payment/transfer, and a bank-statement line
# categorized during reconciliation carries its own vch_type (Receipt vs
# Payment) that must be inferred from which side of the entry is the bank.
_BANK_QUEUE_DESCRIPTION_PATTERNS = (
    "Sales:%",
    "Payment:%",
    "Loan Received:%",
    "Bank Stmt:%",
    "Transfer:%",
)

def _fetch_pending_bank_movements(start_date: str, end_date: str, bank_ledger_name: str = None) -> list[dict]:
    start_date = start_date.replace('-', '')
    end_date = end_date.replace('-', '')
    with get_db() as conn:
        cursor = conn.cursor()
        placeholders = " OR ".join(["description LIKE ?"] * len(_BANK_QUEUE_DESCRIPTION_PATTERNS))
        cursor.execute(f"""
            SELECT payload, description FROM offline_queue
            WHERE operation_type = 'POST_VOUCHER' AND status = 'PENDING' AND ({placeholders})
        """, list(_BANK_QUEUE_DESCRIPTION_PATTERNS))
        rows = cursor.fetchall()

    movements = []
    for row in rows:
        try:
            payload = json.loads(row['payload']) if row['payload'] else {}
        except (json.JSONDecodeError, TypeError):
            continue
        if payload.get('delivery_uncertain') is True:
            continue

        date = payload.get('tally_date') or payload.get('date')
        if not date:
            continue
        date = date.replace('-', '')
        if not (start_date <= date <= end_date):
            continue

        description = row['description']
        ledger = None
        amount = 0.0
        voucher_type = None
        reference = None

        if description.startswith("Sales:"):
            ledger = payload.get('ledger')
            amount = float(payload.get('amount') or 0)
            voucher_type = 'Sale'
        elif description.startswith("Payment:"):
            ledger = payload.get('credit_ledger')
            amount = -float(payload.get('amount') or 0)
            voucher_type = 'Payment'
            reference = payload.get('debit_ledger')
        elif description.startswith("Loan Received:"):
            ledger = payload.get('received_into_ledger')
            amount = float(payload.get('principal_amount') or 0)
            voucher_type = 'Receipt'
            reference = payload.get('lender_name')
        elif description.startswith("Bank Stmt:") and not description.startswith("Bank Stmt Transfer:"):
            bank_name = payload.get('bank_ledger_name')
            ledger = bank_name
            is_receipt = payload.get('debit_ledger') == bank_name
            raw_amount = float(payload.get('amount') or 0)
            amount = raw_amount if is_receipt else -raw_amount
            voucher_type = 'Receipt' if is_receipt else 'Payment'
            reference = payload.get('ledger')
        elif description.startswith("Transfer:"):
            amount_val = float(payload.get('amount') or 0)
            if payload.get('from_account') and (not bank_ledger_name or payload.get('from_account') == bank_ledger_name):
                movements.append({
                    "date": date, "voucher_type": "Contra", "voucher_number": None,
                    "reference": payload.get('to_account'), "amount": -amount_val,
                    "is_deemed_positive": None, "voucher_id": None,
                    "ledger_name": payload.get('from_account'), "is_pending": True,
                })
            if payload.get('to_account') and (not bank_ledger_name or payload.get('to_account') == bank_ledger_name):
                movements.append({
                    "date": date, "voucher_type": "Contra", "voucher_number": None,
                    "reference": payload.get('from_account'), "amount": amount_val,
                    "is_deemed_positive": None, "voucher_id": None,
                    "ledger_name": payload.get('to_account'), "is_pending": True,
                })
            continue
        else:
            continue

        if not ledger:
            continue
        if bank_ledger_name and ledger != bank_ledger_name:
            continue

        movements.append({
            "date": date,
            "voucher_type": voucher_type,
            "voucher_number": None,
            "reference": reference,
            "amount": amount,
            "is_deemed_positive": None,
            "voucher_id": None,
            "ledger_name": ledger,
            "is_pending": True,
        })

    return movements

_BANK_PARENTS = ("Bank Accounts", "Bank OD A/c")

def list_bank_accounts():
    """Every Bank-parent ledger with its current balance (opening_balance
    plus every confirmed and pending movement to date) -- reuses the
    creditor ledger machinery (it's generic on ledger_name) rather than a
    second balance implementation."""
    from datetime import datetime
    today_str = datetime.now().strftime("%Y%m%d")
    with get_db() as conn:
        cursor = conn.cursor()
        placeholders = ",".join(["?"] * len(_BANK_PARENTS))
        cursor.execute(f"SELECT name, parent FROM ledgers WHERE parent IN ({placeholders}) ORDER BY name", _BANK_PARENTS)
        banks = [dict(r) for r in cursor.fetchall()]

    accounts = []
    for b in banks:
        result = get_bank_ledger_movements(b['name'], "20000101", today_str)
        accounts.append({"name": b['name'], "parent": b['parent'], "balance": round(result['period_closing'], 2)})
    return accounts

def get_bank_ledger_movements(bank_ledger_name: str, start_date: str, end_date: str):
    start_date = start_date.replace('-', '')
    end_date = end_date.replace('-', '')
    fy_start, _ = get_financial_year(start_date)
    pre_period_end = format_date(parse_date(start_date) - timedelta(days=1))
    pending = _fetch_pending_bank_movements(start_date, end_date, bank_ledger_name)
    pending_before = _fetch_pending_bank_movements(fy_start, pre_period_end, bank_ledger_name)
    return get_creditor_ledger_movements(
        bank_ledger_name, start_date, end_date,
        _pending_movements=pending,
        _pending_movements_before_period=pending_before,
    )
