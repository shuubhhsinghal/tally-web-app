import json
import re
import datetime
from typing import Optional

from backend.database import get_db, get_all_ledgers

OWN_BANK_LEDGER_PARENTS = ("Bank Accounts", "Bank OD A/c")

# NEFT is the one narration family with a genuinely standardized, network-wide
# UTR that's shared identically between the remitting and beneficiary bank's
# own statements (IFSC-style 4-letter prefix + serial digits). IMPS/UPI/MOBFT
# narrations embed a numeric code too, but it's very likely each bank's own
# internal transaction reference rather than a value shared across banks --
# real two-bank sample data would be needed to trust those, so they're
# deliberately NOT used as a match key here, only NEFT's UTR is.
_NEFT_UTR_RE = re.compile(r'NEFT:.*?\b([A-Z]{4}[A-Z0-9]?\d{7,12})\b')


def get_own_bank_ledger_names() -> list:
    """Ledgers that are one of the user's own bank accounts -- same filter
    already used by the frontend's bank-ledger dropdown
    (frontend/src/app/bank-statement/page.js's bankOptions)."""
    return [l['name'] for l in get_all_ledgers() if l.get('parent') in OWN_BANK_LEDGER_PARENTS]


def extract_reference_number(narration: str) -> Optional[str]:
    if not narration:
        return None
    m = _NEFT_UTR_RE.search(narration.upper())
    return m.group(1) if m else None


def _dates_within_window(date_a: str, date_b: str, window_days: int) -> bool:
    try:
        da = datetime.datetime.strptime(date_a, "%Y%m%d")
        db = datetime.datetime.strptime(date_b, "%Y%m%d")
    except (ValueError, TypeError):
        return False
    return abs((da - db).days) <= window_days


def _find_pending_match(transaction: dict, current_bank_ledger: str, other_bank_ledgers: list, window_days: int) -> Optional[dict]:
    is_withdrawal = float(transaction.get('withdraw') or 0) > 0
    amount = float(transaction['withdraw']) if is_withdrawal else float(transaction['deposit'])

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, payload FROM offline_queue
            WHERE operation_type = 'POST_VOUCHER' AND status = 'PENDING' AND description LIKE 'Bank Stmt:%'
        """)
        rows = cursor.fetchall()

    for row in rows:
        try:
            payload = json.loads(row['payload']) if row['payload'] else {}
        except (json.JSONDecodeError, TypeError):
            continue

        candidate_bank = payload.get('bank_ledger_name')
        if candidate_bank not in other_bank_ledgers:
            continue
        if payload.get('bank_ledger_name') == current_bank_ledger:
            continue
        if not _dates_within_window(str(payload.get('date') or ''), transaction['date'], window_days):
            continue
        if round(float(payload.get('amount') or 0), 2) != round(amount, 2):
            continue

        # The candidate's direction relative to ITS OWN bank ledger must be
        # opposite this transaction's direction: money left the candidate's
        # bank (debit_ledger == its own bank ledger) matches a deposit here;
        # money arrived at the candidate's bank (credit_ledger == its own
        # bank ledger) matches a withdrawal here.
        money_left_candidate_bank = payload.get('debit_ledger') == candidate_bank
        money_arrived_at_candidate_bank = payload.get('credit_ledger') == candidate_bank
        if is_withdrawal and not money_arrived_at_candidate_bank:
            continue
        if not is_withdrawal and not money_left_candidate_bank:
            continue

        if payload.get('delivery_uncertain') is True:
            # Delivery to Tally is unconfirmed for the candidate -- it may
            # already exist there, so it must never be silently cancelled.
            # Surface it like a synced match (informational only).
            return {
                "source": "synced",
                "bank_ledger_name": candidate_bank,
                "date": payload.get('date'),
                "amount": amount,
                "narration": payload.get('narration'),
            }

        return {
            "source": "pending",
            "queue_id": row['id'],
            "bank_ledger_name": candidate_bank,
            "date": payload.get('date'),
            "amount": amount,
            "narration": payload.get('narration'),
        }

    return None


def _find_synced_match(transaction: dict, current_bank_ledger: str, other_bank_ledgers: list, window_days: int) -> Optional[dict]:
    is_withdrawal = float(transaction.get('withdraw') or 0) > 0
    amount = float(transaction['withdraw']) if is_withdrawal else float(transaction['deposit'])
    date = transaction['date']

    try:
        target_date = datetime.datetime.strptime(date, "%Y%m%d")
    except (ValueError, TypeError):
        return None
    date_from = (target_date - datetime.timedelta(days=window_days)).strftime("%Y%m%d")
    date_to = (target_date + datetime.timedelta(days=window_days)).strftime("%Y%m%d")

    if is_withdrawal:
        # Need the OTHER bank's leg to show money ARRIVING there:
        # ISDEEMEDPOSITIVE=Yes (a debit entry), AMOUNT stored negative.
        sign_clause = "rle.is_deemed_positive = 1 AND rle.amount < 0"
    else:
        # Need the OTHER bank's leg to show money LEAVING there:
        # ISDEEMEDPOSITIVE=No (a credit entry), AMOUNT stored positive.
        sign_clause = "rle.is_deemed_positive = 0 AND rle.amount > 0"

    placeholders = ",".join("?" for _ in other_bank_ledgers)
    if not other_bank_ledgers:
        return None

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT rv.date, rv.narration, rle.ledger_name, rle.amount
            FROM reporting_vouchers rv
            JOIN reporting_ledger_entries rle ON rle.voucher_id = rv.id
            WHERE rv.voucher_type IN ('Payment', 'Receipt')
              AND rv.date >= ? AND rv.date <= ?
              AND rle.ledger_name IN ({placeholders})
              AND ABS(rle.amount) = ?
              AND {sign_clause}
        """, [date_from, date_to, *other_bank_ledgers, round(amount, 2)])
        row = cursor.fetchone()

    if not row:
        return None

    return {
        "source": "synced",
        "bank_ledger_name": row['ledger_name'],
        "date": row['date'],
        "amount": amount,
        "narration": row['narration'],
    }


def find_transfer_match(transaction: dict, current_bank_ledger: str, own_bank_ledgers: list, window_days: int = 2) -> Optional[dict]:
    """Looks for another own-bank-ledger transaction that's likely the other
    side of this one (an inter-account transfer), across both the local
    offline queue (not yet synced to Tally -- the actionable/mergeable case)
    and Tally-confirmed reporting data (informational only, never merged
    automatically). Returns None if nothing matches."""
    if float(transaction.get('withdraw') or 0) <= 0 and float(transaction.get('deposit') or 0) <= 0:
        return None

    other_bank_ledgers = [b for b in own_bank_ledgers if b != current_bank_ledger]
    if not other_bank_ledgers:
        return None

    match = _find_pending_match(transaction, current_bank_ledger, other_bank_ledgers, window_days)
    if not match:
        match = _find_synced_match(transaction, current_bank_ledger, other_bank_ledgers, window_days)

    if match:
        ref_a = extract_reference_number(transaction.get('raw_narration', ''))
        match['reference_match'] = bool(ref_a) and ref_a == extract_reference_number(match.get('narration', '') or '')

    return match
