"""Posts a loan's accrued interest as its own voucher, once per completed
calendar month, decoupled from whatever the borrower actually paid that
month. See compute_pending_interest_accruals in backend/database.py for the
math; this module turns each pending period into a real Tally voucher."""

from datetime import date
from xml.sax.saxutils import escape
from backend.database import (
    queue_operation, queue_ledger_creation_with_opening_balance, MasterConflictException,
    get_active_loans_for_interest_accrual, compute_pending_interest_accruals, record_interest_accrual,
)

# Loans are business-wide liabilities, not tied to a store.
LOAN_LEDGER_PARENT = "Loans (Liability)"
# One shared expense ledger across every loan -- keeps the P&L to a single,
# clean "Loan Interest" line rather than one per lender.
INTEREST_LEDGER_NAME = "Loan Interest"
INTEREST_LEDGER_PARENT = "Indirect Expenses"


def _build_accrual_voucher_xml(loan: dict, period_end: str, amount: float) -> str:
    safe_loan_ledger = escape(loan['ledger_name'])
    safe_interest_ledger = escape(INTEREST_LEDGER_NAME)
    tally_date = period_end.replace("-", "")
    narration = escape(f"Interest accrued to {loan['lender_name']} for {period_end[:7]}")
    return f"""<ENVELOPE>
  <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
  <BODY><IMPORTDATA>
    <REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC>
    <REQUESTDATA><TALLYMESSAGE>
      <VOUCHER VCHTYPE="Journal" ACTION="Create">
        <DATE>{tally_date}</DATE>
        <VOUCHERTYPENAME>Journal</VOUCHERTYPENAME>
        <NARRATION>{narration}</NARRATION>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{safe_interest_ledger}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
          <AMOUNT>-{amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{safe_loan_ledger}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          <AMOUNT>{amount}</AMOUNT>
        </ALLLEDGERENTRIES.LIST>
      </VOUCHER>
    </TALLYMESSAGE></REQUESTDATA>
  </IMPORTDATA></BODY>
</ENVELOPE>"""


def post_pending_interest_accruals(as_of: date = None) -> int:
    """Queues one voucher per loan per fully-completed calendar month that
    hasn't been accrued yet. Queue-first, same as every other write in this
    app -- actual delivery to Tally happens via the existing offline-queue
    flush (flush_offline_queue), not inline here, since this runs from the
    background sync loop with no request/response cycle to report back to.
    Returns how many accrual vouchers were queued, for logging."""
    as_of = as_of or date.today()

    try:
        queue_ledger_creation_with_opening_balance(INTEREST_LEDGER_NAME, INTEREST_LEDGER_PARENT)
    except MasterConflictException:
        # A real definition conflict on the shared ledger needs a human to
        # resolve it in Masters -- don't let it block other loans' accruals.
        pass

    posted = 0
    for loan in get_active_loans_for_interest_accrual():
        for period in compute_pending_interest_accruals(loan, as_of):
            xml_data = _build_accrual_voucher_xml(loan, period['period_end'], period['amount'])
            queue_id = queue_operation(
                "POST_VOUCHER", xml_data,
                {"loan_id": loan['id'], "ledger_name": loan['ledger_name'], **period},
                f"Loan Interest: {period['amount']} for {loan['lender_name']} ({period['period_start']} to {period['period_end']})",
            )
            record_interest_accrual(loan['id'], period['period_start'], period['period_end'], period['amount'], queue_id)
            posted += 1
    return posted
