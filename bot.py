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
    except:
        pass

# ===== LOGIN =====
def login():
    for _ in range(3):
        try:
            obj = SmartConnect(api_key=API_KEY)
            totp = pyotp.TOTP(TOTP_SECRET).now()
            data = obj.generateSession(CLIENT_ID, PASSWORD, totp)

            if data['status']:
                obj.setAccessToken(data['data']['jwtToken'])
                return obj
        except:
            time.sleep(2)

    raise Exception("Login Failed")

API_KEY = "VYFnGUA8"
CLIENT_ID = "M373866"
PASSWORD = "0917"
TOTP_SECRET = "3MLPA7DT7BA674CP73DHFDWJ2Q"

obj = login()
send("🚀 FULL STRATEGY BACKTEST STARTED")

# ===== DATE =====
start = datetime(2026, 2, 1)
end = datetime(2026, 4, 30)

# ===== F&O HIGH VOLUME STOCKS =====
SECTORS = {
    "BANK": {"HDFCBANK":"1333","ICICIBANK":"4963","SBIN":"3045"},
    "IT": {"TCS":"11536","INFY":"1594"},
    "ENERGY": {"RELIANCE":"2885","ONGC":"2475"},
    "AUTO": {"TATAMOTORS":"3456","MARUTI":"10999"},
}

# ================= DATA =================
def get_data(token):
    global obj

    all_data = []
    current = start

    while current < end:
        nxt = current + timedelta(days=3)

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

            if res and 'data' in res:
                all_data.extend(res['data'])

        except:
            pass

        current = nxt
        time.sleep(0.6)

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
        print(f"Fetching {sym}")
        df = get_data(tok)

        if not df.empty:
            market[sym] = {"df": df, "sector": sec}

send(f"✅ Loaded {len(market)} stocks")

# ================= BACKTEST =================
results = []
logs = []

dates = sorted(set(d for v in market.values() for d in v["df"]["date"]))

for day in dates:

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

        # ===== VOLUME =====
        vol_now = day_df.iloc[3]['volume']
        avg_vol = day_df.iloc[:5]['volume'].mean()
        vol_ok = vol_now > 1.5 * avg_vol if avg_vol else False

        # ===== ORB =====
        high_920 = day_df.iloc[:3]['high'].max()
        low_920 = day_df.iloc[:3]['low'].min()
        orb = close_930 > high_920 or close_930 < low_920

        sector_strength.setdefault(sec, []).append(change)

        if abs(change) < 0.7:
            continue

        pool.append({
            "sym": sym,
            "sector": sec,
            "change": change,
            "ltp": close_930,
            "df": day_df,
            "orb": orb,
            "vol": vol_ok
        })

    if not pool:
        continue

    # ===== SECTOR =====
    sector_strength = {k: sum(v)/len(v) for k,v in sector_strength.items()}

    # ===== SCORING =====
    scored = []

    for s in pool:
        score = 0

        if abs(s["change"]) > 1: score += 1
        if s["orb"]: score += 1
        if s["vol"]: score += 1
        if abs(sector_strength.get(s["sector"],0)) > 0.5: score += 1

        scored.append((score, s))

    scored.sort(key=lambda x: (x[0], abs(x[1]["change"])), reverse=True)

    # ===== TOP 2 → FINAL =====
    top2 = scored[:2]
    final = top2[0][1]

    direction = "BUY" if final["change"] > 0 else "SELL"
    entry = final["ltp"]

    sl = entry * 0.99 if direction == "BUY" else entry * 1.01
    tp = entry * 1.02 if direction == "BUY" else entry * 0.98

    pnl = 0
    result = "NONE"

    for _, row in final["df"].iterrows():
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
    logs.append(f"{day} | {direction} {final['sym']} | {result} | {pnl}%")

# ================= RESULT =================
total = len(results)
wins = len([x for x in results if x > 0])
winrate = (wins / total) * 100 if total else 0

msg = f"""
📊 FULL STRATEGY BACKTEST

Trades: {total}
Win Rate: {round(winrate,2)}%
"""

send(msg)
print(msg)

send("🔥 SAMPLE:\n" + "\n".join(logs[:10]))
