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

send("🚀 FINAL OPTIMIZED BACKTEST STARTED")

# ===== STOCKS =====
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
# ===== DATE =====
end = datetime.now()
start = end - timedelta(days=90)

# ================= DATA =================
def get_data(token):
    all_data = []
    current = start

    while current < end:
        nxt = current + timedelta(days=5)

        try:
            res = obj.getCandleData({
                "exchange":"NSE",
                "symboltoken":token,
                "interval":"FIVE_MINUTE",
                "fromdate":current.strftime("%Y-%m-%d 09:15"),
                "todate":nxt.strftime("%Y-%m-%d 15:30")
            })

            if res and 'data' in res:
                all_data.extend(res['data'])
        except:
            pass

        current = nxt
        time.sleep(0.2)

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

        day_df = df[df['date']==day].sort_values("time")
        if len(day_df) < 6:
            continue

        # 9:30 logic
        row = day_df.iloc[3]
        open_p = day_df.iloc[0]['open']
        ltp = row['close']

        change = ((ltp-open_p)/open_p)*100

        # ORB (light)
        high_920 = day_df.iloc[:3]['high'].max()
        low_920 = day_df.iloc[:3]['low'].min()
        orb = ltp > high_920 or ltp < low_920

        sector_strength.setdefault(sec, []).append(change)

        if abs(change) < 0.7:
            continue

        pool.append({
            "sym": sym,
            "sector": sec,
            "change": change,
            "ltp": ltp,
            "df": day_df,
            "orb": orb
        })

    if not pool:
        continue

    # sector avg
    sector_strength = {k: sum(v)/len(v) for k,v in sector_strength.items()}

    # scoring
    scored = []

    for s in pool:
        score = 0

        if abs(s["change"]) > 1: score += 1
        if s["orb"]: score += 1
        if abs(sector_strength.get(s["sector"],0)) > 0.5: score += 1

        scored.append((score, s))

    scored.sort(key=lambda x: (x[0], abs(x[1]["change"])), reverse=True)

    top = scored[:2]
    final = top[0][1]

    direction = "BUY" if final["change"] > 0 else "SELL"
    entry = final["ltp"]

    sl = entry * 0.99 if direction=="BUY" else entry*1.01
    tp = entry * 1.02 if direction=="BUY" else entry*0.98

    pnl = 0
    result = "NONE"

    for _, row in final["df"].iterrows():
        if direction=="BUY":
            if row['high'] >= tp:
                pnl = 2; result="TP"; break
            elif row['low'] <= sl:
                pnl = -1; result="SL"; break
        else:
            if row['low'] <= tp:
                pnl = 2; result="TP"; break
            elif row['high'] >= sl:
                pnl = -1; result="SL"; break

    results.append(pnl)
    logs.append(f"{day} | {direction} {final['sym']} | {result} | {pnl}%")

# ================= RESULT =================
total = len(results)
wins = len([x for x in results if x>0])
winrate = (wins/total)*100 if total else 0

msg = f"""
📊 FINAL OPTIMIZED RESULT

Trades: {total}
Win Rate: {round(winrate,2)}%
"""

send(msg)
print(msg)
