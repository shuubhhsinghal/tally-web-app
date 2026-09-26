from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request
import requests
from xml.sax.saxutils import escape
from backend.database import (
    queue_operation, MasterConflictException, get_master_dependency_state, is_master_pending_sync,
    set_delivery_uncertain, update_queue_status, queue_ledger_creation_with_opening_balance,
    create_loan, list_loans, get_todays_total_loan_due,
)
from backend.services.tally_response import parse_tally_response
from backend.connector.transport import tally_transport
from backend.services.loan_interest_accrual import LOAN_LEDGER_PARENT

router = APIRouter()

def _require_owner(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not user["is_owner"]:
        raise HTTPException(status_code=403, detail="Only the owner account can manage loans.")
    return user

async def _deliver_or_queue_voucher(queue_id: int, xml_data: str, ledger_names: list):
    """Shared tail end of posting a loan voucher: check every involved ledger
    is actually usable in Tally, then attempt immediate delivery with the
    same timeout/connectivity handling used across the app, falling back to
    the offline queue on anything ambiguous or unreachable."""
    for ledger_name in ledger_names:
        state, error_msg = get_master_dependency_state("LEDGER", ledger_name, None)
        if state == "FAILED":
            update_queue_status(queue_id, "FAILED", f"Cannot post because ledger '{ledger_name}' failed to sync to Tally.")
            raise HTTPException(status_code=400, detail=f"Cannot post because ledger '{ledger_name}' failed to sync to Tally. Resolve the master first.")
        if state == "MISSING":
            update_queue_status(queue_id, "FAILED", f"Cannot post because ledger '{ledger_name}' is not available in Tally or pending sync.")
            raise HTTPException(status_code=400, detail=f"Cannot post because ledger '{ledger_name}' is not available in Tally or pending sync. Create or refresh the master before posting.")
        if state == "CONFLICT":
            update_queue_status(queue_id, "FAILED", f"Definition conflict for ledger '{ledger_name}': {error_msg}")
            raise HTTPException(status_code=409, detail=f"Cannot post due to definition conflict for ledger '{ledger_name}': {error_msg}")
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
        return {"status": "success", "message": "Loan posted successfully"}
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
        print(f"Unexpected error posting loan: {e}")
        update_queue_status(queue_id, "FAILED", str(e))
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.")


@router.get("")
def get_loans(request: Request):
    _require_owner(request)
    return {"lenders": list_loans(), "today_total_due": get_todays_total_loan_due()}


class CreateLoanRequest(BaseModel):
    lender_name: str
    principal_amount: float
    daily_amount: float
    number_of_days: int
    start_date: str  # YYYY-MM-DD
    received_into_ledger: str  # bank/cash ledger the money landed in

@router.post("")
async def create_loan_endpoint(payload: CreateLoanRequest, request: Request):
    """Taking a new loan is a real transaction: the money actually lands in
    a bank/cash account, and the lender's liability goes up by the same
    amount. This is the only entry point into the Loans feature -- an
    existing outstanding balance with a lender (e.g. one you're carrying in
    from before you started using this app) is set directly in Tally as that
    lender ledger's opening balance instead, since no cash actually arrives
    the day you start tracking it here; this endpoint would incorrectly show
    that as fresh money received today."""
    current_user = _require_owner(request)

    if payload.principal_amount <= 0 or payload.daily_amount <= 0 or payload.number_of_days <= 0:
        raise HTTPException(status_code=400, detail="Amounts and number of days must be greater than zero.")
    lender_name = payload.lender_name.strip()
    if not lender_name:
        raise HTTPException(status_code=400, detail="Lender name is required.")
    received_into = payload.received_into_ledger.strip()
    if not received_into:
        raise HTTPException(status_code=400, detail="Select the account this loan was received into.")

    total_repayment_amount = round(payload.daily_amount * payload.number_of_days, 2)
    # One ledger per lender, not per loan -- a second loan from a lender who
    # already has one just posts another entry against their existing
    # ledger, the same way a real running account with them would work.
    ledger_name = f"Loan - {lender_name}"
    try:
        queue_ledger_creation_with_opening_balance(ledger_name, LOAN_LEDGER_PARENT)
    except MasterConflictException as e:
        raise HTTPException(status_code=409, detail=str(e))

    tally_date = payload.start_date.replace("-", "")
    narration = escape(f"Loan received from {lender_name}")
    xml_data = f"""<ENVELOPE>
  <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
  <BODY><IMPORTDATA>
    <REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC>
    <REQUESTDATA><TALLYMESSAGE>
      <VOUCHER VCHTYPE="Receipt" ACTION="Create">
        <DATE>{tally_date}</DATE>
        <VOUCHERTYPENAME>Receipt</VOUCHERTYPENAME>
        <NARRATION>{narration}</NARRATION>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{escape(received_into)}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          <AMOUNT>-{payload.principal_amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{escape(ledger_name)}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          <AMOUNT>{payload.principal_amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
      </VOUCHER>
    </TALLYMESSAGE></REQUESTDATA>
  </IMPORTDATA></BODY>
</ENVELOPE>"""

    queue_payload = payload.model_dump()
    queue_payload['created_by'] = current_user['name']
    queue_payload['ledger_name'] = ledger_name
    queue_id = queue_operation("POST_VOUCHER", xml_data, queue_payload, f"Loan Received: {payload.principal_amount} from {lender_name}")

    # Deliberately attempted BEFORE create_loan below: _deliver_or_queue_voucher
    # raises an HTTPException on an outright failure (a rejected voucher or a
    # failed/missing/conflicting ledger dependency), which must stop execution
    # here and never reach create_loan. Otherwise a rejected receipt voucher
    # would still leave a loan row behind -- invisible to the user (the
    # frontend only shows the error and never refreshes the list on failure),
    # but very much visible to the interest-accrual background job, which
    # would start posting real monthly interest against a loan whose
    # principal was never actually received in Tally.
    delivery_result = await _deliver_or_queue_voucher(queue_id, xml_data, [received_into, ledger_name])

    loan = create_loan(
        lender_name=lender_name,
        ledger_name=ledger_name,
        principal_amount=payload.principal_amount,
        total_repayment_amount=total_repayment_amount,
        daily_amount=payload.daily_amount,
        start_date=payload.start_date,
        received_into_ledger=received_into,
        created_by=current_user['name'],
        queue_id=queue_id,
    )

    return {**delivery_result, "loan": loan}
