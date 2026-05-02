import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from SmartApi import SmartConnect
import pyotp
import requests
import time

# ========= TELEGRAM =========
TOKEN = "8691427620:AAF5vkJmHqETtm2TyhEd6CLdozCPsa57ATg"
CHAT_ID = "890425913"

def send(msg):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
    except:
        pass

# ========= LOGIN =========
API_KEY = "VYFnGUA8"
CLIENT_ID = "M373866"
PASSWORD = "0917"
TOTP_SECRET = "3MLPA7DT7BA674CP73DHFDWJ2Q"

session_time = None

def login():
    global session_time
    for _ in range(3):
        try:
            obj = SmartConnect(api_key=API_KEY)
            totp = pyotp.TOTP(TOTP_SECRET).now()
            data = obj.generateSession(CLIENT_ID, PASSWORD, totp)

            if data.get("status"):
                obj.setAccessToken(data['data']['jwtToken'])
                session_time = datetime.now()
                print("Login Success")
                return obj
        except:
            time.sleep(3)

    raise Exception("Login Failed")

obj = login()
send("🚀 BACKTEST STARTED (30 Days)")

# ========= LAST 30 DAYS =========
today = datetime.now()

# safety if server clock is wrong
if today.year > 2025:
    today = datetime(2025, 4, 30)

end = today
start = end - timedelta(days=30)

# ========= YOUR CONFIG =========
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

INDICES = {
    "NIFTY": "99926000",
    "BANKNIFTY": "99926009"
}

# ========= DATA FETCH =========
def get_data(token):
    global obj, session_time

    all_data = []
    current = start

    while current < end:
        nxt = current + timedelta(days=1)

        # refresh session every 5 mins
        if session_time and (datetime.now() - session_time).seconds > 300:
            obj = login()

        try:
            res = obj.getCandleData({
                "exchange": "NSE",
                "symboltoken": token,
                "interval": "FIVE_MINUTE",
                "fromdate": current.strftime("%Y-%m-%d 09:15"),
                "todate": nxt.strftime("%Y-%m-%d 15:30")
            })

            if res and res.get("errorCode") == "AG8001":
                print("Token issue → wait + relogin")
                time.sleep(6)
                obj = login()
                continue

            if res and res.get("data"):
                all_data.extend(res["data"])

        except Exception as e:
            print("API error:", e)

        current = nxt

        # 🔥 KEY FIX: slow calls
        time.sleep(2.2)

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data, columns=["time","open","high","low","close","volume"])
    df['time'] = pd.to_datetime(df['time'])
    df['date'] = df['time'].dt.date
    return df

# ========= LOAD =========
market = {}
for sec, stocks in SECTORS.items():
    for sym, tok in stocks.items():
        print(f"Fetching {sym}")
        df = get_data(tok)
        if not df.empty:
            market[sym] = {"df": df, "sector": sec}

index_data = {}
for name, tok in INDICES.items():
    df = get_data(tok)
    if not df.empty:
        index_data[name] = df

send(f"Loaded {len(market)} stocks")

# ========= BACKTEST =========
results = []

dates = sorted(set(d for v in market.values() for d in v["df"]["date"]))

for day in dates:

    try:
        n_df = index_data["NIFTY"]
        n_day = n_df[n_df['date'] == day].sort_values("time")

        if len(n_day) < 4:
            continue

        n_open = n_day.iloc[0]['open']
        n_930  = n_day.iloc[3]['close']
        index_dir = 1 if n_930 > n_open else -1

    except:
        continue

    sector_strength = {}
    pool = []

    for sym, data in market.items():

        df = data["df"]
        sec = data["sector"]

        day_df = df[df['date'] == day].sort_values("time")

        if len(day_df) < 6:
            continue

        open_p = day_df.iloc[0]['open']
        close_930 = day_df.iloc[3]['close']

        change = ((close_930 - open_p) / open_p) * 100

        sector_strength.setdefault(sec, []).append(change)

        pool.append((sym, sec, change, close_930, day_df))

    if not pool:
        continue

    sector_strength = {k: sum(v)/len(v) for k,v in sector_strength.items()}

    signals = []
    for sym, sec, change, ltp, df in pool:
        sec_str = sector_strength.get(sec, 0)

        if abs(change) < 0.7:
            continue
        if (change > 0 and index_dir < 0) or (change < 0 and index_dir > 0):
            continue
        if abs(sec_str) < 0.3:
            continue

        signals.append((sym, change, ltp, df))

    if not signals:
        continue

    signals.sort(key=lambda x: abs(x[1]), reverse=True)

    sym, change, entry, df = signals[0]

    direction = "BUY" if change > 0 else "SELL"

    sl = entry * 0.99 if direction == "BUY" else entry * 1.01
    tp = entry * 1.02 if direction == "BUY" else entry * 0.98

    pnl = 0

    for _, row in df.iterrows():
        if direction == "BUY":
            if row['high'] >= tp:
                pnl = 2; break
            elif row['low'] <= sl:
                pnl = -1; break
        else:
            if row['low'] <= tp:
                pnl = 2; break
            elif row['high'] >= sl:
                pnl = -1; break

    results.append(pnl)

# ========= RESULT =========
total = len(results)
wins = len([x for x in results if x > 0])
winrate = (wins / total) * 100 if total else 0

msg = f"""
📊 BACKTEST RESULT (30 DAYS)

Trades: {total}
Win Rate: {round(winrate,2)}%
"""

print(msg)
send(msg)
