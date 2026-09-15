import requests
import xml.etree.ElementTree as ET
import random
from datetime import datetime, timedelta
import json

TALLY_URL = "http://100.107.220.58:9000"

STORES = ["Mahagun", "Gulshan", "Vvip"]

random.seed(42)

def generate_date(month=10, year=2026):
    day = random.randint(1, 31)
    return f"{year}{month:02d}{day:02d}"

expected_results = {
    "Mahagun": {"Sales": 0.0, "Purchases": 0.0, "Direct Expenses": 0.0, "Indirect Expenses": 0.0},
    "Gulshan": {"Sales": 0.0, "Purchases": 0.0, "Direct Expenses": 0.0, "Indirect Expenses": 0.0},
    "Vvip": {"Sales": 0.0, "Purchases": 0.0, "Direct Expenses": 0.0, "Indirect Expenses": 0.0},
    "Unallocated": {"Sales": 0.0, "Purchases": 0.0, "Direct Expenses": 0.0, "Indirect Expenses": 0.0},
}

voucher_xml = ""

def track_result(store, account, amount, is_expense=False, is_income=False, is_purchase=False, is_sales=False):
    key = store if store else "Unallocated"
    if is_expense:
        if account in ["packaging"]:
            expected_results[key]["Direct Expenses"] += amount
        else:
            expected_results[key]["Indirect Expenses"] += amount
    elif is_income:
        expected_results[key]["Indirect Expenses"] -= amount
    elif is_purchase:
        expected_results[key]["Purchases"] += amount
    elif is_sales:
        expected_results[key]["Sales"] += amount

def add_voucher(vch_type, date, ledgers):
    global voucher_xml
    vch_xml = f"""
    <TALLYMESSAGE xmlns:UDF="TallyUDF">
      <VOUCHER VCHTYPE="{vch_type}" ACTION="Create" OBJVIEW="Accounting Voucher View">
        <DATE>{date}</DATE>
        <VOUCHERTYPENAME>{vch_type}</VOUCHERTYPENAME>
        <VOUCHERNUMBER>TEST-{random.randint(10000, 99999)}</VOUCHERNUMBER>
        <PARTYLEDGERNAME>{ledgers[0]['name']}</PARTYLEDGERNAME>
    """
    for l in ledgers:
        amount = l['amount'] if l['is_debit'] else -l['amount']
        vch_xml += f"""
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>{l['name']}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>{"Yes" if l['is_debit'] else "No"}</ISDEEMEDPOSITIVE>
          <AMOUNT>{amount}</AMOUNT>
        """
        if l.get('cost_centre'):
            vch_xml += f"""
          <CATEGORYALLOCATIONS.LIST>
            <CATEGORYNAME>Primary Cost Category</CATEGORYNAME>
            <ISDEEMEDPOSITIVE>{"Yes" if l['is_debit'] else "No"}</ISDEEMEDPOSITIVE>
            <COSTCENTREALLOCATIONS.LIST>
              <NAME>{l['cost_centre']}</NAME>
              <AMOUNT>{amount}</AMOUNT>
            </COSTCENTREALLOCATIONS.LIST>
          </CATEGORYALLOCATIONS.LIST>
            """
        vch_xml += """
        </ALLLEDGERENTRIES.LIST>
        """
    vch_xml += """
      </VOUCHER>
    </TALLYMESSAGE>
    """
    voucher_xml += vch_xml

# 1. SALES
for _ in range(150):
    for store in STORES:
        amt = round(random.uniform(50.0, 50000.0), 2)
        add_voucher("Sales", generate_date(), [
            {'name': 'cash ' + store.lower(), 'amount': amt, 'is_debit': True, 'cost_centre': store},
            {'name': 'sales', 'amount': amt, 'is_debit': False, 'cost_centre': store}
        ])
        track_result(store, 'sales', amt, is_sales=True)

# 2. PURCHASES
for _ in range(50):
    for store in STORES:
        amt = round(random.uniform(100.0, 30000.0), 2)
        add_voucher("Purchase", generate_date(), [
            {'name': 'A K Traders', 'amount': amt, 'is_debit': False, 'cost_centre': store},
            {'name': 'purchase', 'amount': amt, 'is_debit': True, 'cost_centre': store}
        ])
        track_result(store, 'purchase', amt, is_purchase=True)

# 3. DIRECT EXPENSES
for _ in range(30):
    for store in STORES:
        amt = round(random.uniform(10.0, 500.0), 2)
        add_voucher("Payment", generate_date(), [
            {'name': 'packaging', 'amount': amt, 'is_debit': True, 'cost_centre': store},
            {'name': 'cash ' + store.lower(), 'amount': amt, 'is_debit': False, 'cost_centre': store}
        ])
        track_result(store, 'packaging', amt, is_expense=True)

# 4. INDIRECT EXPENSES
expenses = ['Salary', 'rent', 'Electricity and Maintenance', 'transport', 'Petty Expense']
for _ in range(100):
    for store in STORES:
        exp = random.choice(expenses)
        amt = round(random.uniform(500.0, 15000.0), 2)
        is_accrued = random.choice([True, False])
        if is_accrued:
            add_voucher("Journal", generate_date(), [
                {'name': exp, 'amount': amt, 'is_debit': True, 'cost_centre': store},
                {'name': 'Pending Ledger', 'amount': amt, 'is_debit': False, 'cost_centre': store}
            ])
        else:
            add_voucher("Payment", generate_date(), [
                {'name': exp, 'amount': amt, 'is_debit': True, 'cost_centre': store},
                {'name': 'cash ' + store.lower(), 'amount': amt, 'is_debit': False, 'cost_centre': store}
            ])
        track_result(store, exp, amt, is_expense=True)

# 5. INCOME
for _ in range(20):
    for store in STORES:
        amt = round(random.uniform(1.0, 100.0), 2)
        add_voucher("Receipt", generate_date(), [
            {'name': 'cash ' + store.lower(), 'amount': amt, 'is_debit': True, 'cost_centre': store},
            {'name': 'Rounding Off', 'amount': amt, 'is_debit': False, 'cost_centre': store}
        ])
        track_result(store, 'Rounding Off', amt, is_income=True)

# 6. UNALLOCATED ENTRIES
for _ in range(10):
    amt = round(random.uniform(10.0, 200.0), 2)
    add_voucher("Journal", generate_date(), [
        {'name': 'Petty Expense', 'amount': amt, 'is_debit': True, 'cost_centre': None},
        {'name': 'cash mahagun', 'amount': amt, 'is_debit': False, 'cost_centre': None}
    ])
    track_result(None, 'Petty Expense', amt, is_expense=True)

# 7. CONTRA
for _ in range(20):
    store = random.choice(STORES)
    amt = round(random.uniform(100.0, 5000.0), 2)
    add_voucher("Contra", generate_date(), [
        {'name': 'canara bank gulshan 140', 'amount': amt, 'is_debit': True, 'cost_centre': None},
        {'name': 'cash ' + store.lower(), 'amount': amt, 'is_debit': False, 'cost_centre': None}
    ])

print("Total vouchers generated:", voucher_xml.count("</VOUCHER>"))

envelope = f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
      </REQUESTDESC>
      <REQUESTDATA>
        {voucher_xml}
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""

print("Sending payload...")
response = requests.post(TALLY_URL, data=envelope.encode('utf-8'), headers={'Content-Type': 'text/xml'}, timeout=60)
print(response.text)

stocks = {
    "Mahagun": {"30-Sep": 75000.0, "31-Oct": 95000.0},
    "Gulshan": {"30-Sep": 55000.0, "31-Oct": 72000.0},
    "Vvip": {"30-Sep": 65000.0, "31-Oct": 83000.0}
}

stock_xml = ""
for store, vals in stocks.items():
    stock_xml += f"""
    <TALLYMESSAGE xmlns:UDF="TallyUDF">
      <LEDGER NAME="stock {store.lower()}" ACTION="Alter">
        <NAME>stock {store.lower()}</NAME>
        <CLOSINGBALANCES.LIST>
          <DATE>20260930</DATE>
          <AMOUNT>{-vals['30-Sep']}</AMOUNT>
        </CLOSINGBALANCES.LIST>
        <CLOSINGBALANCES.LIST>
          <DATE>20261031</DATE>
          <AMOUNT>{-vals['31-Oct']}</AMOUNT>
        </CLOSINGBALANCES.LIST>
      </LEDGER>
    </TALLYMESSAGE>
    """

envelope_stock = f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>All Masters</REPORTNAME>
      </REQUESTDESC>
      <REQUESTDATA>
        {stock_xml}
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""

print("Sending stock payload...")
response = requests.post(TALLY_URL, data=envelope_stock.encode('utf-8'), headers={'Content-Type': 'text/xml'}, timeout=60)
print(response.text)

with open("expected_results.json", "w") as f:
    json.dump({"results": expected_results, "stocks": stocks}, f, indent=4)
print("Saved expected results to expected_results.json")
