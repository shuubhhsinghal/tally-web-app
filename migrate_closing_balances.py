from backend.database import get_db

def migrate():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS reporting_ledger_closing_balances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ledger_name TEXT NOT NULL,
                date TEXT NOT NULL,
                amount REAL NOT NULL,
                UNIQUE(ledger_name, date)
            )
        """)
        print("Migration successful: created reporting_ledger_closing_balances")

if __name__ == '__main__':
    migrate()
