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
    "BANK": ["HDFCBANK","ICICIBANK","SBIN","AXISBANK","KOTAKBANK","INDUSINDBK"],
    "IT": ["TCS","INFY","HCLTECH","TECHM","WIPRO","LTIM"],
    "AUTO": ["TATAMOTORS","MARUTI","M&M","BAJAJ-AUTO","EICHERMOT"],
    "PHARMA": ["SUNPHARMA","CIPLA","DRREDDY","DIVISLAB"],
    "FMCG": ["ITC","HINDUNILVR","NESTLEIND","BRITANNIA"],
    "METAL": ["TATASTEEL","JSWSTEEL","HINDALCO","VEDL"],
    "ENERGY": ["RELIANCE","ONGC","NTPC","POWERGRID"],
    "NBFC": ["BAJFINANCE","BAJAJFINSV","CHOLAFIN"],
    "INFRA": ["LT","ADANIPORTS","ADANIENT"],
}

INDEX = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK"
}

# ================= DATA =================
def get_data(symbol):
    df = yf.download(symbol + ".NS", period="3mo", interval="5m", progress=False)
    if df.empty:
        return pd.DataFrame()

    df = df.reset_index()
    df.rename(columns={
        "Datetime":"time","Open":"open","High":"high",
        "Low":"low","Close":"close","Volume":"volume"
    }, inplace=True)

    df['date'] = df['time'].dt.date
    return df

def get_index(symbol):
    df = yf.download(symbol, period="3mo", interval="5m", progress=False)
    df = df.reset_index()
    df.rename(columns={"Datetime":"time","Open":"open","Close":"close"}, inplace=True)
    df['date'] = df['time'].dt.date
    return df

# ================= LOAD =================
market_data = {}
for sec, stocks in SECTORS.items():
    for sym in stocks:
        df = get_data(sym)
        if not df.empty:
            market_data[sym] = {"sector": sec, "df": df}

send(f"✅ Loaded {len(market_data)} stocks")

nifty = get_index(INDEX["NIFTY"])
banknifty = get_index(INDEX["BANKNIFTY"])

# ================= BACKTEST =================
results = []
logs = []

dates = sorted(set(
    d for v in market_data.values()
    for d in v["df"]["date"]
))

for day in dates:

    try:
        n_df = nifty[nifty['date'] == day].iloc[:4]
        b_df = banknifty[banknifty['date'] == day].iloc[:4]

        if len(n_df) < 4 or len(b_df) < 4:
            continue

        n_change = (n_df.iloc[3]['close'] - n_df.iloc[0]['open']) / n_df.iloc[0]['open'] * 100
        b_change = (b_df.iloc[3]['close'] - b_df.iloc[0]['open']) / b_df.iloc[0]['open'] * 100

    except:
        continue

    # Skip sideways market
    if abs(n_change) < 0.3:
        continue

    sector_strength = {}
    pool = []

    for sym, data in market_data.items():

        df = data["df"]
        sec = data["sector"]

        day_df = df[df['date'] == day].sort_values(by="time")

        if len(day_df) < 6:
            continue

        first6 = day_df.iloc[:6]

        open_p = first6.iloc[0]['open']
        ltp = first6.iloc[3]['close']

        change = ((ltp - open_p) / open_p) * 100

        # ORB
        high_920 = first6.iloc[:3]['high'].max()
        low_920 = first6.iloc[:3]['low'].min()
        breakout = ltp > high_920 or ltp < low_920

        # Volume
        vol_now = first6.iloc[3]['volume']
        avg_vol = first6.iloc[:5]['volume'].mean()
        volume_ok = vol_now > 1.5 * avg_vol if avg_vol != 0 else False

        sector_strength.setdefault(sec, []).append(change)

        if abs(change) < 0.7:
            continue

        pool.append({
            "sym": sym,
            "sector": sec,
            "ltp": ltp,
            "change": change,
            "breakout": breakout,
            "volume": volume_ok
        })

    # Sector strength
    sector_strength = {k: sum(v)/len(v) for k, v in sector_strength.items() if v}

    signals = []

    for s in pool:

        sec_str = sector_strength.get(s["sector"], 0)
        direction = "BUY" if s["change"] > 0 else "SELL"

        score = 0

        if abs(sec_str) > 0.5: score += 1
        if abs(s["change"]) > 1: score += 1
        if s["breakout"]: score += 1
        if s["volume"]: score += 1

        if direction == "BUY" and n_change > 0: score += 1
        if direction == "SELL" and n_change < 0: score += 1

        if score >= 3:
            signals.append((s, direction))

    if not signals:
        continue

    signals.sort(key=lambda x: abs(x[0]["change"]), reverse=True)

    top2 = signals[:2]
    final = top2[0][0]
    direction = top2[0][1]

    entry = final["ltp"]

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
📊 BACKTEST RESULT (3 Months)

Total Trades: {total}
Win Rate: {round(winrate,2)}%
Avg Return: {round(avg,2)}%
"""

print(summary)
send(summary)

# Sample trades
send("🔥 SAMPLE TRADES:\n" + "\n".join(logs[:15]))
