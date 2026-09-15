import os
import re
import json
from google import genai
from google.genai import types
from json_repair import repair_json

from backend.database import get_all_stock_items, get_all_aliases, get_db

def normalize_item_name(name: str) -> str:
    name = (name or "").strip().casefold()
    name = re.sub(r'\s+', ' ', name)
    name = re.sub(r'\s*\(\s*', '(', name)
    name = re.sub(r'\s*\)\s*', ')', name)
    return name

def map_items_to_tally(raw_items: list) -> list:
    stock_items = get_all_stock_items()
    aliases = get_all_aliases()
    stock_cache = {normalize_item_name(i['name']): i for i in stock_items}

    pending_item_names = []
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT normalized_name, original_name FROM pending_masters WHERE entity_type = 'ITEM' AND status IN ('PENDING', 'SYNCED_WAITING_CONFIRMATION')")
        for row in cursor.fetchall():
            norm = row["normalized_name"]
            orig = row["original_name"]
            pending_item_names.append(orig)
            if norm not in stock_cache:
                stock_cache[norm] = {"name": orig}

    mapped_items = []
    unmapped_raw_items = []

    for item in raw_items:
        original_name = item.get('name') or 'Unknown'
        norm_name = normalize_item_name(original_name)

        match_found = False
        mapped_name = None
        
        # Safely handle None or empty strings for UOM
        mapped_unit = (item.get('uom') or 'PCS').upper()

        if norm_name in aliases:
            mapped_name = aliases[norm_name]
            match_found = True
        else:
            for stock_key, stock_data in stock_cache.items():
                if stock_key == norm_name or stock_data.get('name', '').lower().strip() == original_name.lower().strip():
                    mapped_name = stock_data.get('name')
                    mapped_unit = stock_data.get('unit', mapped_unit)
                    match_found = True
                    break

        item['mapped_name'] = mapped_name if match_found else ""
        item['mapped_unit'] = mapped_unit
        item['is_mapped'] = match_found

        if match_found:
            mapped_items.append(item)
        else:
            unmapped_raw_items.append(item)
            mapped_items.append(item)

    if unmapped_raw_items:
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            client = genai.Client(api_key=api_key)
            tally_item_names = [i['name'] for i in stock_items]
            seen_tally_names = set(normalize_item_name(n) for n in tally_item_names)
            for name in pending_item_names:
                norm = normalize_item_name(name)
                if norm not in seen_tally_names:
                    tally_item_names.append(name)
                    seen_tally_names.add(norm)

            master_list_str = "\n".join(f"- {name}" for name in tally_item_names)

            map_prompt = f"""
            You are a data-mapping assistant. 
            I have extracted the following raw items from an invoice: {json.dumps(unmapped_raw_items)}
            
            Here is my Tally Master Stock List:
            {master_list_str}
            
            For each raw item, find the exact matching string from the Tally Master Stock List. You must account for typos, case differences, and spacing (e.g. '400g' vs '400gm').
            If a confident match is found, add a key called 'mapped_name' to the item object containing the exact Tally string. 
            If no match is found, leave 'mapped_name' empty.
            
            Return the entire updated invoice JSON using this schema:
            {{
              "items": [
                {{"name": "...", "mapped_name": "..."}}
              ]
            }}
            """
            try:
                map_response = client.models.generate_content(
                    model='gemini-3.5-flash-lite',
                    contents=[map_prompt],
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                )
                map_text = map_response.text.strip()
                map_text = re.sub(r'^```json\s*', '', map_text)
                map_text = re.sub(r'\s*```$', '', map_text)
                map_data = repair_json(map_text, return_objects=True)

                if isinstance(map_data, dict) and "items" in map_data:
                    for mapped_row in map_data["items"]:
                        for out_row in mapped_items:
                            if out_row['name'] == mapped_row['name'] and mapped_row.get('mapped_name'):
                                if mapped_row['mapped_name'] in tally_item_names:
                                    out_row['mapped_name'] = mapped_row['mapped_name']
                                    out_row['is_mapped'] = True
                                    norm_mapped = normalize_item_name(out_row['mapped_name'])
                                    if norm_mapped in stock_cache:
                                        out_row['mapped_unit'] = stock_cache[norm_mapped].get('unit', out_row['mapped_unit'])
            except Exception as e:
                print(f"--- [EXTRACTION V4] Text Mapping failed: {e} ---", flush=True)

    return mapped_items

def map_supplier_to_tally(supplier_name: str) -> str:
    import difflib
    from backend.database import get_all_ledgers
    
    ledgers = get_all_ledgers()
    cached_suppliers_list = []
    for l in ledgers:
        parent = (l.get('parent') or '').lower()
        if "creditor" in parent or "loan" in parent:
            cached_suppliers_list.append(l['name'].title())

    mapped_supplier = ""
    if supplier_name and cached_suppliers_list:
        matches = difflib.get_close_matches(
            supplier_name,
            cached_suppliers_list,
            n=1,
            cutoff=0.8
        )
        if matches:
            mapped_supplier = matches[0]
            
    return mapped_supplier or (supplier_name or "Unknown Supplier").title()
