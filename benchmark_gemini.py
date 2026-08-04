import asyncio
import os
import time
import datetime

import bot

def _prompt(text: str) -> str:
    return f"""Extract details for a Payment. DO NOT invent ledger names. Extract the exact raw strings from the text.
Return ONLY JSON with:
- amount: (number)
- date: (DD-MM-YYYY format, default to today {datetime.datetime.now().strftime('%d-%m-%Y')})
- payee: (raw string of who is being paid)
- paid_from: (raw string of the bank or cash source, null if not mentioned)
- cost_center: (Mahagun, Vvip, or Gulshan; return null if unclear)
- narration: (the exact raw user message)

User text:
{text}"""

async def run_benchmark():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY missing.")
        return
        
    bot.configure_gemini(api_key)
    
    minimal_prompt = "Extract the payee, amount, and date from: Paid 12000 to Lokesh Mahagun"
    
    print("\n>>> RUNNING BENCHMARK 1: MINIMAL PROMPT <<<")
    try:
        await bot.generate_gemini(minimal_prompt, json_mode=False, model_name="gemini-3.5-flash-lite")
    except Exception as e:
        print(f"Failed: {e}")
        
    user_text = "Paid 12000 to Lokesh Mahagun"
    prod_prompt = _prompt(user_text)
    
    print("\n>>> RUNNING BENCHMARK 2: PRODUCTION PROMPT <<<")
    try:
        await bot.generate_gemini(prod_prompt, json_mode=True, model_name="gemini-3.5-flash-lite")
    except Exception as e:
        print(f"Failed: {e}")

if __name__ == "__main__":
    asyncio.run(run_benchmark())
