import json

transcript_path = r"C:\Users\Muhammad Abbas\.gemini\antigravity\brain\523e57a8-7eff-43ef-a1b3-18cabedbbae4\.system_generated\logs\transcript.jsonl"

with open(transcript_path, "r", encoding="utf-8") as f:
    for line in f:
        d = json.loads(line)
        idx = d.get("step_index")
        if idx in range(16565, 16580):
            print(f"=== STEP {idx} [{d.get('type')}] ===")
            if d.get("tool_calls"):
                print("  TOOL_CALLS:", d["tool_calls"])
            if d.get("content"):
                print("  CONTENT:", str(d["content"])[:300])
