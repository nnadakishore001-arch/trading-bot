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

send("🚀 FINAL BACKTEST STARTED (9:30 STRATEGY)")

# ===== SECTORS =====
SECTORS = {
    "BANK": {"HDFCBANK":"HDFCBANK","ICICIBANK":"ICICIBANK","SBIN":"SBIN","AXISBANK":"AXISBANK","KOTAKBANK":"KOTAKBANK","INDUSINDBK":"INDUSINDBK"},
    "IT": {"TCS":"TCS","INFY":"INFY","HCLTECH":"HCLTECH","TECHM":"TECHM","WIPRO":"WIPRO","LTIM":"LTIM"},
    "AUTO": {"TATAMOTORS":"TATAMOTORS","MARUTI":"MARUTI","M&M":"M&M","BAJAJ-AUTO":"BAJAJ-AUTO","EICHERMOT":"EICHERMOT"},
    "PHARMA": {"SUNPHARMA":"SUNPHARMA","CIPLA":"CIPLA","DRREDDY":"DRREDDY","DIVISLAB":"DIVISLAB"},
    "FMCG": {"ITC":"ITC","HINDUNILVR":"HINDUNILVR","NESTLEIND":"NESTLEIND","BRITANNIA":"BRITANNIA"},
    "METAL": {"TATASTEEL":"TATASTEEL","JSWSTEEL":"JSWSTEEL","HINDALCO":"HINDALCO","VEDL":"VEDL"},
    "ENERGY": {"RELIANCE":"RELIANCE","ONGC":"ONGC","NTPC":"NTPC","POWERGRID":"POWERGRID"},
    "NBFC": {"BAJFINANCE":"BAJFINANCE","BAJAJFINSV":"BAJAJFINSV","CHOLAFIN":"CHOLAFIN"},
    "INFRA": {"LT":"LT","ADANIPORTS":"ADANIPORTS","ADANIENT":"ADANIENT"},
}

# ================= GET DATA =================
def get_data(symbol):
    try:
        df = yf.download(symbol + ".NS", period="3mo", interval="5m", progress=False)

        if df.empty:
            return pd.DataFrame()

        df = df.reset_index()

        df.rename(columns={
            "Datetime":"time",
            "Open":"open",
            "High":"high",
            "Low":"low",
            "Close":"close",
            "Volume":"volume"
        }, inplace=True)

        df['date'] = df['time'].dt.date

        return df

    except:
        return pd.DataFrame()

# ================= LOAD DATA =================
market_data = {}

for sec, stocks in SECTORS.items():
    for sym in stocks.keys():

        df = get_data(sym)

        if not df.empty and len(df) > 50:
            market_data[sym] = {"sector": sec, "df": df}

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

        day_df = df[df['date'] == day].sort_values(by="time")

        if len(day_df) < 4:
            continue

        # ===== 9:30 candle =====
        first4 = day_df.iloc[:4]

        open_p = first4.iloc[0]['open']
        ltp = first4.iloc[3]['close']

        change = ((ltp - open_p) / open_p) * 100

        sector_strength.setdefault(sec, []).append(change)

        if abs(change) < 1:
            continue

        pool.append({
            "sym": sym,
            "sector": sec,
            "ltp": ltp,
            "change": change
        })

    sector_strength = {
        k: sum(v) / len(v)
        for k, v in sector_strength.items() if v
    }

    if not pool:
        continue

    # ===== TOP 2 =====
    pool = sorted(pool, key=lambda x: abs(x['change']), reverse=True)
    top2 = pool[:2]

    final = top2[0]

    direction = "BUY" if final['change'] > 0 else "SELL"
    entry = final['ltp']

    # ===== SL / TP =====
    sl = entry * 0.99 if direction == "BUY" else entry * 1.01
    tp = entry * 1.02 if direction == "BUY" else entry * 0.98

    full_day = df[df['date'] == day]

    pnl = 0
    result = "NONE"

    for _, row in full_day.iterrows():

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

    logs.append(f"{day} | {direction} {final['sym']} | {result} | {pnl}%")

# ================= RESULT =================
total = len(results)
wins = len([x for x in results if x > 0])
winrate = (wins / total) * 100 if total else 0
avg = np.mean(results) if results else 0

summary = f"""
📊 FINAL BACKTEST (9:30 STRATEGY)

Total Trades: {total}
Win Rate: {round(winrate,2)}%
Avg Return: {round(avg,2)}%
"""

print(summary)
send(summary)

send("🔥 SAMPLE TRADES:\n" + "\n".join(logs[:15]))
