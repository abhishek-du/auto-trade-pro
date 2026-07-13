import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

def fetch_gnews(query: str, max_results: int = 3):
    encoded_query = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            xml_data = response.read()
        root = ET.fromstring(xml_data)
        
        results = []
        for item in root.findall('.//item')[:max_results]:
            title = item.findtext('title')
            pub_date = item.findtext('pubDate')
            if title:
                results.append({"title": title, "body": pub_date})
        return results
    except Exception as e:
        print(f"GNews failed: {e}")
        return []

if __name__ == "__main__":
    res = fetch_gnews("Infosys NSE stock news", 5)
    for r in res:
        print(r)
