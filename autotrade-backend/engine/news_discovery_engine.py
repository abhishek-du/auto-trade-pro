from typing import List, Dict
from utils.logger import logger


class DuplicateEventEngine:
    """
    Highest Priority Component: Deduplication & Clustering.
    Prevents a single event (e.g. L&T order win reported by 4 outlets) from
    being falsely interpreted as 4 independent bullish catalysts.
    """
    async def cluster_news(self, raw_articles: List[Dict]) -> List[Dict]:
        import difflib

        clusters = []
        for article in raw_articles:
            headline = article.get("headline", "")
            if not headline:
                continue

            matched_cluster = None
            for cluster in clusters:
                primary_headline = cluster["articles"][0]["headline"]
                similarity = difflib.SequenceMatcher(None, headline.lower(), primary_headline.lower()).ratio()

                if similarity > 0.5: # 50% similarity threshold
                    matched_cluster = cluster
                    break

            if matched_cluster:
                matched_cluster["articles"].append(article)
            else:
                clusters.append({
                    "primary_headline": headline,
                    "articles": [article],
                    "cluster_size": 1
                })

        clustered_events = []
        for c in clusters:
            clustered_events.append({
                "headline": c["primary_headline"],
                "source_count": len(c["articles"]),
                "articles": c["articles"]
            })

        return clustered_events
