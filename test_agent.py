import json
from agent import run_turn

def print_res(res):
    if "steps" in res:
        for step in res["steps"]:
            print(f"\n[STEP] Tool: {step.get('tool')}")
            print(f"Args: {step.get('args')}")
            if step.get('result'):
                print(f"Result: {str(step.get('result'))[:200]}...")

session = {}
user_prompt = """We operate a SaaS knowledge platform. 80 million documents, 5k tenants, 5k-50k docs per tenant. Filters always include tenant_id. 1024 dims. 400 QPS. 40ms p50, 100ms p95. No existing Couchbase footprint (Greenfield). Growth is stable (80M-100M)."""

print("--- TURN 1 ---")
res = run_turn(user_prompt, session)
print_res(res)

print("\n--- TURN 2 ---")
# User confirms it's Greenfield and stable growth (already in prompt but let's be explicit if it asks)
res2 = run_turn("Yes, it is greenfield and growth is stable around 80M.", session)
print_res(res2)

print("\n--- FINAL OUTPUT ---")
print(json.dumps(res2.get("payload", {}), indent=2))
