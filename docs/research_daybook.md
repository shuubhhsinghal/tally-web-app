# Daybook Research Report

## A. Voucher Types Found
The existing reporting database contains the following voucher types (Total 32 vouchers):
- **Contra**: 3 (e.g., #8, #7, #9)
- **Payment**: 10 (e.g., #104, #105, #106)
- **Purchase**: 11 (e.g., #52, #53, #58)
- **Receipt**: 2 (e.g., #34, #35)
- **Sales**: 6 (e.g., #27, #28, #29)
*(Note: Journal, Debit Note, and Credit Note are standard Tally types but do not currently exist in the active dataset).*

## B. Database Structure by Voucher Type
Tally's double-entry accounting is represented in `reporting_ledger_entries`. 
**Accounting Rule:** `amount < 0` (or `is_deemed_positive = 1`) = **DEBIT**. `amount > 0` (or `is_deemed_positive = 0`) = **CREDIT**.

- **Contra**
  - **Debit**: The receiving cash/bank ledger (e.g., `cash mahagun`, `-1000.0`).
  - **Credit**: The paying cash/bank ledger (e.g., `cash gulshan`, `1000.0`).
- **Payment**
  - **Debit**: The party or expense ledger (e.g., `Bank Suspense Account`, `-4000.0`).
  - **Credit**: The bank ledger (e.g., `union bank mahagun 133`, `4000.0`).
- **Purchase**
  - **Debit**: The purchase account (`-4169.95`) and tax ledgers (CGST/SGST, `-104.26`).
  - **Credit**: The supplier party ledger (`Sowmya Trading Company`, `4378.47`).
  - **Inventory**: Present, mapped to the stock items purchased.
- **Receipt**
  - **Debit**: The bank ledger (e.g., `union bank mahagun 133`, `-5000.0`).
  - **Credit**: The party or income ledger (e.g., `Bank Suspense Account`, `5000.0`).
- **Sales**
  - **Debit**: The cash/bank/debtor ledger (`cash mahagun`, `-29292.0`).
  - **Credit**: The sales account (`sales`, `29292.0`).

## C. Cost Centre Behavior
Cost Centre allocations are stored in `reporting_cost_centre_allocations` and are attached to specific `ledger_entry_id`s, **not** the voucher as a whole.
- **Sales**: Allocations are attached to the **Credit** ledger entry (the `sales` account).
- **Purchase**: Allocations are attached to the **Debit** ledger entry (the `purchase` account).
- **Payments/Receipts**: Typically unallocated, though they could be attached to expense/income ledgers if configured in Tally.
- **Multiple Allocations**: Yes. A single Purchase voucher can have multiple Cost Centres if the bill is split (e.g., Purchase #53 has 4 items, yielding 4 separate Cost Centre allocation rows for "Mahagun").
- **No Allocations**: Yes. Payments, Receipts, and Contras currently have no allocations.

## D. Correct Daybook Amount Logic
The Daybook must show **one summarized voucher-level amount**, NOT individual ledger entries. 
If we display individual ledger entries, a single Purchase voucher with 5 items and 2 taxes would appear 8 times, massively inflating the visual Daybook total.
**Calculation**: The voucher's total amount is exactly `SUM(abs(amount))` of all DEBIT entries (where `is_deemed_positive = 1`). Total Debits will always equal Total Credits.

## E. Existing Drill-Down Capability
The generic `GET /api/reporting/vouchers/{voucher_id}` endpoint in `reporting_vouchers_service.py` is **insufficient** for a full Daybook drill-down:
1. It completely omits `reporting_cost_centre_allocations`.
2. It omits the `narration` field from `reporting_vouchers` in the SQL `SELECT` statement.
3. It omits `tally_guid`.

## F. Missing Data/Fields in SQLite Sync
The current Tally XML Sync captures the core accounting, but is missing these fields which are crucial for a Daybook:
1. **Reference Number & Date**: Extremely important for Purchases to show the Supplier's Bill Number.
2. **Cheque Number/Instrument Details**: Missing for Payments/Receipts.
3. **Effective Date**: Missing (only `date` is captured).

## G. Tally-vs-Database Validation
The SQLite structure perfectly matches Tally's `Accounting Voucher View` export XML (`<LEDGERENTRIES.LIST>`). 
- Tally's `amount` sign convention is precisely mirrored in the database.
- Tally's `<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>` maps exactly to `is_deemed_positive = 1` (Debit).

## H. Recommended Minimal Implementation Changes
1. **Update `reporting_vouchers_service.py`**: Modify the generic drill-down endpoint to include Cost Centre allocations and `narration`.
2. **Daybook Endpoint (`GET /api/reporting/daybook`)**: Create a new endpoint that joins `reporting_vouchers`, summarizes the total Debit amount, and LEFT JOINs the relevant Cost Centre allocation (handling multiple by grouping or marking as "Multiple").
3. **Enhance XML Sync (Optional but Recommended)**: Add `<FETCH>Reference, ReferenceDate</FETCH>` to the `VoucherCollection` payload in `tally_reporting_sync.py` to capture supplier bill numbers.
