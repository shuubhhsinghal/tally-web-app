from fastapi import APIRouter, Depends, Query, HTTPException, Request
from typing import Optional
from backend.database import get_db
from backend.services.tally_group_stock import fetch_stock_balances_from_db
from backend.utils.reporting_utils import check_data_completeness, get_previous_month_period, get_month_boundaries
from backend.services.auth_helpers import resolve_view_store_filter

router = APIRouter(prefix="/api/reporting/profit-loss", tags=["Reporting P&L"])

def calculate_pl_for_store(store: Optional[str], start_date: str, end_date: str, stock_balances: dict, db):
    # Store-specific logic
    if store == "Unallocated":
        cc_condition = "a.cost_centre_name IS NULL"
        store_params = ()
    elif store == "Combined":
        cc_condition = "1=1"
        store_params = ()
    else:
        cc_condition = "a.cost_centre_name = ?"
        store_params = (store,)
        
    def get_group_sum(group_name):
        query = f"""
            SELECT SUM(IFNULL(a.amount, e.amount))
            FROM reporting_ledger_entries e
            JOIN ledgers l ON e.ledger_name = l.name
            JOIN reporting_vouchers v ON e.voucher_id = v.id
            LEFT JOIN reporting_cost_centre_allocations a ON e.id = a.ledger_entry_id
            WHERE l.parent = ?
            AND v.date >= ? AND v.date <= ?
            AND {cc_condition}
        """
        params = (group_name, start_date, end_date) + store_params
        res = db.execute(query, params).fetchone()[0]
        return res if res is not None else 0.0

    raw_sales = get_group_sum('Sales Accounts')
    raw_purchases = get_group_sum('Purchase Accounts')
    raw_direct_exp = get_group_sum('Direct Expenses')
    raw_indirect_exp = get_group_sum('Indirect Expenses')

    sales = raw_sales
    purchases = -raw_purchases
    direct_expenses = -raw_direct_exp
    indirect_expenses = -raw_indirect_exp

    missing_stores = []

    if store in ["Mahagun", "Gulshan", "Vvip"]:
        store_key = store.lower()
        if store_key in stock_balances:
            op_stock = stock_balances[store_key]['opening']
            cl_stock = stock_balances[store_key]['closing']
        else:
            op_stock = None
            cl_stock = None
            missing_stores.append(store)
    elif store == "Combined":
        op_stocks = []
        cl_stocks = []
        for st in ["mahagun", "gulshan", "vvip"]:
            if st in stock_balances:
                op_stocks.append(stock_balances[st]['opening'])
                cl_stocks.append(stock_balances[st]['closing'])
            else:
                missing_stores.append(st.capitalize())
        
        if missing_stores:
            op_stock = None
            cl_stock = None
        else:
            op_stock = sum(op_stocks)
            cl_stock = sum(cl_stocks)
    else: # Unallocated has no stock
        op_stock = 0.0
        cl_stock = 0.0

    if op_stock is not None and cl_stock is not None:
        cogs = op_stock + purchases - cl_stock
        gross_profit = sales - cogs
    else:
        cogs = None
        gross_profit = None
    
    if gross_profit is not None:
        net_profit = gross_profit - direct_expenses - indirect_expenses
    else:
        net_profit = None

    return {
        "store": store,
        "missing_stores": missing_stores,
        "revenue": {
            "net_sales": sales
        },
        "cost_of_goods_sold": {
            "opening_stock": op_stock,
            "net_purchases": purchases,
            "closing_stock": cl_stock,
            "cogs": cogs
        },
        "gross_profit": gross_profit,
        "expenses": {
            "direct_expenses": direct_expenses,
            "indirect_expenses": indirect_expenses
        },
        "net_profit": net_profit
    }

@router.get("/")
def get_profit_loss(
    request: Request,
    start_month: str = Query(..., description="Start Month in YYYY-MM format"),
    end_month: str = Query(..., description="End Month in YYYY-MM format"),
    cost_centre: Optional[str] = Query(None, description="Specific Cost Centre")
):
    # A staff account is always forced to their own store's P&L -- omitting
    # cost_centre entirely would otherwise return every store's numbers
    # plus a company-wide "combined" figure (see the un-filtered branch
    # below), which is exactly the cross-store leak this exists to close.
    cost_centre = resolve_view_store_filter(request.state.user, cost_centre)

    start_date, _ = get_month_boundaries(start_month)
    _, end_date = get_month_boundaries(end_month)
    
    try:
        stock_balances = fetch_stock_balances_from_db(start_month, end_month)
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise HTTPException(status_code=400, detail="Something went wrong. Please try again.")
    
    prev_s_month, prev_e_month = get_previous_month_period(start_month, end_month)
    prev_start_date, _ = get_month_boundaries(prev_s_month)
    _, prev_end_date = get_month_boundaries(prev_e_month)
    
    try:
        prev_stock_balances = fetch_stock_balances_from_db(prev_s_month, prev_e_month)
        is_prev_stock_complete = True
    except Exception:
        prev_stock_balances = {}
        is_prev_stock_complete = False

    with get_db() as db:
        is_complete = check_data_completeness(start_date, end_date)
        is_prev_complete = check_data_completeness(prev_start_date, prev_end_date) and is_prev_stock_complete

        if cost_centre:
            current = calculate_pl_for_store(cost_centre, start_date, end_date, stock_balances, db)
            previous = calculate_pl_for_store(cost_centre, prev_start_date, prev_end_date, prev_stock_balances, db) if is_prev_complete else None
            return {
                "is_data_complete": is_complete,
                "period": {"start_month": start_month, "end_month": end_month},
                "previous_period": {"start_month": prev_s_month, "end_month": prev_e_month, "is_data_complete": is_prev_complete},
                "current": current,
                "previous": previous
            }
            
        return {
            "is_data_complete": is_complete,
            "period": {"start_month": start_month, "end_month": end_month},
            "previous_period": {"start_month": prev_s_month, "end_month": prev_e_month, "is_data_complete": is_prev_complete},
            "stores": [
                calculate_pl_for_store("Mahagun", start_date, end_date, stock_balances, db),
                calculate_pl_for_store("Gulshan", start_date, end_date, stock_balances, db),
                calculate_pl_for_store("Vvip", start_date, end_date, stock_balances, db),
            ],
            "unallocated": calculate_pl_for_store("Unallocated", start_date, end_date, stock_balances, db),
            "combined": calculate_pl_for_store("Combined", start_date, end_date, stock_balances, db),
            "stores_previous": [
                calculate_pl_for_store("Mahagun", prev_start_date, prev_end_date, prev_stock_balances, db),
                calculate_pl_for_store("Gulshan", prev_start_date, prev_end_date, prev_stock_balances, db),
                calculate_pl_for_store("Vvip", prev_start_date, prev_end_date, prev_stock_balances, db),
            ] if is_prev_complete else [],
            "unallocated_previous": calculate_pl_for_store("Unallocated", prev_start_date, prev_end_date, prev_stock_balances, db) if is_prev_complete else None,
            "combined_previous": calculate_pl_for_store("Combined", prev_start_date, prev_end_date, prev_stock_balances, db) if is_prev_complete else None
        }

