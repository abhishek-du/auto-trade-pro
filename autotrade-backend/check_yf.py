import yfinance as yf

try:
    ticker = yf.Ticker("SUNTV.NS")
    data = ticker.history(period="1d", interval="1m")
    if not data.empty:
        print(f"Current yfinance price for SUNTV: {data['Close'].iloc[-1]}")
    else:
        print("No data available from yfinance.")
except Exception as e:
    print(f"Error fetching yfinance: {e}")
