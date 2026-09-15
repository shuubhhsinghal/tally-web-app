from backend.database import get_db

with get_db() as db:
    vouchers = db.execute("SELECT id, voucher_type, voucher_number, party_ledger_name FROM reporting_vouchers ORDER BY id LIMIT 10").fetchall()
    
    print("Voucher Type | Voucher No | Tally/Expected Amount | Current Daybook Amount | Match?")
    for v in vouchers:
        vid = v[0]
        vtype = v[1]
        vno = v[2]
        party = v[3]
        
        ledgers = db.execute("SELECT ledger_name, amount, is_deemed_positive FROM reporting_ledger_entries WHERE voucher_id = ?", (vid,)).fetchall()
        
        # Current Daybook Amount (SUM of ABS(amount) where is_deemed_positive = 1)
        current_dbk = sum(abs(l[1]) for l in ledgers if l[2] == 1)
        
        # Expected Amount (from Party ledger)
        party_amt = sum(abs(l[1]) for l in ledgers if l[0] == party)
        if party_amt == 0:
            # Fallback to sum of debits
            party_amt = sum(abs(l[1]) for l in ledgers if l[1] < 0)
            
        match = "Yes" if abs(current_dbk - party_amt) < 0.01 else "No"
        
        print(f"{vtype:<12} | {vno:<10} | {party_amt:<21.2f} | {current_dbk:<22.2f} | {match}")
