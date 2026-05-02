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
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
    except Exception as e:
        print("Telegram Error:", e)

# ===== LOGIN (RETRY SAFE) =====
API_KEY = "VYFnGUA8"
CLIENT_ID = "M373866"
PASSWORD = "0917"
TOTP_SECRET = "3MLPA7DT7BA674CP73DHFDWJ2Q"

def login():
    for i in range(3):
        try:
            obj = SmartConnect(api_key=API_KEY)
            totp = pyotp.TOTP(TOTP_SECRET).now()

            data = obj.generateSession(CLIENT_ID, PASSWORD, totp)

            if data['status']:
                obj.setAccessToken(data['data']['jwtToken'])
                print("✅ Login Success")
                return obj

        except Exception as e:
            print("Login retry...", e)
            time.sleep(2)

    raise Exception("Login Failed")

obj = login()
send("🚀 BACKTEST STARTED")

# ===== DATE RANGE =====
start = datetime(2025, 2, 1)
end = datetime(2025, 4, 30)

# ===== SAFE STOCK SET (AVOID RATE LIMIT) =====
SECTORS = {
    "BANK": {"HDFCBANK":"1333","ICICIBANK":"4963"},
    "IT": {"TCS":"11536","INFY":"1594"},
    "ENERGY": {"RELIANCE":"2885"}
}

# ================= DATA =================
def get_data(token):
    global obj

    all_data = []
    current = start

    while current < end:
        nxt = current + timedelta(days=3)

        retry = 0
        success = False

        while retry < 3:
            try:
                res = obj.getCandleData({
                    "exchange": "NSE",
                    "symboltoken": token,
                    "interval": "FIVE_MINUTE",
                    "fromdate": current.strftime("%Y-%m-%d 09:15"),
                    "todate": nxt.strftime("%Y-%m-%d 15:30")
                })

                # Token expired → re-login
                if res and res.get("errorCode") == "AG8001":
                    print("🔁 Token expired → Re-login")
                    obj = login()
                    retry += 1
                    continue

                if res and 'data' in res and res['data']:
                    all_data.extend(res['data'])

                success = True
                break

            except Exception as e:
                print("API Error:", e)
                retry += 1
                time.sleep(1)

        if not success:
            print(f"⚠️ Skipping {current} → {nxt}")

        current = nxt
        time.sleep(0.6)   # 🔥 RATE LIMIT FIX

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data, columns=["time","open","high","low","close","volume"])
    df['time'] = pd.to_datetime(df['time'])
    df['date'] = df['time'].dt.date

    return df

# ================= LOAD =================
market = {}

for sec, stocks in SECTORS.items():
    for sym, tok in stocks.items():
        print(f"Fetching {sym}...")
        df = get_data(tok)

        print(sym, "rows:", len(df))

        if not df.empty:
            market[sym] = {"df": df, "sector": sec}

send(f"✅ Loaded {len(market)} stocks")

if not market:
    send("❌ No data loaded")
    exit()

# ================= BACKTEST =================
results = []
logs = []

dates = sorted(set(d for v in market.values() for d in v["df"]["date"]))

for day in dates:

    pool = []

    for sym, data in market.items():

        df = data["df"]
        day_df = df[df['date'] == day].sort_values("time")

        if len(day_df) < 6:
            continue

        # ===== 9:30 LOGIC =====
        open_p = day_df.iloc[0]['open']
        ltp = day_df.iloc[3]['close']

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
                pnl = 2; result = "TP"; break
            elif row['low'] <= sl:
                pnl = -1; result = "SL"; break
        else:
            if row['low'] <= tp:
                pnl = 2; result = "TP"; break
            elif row['high'] >= sl:
                pnl = -1; result = "SL"; break

    results.append(pnl)
    logs.append(f"{day} | {direction} {sym} | {result} | {pnl}%")

# ================= RESULT =================
total = len(results)
wins = len([x for x in results if x > 0])
winrate = (wins / total) * 100 if total else 0
avg = np.mean(results) if results else 0

msg = f"""
📊 FINAL BACKTEST RESULT

Trades: {total}
Win Rate: {round(winrate,2)}%
Avg Return: {round(avg,2)}%
"""

print(msg)
send(msg)

send("🔥 SAMPLE TRADES:\n" + "\n".join(logs[:10]))
