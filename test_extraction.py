import json
with open("/Users/shubh/.gemini/antigravity-ide/brain/45b431d2-9393-42f3-b8fd-4b3032446693/.system_generated/logs/transcript_full.jsonl", "r") as f:
    for line in f:
        if "printed_gst_pct" in line:
            try:
                data = json.loads(line)
                if "content" in data:
                    print(data["content"])
            except:
                pass
