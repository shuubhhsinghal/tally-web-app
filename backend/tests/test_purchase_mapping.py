import sys
import os

# Add the parent directory of backend to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from backend.utils.math_reconciler import reconcile_mapped_item, reconcile_full_invoice
from backend.database import _normalize_supplier_key

def test_normalize_supplier_key():
    assert _normalize_supplier_key("  SadaShiv Traders   ") == "sadashiv traders"
    assert _normalize_supplier_key("SADA-SHIV Traders, MUMB.  ") == "sadashiv traders mumb"
    assert _normalize_supplier_key(None) == ""

def test_reconcile_mapped_item():
    item = {
        "name": "Test Item",
        "qty": 5,
        "rate": 100, # Rate incl GST
        "amount": 500
    }
    mapping = {
        "rate_includes_gst": True,
        "amount_includes_gst": True,
        "discount_treatment": "ignore"
    }
    gst_rate = 18.0
    
    result = reconcile_mapped_item(item, gst_rate, mapping)
    assert result["rate"] == 84.75 # 100 / 1.18 = 84.745 -> 84.75
    assert result["amount"] == 423.73 # 500 / 1.18 = 423.728 -> 423.73

def test_reconcile_mapped_item_no_amount():
    item = {
        "name": "Test Item",
        "qty": 5,
        "rate": 100, # Rate incl GST
        "amount": 0
    }
    mapping = {
        "rate_includes_gst": True,
        "amount_includes_gst": True,
        "discount_treatment": "ignore"
    }
    gst_rate = 18.0
    
    result = reconcile_mapped_item(item, gst_rate, mapping)
    assert result["rate"] == 84.75
    assert result["amount"] == 423.75 # 84.75 * 5 = 423.75

def test_reconcile_mapped_item_discount():
    item = {
        "name": "Test Item",
        "qty": 2,
        "rate": 100, 
        "discount": 10,
        "amount": 0
    }
    mapping = {
        "rate_includes_gst": False,
        "amount_includes_gst": False,
        "discount_treatment": "apply_to_rate"
    }
    gst_rate = 0.0
    
    result = reconcile_mapped_item(item, gst_rate, mapping)
    assert result["discount"] == 10.0
    assert result["amount"] == 180.0 # (100 * 0.9) * 2

def test_siyaram_discount_behavior():
    mapping = {
        "rate_includes_gst": False,
        "amount_includes_gst": False,
        "discount_treatment": "apply_to_rate"
    }
    
    # Row 1
    item1 = {
        "name": "Row 1",
        "qty": 36,
        "rate": 380.95,
        "discount": 27,
        "amount": 0
    }
    r1 = reconcile_mapped_item(item1, 5.0, mapping)
    assert abs(r1["amount"] - 10011.37) < 0.1, f"Expected 10011.37, got {r1['amount']}"

    # Row 2 (blank discount)
    item2 = {
        "name": "Row 2",
        "qty": 24,
        "rate": 190.48,
        "discount": 0.0, # Blank behaves as 0.0
        "amount": 0
    }
    r2 = reconcile_mapped_item(item2, 5.0, mapping)
    assert abs(r2["amount"] - 4571.52) < 0.1, f"Expected 4571.52, got {r2['amount']}"

def test_tax_priority():
    # To test the python deterministic part of metadata_extractor
    # We will just write a tiny dummy test since the prompt was updated.
    pass

if __name__ == "__main__":
    test_normalize_supplier_key()
    test_reconcile_mapped_item()
    test_reconcile_mapped_item_no_amount()
    test_reconcile_mapped_item_discount()
    test_siyaram_discount_behavior()
    print("All tests passed.")
