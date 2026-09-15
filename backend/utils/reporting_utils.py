from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from backend.database import get_db

def parse_date(date_str: str) -> datetime:
    # Tally dates are often YYYYMMDD, but could be YYYY-MM-DD
    date_str = date_str.replace("-", "")
    return datetime.strptime(date_str, "%Y%m%d")

def format_date(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")

def check_data_completeness(start_date: str, end_date: str) -> bool:
    """
    Checks if the requested date range is fully covered by successful syncs.
    We just check if any single sync range completely covers the requested range.
    A more advanced version would merge overlapping intervals.
    """
    s_dt = parse_date(start_date)
    e_dt = parse_date(end_date)
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT start_date, end_date FROM reporting_sync_history")
        history = cursor.fetchall()
        
        # Simple check: Does any single sync cover the requested range?
        for row in history:
            h_start = parse_date(row['start_date'])
            h_end = parse_date(row['end_date'])
            if h_start <= s_dt and h_end >= e_dt:
                return True
                
    return False

def get_previous_period(start_date: str, end_date: str) -> tuple[str, str]:
    """
    Calendar-aware previous period logic.
    """
    s_dt = parse_date(start_date)
    e_dt = parse_date(end_date)
    days_diff = (e_dt - s_dt).days + 1
    
    # 1. Is it a single week? (7 days, Mon-Sun or similar)
    if days_diff == 7:
        prev_s = s_dt - timedelta(days=7)
        prev_e = e_dt - timedelta(days=7)
        return format_date(prev_s), format_date(prev_e)
        
    # 2. Is it a calendar month?
    if s_dt.day == 1:
        # Check if e_dt is the last day of that month
        next_month_start = s_dt + relativedelta(months=1)
        if e_dt == next_month_start - timedelta(days=1):
            prev_s = s_dt - relativedelta(months=1)
            prev_e = s_dt - timedelta(days=1)
            return format_date(prev_s), format_date(prev_e)
            
    # 3. Is it a quarter?
    if s_dt.day == 1 and s_dt.month in [1, 4, 7, 10]:
        next_q_start = s_dt + relativedelta(months=3)
        if e_dt == next_q_start - timedelta(days=1):
            prev_s = s_dt - relativedelta(months=3)
            prev_e = s_dt - timedelta(days=1)
            return format_date(prev_s), format_date(prev_e)
            
    # 4. Is it a Financial Year? (April 1 to March 31)
    if s_dt.day == 1 and s_dt.month == 4:
        next_y_start = s_dt + relativedelta(years=1)
        if e_dt == next_y_start - timedelta(days=1):
            prev_s = s_dt - relativedelta(years=1)
            prev_e = s_dt - timedelta(days=1)
            return format_date(prev_s), format_date(prev_e)
            
    # 5. Custom / fallback: Subtract same number of days
    prev_s = s_dt - timedelta(days=days_diff)
    prev_e = e_dt - timedelta(days=days_diff)
    return format_date(prev_s), format_date(prev_e)

def get_financial_year(date_str: str) -> tuple[str, str]:
    """
    Returns the financial year start and end dates (YYYYMMDD) for a given date.
    Financial year starts on April 1st and ends on March 31st of the following year.
    """
    dt = parse_date(date_str)
    if dt.month >= 4:
        fy_start = datetime(dt.year, 4, 1)
        fy_end = datetime(dt.year + 1, 3, 31)
    else:
        fy_start = datetime(dt.year - 1, 4, 1)
        fy_end = datetime(dt.year, 3, 31)
    
    return format_date(fy_start), format_date(fy_end)

def get_previous_month_period(start_month: str, end_month: str) -> tuple[str, str]:
    s_dt = datetime.strptime(start_month, "%Y-%m")
    e_dt = datetime.strptime(end_month, "%Y-%m")
    
    diff_months = (e_dt.year - s_dt.year) * 12 + e_dt.month - s_dt.month + 1
    
    prev_s_dt = s_dt - relativedelta(months=diff_months)
    prev_e_dt = e_dt - relativedelta(months=diff_months)
    
    return prev_s_dt.strftime("%Y-%m"), prev_e_dt.strftime("%Y-%m")

def get_month_boundaries(year_month: str) -> tuple[str, str]:
    import calendar
    dt = datetime.strptime(year_month, "%Y-%m")
    last_day = calendar.monthrange(dt.year, dt.month)[1]
    first_day = f"{dt.year}{dt.month:02d}01"
    last_day = f"{dt.year}{dt.month:02d}{last_day:02d}"
    return first_day, last_day
