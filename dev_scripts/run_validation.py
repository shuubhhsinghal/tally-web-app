import sys
from unittest.mock import MagicMock
sys.modules['fastapi'] = MagicMock()

from backend.database import get_db
from backend.routers.reporting_pl import calculate_pl_for_store

with get_db() as db:
    combined = calculate_pl_for_store("Combined", "20260401", "20270331", db)
    mahagun = calculate_pl_for_store("Mahagun", "20260401", "20270331", db)
    vvip = calculate_pl_for_store("Vvip", "20260401", "20270331", db)
    gulshan = calculate_pl_for_store("Gulshan", "20260401", "20270331", db)
    unalloc = calculate_pl_for_store("Unallocated", "20260401", "20270331", db)
    
    def print_store(res):
        print(f"--- {res['store']} ---")
        print(f"Sales: {res['revenue']['net_sales']}")
        print(f"Opening Stock: {res['cost_of_goods_sold']['opening_stock']}")
        print(f"Purchases: {res['cost_of_goods_sold']['net_purchases']}")
        print(f"Closing Stock: {res['cost_of_goods_sold']['closing_stock']}")
        print(f"COGS: {res['cost_of_goods_sold']['cogs']}")
        print(f"Gross Profit: {res['gross_profit']}")
        print(f"Direct Exp: {res['expenses']['direct_expenses']}")
        print(f"Indirect Exp: {res['expenses']['indirect_expenses']}")
        print(f"Net Profit: {res['net_profit']}")
        print()

    print_store(mahagun)
    print_store(gulshan)
    print_store(vvip)
    print_store(unalloc)
    print_store(combined)

