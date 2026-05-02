import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from SmartApi import SmartConnect
import pyotp
import requests
import time

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

obj = SmartConnect(api_key=API_KEY)
totp = pyotp.TOTP(TOTP_SECRET).now()
obj.generateSession(CLIENT_ID, PASSWORD, totp)

send("🚀 FEB–APR BACKTEST STARTED")

# ===== DATE RANGE (FEB → APR) =====
end = datetime.now()
start = end - timedelta(days=90)

# ===== F&O STOCKS =====
TOKENS = {
    "RELIANCE": "2885",
    "TCS": "11536",
    "HDFCBANK": "1333",
    "INFY": "1594",
    "ICICIBANK": "4963",
    "SBIN": "3045",
    "LT": "11483"
}

# ================= GET DATA =================
def get_data(token):
    all_data = []
    current = start

    while current < end:
        nxt = current + timedelta(days=5)

        try:
            params = {
                "exchange": "NSE",
                "symboltoken": token,
                "interval": "FIVE_MINUTE",
                "fromdate": current.strftime("%Y-%m-%d 09:15"),
                "todate": nxt.strftime("%Y-%m-%d 15:30")
            }

            res = obj.getCandleData(params)

            if res and 'data' in res and res['data']:
                all_data.extend(res['data'])

        except:
            pass

        current = nxt
        time.sleep(0.3)

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data)
    df.columns = ["time","open","high","low","close","volume"]

    df['time'] = pd.to_datetime(df['time'])
    df['date'] = df['time'].dt.date

    return df

# ================= LOAD =================
market_data = {}

for sym, token in TOKENS.items():
    df = get_data(token)

    print(sym, len(df))

    if not df.empty:
        market_data[sym] = df

send(f"✅ Loaded {len(market_data)} stocks")

if not market_data:
    send("❌ No data from API")
    exit()

# ================= BACKTEST =================
results = []
logs = []

dates = sorted(set(
    d for df in market_data.values()
    for d in df["date"]
))

for day in dates:

    pool = []

    for sym, df in market_data.items():

        day_df = df[df['date'] == day].sort_values(by="time")

        if len(day_df) < 6:
            continue

        # ===== REAL 9:30 LOGIC =====
        day_df['t'] = day_df['time'].dt.strftime("%H:%M")

        row_930 = day_df[day_df['t'] == "09:30"]

        if row_930.empty:
            row_930 = day_df.iloc[:6]

        row_930 = row_930.iloc[0]

        open_p = day_df.iloc[0]['open']
        ltp = row_930['close']

        change = ((ltp - open_p) / open_p) * 100

        if abs(change) < 0.7:
            continue

        pool.append((sym, change, ltp, day_df))

    if not pool:
        continue

    pool.sort(key=lambda x: abs(x[1]), reverse=True)

    sym, change, entry, df = pool[0]

    direction = "BUY" if change > 0 else "SELL"

    sl = entry * 0.99 if direction == "BUY" else entry * 1.01
    tp = entry * 1.02 if direction == "BUY" else entry * 0.98

    pnl = 0
    result = "NONE"

    for _, row in df.iterrows():

        if direction == "BUY":
            if row['high'] >= tp:
                pnl = 2
                result = "TP"
                break
            elif row['low'] <= sl:
                pnl = -1
                result = "SL"
                break

        else:
            if row['low'] <= tp:
                pnl = 2
                result = "TP"
                break
            elif row['high'] >= sl:
                pnl = -1
                result = "SL"
                break

    results.append(pnl)
    logs.append(f"{day} | {direction} {sym} | {result} | {pnl}%")

# ================= RESULT =================
total = len(results)
wins = len([x for x in results if x > 0])
winrate = (wins / total) * 100 if total else 0
avg = np.mean(results) if results else 0

msg = f"""
📊 FEB–APR BACKTEST RESULT

Trades: {total}
Win Rate: {round(winrate,2)}%
Avg Return: {round(avg,2)}%
"""

print(msg)
send(msg)

send("🔥 SAMPLE TRADES:\n" + "\n".join(logs[:15]))
