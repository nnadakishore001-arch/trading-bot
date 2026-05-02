import requests
import time
from datetime import datetime
from SmartApi import SmartConnect
import pyotp
import numpy as np

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

# ========= INDEX TOKENS =========
INDICES = {
    "NIFTY": "99926000",
    "BANKNIFTY": "99926009"
}

# ========= INDICATORS =========
def rsi(closes):
    diff = np.diff(closes)
    gain = np.mean([x for x in diff if x > 0] or [0])
    loss = np.mean([-x for x in diff if x < 0] or [1])
    rs = gain / loss if loss else 1
    return 100 - (100 / (1 + rs))

def adx(c):
    closes = [x[4] for x in c]
    return min(np.std(closes)*10, 50)

def atr(c):
    tr=[]
    for i in range(1,len(c)):
        tr.append(max(c[i][2]-c[i][3],abs(c[i][2]-c[i-1][4])))
    return np.mean(tr) if tr else 0

# ========= DATA =========
def get_candles(client, token):
    now = datetime.now()
    params = {
        "exchange":"NSE",
        "symboltoken":token,
        "interval":"FIVE_MINUTE",
        "fromdate":now.strftime("%Y-%m-%d 09:15"),
        "todate":now.strftime("%Y-%m-%d 09:35")
    }
    data = client.getCandleData(params)
    return data["data"] if data["data"] else []

# ========= INDEX CONFIRMATION =========
def get_index_bias(client):
    bias = {}

    for name, token in INDICES.items():
        try:
            data = client.ltpData("NSE", name, token)['data']
            change = ((data['ltp'] - data['open']) / data['open']) * 100
            bias[name] = change
        except:
            bias[name] = 0

    return bias

# ========= PREMARKET =========
def premarket_summary(client):

    sector_strength = {}
    movers = []

    for sec, stocks in SECTORS.items():
        changes = []

        for sym, tok in stocks.items():
            try:
                data = client.ltpData("NSE", sym, tok)['data']
                change = ((data['ltp'] - data['open']) / data['open']) * 100

                changes.append(change)
                movers.append((sym, change))
            except:
                pass

        if changes:
            sector_strength[sec] = sum(changes)/len(changes)

    top_sec = sorted(sector_strength.items(), key=lambda x:x[1], reverse=True)[:3]
    top_stocks = sorted(movers, key=lambda x:abs(x[1]), reverse=True)[:3]

    index_bias = get_index_bias(client)

    msg = "📊 PRE-MARKET SUMMARY\n\n"

    msg += "Top Sectors:\n"
    for s,v in top_sec:
        msg += f"{s} {round(v,2)}%\n"

    msg += "\nTop Movers:\n"
    for s,v in top_stocks:
        msg += f"{s} {round(v,2)}%\n"

    msg += f"\nNIFTY: {round(index_bias['NIFTY'],2)}%"
    msg += f"\nBANKNIFTY: {round(index_bias['BANKNIFTY'],2)}%"

    send(msg)

# ========= MAIN STRATEGY =========
def run(client):

    sector_strength={}
    pool=[]

    index_bias = get_index_bias(client)

    for sec,stocks in SECTORS.items():
        changes=[]

        for sym,tok in stocks.items():
            candles=get_candles(client,tok)
            if len(candles)<4: continue

            open_p=candles[0][1]
            ltp=candles[-1][4]
            change=((ltp-open_p)/open_p)*100

            closes=[x[4] for x in candles]

            # ORB
            high_920=max([c[2] for c in candles[:2]])
            low_920=min([c[3] for c in candles[:2]])
            breakout = ltp > high_920 or ltp < low_920

            # Volume
            vol_now=candles[-1][5]
            avg_vol=np.mean([c[5] for c in candles[:-1]])
            volume_ok = vol_now > 1.5 * avg_vol

            pool.append({
                "sym":sym,
                "sector":sec,
                "ltp":ltp,
                "change":change,
                "rsi":rsi(closes),
                "adx":adx(candles),
                "atr":atr(candles),
                "breakout":breakout,
                "volume":volume_ok
            })

            changes.append(change)

        if changes:
            sector_strength[sec]=sum(changes)/len(changes)

    # ===== FILTER =====
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

        # INDEX CONFIRMATION
        if direction=="BUY" and index_bias["NIFTY"]>0: score+=1
        if direction=="SELL" and index_bias["NIFTY"]<0: score+=1

        if score>=4:
            signals.append((s,sec_str,direction,score))

    signals.sort(key=lambda x:x[3], reverse=True)
    top2=signals[:2]

    if not top2:
        send("⚠️ No strong trades today")
        return

    # ===== SEND TOP 2 =====
    msg="📊 TOP 2 STOCKS\n\n"
    for s,sec_str,direction,_ in top2:
        msg+=f"{direction} {s['sym']} ({s['sector']})\n"
    send(msg)

    # ===== FINAL PICK =====
    s,sec_str,direction,_=top2[0]

    ltp=s["ltp"]
    risk=s["atr"]

    if direction=="BUY":
        sl=ltp-risk
        tp1=ltp+risk*1.5
        tp2=ltp+risk*3
        option="CE"
    else:
        sl=ltp+risk
        tp1=ltp-risk*1.5
        tp2=ltp-risk*3
        option="PE"

    strike = round(ltp / 50) * 50

    final_msg=f"""
{direction} {s['sym']} [A+]
Sector: {s['sector']} ({round(sec_str,2)}%)

Option: {s['sym']} {strike} {option}

Entry  : ₹{round(ltp,2)}
SL     : ₹{round(sl,2)}
TP1    : ₹{round(tp1,2)}  (1:1.5)
TP2    : ₹{round(tp2,2)}  (1:3)

RSI: {round(s['rsi'],2)} | ADX: {round(s['adx'],2)}
Time: 09:30 IST
"""
    send(final_msg)

# ========= LOOP =========
client = login()
last_915=None
last_930=None

while True:
    now=datetime.now()

    if now.hour==9 and now.minute==15:
        if last_915!=now.date():
            premarket_summary(client)
            last_915=now.date()

    if now.hour==9 and now.minute==30:
        if last_930!=now.date():
            run(client)
            last_930=now.date()

    time.sleep(10)
