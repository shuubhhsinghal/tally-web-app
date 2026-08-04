# Tally Invoice Scanner Bot

Telegram bot for creating Tally Purchase, Sales, Payment, Receipt, and bank-statement vouchers. Invoice and receipt extraction uses **Google Gemini 3.5 Flash-Lite** through the supported `google-genai` Python SDK.

## Setup

Requires Python 3.9 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt # Automatically installs rapidfuzz for intelligent ledger resolution
export TELEGRAM_BOT_TOKEN='your Telegram token'
export GEMINI_API_KEY='your Google AI Studio key'
export TALLY_URL='http://127.0.0.1:9000'  # optional
python bot.py
```

`GEMINI_MODEL` is optional and defaults to `gemini-3.5-flash-lite`.

## Runtime files

- `ledgers.json`: permitted ledger and cost-centre names.
- `rules.json`: narration keyword mappings for each bank ledger.
- `purchases_db.json` and `sales_db.json`: per-user export queues.

The bot needs Tally running with XML/HTTP enabled at `TALLY_URL`. Confirm every ledger emitted by the bot exists in Tally before posting live vouchers.
