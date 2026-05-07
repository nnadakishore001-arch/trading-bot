
# =========================================================
# FINAL LIVE INTRADAY SCANNER (DEBUGGED)
# RAILWAY READY
# ANGEL ONE + TELEGRAM
# =========================================================

from SmartApi import SmartConnect
import pandas as pd
import numpy as np
import pyotp
import requests
import traceback
import os
import time
from datetime import datetime, timedelta

# =========================================================
# ENV VARIABLES FROM RAILWAY
# =========================================================
API_KEY = os.getenv("ANGEL_API_KEY")
CLIENT_CODE = os.getenv("ANGEL_CLIENT_CODE")
PASSWORD = os.getenv("ANGEL_PASSWORD")
TOTP_SECRET = os.getenv("ANGEL_TOTP")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# =========================================================
# CONFIGURATION
# =========================================================
    SECTORS = {
    "BANK": {"HDFCBANK":"1333","ICICIBANK":"4963","SBIN":"3045","AXISBANK":"5900","KOTAKBANK":"1922"},
    "IT": {"TCS":"11536","INFY":"1594","HCLTECH":"7229","TECHM":"13538","WIPRO":"3787"},
    "AUTO": {"TATAMOTORS":"3456","MARUTI":"10999","M&M":"2031","BAJAJ-AUTO":"16669"},
    "PHARMA": {"SUNPHARMA":"3351","CIPLA":"694","DRREDDY":"881","DIVISLAB":"10940"},
    "FMCG": {"ITC":"1660","HINDUNILVR":"1394","NESTLEIND":"17963"},
    "METAL": {"TATASTEEL":"3499","JSWSTEEL":"11723","HINDALCO":"1363"},
    "ENERGY": {"RELIANCE":"2885","ONGC":"2475","NTPC":"11630","POWERGRID":"14977"},
    "NBFC": {"BAJFINANCE":"317","BAJAJFINSV":"16675"},
    "INFRA": {"LT":"11483","ADANIPORTS":"15083"}
}

INDEXES = {
    "NIFTY": "99926000",
    "BANKNIFTY": "99926009"
}

MIN_SCORE = 4

# =========================================================
# LOGIN
# =========================================================
print("\n==========================")
print("ANGEL ONE LOGIN")
print("==========================")

obj = SmartConnect(api_key=API_KEY)
data = obj.generateSession(
    CLIENT_CODE,
    PASSWORD,
    pyotp.TOTP(TOTP_SECRET).now()
)

if not data or not data.get("status"):
    print("LOGIN FAILED")
    print(data)
    quit()

print("LOGIN SUCCESS")
feedToken = obj.getfeedToken()

# =========================================================
# TELEGRAM
# =========================================================
def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message}
        requests.post(url, data=payload)
    except Exception as e:
        print(f"TELEGRAM ERROR: {e}")

# =========================================================
# HISTORICAL DATA & INDICATORS
# =========================================================
def get_historical_data(symbol, token):
    try:
        to_date = datetime.now()
        from_date = to_date - timedelta(days=5)

        historicParam = {
            "exchange": "NSE",
            "symboltoken": token,
            "interval": "FIVE_MINUTE",
            "fromdate": from_date.strftime("%Y-%m-%d 09:15"),
            "todate": to_date.strftime("%Y-%m-%d %H:%M")
        }

        response = obj.getCandleData(historicParam)

        # 1. FIX: Null Response Safety Check
        if not response or not response.get("status") or "data" not in response:
            print(f"{symbol} -> API ERROR / NO DATA")
            return None

        candles = response.get("data")
        if not candles or len(candles) == 0:
            print(f"{symbol} -> ZERO CANDLES")
            return None

        df = pd.DataFrame(candles, columns=["datetime", "open", "high", "low", "close", "volume"])
        
        # 2. FIX: Convert datetime string to actual Datetime object (Crucial for VWAP)
        df['datetime'] = pd.to_datetime(df['datetime'])

        numeric_cols = ["open", "high", "low", "close", "volume"]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df.dropna(inplace=True)
        if len(df) < 20:
            return None

        # EMA
        df["EMA20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["EMA50"] = df["close"].ewm(span=50, adjust=False).mean()

        # 3. FIX: Standard RSI Calculation using Wilder's Smoothing
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.ewm(com=13, adjust=False).mean()
        avg_loss = loss.ewm(com=13, adjust=False).mean()
        rs = avg_gain / avg_loss
        df["RSI"] = 100 - (100 / (1 + rs))

        # 4. FIX: Accurate Intraday VWAP (Resets Daily)
        typical_price = (df["high"] + df["low"] + df["close"]) / 3
        df['typical_vol'] = typical_price * df["volume"]
        
        # Group by date to reset cumsum every day
        df['VWAP'] = df.groupby(df['datetime'].dt.date)['typical_vol'].cumsum() / df.groupby(df['datetime'].dt.date)['volume'].cumsum()

        # VOL RATIO
        avg_volume = df["volume"].rolling(20).mean()
        df["VOL_RATIO"] = df["volume"] / avg_volume

        df.dropna(inplace=True)
        return df

    except Exception as e:
        print(f"\n{symbol} DATA ERROR")
        traceback.print_exc()
        return None

# =========================================================
# INDEX TREND
# =========================================================
def get_market_trend():
    bullish_count = 0
    for index_name, token in INDEXES.items():
        try:
            df = get_historical_data(index_name, token)
            if df is None:
                continue
            latest = df.iloc[-1]
            if latest["close"] > latest["EMA20"]:
                bullish_count += 1
        except Exception:
            pass

    return "BULLISH" if bullish_count >= 1 else "SIDEWAYS"

# =========================================================
# CORE SCANNER FUNCTION
# =========================================================
def run_scanner():
    print(f"\n--- SCAN STARTED AT {datetime.now().strftime('%H:%M:%S')} ---")
    signals = []
    rejections = []
    
    market_trend = get_market_trend()
    print(f"MARKET TREND: {market_trend}")

    for symbol, token in STOCKS.items():
        try:
            df = get_historical_data(symbol, token)
            if df is None:
                rejections.append(f"{symbol} -> NO VALID DATA")
                continue

            latest = df.iloc[-1]
            close = latest["close"]
            ema20, ema50 = latest["EMA20"], latest["EMA50"]
            vwap, rsi, volume_ratio = latest["VWAP"], latest["RSI"], latest["VOL_RATIO"]

            if pd.isna(close) or pd.isna(vwap) or pd.isna(rsi):
                rejections.append(f"{symbol} -> INDICATOR NaN")
                continue

            score = 0
            reasons = []

            if close > vwap:
                score += 1
            else:
                reasons.append("Below VWAP")

            if ema20 > ema50:
                score += 1
            else:
                reasons.append("EMA Weak")

            if rsi > 52:
                score += 1
            else:
                reasons.append(f"RSI {round(rsi,2)}")

            if volume_ratio > 1.2:
                score += 1
            else:
                reasons.append(f"VOL {round(volume_ratio,2)}")

            if market_trend == "BULLISH":
                score += 1

            if score >= MIN_SCORE:
                target = round(close * 1.01, 2)
                stoploss = round(close * 0.995, 2)

                signal = f"🚀 BUY SIGNAL\nSTOCK: {symbol}\nPRICE: {round(close,2)}\nTARGET: {target}\nSTOPLOSS: {stoploss}\nRSI: {round(rsi,2)}\nSCORE: {score}/5"
                print(signal)
                send_telegram(signal)
                signals.append(symbol)
            else:
                rejections.append(f"{symbol} -> SCORE {score} ({', '.join(reasons)})")

        except Exception as e:
            print(f"{symbol} CRITICAL ERROR")
            traceback.print_exc()

    print("\n--- SCAN SUMMARY ---")
    print(f"TREND: {market_trend} | SIGNALS: {len(signals)} | REJECTIONS: {len(rejections)}")

# =========================================================
# 5. FIX: CONTINUOUS KEEP-ALIVE LOOP
# =========================================================
while True:
    try:
        run_scanner()
    except Exception as e:
        print("CRITICAL SCANNER LOOP ERROR")
        traceback.print_exc()
    
    print(f"\nWAITING 5 MINUTES... NEXT SCAN AT {(datetime.now() + timedelta(minutes=5)).strftime('%H:%M:%S')}")
    # Sleep for 5 minutes before scanning again
    time.sleep(300)
