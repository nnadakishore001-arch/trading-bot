
# =========================================================
# FINAL LIVE INTRADAY SCANNER
# ANGEL ONE + TELEGRAM + SECTOR SCANNER
# RAILWAY READY
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
# ENV VARIABLES
# =========================================================

API_KEY = os.getenv("API_KEY")

CLIENT_ID = os.getenv("CLIENT_ID")

PASSWORD = os.getenv("PASSWORD")

TOTP_SECRET = os.getenv("TOTP_SECRET")

TG_TOKEN = os.getenv("TG_TOKEN")

CHAT_ID = os.getenv("CHAT_ID")

# =========================================================
# ENV VALIDATION
# =========================================================

required_env = {

    "API_KEY": API_KEY,

    "CLIENT_ID": CLIENT_ID,

    "PASSWORD": PASSWORD,

    "TOTP_SECRET": TOTP_SECRET,

    "TG_TOKEN": TG_TOKEN,

    "CHAT_ID": CHAT_ID
}

for key, value in required_env.items():

    if not value:

        raise Exception(
            f"{key} environment variable missing in Railway"
        )

print("\nALL ENV VARIABLES LOADED SUCCESSFULLY")

# =========================================================
# SECTOR STOCKS
# =========================================================

SECTORS = {

    "BANK": {
        "HDFCBANK":"1333",
        "ICICIBANK":"4963",
        "SBIN":"3045",
        "AXISBANK":"5900",
        "KOTAKBANK":"1922"
    },

    "IT": {
        "TCS":"11536",
        "INFY":"1594",
        "HCLTECH":"7229",
        "TECHM":"13538",
        "WIPRO":"3787"
    },

    "AUTO": {
        "TATAMOTORS":"3456",
        "MARUTI":"10999",
        "M&M":"2031",
        "BAJAJ-AUTO":"16669"
    },

    "PHARMA": {
        "SUNPHARMA":"3351",
        "CIPLA":"694",
        "DRREDDY":"881",
        "DIVISLAB":"10940"
    },

    "FMCG": {
        "ITC":"1660",
        "HINDUNILVR":"1394",
        "NESTLEIND":"17963"
    },

    "METAL": {
        "TATASTEEL":"3499",
        "JSWSTEEL":"11723",
        "HINDALCO":"1363"
    },

    "ENERGY": {
        "RELIANCE":"2885",
        "ONGC":"2475",
        "NTPC":"11630",
        "POWERGRID":"14977"
    },

    "NBFC": {
        "BAJFINANCE":"317",
        "BAJAJFINSV":"16675"
    },

    "INFRA": {
        "LT":"11483",
        "ADANIPORTS":"15083"
    }
}

# =========================================================
# INDEX TOKENS
# =========================================================

INDEXES = {
    "NIFTY": "99926000",
    "BANKNIFTY": "99926009"
}

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

if not data["status"]:

    print("LOGIN FAILED")
    print(data)

    raise Exception("Angel One Login Failed")

print("LOGIN SUCCESS")

feedToken = obj.getfeedToken()

# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message):

    try:

        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

        payload = {
            "chat_id": CHAT_ID,
            "text": message
        }

        requests.post(url, data=payload)

    except Exception as e:

        print("TELEGRAM ERROR")
        print(e)

# =========================================================
# HISTORICAL DATA
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

        print(f"\n{symbol} API RESPONSE RECEIVED")

        if "data" not in response:

            print(f"{symbol} -> NO DATA KEY")
            print(response)

            return None

        candles = response["data"]

        if candles is None:

            print(f"{symbol} -> EMPTY CANDLES")

            return None

        if len(candles) == 0:

            print(f"{symbol} -> ZERO CANDLES")

            return None

        df = pd.DataFrame(
            candles,
            columns=[
                "datetime",
                "open",
                "high",
                "low",
                "close",
                "volume"
            ]
        )

        if df.empty:

            print(f"{symbol} -> EMPTY DATAFRAME")

            return None

        # =================================================
        # NUMERIC CONVERSION
        # =================================================

        numeric_cols = [
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]

        for col in numeric_cols:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        df.dropna(inplace=True)

        if len(df) < 20:

            print(f"{symbol} -> NOT ENOUGH DATA")

            return None

        # =================================================
        # EMA
        # =================================================

        df["EMA20"] = df["close"].ewm(
            span=20,
            adjust=False
        ).mean()

        df["EMA50"] = df["close"].ewm(
            span=50,
            adjust=False
        ).mean()

        # =================================================
        # RSI
        # =================================================

        delta = df["close"].diff()

        gain = delta.where(delta > 0, 0)

        loss = -delta.where(delta < 0, 0)

        avg_gain = gain.rolling(14).mean()

        avg_loss = loss.rolling(14).mean()

        rs = avg_gain / avg_loss

        df["RSI"] = 100 - (100 / (1 + rs))

        # =================================================
        # VWAP
        # =================================================

        typical_price = (
            df["high"] +
            df["low"] +
            df["close"]
        ) / 3

        df["VWAP"] = (
            (typical_price * df["volume"]).cumsum()
            /
            df["volume"].cumsum()
        )

        # =================================================
        # VOLUME RATIO
        # =================================================

        avg_volume = df["volume"].rolling(20).mean()

        df["VOL_RATIO"] = (
            df["volume"] / avg_volume
        )

        df.dropna(inplace=True)

        if df.empty:

            print(f"{symbol} -> INDICATOR NaN")

            return None

        return df

    except Exception as e:

        print(f"\n{symbol} DATA ERROR")

        traceback.print_exc()

        return None

# =========================================================
# MARKET TREND
# =========================================================

def get_market_trend():

    bullish_count = 0

    for index_name, token in INDEXES.items():

        try:

            print(f"\nCHECKING INDEX: {index_name}")

            df = get_historical_data(index_name, token)

            if df is None:

                print(f"{index_name} FAILED")

                continue

            latest = df.iloc[-1]

            close = latest["close"]

            ema20 = latest["EMA20"]

            print(f"""
====================
{index_name}

CLOSE: {close}
EMA20: {ema20}
====================
""")

            if close > ema20:
                bullish_count += 1

        except Exception as e:

            traceback.print_exc()

    if bullish_count >= 1:
        return "BULLISH"

    return "SIDEWAYS"

# =========================================================
# MAIN
# =========================================================

signals = []

rejections = []

market_trend = get_market_trend()

print(f"\nMARKET TREND: {market_trend}")

send_telegram(
    f"📊 MARKET TREND: {market_trend}"
)

MIN_SCORE = 4

# =========================================================
# SECTOR LOOP
# =========================================================

for sector_name, sector_stocks in SECTORS.items():

    print(f"\n==========================")
    print(f"SCANNING SECTOR: {sector_name}")
    print("==========================")

    for symbol, token in sector_stocks.items():

        try:

            df = get_historical_data(symbol, token)

            if df is None:

                rejection = (
                    f"{sector_name} | "
                    f"{symbol} -> NO VALID DATA"
                )

                print(rejection)

                rejections.append(rejection)

                continue

            latest = df.iloc[-1]

            close = latest["close"]

            ema20 = latest["EMA20"]

            ema50 = latest["EMA50"]

            vwap = latest["VWAP"]

            rsi = latest["RSI"]

            volume_ratio = latest["VOL_RATIO"]

            if any([
                pd.isna(close),
                pd.isna(ema20),
                pd.isna(ema50),
                pd.isna(vwap),
                pd.isna(rsi),
                pd.isna(volume_ratio)
            ]):

                rejection = (
                    f"{sector_name} | "
                    f"{symbol} -> INDICATOR NaN"
                )

                print(rejection)

                rejections.append(rejection)

                continue

            # =================================================
            # SCORE
            # =================================================

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
                reasons.append(
                    f"VOL {round(volume_ratio,2)}"
                )

            if market_trend == "BULLISH":
                score += 1

            # =================================================
            # DEBUG
            # =================================================

            print(f"""
====================
SECTOR: {sector_name}

STOCK: {symbol}

CLOSE: {close}

VWAP: {vwap}

EMA20: {ema20}
EMA50: {ema50}

RSI: {rsi}

VOL_RATIO: {volume_ratio}

SCORE: {score}
====================
""")

            # =================================================
            # SIGNAL
            # =================================================

            if score >= MIN_SCORE:

                target = round(close * 1.01, 2)

                stoploss = round(close * 0.995, 2)

                signal = f"""
🚀 BUY SIGNAL

SECTOR: {sector_name}

STOCK: {symbol}

PRICE: {round(close,2)}

TARGET: {target}

STOPLOSS: {stoploss}

RSI: {round(rsi,2)}

VOL_RATIO: {round(volume_ratio,2)}

SCORE: {score}

TIME: {datetime.now().strftime("%H:%M:%S")}
"""

                print(signal)

                send_telegram(signal)

                signals.append(signal)

            else:

                rejection = (
                    f"{sector_name} | "
                    f"{symbol} -> "
                    f"SCORE {score} -> "
                    f"{', '.join(reasons)}"
                )

                print(rejection)

                rejections.append(rejection)

        except Exception as e:

            print(f"{symbol} CRITICAL ERROR")

            traceback.print_exc()

# =========================================================
# FINAL SUMMARY
# =========================================================

summary = f"""
=========================
FINAL SUMMARY
=========================

MARKET TREND: {market_trend}

TOTAL SIGNALS: {len(signals)}

TOTAL REJECTIONS: {len(rejections)}
"""

print(summary)

send_telegram(summary)

# =========================================================
# REJECTION LOGS
# =========================================================

for reject in rejections:

    print(reject)

print("\nSCAN COMPLETED")

# =========================================================
# KEEP ALIVE
# =========================================================

while True:

    print(
        f"BOT ACTIVE -> "
        f"{datetime.now().strftime('%H:%M:%S')}"
    )

    time.sleep(300)
