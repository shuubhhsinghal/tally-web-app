import re
import json

def main():
    try:
        with open('/Users/shubh/Desktop/Master.xml', 'r', encoding='utf-16', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading file: {e}")
        return

    # Build groups map
    groups = {}
    group_blocks = re.findall(r'<GROUP.*?NAME="([^"]+)".*?>(.*?)</GROUP>', content, re.DOTALL)
    for gname, gbody in group_blocks:
        parent_match = re.search(r'<PARENT.*?>(.*?)</PARENT>', gbody)
        parent = parent_match.group(1).strip() if parent_match and parent_match.group(1).strip() else None
        groups[gname] = parent

    ledgers = []
    cost_center_ledgers = []
    contra_ledgers = []

    ledger_blocks = re.findall(r'<LEDGER.*?NAME="([^"]+)".*?>(.*?)</LEDGER>', content, re.DOTALL)
    for lname, lbody in ledger_blocks:
        if not lname:
            continue
            
        ledgers.append(lname)
        parent_match = re.search(r'<PARENT.*?>(.*?)</PARENT>', lbody)
        parent = parent_match.group(1).strip() if parent_match and parent_match.group(1).strip() else None
        
        curr = parent
        is_expense = False
        is_contra = False
        
        while curr:
            if 'Expense' in curr or 'Purchase' in curr:
                is_expense = True
            if 'Bank' in curr or 'Cash' in curr:
                is_contra = True
            curr = groups.get(curr)
            
        if is_expense:
            cost_center_ledgers.append(lname)
        if is_contra:
            contra_ledgers.append(lname)

    # Output to a file so we don't truncate in console
    with open('/Users/shubh/Desktop/INV Scanner/output_ledgers.py', 'w', encoding='utf-8') as out:
        out.write("TALLY_LEDGERS = " + json.dumps(ledgers, indent=4) + "\n\n")
        out.write("COST_CENTER_LEDGERS = " + json.dumps(cost_center_ledgers, indent=4) + "\n\n")
        out.write("CONTRA_LEDGERS = " + json.dumps(contra_ledgers, indent=4) + "\n\n")
    
    print("Done. Saved to output_ledgers.py")

if __name__ == "__main__":
    main()
