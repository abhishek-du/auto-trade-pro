import asyncio
import re
from sqlalchemy import text
from db.database import get_db

async def update_db():
    async for db in get_db():
        keys = {
            'max_portfolio_risk': '1.0',
            'max_risk_per_trade': '0.12',
            'paper_confidence_threshold': '30.0',
            'risk_per_trade_min': '0.10',
            'risk_per_trade_max': '0.12'
        }
        for k, v in keys.items():
            await db.execute(text(
                "INSERT INTO runtime_settings (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ), {"k": k, "v": v})
        await db.commit()
        print("Updated runtime_settings in DB.")
        break

def update_env():
    with open('.env', 'r') as f:
        content = f.read()
    
    content = re.sub(r'MAX_RISK_PER_TRADE=[^\n]+', 'MAX_RISK_PER_TRADE=0.12', content)
    content = re.sub(r'PAPER_CONFIDENCE_THRESHOLD=[^\n]+', 'PAPER_CONFIDENCE_THRESHOLD=30.0', content)
    content = re.sub(r'MAX_PORTFOLIO_RISK=[^\n]+', 'MAX_PORTFOLIO_RISK=1.00', content)
    content = re.sub(r'RISK_PER_TRADE_MIN=[^\n]+', 'RISK_PER_TRADE_MIN=0.10', content)
    content = re.sub(r'RISK_PER_TRADE_MAX=[^\n]+', 'RISK_PER_TRADE_MAX=0.12', content)
    
    with open('.env', 'w') as f:
        f.write(content)
    print("Updated .env file.")

async def main():
    update_env()
    await update_db()

if __name__ == "__main__":
    asyncio.run(main())
