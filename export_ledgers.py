import json

def main():
    ledgers_data = {
        "TALLY_LEDGERS": [
            "Profit & Loss A/c",
            "ABC",
            "ABC Traders",
            "BALAJEE SALES",
            "Cash Gulshan",
            "Cash Mahagun",
            "Cash Vvip",
            "Drawings 3024",
            "Drawings 7002",
            "Electricity and Maintenance",
            "Federal Bank Gulshan",
            "Lokesh Gulshan",
            "Paytm 845 Loan",
            "Petty Expenses",
            "Purchase",
            "Salary",
            "SALES",
            "shyam trader",
            "SHYAM TRADERS",
            "Union Bank Mahagun",
            "Union Bank Vvip"
        ],
        "COST_CENTER_LEDGERS": [
            "Electricity and Maintenance",
            "Petty Expenses",
            "Purchase",
            "Salary"
        ],
        "CONTRA_LEDGERS": [
            "Cash Gulshan",
            "Cash Mahagun",
            "Cash Vvip",
            "Federal Bank Gulshan",
            "Union Bank Mahagun",
            "Union Bank Vvip"
        ]
    }
    with open('/Users/shubh/Desktop/INV Scanner/ledgers.json', 'w') as f:
        json.dump(ledgers_data, f, indent=4)
    print("Exported successfully.")

if __name__ == "__main__":
    main()
