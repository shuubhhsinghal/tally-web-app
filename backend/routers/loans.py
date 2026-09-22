from typing import List
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request
import requests
from xml.sax.saxutils import escape
from backend.database import (
    queue_operation, MasterConflictException, get_master_dependency_state, is_master_pending_sync,
    set_delivery_uncertain, update_queue_status, queue_ledger_creation_with_opening_balance,
    create_loan, list_loans, get_loan_by_id, get_loan_detail, record_loan_payment, get_todays_total_loan_due,
    loan_ledger_name_exists,
)
from backend.services.tally_response import parse_tally_response
from backend.connector.transport import tally_transport
from backend.services.loan_interest_accrual import LOAN_LEDGER_PARENT, INTEREST_LEDGER_NAME, INTEREST_LEDGER_PARENT

router = APIRouter()

def _require_owner(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not user["is_owner"]:
        raise HTTPException(status_code=403, detail="Only the owner account can manage loans.")
    return user

def _unique_loan_ledger_name(lender_name: str, start_date: str) -> str:
    """A lender can have more than one loan running at once (a top-up, a
    fresh loan while an old one is still being paid off) -- each one needs
    its own Tally ledger, or the second loan would silently share the
    first's balance instead of being tracked separately."""
    base = f"Loan - {lender_name}"
    if not loan_ledger_name_exists(base):
        return base
    candidate = f"{base} ({start_date})"
    suffix = 2
    while loan_ledger_name_exists(candidate):
        candidate = f"{base} ({start_date}) #{suffix}"
        suffix += 1
    return candidate

async def _deliver_or_queue_voucher(queue_id: int, xml_data: str, ledger_names: list):
    """Shared tail end of posting any loan voucher (a single repayment, a
    combined multi-loan repayment, or -- in principle -- anything else loans
    posts): check every involved ledger is actually usable in Tally, then
    attempt immediate delivery with the same timeout/connectivity handling
    used across the app, falling back to the offline queue on anything
    ambiguous or unreachable."""
    for ledger_name in ledger_names:
        state, error_msg = get_master_dependency_state("LEDGER", ledger_name, None)
        if state == "FAILED":
            update_queue_status(queue_id, "FAILED", f"Cannot post repayment because ledger '{ledger_name}' failed to sync to Tally.")
            raise HTTPException(status_code=400, detail=f"Cannot post repayment because ledger '{ledger_name}' failed to sync to Tally. Resolve the master first.")
        if state == "MISSING":
            update_queue_status(queue_id, "FAILED", f"Cannot post repayment because ledger '{ledger_name}' is not available in Tally or pending sync.")
            raise HTTPException(status_code=400, detail=f"Cannot post repayment because ledger '{ledger_name}' is not available in Tally or pending sync. Create or refresh the master before posting.")
        if state == "CONFLICT":
            update_queue_status(queue_id, "FAILED", f"Definition conflict for ledger '{ledger_name}': {error_msg}")
            raise HTTPException(status_code=409, detail=f"Cannot post repayment due to definition conflict for ledger '{ledger_name}': {error_msg}")
        if is_master_pending_sync(state):
            return {"status": "queued", "reason": "pending_master_dependency", "message": f"Saved to offline queue because ledger '{ledger_name}' is still pending sync to Tally."}

    try:
        set_delivery_uncertain(queue_id, True)
        response = await tally_transport.post(xml_data.encode('utf-8'), timeout=10)
        parsed = parse_tally_response(response.text, "POST_VOUCHER")

        if not parsed["is_success"]:
            set_delivery_uncertain(queue_id, False)
            update_queue_status(queue_id, "FAILED", parsed['error_message'])
            raise HTTPException(status_code=400, detail=f"Tally rejected the entry: {parsed['error_message']}")

        set_delivery_uncertain(queue_id, False)
        update_queue_status(queue_id, "SYNCED")
        return {"status": "success", "message": "Payment posted successfully"}
    except requests.exceptions.ConnectTimeout:
        # The connection itself never established -- as safe as
        # ConnectionError, nothing was ever sent.
        set_delivery_uncertain(queue_id, False)
        return {"status": "queued", "message": "Saved to offline queue."}
    except requests.exceptions.Timeout:
        # Ambiguous: the request may have reached Tally before the response
        # was lost. Leave delivery_uncertain set (already persisted above) so
        # a manual retry is blocked until someone verifies in Tally.
        return {"status": "queued", "message": "Saved to offline queue."}
    except requests.exceptions.ConnectionError:
        set_delivery_uncertain(queue_id, False)
        return {"status": "queued", "message": "Saved to offline queue."}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Unexpected error posting loan repayment: {e}")
        update_queue_status(queue_id, "FAILED", str(e))
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")


@router.get("")
def get_loans(request: Request):
    _require_owner(request)
    return {"loans": list_loans(), "today_total_due": get_todays_total_loan_due()}

@router.get("/{loan_id}")
def get_loan(loan_id: int, request: Request):
    _require_owner(request)
    loan = get_loan_detail(loan_id)
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found.")
    return loan


class CreateLoanRequest(BaseModel):
    lender_name: str
    principal_amount: float
    total_repayment_amount: float
    daily_amount: float
    start_date: str  # YYYY-MM-DD

@router.post("")
def create_loan_endpoint(payload: CreateLoanRequest, request: Request):
    current_user = _require_owner(request)

    if payload.principal_amount <= 0 or payload.total_repayment_amount <= 0 or payload.daily_amount <= 0:
        raise HTTPException(status_code=400, detail="Amounts must be greater than zero.")
    if payload.total_repayment_amount < payload.principal_amount:
        raise HTTPException(status_code=400, detail="Total repayment amount can't be less than the principal.")
    lender_name = payload.lender_name.strip()
    if not lender_name:
        raise HTTPException(status_code=400, detail="Lender name is required.")

    ledger_name = _unique_loan_ledger_name(lender_name, payload.start_date)
    try:
        queue_ledger_creation_with_opening_balance(ledger_name, LOAN_LEDGER_PARENT, opening_balance=payload.principal_amount)
    except MasterConflictException as e:
        raise HTTPException(status_code=409, detail=str(e))

    return create_loan(
        lender_name=lender_name,
        ledger_name=ledger_name,
        principal_amount=payload.principal_amount,
        total_repayment_amount=payload.total_repayment_amount,
        daily_amount=payload.daily_amount,
        start_date=payload.start_date,
        created_by=current_user['name'],
    )


class RepayLoanRequest(BaseModel):
    amount: float
    date: str  # YYYY-MM-DD
    paid_from: str  # bank/cash ledger name

@router.post("/{loan_id}/repay")
async def repay_loan(loan_id: int, payload: RepayLoanRequest, request: Request):
    """The one and only way to record a loan payment: pick the loan, enter
    the amount actually paid, hit Push to Tally -- it posts a plain two-line
    voucher (reduce the loan ledger, reduce the bank/cash ledger) for exactly
    what was paid, no matter whether it's on time, late, short, or extra.
    Interest is handled separately, accruing on its own monthly schedule (see
    backend/services/loan_interest_accrual.py) rather than being split out of
    each payment, so this stays correct however irregular the real payment
    history is."""
    current_user = _require_owner(request)

    loan = get_loan_by_id(loan_id)
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found.")
    if loan['status'] != 'active':
        raise HTTPException(status_code=400, detail="This loan is already closed.")
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero.")

    safe_loan_ledger = escape(loan['ledger_name'])
    safe_paid_from = escape(payload.paid_from)
    tally_date = payload.date.replace("-", "")
    narration = escape(f"Loan repayment to {loan['lender_name']}")

    xml_data = f"""<ENVELOPE>
  <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
  <BODY><IMPORTDATA>
    <REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC>
    <REQUESTDATA><TALLYMESSAGE>
      <VOUCHER VCHTYPE="Payment" ACTION="Create">
        <DATE>{tally_date}</DATE>
        <VOUCHERTYPENAME>Payment</VOUCHERTYPENAME>
        <NARRATION>{narration}</NARRATION>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{safe_loan_ledger}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          <AMOUNT>-{payload.amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{safe_paid_from}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          <AMOUNT>{payload.amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
      </VOUCHER>
    </TALLYMESSAGE></REQUESTDATA>
  </IMPORTDATA></BODY>
</ENVELOPE>"""

    queue_payload = payload.model_dump()
    queue_payload['created_by'] = current_user['name']
    queue_payload['loan_id'] = loan_id
    queue_id = queue_operation("POST_VOUCHER", xml_data, queue_payload, f"Loan Repayment: {payload.amount} to {loan['lender_name']}")
    record_loan_payment(loan_id, payload.date, payload.amount, queue_id, current_user['name'])

    return await _deliver_or_queue_voucher(queue_id, xml_data, [loan['ledger_name'], payload.paid_from])


class LoanAllocation(BaseModel):
    loan_id: int
    amount: float

class CombinedRepayRequest(BaseModel):
    lender_name: str
    date: str  # YYYY-MM-DD
    paid_from: str
    allocations: List[LoanAllocation]

@router.post("/repay-combined")
async def repay_loans_combined(payload: CombinedRepayRequest, request: Request):
    """For a lender with more than one loan open at once: one amount you
    actually handed over, split across that lender's loans however you say
    -- posted as a single voucher (one debit line per loan, one credit for
    the bank/cash ledger), matching that it was really one handover of
    cash, not several separate ones."""
    current_user = _require_owner(request)

    if not payload.allocations:
        raise HTTPException(status_code=400, detail="At least one allocation is required.")

    loans_by_id = {}
    for alloc in payload.allocations:
        if alloc.amount <= 0:
            raise HTTPException(status_code=400, detail="Each allocation amount must be greater than zero.")
        loan = get_loan_by_id(alloc.loan_id)
        if not loan:
            raise HTTPException(status_code=404, detail=f"Loan {alloc.loan_id} not found.")
        if loan['lender_name'] != payload.lender_name:
            raise HTTPException(status_code=400, detail=f"Loan {alloc.loan_id} does not belong to {payload.lender_name}.")
        if loan['status'] != 'active':
            raise HTTPException(status_code=400, detail=f"Loan {alloc.loan_id} is already closed.")
        loans_by_id[alloc.loan_id] = loan

    total_amount = round(sum(a.amount for a in payload.allocations), 2)
    tally_date = payload.date.replace("-", "")
    narration = escape(f"Loan repayment to {payload.lender_name}")

    debit_lines = "".join(f"""
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{escape(loans_by_id[alloc.loan_id]['ledger_name'])}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          <AMOUNT>-{alloc.amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>""" for alloc in payload.allocations)

    xml_data = f"""<ENVELOPE>
  <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
  <BODY><IMPORTDATA>
    <REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC>
    <REQUESTDATA><TALLYMESSAGE>
      <VOUCHER VCHTYPE="Payment" ACTION="Create">
        <DATE>{tally_date}</DATE>
        <VOUCHERTYPENAME>Payment</VOUCHERTYPENAME>
        <NARRATION>{narration}</NARRATION>{debit_lines}
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{escape(payload.paid_from)}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          <AMOUNT>{total_amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
      </VOUCHER>
    </TALLYMESSAGE></REQUESTDATA>
  </IMPORTDATA></BODY>
</ENVELOPE>"""

    queue_payload = payload.model_dump()
    queue_payload['created_by'] = current_user['name']
    queue_id = queue_operation("POST_VOUCHER", xml_data, queue_payload, f"Loan Repayment: {total_amount} to {payload.lender_name}")
    for alloc in payload.allocations:
        record_loan_payment(alloc.loan_id, payload.date, alloc.amount, queue_id, current_user['name'])

    ledger_names = [loans_by_id[alloc.loan_id]['ledger_name'] for alloc in payload.allocations] + [payload.paid_from]
    return await _deliver_or_queue_voucher(queue_id, xml_data, ledger_names)
