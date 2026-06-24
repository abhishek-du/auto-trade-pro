import pandas as pd
import numpy as np

try:
    import talib
    TALIB_AVAILABLE = True
except ImportError:
    TALIB_AVAILABLE = False

def detect_candlestick_patterns(df: pd.DataFrame) -> list[str]:
    """Detect common candlestick patterns (Hammer, Engulfing, Doji, etc.) on the last candle."""
    patterns = []
    
    if len(df) < 5:
        return patterns
        
    open_p = df['open'].values.astype(np.float64)
    high_p = df['high'].values.astype(np.float64)
    low_p = df['low'].values.astype(np.float64)
    close_p = df['close'].values.astype(np.float64)

    if TALIB_AVAILABLE:
        engulfing = talib.CDLENGULFING(open_p, high_p, low_p, close_p)
        if engulfing[-1] == 100:
            patterns.append("Bullish Engulfing")
        elif engulfing[-1] == -100:
            patterns.append("Bearish Engulfing")

        hammer = talib.CDLHAMMER(open_p, high_p, low_p, close_p)
        if hammer[-1] != 0:
            patterns.append("Hammer")

        hanging_man = talib.CDLHANGINGMAN(open_p, high_p, low_p, close_p)
        if hanging_man[-1] != 0:
            patterns.append("Hanging Man")

        shooting_star = talib.CDLSHOOTINGSTAR(open_p, high_p, low_p, close_p)
        if shooting_star[-1] != 0:
            patterns.append("Shooting Star")

        doji = talib.CDLDOJI(open_p, high_p, low_p, close_p)
        if doji[-1] != 0:
            patterns.append("Doji")

        morning_star = talib.CDLMORNINGSTAR(open_p, high_p, low_p, close_p)
        if morning_star[-1] != 0:
            patterns.append("Morning Star")

        evening_star = talib.CDLEVENINGSTAR(open_p, high_p, low_p, close_p)
        if evening_star[-1] != 0:
            patterns.append("Evening Star")

        harami = talib.CDLHARAMI(open_p, high_p, low_p, close_p)
        if harami[-1] == 100:
            patterns.append("Bullish Harami")
        elif harami[-1] == -100:
            patterns.append("Bearish Harami")

        piercing = talib.CDLPIERCING(open_p, high_p, low_p, close_p)
        if piercing[-1] != 0:
            patterns.append("Piercing Pattern")

        dark_cloud = talib.CDLDARKCLOUDCOVER(open_p, high_p, low_p, close_p)
        if dark_cloud[-1] != 0:
            patterns.append("Dark Cloud Cover")

    else:
        last_o, last_h, last_l, last_c = open_p[-1], high_p[-1], low_p[-1], close_p[-1]
        prev_o, prev_h, prev_l, prev_c = open_p[-2], high_p[-2], low_p[-2], close_p[-2]

        # Bullish / Bearish Engulfing
        if prev_c < prev_o and last_c > last_o and last_c >= prev_o and last_o <= prev_c:
            patterns.append("Bullish Engulfing")
        elif prev_c > prev_o and last_c < last_o and last_c <= prev_o and last_o >= prev_c:
            patterns.append("Bearish Engulfing")

        # Bullish / Bearish Harami (P2 body contained inside P1 body)
        p1_top = max(prev_o, prev_c)
        p1_bot = min(prev_o, prev_c)
        p2_top = max(last_o, last_c)
        p2_bot = min(last_o, last_c)
        if p2_top < p1_top and p2_bot > p1_bot:
            if prev_c < prev_o and last_c > last_o:
                patterns.append("Bullish Harami")
            elif prev_c > prev_o and last_c < last_o:
                patterns.append("Bearish Harami")

        body = abs(last_c - last_o)
        lower_shadow = min(last_c, last_o) - last_l
        upper_shadow = last_h - max(last_c, last_o)

        # Hammer (at bottom of downtrend: long lower shadow, small body)
        if lower_shadow > body * 2 and upper_shadow < body * 0.5 and last_c > last_o:
            patterns.append("Hammer")

        # Hanging Man (same shape as Hammer but bearish — context differentiated by caller)
        elif lower_shadow > body * 2 and upper_shadow < body * 0.5 and last_c <= last_o:
            patterns.append("Hanging Man")

        # Shooting Star (long upper shadow, small body, at top)
        if upper_shadow > body * 2 and lower_shadow < body * 0.5:
            patterns.append("Shooting Star")

        # Doji (near-zero body)
        if body <= (last_h - last_l) * 0.1:
            patterns.append("Doji")

    return patterns
