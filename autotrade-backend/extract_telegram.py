import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime, timedelta

def fetch_telegram_messages():
    url = "https://t.me/s/eagleeyesmarketanalysis"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    messages = soup.find_all('div', class_='tgme_widget_message')
    
    extracted = []
    
    for msg in messages:
        text_div = msg.find('div', class_='tgme_widget_message_text')
        date_time = msg.find('time', class_='time')
        
        if text_div and date_time:
            msg_text = text_div.get_text(separator='\n').strip()
            msg_time = date_time.get('datetime') # e.g. "2026-07-10T08:30:00+00:00"
            extracted.append({
                "time": msg_time,
                "text": msg_text
            })
            
    # Filter for today (July 13, 2026)
    target_dates = ['2026-07-13']
    filtered = [m for m in extracted if any(d in m['time'] for d in target_dates)]
    
    with open('telegram_analysis.md', 'w') as f:
        f.write("# Eagle Eyes Market Analysis (July 13, 2026)\n\n")
        if not filtered:
            f.write("No messages found for today or yesterday.\n")
        else:
            for m in filtered:
                f.write(f"### {m['time']}\n")
                f.write(f"{m['text']}\n\n")
                f.write("---\n")
    print(f"Extracted {len(filtered)} messages.")

if __name__ == "__main__":
    fetch_telegram_messages()
