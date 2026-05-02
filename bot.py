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

def login():
    for _ in range(5):
        try:
            obj = SmartConnect(api_key=API_KEY)
            totp = pyotp.TOTP(TOTP_SECRET).now()
            data = obj.generateSession(CLIENT_ID, PASSWORD, totp)

            if data.get("status"):
                obj.setAccessToken(data['data']['jwtToken'])
                return obj
        except:
            time.sleep(2)
    raise Exception("Login Failed")

obj = login()
send("🚀 BACKTEST STARTED")

# ========= SAFE LAST 3 MONTHS =========
today = datetime.now()

# Fix wrong system time (like 2026 bug)
if today.year > 2025:
    today = datetime(2025, 4, 30)

end = today
start = end - timedelta(days=90)

# ========= YOUR CONFIG (UNCHANGED) =========
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
    global obj
    all_data = []
    current = start

    while current < end:
        nxt = current + timedelta(days=2)

        for _ in range(3):
            try:
                res = obj.getCandleData({
                    "exchange": "NSE",
                    "symboltoken": token,
                    "interval": "FIVE_MINUTE",
                    "fromdate": current.strftime("%Y-%m-%d 09:15"),
                    "todate": nxt.strftime("%Y-%m-%d 15:30")
                })

                if res and res.get("errorCode") == "AG8001":
                    obj = login()
                    continue

                if res and res.get("data"):
                    all_data.extend(res["data"])
                break

            except:
                time.sleep(1)

        current = nxt
        time.sleep(0.6)

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

send(f"✅ Loaded {len(market)} stocks")

if not market:
    send("❌ No data loaded")
    exit()

# ========= BACKTEST =========
results = []
logs = []

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

        pool.append({
            "sym": sym,
            "sector": sec,
            "change": change,
            "ltp": close_930,
            "df": day_df
        })

    if not pool:
        continue

    sector_strength = {k: sum(v)/len(v) for k,v in sector_strength.items()}

    signals = []
    for s in pool:
        sec_str = sector_strength.get(s["sector"], 0)

        if abs(s["change"]) < 0.7:
            continue

        if (s["change"] > 0 and index_dir < 0) or (s["change"] < 0 and index_dir > 0):
            continue

        if abs(sec_str) < 0.3:
            continue

        signals.append(s)

    if not signals:
        continue

    signals.sort(key=lambda x: abs(x["change"]), reverse=True)

    final = signals[0]

    direction = "BUY" if final["change"] > 0 else "SELL"
    entry = final["ltp"]

    sl = entry * 0.99 if direction == "BUY" else entry * 1.01
    tp = entry * 1.02 if direction == "BUY" else entry * 0.98

    pnl = 0

    for _, row in final["df"].iterrows():

        if direction == "BUY":
            if row['high'] >= tp:
                pnl = 2
                break
            elif row['low'] <= sl:
                pnl = -1
                break
        else:
            if row['low'] <= tp:
                pnl = 2
                break
            elif row['high'] >= sl:
                pnl = -1
                break

    results.append(pnl)
    logs.append(f"{day} | {direction} {final['sym']} | {pnl}%")

# ========= RESULT =========
total = len(results)
wins = len([x for x in results if x > 0])
winrate = (wins / total) * 100 if total else 0

msg = f"""
📊 BACKTEST RESULT

Trades: {total}
Win Rate: {round(winrate,2)}%
"""

print(msg)
send(msg)

send("🔥 SAMPLE:\n" + "\n".join(logs[:10]))
