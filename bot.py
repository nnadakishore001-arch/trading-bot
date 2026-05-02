import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from SmartApi import SmartConnect
import pyotp
import requests

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

send("📊 BACKTEST (LIVE LOGIC) STARTED")

# ===== DATE RANGE =====
end = datetime.now()
start = end - timedelta(days=90)

# ===== SAME SECTORS =====
SECTORS = {
    "BANK": {"HDFCBANK":"1333","ICICIBANK":"4963","SBIN":"3045","AXISBANK":"5900","KOTAKBANK":"1922","INDUSINDBK":"5258"},
    "IT": {"TCS":"11536","INFY":"1594","HCLTECH":"7229","TECHM":"13538","WIPRO":"3787","LTIM":"17818"},
    "AUTO": {"TATAMOTORS":"3456","MARUTI":"10999","M&M":"2031","BAJAJ-AUTO":"16669","EICHERMOT":"910"},
    "PHARMA": {"SUNPHARMA":"3351","CIPLA":"694","DRREDDY":"881","DIVISLAB":"10940"},
    "FMCG": {"ITC":"1660","HINDUNILVR":"1394","NESTLEIND":"17963","BRITANNIA":"547"},
    "METAL": {"TATASTEEL":"3499","JSWSTEEL":"11723","HINDALCO":"1363","VEDL":"3063"},
    "ENERGY": {"RELIANCE":"2885","ONGC":"2475","NTPC":"11630","POWERGRID":"14977"},
    "NBFC": {"BAJFINANCE":"317","BAJAJFINSV":"16675","CHOLAFIN":"685"},
    "INFRA": {"LT":"11483","ADANIPORTS":"15083","ADANIENT":"25"},
}

def get_data(token):

    all_data = []

    current_start = start

    while current_start < end:

        current_end = current_start + timedelta(days=7)

        if current_end > end:
            current_end = end

        try:
            params = {
                "exchange": "NSE",
                "symboltoken": token,
                "interval": "FIVE_MINUTE",
                "fromdate": current_start.strftime("%Y-%m-%d 09:15"),
                "todate": current_end.strftime("%Y-%m-%d 15:30")
            }

            res = obj.getCandleData(params)

            if res and 'data' in res and res['data']:
                all_data.extend(res['data'])

        except:
            pass

        current_start = current_end

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data)

    df = df.iloc[:, :6]
    df.columns = ["time","open","high","low","close","volume"]

    df['time'] = pd.to_datetime(df['time'], errors='coerce')

    for col in ["open","high","low","close","volume"]:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    df = df.dropna()
    df['date'] = df['time'].dt.date

    return df
# ================= LOAD =================
market_data = {}

for sec, stocks in SECTORS.items():
    for sym, token in stocks.items():
        df = get_data(token)
        if not df.empty and len(df) > 50:
            market_data[sym] = {"sector":sec,"df":df}

send(f"✅ Loaded {len(market_data)} stocks")

# ================= BACKTEST =================
results = []
logs = []

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

        day_df = df[df['date']==day].sort_values(by="time")

        if len(day_df) < 4:
            continue

        # ===== 9:30 candle =====
        first4 = day_df.iloc[:4]

        open_p = first4.iloc[0]['open']
        ltp = first4.iloc[3]['close']

        change = ((ltp - open_p)/open_p)*100

        sector_strength.setdefault(sec, []).append(change)

        if abs(change) < 1:
            continue

        pool.append({
            "sym": sym,
            "sector": sec,
            "ltp": ltp,
            "change": change
        })

    # ===== SECTOR STRENGTH =====
    sector_strength = {
        k: sum(v)/len(v)
        for k,v in sector_strength.items() if v
    }

    if not pool:
        continue

    # ===== PICK TOP 2 =====
    pool = sorted(pool, key=lambda x: abs(x['change']), reverse=True)
    top2 = pool[:2]

    final = top2[0]

    direction = "BUY" if final['change'] > 0 else "SELL"
    entry = final['ltp']

    # ===== SL / TP =====
    sl = entry * 0.99 if direction == "BUY" else entry * 1.01
    tp = entry * 1.02 if direction == "BUY" else entry * 0.98

    full_day = df[df['date']==day]

    pnl = 0
    result = "NONE"

    for _, row in full_day.iterrows():

        high = row['high']
        low = row['low']

        if direction == "BUY":
            if high >= tp:
                pnl = 2
                result = "TP"
                break
            elif low <= sl:
                pnl = -1
                result = "SL"
                break

        else:
            if low <= tp:
                pnl = 2
                result = "TP"
                break
            elif high >= sl:
                pnl = -1
                result = "SL"
                break

    results.append(pnl)

    logs.append(f"{day} | {direction} {final['sym']} | {result} | {pnl}%")

# ================= RESULT =================
total = len(results)
wins = len([x for x in results if x > 0])
winrate = (wins / total)*100 if total else 0
avg = np.mean(results) if results else 0

summary = f"""
📊 BACKTEST RESULT (LIVE LOGIC)

Total Trades: {total}
Win Rate: {round(winrate,2)}%
Avg Return: {round(avg,2)}%
"""

print(summary)
send(summary)

# ===== SAMPLE TRADES =====
send("🔥 SAMPLE TRADES:\n" + "\n".join(logs[:15]))
