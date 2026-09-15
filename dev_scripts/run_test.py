import sys
sys.path.append(".")
from backend.tests.test_purchase_included_in_rate import test_v4_gst_combinations

try:
    test_v4_gst_combinations()
    print("ALL TESTS PASSED!")
except AssertionError as e:
    import traceback
    traceback.print_exc()
