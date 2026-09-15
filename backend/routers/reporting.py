from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from backend.services.tally_reporting_sync import async_sync_cost_centres, async_sync_vouchers
from backend.services.reporting_sales_service import calculate_sales, get_sales_trend, get_store_comparisons
from backend.services.reporting_purchase_service import calculate_purchases, get_purchases_trend, get_store_comparisons as get_purchase_store_comparisons, get_item_purchase_analysis
from backend.utils.reporting_utils import check_data_completeness, get_previous_period
from backend.database import get_db

router = APIRouter(prefix="/reporting", tags=["Reporting"])

@router.post("/sync/cost-centres")
async def sync_cost_centres():
    try:
        count = await async_sync_cost_centres()
        return {"status": "success", "count": count, "message": f"Successfully synced {count} cost centres"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sync/vouchers")
async def sync_vouchers(start_date: Optional[str] = Query(None, description="Format: YYYYMMDD"), end_date: Optional[str] = Query(None, description="Format: YYYYMMDD")):
    try:
        res = await async_sync_vouchers(start_date, end_date)
        count = res["vouchers_count"]
        failed_months = res["failed_months"]
        
        msg = f"Successfully synced {count} vouchers."
        if failed_months:
            msg += f" WARNING: Stock sync failed for months: {', '.join(failed_months)}."
            
        return {
            "status": "success", 
            "count": count, 
            "failed_months": failed_months,
            "message": msg
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/inspect/cost-centres")
async def inspect_cost_centres():
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM cost_centres")
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/inspect/vouchers")
async def inspect_vouchers(limit: int = 100, offset: int = 0):
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM reporting_vouchers ORDER BY date DESC, id DESC LIMIT ? OFFSET ?", (limit, offset))
            vouchers = [dict(row) for row in cursor.fetchall()]
            
            for vch in vouchers:
                cursor.execute("SELECT * FROM reporting_ledger_entries WHERE voucher_id = ?", (vch['id'],))
                ledgers = [dict(row) for row in cursor.fetchall()]
                
                for ledg in ledgers:
                    cursor.execute("SELECT * FROM reporting_cost_centre_allocations WHERE ledger_entry_id = ?", (ledg['id'],))
                    ledg['cost_centre_allocations'] = [dict(row) for row in cursor.fetchall()]
                    
                vch['ledger_entries'] = ledgers
                
                cursor.execute("SELECT * FROM reporting_inventory_entries WHERE voucher_id = ?", (vch['id'],))
                vch['inventory_entries'] = [dict(row) for row in cursor.fetchall()]
                
            return vouchers
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/inspect/counts")
async def inspect_counts():
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as c FROM cost_centres")
            cc_count = cursor.fetchone()['c']
            cursor.execute("SELECT COUNT(*) as c FROM reporting_vouchers")
            vch_count = cursor.fetchone()['c']
            cursor.execute("SELECT COUNT(*) as c FROM reporting_ledger_entries")
            ledg_count = cursor.fetchone()['c']
            cursor.execute("SELECT COUNT(*) as c FROM reporting_inventory_entries")
            inv_count = cursor.fetchone()['c']
            
            return {
                "cost_centres": cc_count,
                "vouchers": vch_count,
                "ledger_entries": ledg_count,
                "inventory_entries": inv_count
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sales")
async def get_sales_report(
    start_date: str = Query(..., description="Format: YYYYMMDD"),
    end_date: str = Query(..., description="Format: YYYYMMDD"),
    cost_centre: Optional[str] = Query(None, description="Filter by cost centre name or 'Unallocated', or None for Combined")
):
    try:
        is_complete = check_data_completeness(start_date, end_date)
        
        prev_s, prev_e = get_previous_period(start_date, end_date)
        is_prev_complete = check_data_completeness(prev_s, prev_e)
        
        net_sales = calculate_sales(start_date, end_date, cost_centre)
        
        if is_prev_complete:
            prev_net_sales = calculate_sales(prev_s, prev_e, cost_centre)
            change_amount = net_sales - prev_net_sales
            change_pct = (change_amount / prev_net_sales * 100) if prev_net_sales != 0 else (100.0 if net_sales > 0 else 0.0)
        else:
            prev_net_sales = None
            change_amount = None
            change_pct = None
        
        trend = get_sales_trend(start_date, end_date, cost_centre)
        store_comparison = get_store_comparisons(start_date, end_date)
        
        return {
            "is_data_complete": is_complete,
            "period": {"start": start_date, "end": end_date},
            "previous_period": {"start": prev_s, "end": prev_e, "is_data_complete": is_prev_complete},
            "summary": {
                "net_sales": round(net_sales, 2),
                "previous_net_sales": round(prev_net_sales, 2) if prev_net_sales is not None else None,
                "change_amount": round(change_amount, 2) if change_amount is not None else None,
                "change_percentage": round(change_pct, 2) if change_pct is not None else None
            },
            "trend": trend,
            "store_comparison": store_comparison
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/purchases")
def get_purchases_report(start_date: str = Query(...), end_date: str = Query(...), cost_centre: Optional[str] = None, page: int = Query(1), limit: int = Query(50), search: str = Query(""), sort_by: str = Query("value_desc")):
    try:
        is_complete = check_data_completeness(start_date, end_date)
        
        prev_s, prev_e = get_previous_period(start_date, end_date)
        is_prev_complete = check_data_completeness(prev_s, prev_e)
        
        net_purchases = calculate_purchases(start_date, end_date, cost_centre)
        
        if is_prev_complete:
            prev_net_purchases = calculate_purchases(prev_s, prev_e, cost_centre)
            change_amount = net_purchases - prev_net_purchases
            change_pct = (change_amount / prev_net_purchases * 100) if prev_net_purchases != 0 else (100.0 if net_purchases > 0 else 0.0)
        else:
            prev_net_purchases = None
            change_amount = None
            change_pct = None
        
        trend = get_purchases_trend(start_date, end_date, cost_centre)
        store_comparison = get_purchase_store_comparisons(start_date, end_date)
        
        return {
            "is_data_complete": is_complete,
            "period": {"start": start_date, "end": end_date},
            "previous_period": {"start": prev_s, "end": prev_e, "is_data_complete": is_prev_complete},
            "summary": {
                "net_purchases": round(net_purchases, 2),
                "previous_purchases": round(prev_net_purchases, 2) if prev_net_purchases is not None else None,
                "change_amount": round(change_amount, 2) if change_amount is not None else None,
                "change_pct": round(change_pct, 2) if change_pct is not None else None
            },
            "trend": trend,
            "store_comparison": store_comparison
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/purchases/suppliers")
def get_purchase_suppliers_report(
    start_date: str = Query(...),
    end_date: str = Query(...),
    cost_centre: str = Query(None),
    page: int = Query(1),
    limit: int = Query(50),
    search: str = Query(""),
    sort_by: str = Query("value_desc")
):
    from backend.services.reporting_purchase_service import get_supplier_purchase_analysis
    return get_supplier_purchase_analysis(start_date, end_date, page, limit, search, sort_by, cost_centre)

@router.get("/purchases/bills")
def get_purchase_bills_report(
    start_date: str = Query(...),
    end_date: str = Query(...),
    cost_centre: str = Query(None),
    supplier_name: str = Query(None),
    page: int = Query(1),
    limit: int = Query(50),
    sort_by: str = Query("date_desc")
):
    from backend.services.reporting_purchase_service import get_purchase_bills
    return get_purchase_bills(start_date, end_date, cost_centre, supplier_name, page, limit, sort_by)

@router.get("/purchases/bills/{voucher_id}")
def get_purchase_bill_details_report(voucher_id: int):
    from backend.services.reporting_purchase_service import get_bill_details
    from fastapi import HTTPException
    res = get_bill_details(voucher_id)
    if not res:
        raise HTTPException(404, "Bill not found")
    return res

@router.get("/sales/bills")
def get_sales_bills_report(
    start_date: str = Query(...),
    end_date: str = Query(...),
    cost_centre: str = Query(None),
    page: int = Query(1),
    limit: int = Query(50),
    sort_by: str = Query("date_desc")
):
    from backend.services.reporting_sales_service import get_sales_bills
    return get_sales_bills(start_date, end_date, cost_centre, page, limit, sort_by)

@router.get("/sales/bills/{voucher_id}")
def get_sales_bill_details_report(voucher_id: int):
    from backend.services.reporting_sales_service import get_sales_bill_details
    from fastapi import HTTPException
    res = get_sales_bill_details(voucher_id)
    if not res:
        raise HTTPException(404, "Bill not found")
    return res

@router.get("/creditors")
def get_creditors_report(
    start_date: str = Query(...),
    end_date: str = Query(...)
):
    from backend.services.reporting_creditors_service import get_all_creditors_overview
    return get_all_creditors_overview(start_date, end_date)

@router.get("/creditors/{supplier_name}")
def get_creditor_ledger_report(
    supplier_name: str,
    start_date: str = Query(...),
    end_date: str = Query(...)
):
    from backend.services.reporting_creditors_service import get_creditor_ledger_movements
    return get_creditor_ledger_movements(supplier_name, start_date, end_date)

@router.get("/vouchers/{voucher_id}")
def get_generic_voucher_details(voucher_id: int):
    from backend.services.reporting_vouchers_service import get_voucher_details
    from fastapi import HTTPException
    res = get_voucher_details(voucher_id)
    if not res:
        raise HTTPException(404, "Voucher not found")
    return res

@router.get("/daybook")
def get_daybook_report(
    start_date: str = Query(...),
    end_date: str = Query(...),
    cost_centre: Optional[str] = Query(None),
    page: int = Query(1),
    limit: int = Query(50),
    search: str = Query(""),
    sort_by: str = Query("date_desc")
):
    from backend.services.reporting_daybook_service import get_daybook
    return get_daybook(start_date, end_date, cost_centre, page, limit, search, sort_by)
