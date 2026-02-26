import json
from agent import run_turn

session = {}
user_prompt = """We operate a SaaS knowledge platform where documents belong to different customer organizations, and strict tenant isolation is required. The system stores embeddings for approximately 80 million documents across roughly 5,000 tenants, with each tenant having between 5,000 and 50,000 documents. Every search query must be restricted to a specific tenant, meaning queries always include a tenant identifier filter before semantic similarity is applied. The embeddings are 1024 dimensions, and users perform semantic search within their own company’s data, sometimes combined with additional metadata filters such as document type or access level. We expect around 400 queries per second with moderate update rates as customers continuously upload new documents. Our latency targets are below 40 ms p50 and 100 ms p95, and we typically retrieve the top 10 results. Recall quality is important, but predictable latency and efficient filtered search are more critical than global semantic exploration."""

res = run_turn(user_prompt, session)

print("--- AGENT RESPONSE ---")
if "steps" in res:
    for step in res["steps"]:
        print(f"\n[STEP] Tool: {step.get('tool')}")
        print(f"Args: {step.get('args')}")
        print(f"Thought: {step.get('content')}")

print("\n--- FINAL OUTPUT ---")
print(json.dumps(res, indent=2))
