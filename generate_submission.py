import json
import os
from bot import compose_action_for_trigger, store, trigger_priority

def load_data():
    base = os.path.join(os.path.dirname(__file__), "dataset")
    
    cat_dir = os.path.join(base, "categories")
    for fname in os.listdir(cat_dir):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(cat_dir, fname), encoding="utf-8") as f:
            cat = json.load(f)
            store.upsert_context("category", cat.get("slug"), 1, cat)
            
    with open(os.path.join(base, "merchants_seed.json"), encoding="utf-8") as f:
        merchants = json.load(f)["merchants"]
        for m in merchants:
            store.upsert_context("merchant", m["merchant_id"], 1, m)
            
    with open(os.path.join(base, "customers_seed.json"), encoding="utf-8") as f:
        customers = json.load(f)["customers"]
        for c in customers:
            store.upsert_context("customer", c["customer_id"], 1, c)
            
    with open(os.path.join(base, "triggers_seed.json"), encoding="utf-8") as f:
        triggers = json.load(f)["triggers"]
        for t in triggers:
            store.upsert_context("trigger", t["id"], 1, t)

def generate():
    load_data()
    print("Data loaded into store.")
    
    triggers = []
    for (scope, _), item in store.contexts.items():
        if scope == "trigger":
            triggers.append(item["payload"])
            
    # Priority sort to match tick behavior
    triggers.sort(key=trigger_priority, reverse=True)
    
    lines = []
    used_targets = set()
    for i, t in enumerate(triggers):
        # We process every trigger for the submission file to show what the bot WOULD do,
        # even if they target the same merchant. 
        action = compose_action_for_trigger(t["id"])
        if action:
            lines.append({
                "test_id": f"T{len(lines)+1:02d}",
                "trigger_id": t["id"],
                "body": action["body"],
                "cta": action["cta"],
                "send_as": action["send_as"],
                "suppression_key": action["suppression_key"],
                "rationale": action["rationale"]
            })
            
    out_path = os.path.join(os.path.dirname(__file__), "submission.jsonl")
    with open(out_path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
            
    print(f"Generated {out_path} with {len(lines)} composed messages.")

if __name__ == "__main__":
    generate()
