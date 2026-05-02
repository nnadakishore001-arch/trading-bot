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
    "BANK": {"HDFCBANK":"1333","ICICIBANK":"4963","SBIN":"3045","AXISBANK":"5900"},
    "IT": {"TCS":"11536","INFY":"1594","HCLTECH":"7229"},
    "AUTO": {"TATAMOTORS":"3456","MARUTI":"10999"},
    "PHARMA": {"SUNPHARMA":"3351","CIPLA":"694"},
    "ENERGY": {"RELIANCE":"2885","ONGC":"2475","NTPC":"11630"},
}

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

# ========= INDICATORS =========
def rsi(closes):
    diff = np.diff(closes)
    gain = np.mean([x for x in diff if x > 0] or [0])
    loss = np.mean([-x for x in diff if x < 0] or [1])
    rs = gain / loss if loss else 1
    return 100 - (100 / (1 + rs))

def adx(candles):
    closes = [x[4] for x in candles]
    return min(np.std(closes) * 10, 50)

def vwap(c):
    pv, vol = 0, 0
    for i in c:
        tp = (i[2] + i[3] + i[4]) / 3
        pv += tp * i[5]
        vol += i[5]
    return pv / vol if vol else 0

def atr(c):
    tr = []
    for i in range(1, len(c)):
        tr.append(max(c[i][2] - c[i][3], abs(c[i][2] - c[i-1][4])))
    return np.mean(tr) if tr else 0

# ========= CORE =========
def run(client):

    sector_strength = {}
    pool = []

    # ----- Build Data -----
    for sec, stocks in SECTORS.items():
        changes = []

        for sym, tok in stocks.items():
            candles = get_candles(client, tok)
            if len(candles) < 3:
                continue

            open_p = candles[0][1]
            ltp = candles[-1][4]

            change = ((ltp - open_p) / open_p) * 100
            changes.append(change)

            closes = [x[4] for x in candles]

            pool.append({
                "sym": sym,
                "sector": sec,
                "ltp": ltp,
                "change": change,
                "rsi": rsi(closes),
                "adx": adx(candles),
                "vwap": vwap(candles),
                "atr": atr(candles)
            })

        if changes:
            sector_strength[sec] = sum(changes) / len(changes)

    # ----- Scoring System -----
    signals = []

    for s in pool:
        sec_str = sector_strength.get(s["sector"], 0)
        direction = "BUY" if s["change"] > 0 else "SELL"

        score = 0

        if abs(sec_str) > 0.7: score += 1
        if abs(s["change"]) > 1: score += 1
        if s["adx"] > 20: score += 1

        if direction == "BUY" and s["ltp"] > s["vwap"] and s["rsi"] > 55:
            score += 1
        if direction == "SELL" and s["ltp"] < s["vwap"] and s["rsi"] < 45:
            score += 1

        if score >= 4:
            signals.append((s, sec_str, direction, score))

    # ----- Pick Top 2 ONLY -----
    signals.sort(key=lambda x: x[3], reverse=True)
    final = signals[:2]

    # ----- SEND OUTPUT -----
    for s, sec_str, direction, score in final:

        ltp = s["ltp"]
        risk = s["atr"]

        if direction == "BUY":
            sl = ltp - risk
            tp1 = ltp + risk * 1.5
            tp2 = ltp + risk * 3
        else:
            sl = ltp + risk
            tp1 = ltp - risk * 1.5
            tp2 = ltp - risk * 3

        msg = f"""
{direction} {s['sym']} [A+]
Sector: {s['sector']} ({round(sec_str,2)}%)

Entry  : ₹{round(ltp,2)}
SL     : ₹{round(sl,2)}
TP1    : ₹{round(tp1,2)}  (1:1.5)
TP2    : ₹{round(tp2,2)}  (1:3)

RSI: {round(s['rsi'],2)}  |  ADX: {round(s['adx'],2)}
Time: {datetime.now().strftime("%H:%M")} IST
"""
        send(msg)

# ========= RUN AT 9:30 =========
client = login()

while True:
    if datetime.now().strftime("%H:%M") >= "09:30":
        run(client)
        break
    time.sleep(5)
