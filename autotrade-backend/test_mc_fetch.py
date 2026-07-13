import asyncio
from crawler.news_crawler import fetch_free_rss_news

async def main():
    rows = await fetch_free_rss_news()
    mc_rows = [r for r in rows if 'moneycontrol' in r['url']]
    print(f"Total MC News Found: {len(mc_rows)}")
    if mc_rows:
        print(f"Sample MC News: {mc_rows[0]['headline']}")

if __name__ == "__main__":
    asyncio.run(main())
