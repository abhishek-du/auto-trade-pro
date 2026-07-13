from googlesearch import search

print("Testing Google Search (General Web)...")
try:
    results = search("Infosys SEC filings 2026", num_results=5)
    for r in results:
        print(f"URL: {r}")
except Exception as e:
    print(f"Google Search failed: {e}")
