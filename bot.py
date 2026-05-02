import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from SmartApi import SmartConnect
import pyotp
import requests

# ===== TELEGRAM =====
TOKEN = "8706462182:AAHt5JMZ5tfMUjfKTYncwcfHZCflpQY9hHA"
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

send("🚀 PRO BACKTEST STARTED")

# ===== DATE RANGE =====
end = datetime.now()
start = end - timedelta(days=90)

# ===== SECTORS =====
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

# ================= GET DATA =================
def get_data(token):
    try:
        params = {
            "exchange":"NSE",
            "symboltoken":token,
            "interval":"FIVE_MINUTE",
            "fromdate":start.strftime("%Y-%m-%d 09:15"),
            "todate":end.strftime("%Y-%m-%d 15:30")
        }
        res = obj.getCandleData(params)

        if not res or 'data' not in res or not res['data']:
            return pd.DataFrame()

        df = pd.DataFrame(res['data'])
        df = df.iloc[:, :6]
        df.columns = ["time","open","high","low","close","volume"]

        df['time'] = pd.to_datetime(df['time'])
        for col in ["open","high","low","close","volume"]:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        df = df.dropna()
        df['date'] = df['time'].dt.date
        return df

    except:
        return pd.DataFrame()

# ================= LOAD =================
market_data = {}

for sec, stocks in SECTORS.items():
    for sym, token in stocks.items():
        df = get_data(token)
        if not df.empty and len(df) > 50:
            market_data[sym] = {"sector":sec,"df":df}

send(f"✅ Loaded {len(market_data)} stocks")

# ================= INDICATORS =================
def rsi(close):
    diff = np.diff(close)
    gain = np.mean([x for x in diff if x > 0] or [0])
    loss = np.mean([-x for x in diff if x < 0] or [1])
    rs = gain/loss if loss else 1
    return 100 - (100/(1+rs))

def adx(c):
    closes = [x[4] for x in c]
    return min(np.std(closes)*10, 50)

def vwap(df):
    pv = ((df['high']+df['low']+df['close'])/3 * df['volume']).sum()
    vol = df['volume'].sum()
    return pv/vol if vol>0 else 0

# ================= BACKTEST =================
results = []
trade_logs = []

dates = sorted(set(
    d for v in market_data.values()
    for d in v["df"]["date"]
))

for day in dates:

    sector_strength={}
    pool=[]

    for sym,data in market_data.items():

        df=data["df"]
        sec=data["sector"]

        day_df=df[df['date']==day].sort_values(by="time")

        if len(day_df)<6:
            continue

        first6 = day_df.iloc[:6]

        open_p = first6.iloc[0]['open']
        prev_close = df[df['date'] < day]['close'].iloc[-1] if len(df[df['date'] < day])>0 else open_p

        # ===== GAP FILTER =====
        gap = ((open_p - prev_close)/prev_close)*100
        if abs(gap) < 0.5:
            continue

        ltp = first6.iloc[3]['close']
        change=((ltp-open_p)/open_p)*100

        closes = first6['close'].values

        high_920 = first6.iloc[:3]['high'].max()
        low_920 = first6.iloc[:3]['low'].min()
        breakout = ltp > high_920 or ltp < low_920

        vol_now = first6.iloc[3]['volume']
        avg_vol = first6.iloc[:5]['volume'].mean()
        volume_ok = vol_now > 1.5 * avg_vol if avg_vol!=0 else False

        pool.append({
            "sym":sym,
            "sector":sec,
            "ltp":ltp,
            "change":change,
            "rsi":rsi(closes),
            "adx":adx(first6.values.tolist()),
            "vwap":vwap(first6),
            "breakout":breakout,
            "volume":volume_ok
        })

        sector_strength.setdefault(sec, []).append(change)

    sector_strength = {k:sum(v)/len(v) for k,v in sector_strength.items() if v}

    signals=[]

    for s in pool:

        sec_str = sector_strength.get(s["sector"],0)
        direction = "BUY" if s["change"]>0 else "SELL"

        score=0

        if abs(sec_str)>0.7: score+=1
        if abs(s["change"])>1: score+=1
        if s["breakout"]: score+=1
        if s["volume"]: score+=1

        if direction=="BUY" and s["ltp"]>s["vwap"]: score+=1
        if direction=="SELL" and s["ltp"]<s["vwap"]: score+=1

        if direction=="BUY" and s["rsi"]>60 and s["adx"]>25: score+=1
        if direction=="SELL" and s["rsi"]<40 and s["adx"]>25: score+=1

        if score>=5:
            signals.append((s,direction))

    if not signals:
        continue

    s,direction = sorted(signals, key=lambda x:abs(x[0]["change"]), reverse=True)[0]

    entry = s["ltp"]

    full_day = df[df['date']==day].sort_values(by="time")

    tp = entry * 1.015
    sl = entry * 0.99

    pnl = 0
    for _, row in full_day.iterrows():

        if row['time'].hour >= 10 and row['time'].minute >= 30:
            break  # TIME EXIT

        if direction=="BUY":
            if row['high'] >= tp:
                pnl = 1.5
                break
            elif row['low'] <= sl:
                pnl = -1
                break

        else:
            if row['low'] <= entry*0.985:
                pnl = 1.5
                break
            elif row['high'] >= entry*1.01:
                pnl = -1
                break

    results.append(pnl)
    trade_logs.append(f"{day} | {direction} {s['sym']} | {pnl}%")

# ================= RESULT =================
total = len(results)
wins = len([x for x in results if x > 0])
winrate = (wins/total)*100 if total else 0
avg = np.mean(results) if results else 0

send(f"📊 RESULT\nTrades: {total}\nWin Rate: {round(winrate,2)}%\nAvg: {round(avg,2)}%")

send("🔥 SAMPLE TRADES:\n" + "\n".join(trade_logs[:10]))
