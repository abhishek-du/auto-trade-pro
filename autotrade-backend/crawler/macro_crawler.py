"""Macro data crawlers for RBI, SEBI, and PIB."""

import asyncio
from datetime import datetime
import feedparser
import httpx
from utils.logger import logger

def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(raw).replace(tzinfo=None)
    except Exception:
        return None

async def fetch_rbi_press_releases() -> list[dict]:
    """Fetch RBI press releases via HTML scraping."""
    url = "https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx"
    
    def _fetch():
        try:
            from bs4 import BeautifulSoup
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            with httpx.Client(verify=False, timeout=15.0, follow_redirects=True) as client:
                res = client.get(url, headers=headers)
                res.raise_for_status()
            
            soup = BeautifulSoup(res.text, "html.parser")
            rows_data = []
            
            # The dates are in td[0], links in td[1] within <table class="tablebg">
            for table in soup.find_all("table", {"class": "tablebg"}):
                for row in table.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) < 2:
                        continue
                    
                    date_tag = cells[0]
                    title_tag = cells[1].find("a")
                    
                    if not title_tag:
                        continue
                        
                    title = title_tag.text.strip()
                    link = title_tag.get("href", "")
                    # Add base URL if it's relative
                    if link and not link.startswith("http"):
                        link = "https://rbi.org.in/Scripts/" + link
                    
                    date_text = date_tag.text.strip()
                    try:
                        # e.g., 'Jul 15, 2026' -> %b %d, %Y
                        dt = datetime.strptime(date_text, "%b %d, %Y")
                    except ValueError:
                        dt = None
                    
                    rows_data.append({
                        "headline":     title,
                        "source":       "RBI",
                        "url":          link,
                        "published_at": dt,
                    })
            return rows_data
        except Exception as exc:
            logger.error(f"RBI scrape failed: {exc}")
            return []

    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, _fetch)
    if rows:
        logger.info(f"RBI ✓  {len(rows)} press releases")
    return rows

async def fetch_pib_releases() -> list[dict]:
    """Fetch PIB (Press Information Bureau) releases via RSS."""
    # ModId=1 is All Releases
    url = "https://pib.gov.in/RSSFeed.aspx?ModId=1"
    
    def _fetch():
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            with httpx.Client(verify=False, timeout=15.0, follow_redirects=True) as client:
                res = client.get(url, headers=headers)
                res.raise_for_status()
                feed_data = res.text
            
            feed = feedparser.parse(feed_data)
            rows = []
            for entry in feed.entries:
                title = (entry.get("title") or "").strip()
                if not title:
                    continue
                rows.append({
                    "headline":     title,
                    "source":       "PIB",
                    "url":          entry.get("link"),
                    "published_at": _parse_dt(entry.get("published")),
                })
            return rows
        except Exception as exc:
            logger.error(f"PIB RSS parse failed: {exc}")
            return []

    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, _fetch)
    if rows:
        logger.info(f"PIB ✓  {len(rows)} releases")
    return rows

async def fetch_sebi_circulars() -> list[dict]:
    """Fetch recent SEBI circulars/orders via HTML scraping."""
    url = "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=7&smid=0"
    
    def _fetch():
        try:
            from bs4 import BeautifulSoup
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            with httpx.Client(verify=False, timeout=15.0) as client:
                res = client.get(url, headers=headers)
                res.raise_for_status()
                
            soup = BeautifulSoup(res.text, "html.parser")
            rows_data = []
            
            for table in soup.find_all("table"):
                for row in table.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) < 2:
                        continue
                        
                    date_tag = cells[0]
                    title_tag = cells[1].find("a")
                    
                    if not title_tag:
                        continue
                        
                    title = title_tag.text.strip()
                    link = title_tag.get("href", "")
                    date_text = date_tag.text.strip()
                    try:
                        dt = datetime.strptime(date_text, "%b %d, %Y")
                    except ValueError:
                        dt = None
                    
                    rows_data.append({
                        "headline":     title,
                        "source":       "SEBI",
                        "url":          link,
                        "published_at": dt,
                    })
            return rows_data
        except Exception as exc:
            logger.error(f"SEBI scrape failed: {exc}")
            return []

    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, _fetch)
    if rows:
        logger.info(f"SEBI ✓  {len(rows)} circulars")
    return rows
