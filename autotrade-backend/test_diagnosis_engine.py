import asyncio
from db.database import AsyncSessionLocal
from engine.portfolio_doctor import run_full_diagnosis
import traceback

async def main():
    try:
        async with AsyncSessionLocal() as session:
            res = await run_full_diagnosis(
                portfolio_id="5ebbd324-4e0a-40f6-bd96-41678d99e3ac",
                sip_goal_ids=[],
                risk_profile="moderate",
                annual_income=1000000,
                session=session
            )
            print("Success!")
    except Exception as e:
        print("Exception occurred!")
        traceback.print_exc()

asyncio.run(main())
