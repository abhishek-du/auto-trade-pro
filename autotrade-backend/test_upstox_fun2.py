import asyncio
from crawler.upstox_data import get_key_ratios, get_shareholding, get_company_profile

async def fetch_fundamentals_upstox(symbol: str) -> dict:
    try:
        ratios = await get_key_ratios(symbol)
        share = await get_shareholding(symbol)
        prof = await get_company_profile(symbol)
        
        data = {}
        if isinstance(prof, dict):
            if "company_profile" in prof:
                data["company_name"] = prof["company_profile"][:100]
            if "sector_market_cap_inr" in prof:
                data["market_cap_cr"] = prof["sector_market_cap_inr"].get("value")
                
        if isinstance(ratios, list):
            for r in ratios:
                name = r.get("name")
                val_str = str(r.get("company_value", ""))
                val_str = val_str.replace(",", "").replace("%", "")
                try:
                    val = float(val_str)
                except ValueError:
                    val = None
                    
                if name == "P/E" and val: data["pe_ratio"] = val
                if name == "P/B" and val: data["pb_ratio"] = val
                if name == "ROE" and val: data["roe"] = val / 100.0 if val else None
                if name == "ROCE" and val: data["roce"] = val / 100.0 if val else None
                if name == "Quick Ratio" and val: data["current_ratio"] = val # approximation
                
        if isinstance(share, list):
            for s in share:
                cat = s.get("category")
                hist = s.get("history", [])
                if hist:
                    val = hist[0].get("value")
                    try:
                        val = float(val) / 100.0 if val else None
                    except ValueError:
                        val = None
                    if cat == "promoters" and val: data["promoter_holding"] = val
                    if cat == "fii" and val: data["fii_holding"] = val
                    
        return data
    except Exception as e:
        return {"_error": "fetch_failed", "_reason": str(e)}

async def main():
    res = await fetch_fundamentals_upstox("RELIANCE.NS")
    print(res)

if __name__ == "__main__":
    asyncio.run(main())
