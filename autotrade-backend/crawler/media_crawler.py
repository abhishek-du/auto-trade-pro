import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime
from utils.logger import logger
from curl_cffi import requests

def _parse_date(date_str: str) -> datetime:
    # Just return now for simplicity, could parse pubDate if needed
    return datetime.utcnow()

async def fetch_financial_media() -> list[dict]:
    """
    Fetches latest headlines from top tier financial media feeds.
    Uses curl_cffi to bypass Enterprise WAFs (Akamai/Cloudflare) via TLS fingerprinting.
    """
    feeds = {
        'Economic Times': 'https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms',
        'LiveMint': 'https://www.livemint.com/rss/markets',
        'CNBC TV18': 'https://www.cnbctv18.com/commonfeeds/v1/cne/rss/market.xml',
        'Zee Business (Latest)': 'https://www.zeebiz.com/rss.xml',
        'Zee Business (Companies)': 'https://www.zeebiz.com/companies.xml',
        'Zee Business (Energy)': 'https://www.zeebiz.com/energy.xml'
    }

    def _fetch(source, url):
        rows = []
        try:
            # Impersonate Chrome TLS fingerprint to bypass Akamai
            response = requests.get(url, impersonate='chrome110', timeout=15)
            if response.status_code != 200:
                logger.error(f"[media_crawler] {source} returned {response.status_code}")
                return rows
                
            content = response.content
            root = ET.fromstring(content)
            items = root.findall('.//item')[:30] # top 30 from each source per cycle
            for item in items:
                title = item.findtext('title') or ''
                link = item.findtext('link') or url
                if not title: continue
                
                rows.append({
                    "headline": title.strip(),
                    "source": source,
                    "url": link,
                    "published_at": _parse_date(item.findtext('pubDate') or "")
                })
        except Exception as e:
            logger.error(f"[media_crawler] Failed to fetch {source}: {e}")
        return rows

    loop = asyncio.get_event_loop()
    all_news = []
    for source, url in feeds.items():
        res = await loop.run_in_executor(None, _fetch, source, url)
        if res:
            logger.info(f"{source} ✓ {len(res)} items")
            all_news.extend(res)
            
    return all_news
