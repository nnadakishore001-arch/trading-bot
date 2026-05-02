import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from SmartApi import SmartConnect
import pyotp

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

totp = pyotp.TOTP(TOTP_SECRET).now()
obj = SmartConnect(api_key=API_KEY)
obj.generateSession(CLIENT_ID, PASSWORD, totp)

import requests, time, pyotp, numpy as np
from datetime import datetime
from SmartApi import SmartConnect

# ========= TELEGRAM =========
TOKEN = "8706462182:AAHt5JMZ5tfMUjfKTYncwcfHZCflpQY9hHA"
CHAT_ID = "890425913"

def send(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": msg})

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

# ========= F&O SECTORS =========
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

# ===== DATE RANGE =====
end = datetime.now()
start = end - timedelta(days=90)

# ===== GET DATA =====
def get_data(token):
    params = {
        "exchange":"NSE",
        "symboltoken":token,
        "interval":"FIVE_MINUTE",
        "fromdate":start.strftime("%Y-%m-%d 09:15"),
        "todate":end.strftime("%Y-%m-%d 15:30")
    }

    data = obj.getCandleData(params)

    if not data['data']:
        return pd.DataFrame()

    df = pd.DataFrame(data['data'],
                      columns=["time","open","high","low","close","volume"])
    df['time'] = pd.to_datetime(df['time'])
    df['date'] = df['time'].dt.date
    return df

# ===== INDICATORS =====
def rsi(close):
    diff = np.diff(close)
    gain = np.mean([x for x in diff if x > 0] or [0])
    loss = np.mean([-x for x in diff if x < 0] or [1])
    rs = gain/loss if loss else 1
    return 100 - (100/(1+rs))

def adx(close):
    return min(np.std(close)*10,50)

def atr(df):
    tr=[]
    for i in range(1,len(df)):
        tr.append(max(df.iloc[i]['high']-df.iloc[i]['low'],
                      abs(df.iloc[i]['high']-df.iloc[i-1]['close'])))
    return np.mean(tr) if tr else 0

# ===== LOAD DATA =====
market_data = {}

for sec,stocks in SECTORS.items():
    for sym,token in stocks.items():
        df = get_data(token)
        if not df.empty:
            market_data[sym] = {"sector":sec,"df":df}

# ===== BACKTEST =====
results = []

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

        day_df = df[df['date']==day]

        if len(day_df)<4:
            continue

        open_p = day_df.iloc[0]['open']
        ltp = day_df.iloc[3]['close']

        change = ((ltp-open_p)/open_p)*100

        closes = day_df['close'].values[:4]

        # ORB
        high_920 = day_df.iloc[:2]['high'].max()
        low_920 = day_df.iloc[:2]['low'].min()

        breakout = ltp > high_920 or ltp < low_920

        # volume
        vol_now = day_df.iloc[3]['volume']
        avg_vol = day_df.iloc[:3]['volume'].mean()

        volume_ok = vol_now > 1.5 * avg_vol

        pool.append({
            "sym":sym,
            "sector":sec,
            "ltp":ltp,
            "change":change,
            "rsi":rsi(closes),
            "adx":adx(closes),
            "atr":atr(day_df.iloc[:4]),
            "breakout":breakout,
            "volume":volume_ok
        })

        sector_strength.setdefault(sec, []).append(change)

    sector_strength = {k:sum(v)/len(v) for k,v in sector_strength.items() if v}

    signals=[]

    for s in pool:

        sec_str=sector_strength.get(s["sector"],0)
        direction="BUY" if s["change"]>0 else "SELL"

        score=0

        if abs(sec_str)>0.7: score+=1
        if abs(s["change"])>1: score+=1
        if s["adx"]>20: score+=1
        if s["breakout"]: score+=1
        if s["volume"]: score+=1

        if direction=="BUY" and s["rsi"]>55: score+=1
        if direction=="SELL" and s["rsi"]<45: score+=1

        if score>=4:
            signals.append((s,direction))

    if not signals:
        continue

    # pick best
    s,direction = sorted(signals, key=lambda x:abs(x[0]["change"]), reverse=True)[0]

    entry = s["ltp"]
    exit_price = day_df.iloc[-1]['close']

    if direction=="BUY":
        pnl = ((exit_price-entry)/entry)*100
    else:
        pnl = ((entry-exit_price)/entry)*100

    results.append(pnl)

# ===== RESULT =====
total = len(results)
wins = len([x for x in results if x>0])
winrate = (wins/total)*100 if total else 0

print("\n📊 BACKTEST RESULT (3 Months)\n")
print("Total Trades:", total)
print("Win Rate:", round(winrate,2), "%")
print("Avg Return:", round(np.mean(results),2), "%")
