import asyncio
import os
import time
import json
from google import genai
from google.genai import types

def print_header(title):
    print(f"\n{'='*60}")
    print(f"--- {title} ---")
    print(f"{'='*60}")

async def run_test(client, model, contents, test_name, config=None):
    print_header(test_name)
    print(f"Model: {model}")
    print(f"Prompt Size: {len(str(contents))} chars")
    
    # Print SDK configuration values
    if config:
        print("SDK Config (GenerateContentConfig):")
        # Attempt to dump config to dict/json for clear printing
        try:
            print(json.dumps(config.model_dump(), indent=2))
        except Exception:
            print(config)
    else:
        print("SDK Config: None (Default)")

    start_time = time.time()
    first_byte_time = None
    output_text = ""
    
    try:
        response = await client.aio.models.generate_content_stream(
            model=model,
            contents=contents,
            config=config,
        )
        async for chunk in response:
            if first_byte_time is None:
                first_byte_time = time.time()
                print(f"-> TTFB (Time to First Byte): {first_byte_time - start_time:.2f} seconds")
            output_text += chunk.text
            
        end_time = time.time()
        print(f"-> Total Latency: {end_time - start_time:.2f} seconds")
        
        # Count output tokens
        try:
            token_resp = await client.aio.models.count_tokens(model=model, contents=output_text)
            print(f"-> Output Tokens: {token_resp.total_tokens}")
        except Exception:
            print("-> Output Tokens: (Failed to count)")
            
    except Exception as e:
        print(f"!!! Test Failed: {e}")

async def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        api_key = input("Enter GEMINI_API_KEY: ").strip()
        
    client = genai.Client(api_key=api_key)
    model_id = "gemini-3.5-flash-lite"
    
    # Environment Verification
    print_header("ENVIRONMENT VERIFICATION")
    try:
        import importlib.metadata
        print(f"SDK Version (google-genai): {importlib.metadata.version('google-genai')}")
    except Exception:
        print("SDK Version: Unknown")
        
    try:
        print(f"API Endpoint Base URL: {client._api_client.base_url}")
    except AttributeError:
        pass
    print("Adaptive Thinking Default: Depends on model. Checking if SDK supports zero budget...")
    print(f"ThinkingConfig definition: {getattr(types.ThinkingConfig, '__annotations__', 'No annotations found')}")

    # Test 1: Current Configuration (Production Prompt, JSON mode, no thinking config)
    prod_prompt = """Extract details for a Payment. DO NOT invent ledger names. Extract the exact raw strings from the text.
Return ONLY JSON with:
- amount: (number)
- date: (DD-MM-YYYY format, default to today 03-08-2026)
- payee: (raw string of who is being paid)
- paid_from: (raw string of the bank or cash source, null if not mentioned)
- cost_center: (Mahagun, Vvip, or Gulshan; return null if unclear)
- narration: (the exact raw user message)

User text:
Paid 12000 to Lokesh Mahagun"""

    config_test1 = types.GenerateContentConfig(response_mime_type="application/json")
    await run_test(client, model_id, prod_prompt, "Test 1: Current Configuration", config_test1)

    # Test 2: Explicitly Disable/Minimize Thinking
    config_test2 = types.GenerateContentConfig(
        response_mime_type="application/json",
        thinking_config=types.ThinkingConfig(thinking_budget=1, include_thoughts=False)
    )
    await run_test(client, model_id, prod_prompt, "Test 2: Explicitly Minimized Thinking (Budget=1)", config_test2)

    # Test 3: Minimal Prompt
    minimal_prompt = """Extract JSON:
{
 "amount":...,
 "payee":...,
 "date":...
}

Input:
Paid 12000 to Lokesh Mahagun"""
    config_test3 = types.GenerateContentConfig(response_mime_type="application/json")
    await run_test(client, model_id, minimal_prompt, "Test 3: Minimal Prompt", config_test3)

if __name__ == "__main__":
    asyncio.run(main())
