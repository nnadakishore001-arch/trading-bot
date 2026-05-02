import requests
import time
from datetime import datetime
from SmartApi import SmartConnect
import pyotp
import numpy as np

# ===== TELEGRAM =====
TOKEN = "8691427620:AAF5vkJmHqETtm2TyhEd6CLdozCPsa57ATg"
CHAT_ID = "890425913"

def send(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": msg})

# ===== LOGIN =====
API_KEY = "VYFnGUA8"
CLIENT_ID = "M373866"
PASSWORD = "0917"
TOTP_SECRET = "3MLPA7DT7BA674CP73DHFDWJ2Q"

totp = pyotp.TOTP(TOTP_SECRET).now()
obj = SmartConnect(api_key=API_KEY)
obj.generateSession(CLIENT_ID, PASSWORD, totp)

import requests, time, pyotp, numpy as np
from datetime import datetime
from SmartApi import SmartConnect

# ========= TELEGRAM =========
TOKEN = "8706462182:AAHt5JMZ5tfMUjfKTYncwcfHZCflpQY9hHA"
CHAT_ID = "890425913"

def send(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": msg})

# ========= LOGIN =========
API_KEY = "VYFnGUA8"
CLIENT_ID = "M373866"
PASSWORD = "0917"
TOTP_SECRET = "3MLPA7DT7BA674CP73DHFDWJ2Q"

def login():
    obj = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    obj.generateSession(CLIENT_ID, PASSWORD, totp)
    return obj

# ========= F&O SECTORS =========
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
# ================= GET DATA =================
def get_data(token):

    try:
        params = {
            "exchange": "NSE",
            "symboltoken": token,
            "interval": "FIVE_MINUTE",
            "fromdate": start.strftime("%Y-%m-%d 09:15"),
            "todate": end.strftime("%Y-%m-%d 15:30")
        }

        res = obj.getCandleData(params)

        # ===== SAFETY =====
        if not res or 'data' not in res or not res['data']:
            return pd.DataFrame()

        df = pd.DataFrame(res['data'])

        # Fix columns dynamically
        df = df.iloc[:, :6]
        df.columns = ["time","open","high","low","close","volume"]

        # ===== TYPE FIX =====
        df['time'] = pd.to_datetime(df['time'], errors='coerce')

        for col in ["open","high","low","close","volume"]:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        df = df.dropna()

        if df.empty:
            return pd.DataFrame()

        df['date'] = df['time'].dt.date

        return df

    except Exception as e:
        print(f"❌ Error in token {token}: {e}")
        return pd.DataFrame()


# ================= LOAD DATA =================
market_data = {}

for sec, stocks in SECTORS.items():
    for sym, token in stocks.items():

        df = get_data(token)

        if df.empty:
            print(f"⚠️ No data for {sym}")
            continue

        if len(df) < 50:
            print(f"⚠️ Not enough data for {sym}")
            continue

        market_data[sym] = {
            "sector": sec,
            "df": df
        }

print(f"✅ Loaded {len(market_data)} stocks successfully")


# ================= INDICATORS =================
def rsi(close):
    if len(close) < 2:
        return 50
    diff = np.diff(close)
    gain = np.mean([x for x in diff if x > 0] or [0])
    loss = np.mean([-x for x in diff if x < 0] or [1])
    rs = gain / loss if loss else 1
    return 100 - (100 / (1 + rs))


def adx(close):
    if len(close) < 2:
        return 0
    return min(np.std(close) * 10, 50)


def atr(df):
    if len(df) < 2:
        return 0

    tr = []
    for i in range(1, len(df)):
        high = df.iloc[i]['high']
        low = df.iloc[i]['low']
        prev_close = df.iloc[i-1]['close']

        tr.append(max(high - low, abs(high - prev_close)))

    return np.mean(tr) if tr else 0


# ================= BACKTEST =================
results = []

dates = sorted(set(
    d for v in market_data.values()
    for d in v["df"]["date"]
))

for day in dates:

    sector_strength = {}
    pool = []

    for sym, data in market_data.items():

        df = data["df"]
        sec = data["sector"]

        # ===== DAY DATA =====
        day_df = df[df['date'] == day].copy()

        if day_df.empty:
            continue

        day_df = day_df.sort_values(by="time")

        if len(day_df) < 4:
            continue

        # Take first 4 candles safely
        first4 = day_df.head(4)

        open_p = first4.iloc[0]['open']
        ltp = first4.iloc[-1]['close']

        change = ((ltp - open_p) / open_p) * 100

        closes = first4['close'].values

        # ===== ORB =====
        high_920 = first4.iloc[:2]['high'].max()
        low_920 = first4.iloc[:2]['low'].min()
        breakout = ltp > high_920 or ltp < low_920

        # ===== VOLUME =====
        vol_now = first4.iloc[-1]['volume']
        avg_vol = first4.iloc[:3]['volume'].mean()
        volume_ok = vol_now > 1.5 * avg_vol

        pool.append({
            "sym": sym,
            "sector": sec,
            "ltp": ltp,
            "change": change,
            "rsi": rsi(closes),
            "adx": adx(closes),
            "atr": atr(first4),
            "breakout": breakout,
            "volume": volume_ok
        })

        sector_strength.setdefault(sec, []).append(change)

    # ===== SECTOR STRENGTH =====
    sector_strength = {
        k: sum(v)/len(v)
        for k, v in sector_strength.items() if v
    }

    # ===== SIGNAL FILTER =====
    signals = []

    for s in pool:

        sec_str = sector_strength.get(s["sector"], 0)
        direction = "BUY" if s["change"] > 0 else "SELL"

        score = 0

        if abs(sec_str) > 0.7: score += 1
        if abs(s["change"]) > 1: score += 1
        if s["adx"] > 20: score += 1
        if s["breakout"]: score += 1
        if s["volume"]: score += 1

        if direction == "BUY" and s["rsi"] > 55: score += 1
        if direction == "SELL" and s["rsi"] < 45: score += 1

        if score >= 4:
            signals.append((s, direction))

    if not signals:
        continue

    # ===== PICK BEST =====
    s, direction = sorted(
        signals,
        key=lambda x: abs(x[0]["change"]),
        reverse=True
    )[0]

    entry = s["ltp"]

    # ===== EXIT PRICE (FULL DAY FIX) =====
    full_day = df[df['date'] == day].sort_values(by="time")

    if full_day.empty:
        continue

    exit_price = full_day.iloc[-1]['close']

    if direction == "BUY":
        pnl = ((exit_price - entry) / entry) * 100
    else:
        pnl = ((entry - exit_price) / entry) * 100

    results.append(pnl)


# ================= FINAL RESULT =================
total = len(results)
wins = len([x for x in results if x > 0])
winrate = (wins / total) * 100 if total else 0

print("\n📊 BACKTEST RESULT (3 Months)\n")
print("Total Trades:", total)
print("Win Rate:", round(winrate, 2), "%")
print("Avg Return:", round(np.mean(results), 2), "%")
