from backend.services.reporting_creditors_service import get_creditor_ledger_movements

supplier = 'Aditya Agency'
tally_cb = 19914.24

result = get_creditor_ledger_movements(supplier, '20000101', '20261231')
app_cb = result['period_closing']
diff = tally_cb - app_cb

print(f"Supplier: {supplier}")
print(f"Period: All time")
print(f"Tally Closing Balance: {tally_cb}")
print(f"App Closing Balance: {app_cb}")
print(f"Difference: {diff}")
print("Movements found in app DB:")
for m in result['movements']:
    print(f"  {m['date']} | {m['voucher_type']} | {m['voucher_number']} | Amount: {m['amount']}")
