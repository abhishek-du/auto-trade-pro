import requests
from bs4 import BeautifulSoup

def fetch():
    url = "https://t.me/s/eagleeyesmarketanalysis"
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers)
    soup = BeautifulSoup(r.text, 'html.parser')
    messages = soup.find_all('div', class_='tgme_widget_message')
    
    dates = ['2026-07-20', '2026-07-19', '2026-07-18']
    
    for msg in messages:
        t_div = msg.find('div', class_='tgme_widget_message_text')
        d_time = msg.find('time', class_='time')
        if t_div and d_time:
            dt = d_time.get('datetime')
            if any(d in dt for d in dates):
                print(f"--- {dt} ---")
                print(t_div.get_text(separator='\n').strip())
                print("\n")

fetch()
