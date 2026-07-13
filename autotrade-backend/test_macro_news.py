import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

def fetch_macro_news():
    queries = [
        "Donald Trump ceasefire",
        "Global stock market crash reasons today",
        "Nifty Sensex breaking news"
    ]
    
    for query in queries:
        print(f"\n--- Searching Google News for: {query} ---")
        encoded_query = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                xml_data = response.read()
            root = ET.fromstring(xml_data)
            
            for item in root.findall('.//item')[:3]:
                title = item.findtext('title')
                pub_date = item.findtext('pubDate')
                print(f"[{pub_date}] {title}")
        except Exception as e:
            print(f"Failed: {e}")

if __name__ == "__main__":
    fetch_macro_news()
