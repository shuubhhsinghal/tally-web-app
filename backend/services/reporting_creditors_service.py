from backend.database import get_db
from backend.utils.reporting_utils import get_financial_year

def get_creditor_ledger_movements(supplier_name: str, start_date: str, end_date: str):
    """
    Returns the opening balance for the period, the closing balance,
    and a chronological list of all ledger movements within the period.
    """
    start_date = start_date.replace('-', '')
    end_date = end_date.replace('-', '')
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        # 1. Master Opening Balance
        cursor.execute("SELECT opening_balance FROM ledgers WHERE name = ?", (supplier_name,))
        row = cursor.fetchone()
        master_ob = row['opening_balance'] if row else 0.0
        
        # 2. Movements before start_date but after FY start
        fy_start, _ = get_financial_year(start_date)
        cursor.execute("""
            SELECT SUM(rle.amount) as pre_movement
            FROM reporting_ledger_entries rle
            JOIN reporting_vouchers rv ON rle.voucher_id = rv.id
            WHERE rle.ledger_name = ? AND rv.date >= ? AND rv.date < ?
        """, (supplier_name, fy_start, start_date))
        pre_row = cursor.fetchone()
        pre_movement = pre_row['pre_movement'] if pre_row and pre_row['pre_movement'] else 0.0
        
        period_opening = master_ob + pre_movement
        
        # 3. Movements inside the period
        cursor.execute("""
            SELECT rv.date, rv.voucher_type, rv.voucher_number, rv.reference, rle.amount, rle.is_deemed_positive, rv.id as voucher_id
            FROM reporting_ledger_entries rle
            JOIN reporting_vouchers rv ON rle.voucher_id = rv.id
            WHERE rle.ledger_name = ? AND rv.date >= ? AND rv.date <= ?
            ORDER BY rv.date ASC, rv.id ASC
        """, (supplier_name, start_date, end_date))
        
        movements = [dict(r) for r in cursor.fetchall()]
        
        period_movement = sum(m['amount'] for m in movements)
        period_closing = period_opening + period_movement
        
        # Summarize for overview classification
        purchases = sum(m['amount'] for m in movements if m['voucher_type'] == 'Purchase' and m['amount'] > 0)
        returns = sum(abs(m['amount']) for m in movements if m['voucher_type'] in ('Debit Note', 'Credit Note', 'Purchase Return') and m['amount'] < 0)
        payments = sum(abs(m['amount']) for m in movements if m['voucher_type'] == 'Payment' and m['amount'] < 0)
        
        # Any other movement
        classified_total = sum(m['amount'] for m in movements if m['voucher_type'] == 'Purchase' and m['amount'] > 0) \
                           - returns - payments
        
        other_adjustments = period_movement - classified_total
        
        return {
            "period_opening": period_opening,
            "period_closing": period_closing,
            "movements": movements,
            "summary": {
                "purchases": purchases,
                "returns": returns,
                "payments": payments,
                "other_adjustments": other_adjustments,
                "net_movement": period_movement
            }
        }

def get_all_creditors_overview(start_date: str, end_date: str):
    """
    Returns the overview (opening, purchases, payments, returns, adjustments, closing)
    for all Sundry Creditors in the system.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        
        # We only care about ledgers under Sundry Creditors group.
        # parent LIKE '%Creditor%' is a naive way, but Tally allows nested groups under Sundry Creditors.
        # We can fetch all ledgers with parent 'Sundry Creditors'.
        cursor.execute("SELECT name, opening_balance FROM ledgers WHERE parent = 'Sundry Creditors'")
        creditors = [dict(r) for r in cursor.fetchall()]
        
        results = []
        for c in creditors:
            # We can reuse the detailed function for simplicity. If performance is an issue,
            # this can be optimized into a single GROUP BY SQL query later.
            movements = get_creditor_ledger_movements(c['name'], start_date, end_date)
            results.append({
                "supplier_name": c['name'],
                "period_opening": movements['period_opening'],
                "purchases": movements['summary']['purchases'],
                "returns": movements['summary']['returns'],
                "payments": movements['summary']['payments'],
                "other_adjustments": movements['summary']['other_adjustments'],
                "period_closing": movements['period_closing']
            })
            
        return results
