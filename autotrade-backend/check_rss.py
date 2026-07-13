import feedparser

feeds = [
    "https://economictimes.indiatimes.com/markets/rss.cms",
    "https://www.livemint.com/rss/markets",
    "https://www.business-standard.com/rss/markets-106.rss"
]

for url in feeds:
    print(f"Checking {url}")
    f = feedparser.parse(url)
    for entry in f.entries:
        title = entry.title.lower()
        if "birla" in title or "shell" in title or "sprng" in title:
            print("FOUND:", entry.title)
