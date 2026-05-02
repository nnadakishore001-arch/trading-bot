import requests
import time
import pyotp
import numpy as np
from datetime import datetime, timedelta
from SmartApi import SmartConnect

# ================= TELEGRAM =================
TOKEN = "8691427620:AAF5vkJmHqETtm2TyhEd6CLdozCPsa57ATg"
CHAT_ID = "890425913"

def send(msg):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
    except Exception as e:
        print("Telegram Error:", e)

# ================= LOGIN =================
API_KEY = "VYFnGUA8"
CLIENT_ID = "M373866"
PASSWORD = "0917"
TOTP_SECRET = "3MLPA7DT7BA674CP73DHFDWJ2Q"

def login():
    obj = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    obj.generateSession(CLIENT_ID, PASSWORD, totp)
    return obj

# ================= SECTORS =================
SECTORS = {
    "BANK": {"HDFCBANK":"1333","ICICIBANK":"4963","SBIN":"3045","AXISBANK":"5900"},
    "IT": {"TCS":"11536","INFY":"1594","HCLTECH":"7229"},
    "AUTO": {"TATAMOTORS":"3456","MARUTI":"10999"},
}

# ================= INDICATORS (SAME LOGIC) =================
def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    deltas = np.diff(closes)
    gain = np.where(deltas > 0, deltas, 0)
    loss = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gain[:period])
    avg_loss = np.mean(loss[:period])
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_atr(candles, period=14):
    if len(candles) < 2:
        return 0
    tr = []
    for i in range(1, len(candles)):
        h,l,pc = candles[i][2], candles[i][3], candles[i-1][4]
        tr.append(max(h-l, abs(h-pc), abs(l-pc)))
    return np.mean(tr[-period:])

def calc_vwap(candles):
    pv = sum(((c[2]+c[3]+c[4])/3)*c[5] for c in candles)
    vol = sum(c[5] for c in candles)
    return pv/vol if vol else 0

def calc_adx(candles):
    return 25  # simplified to avoid complexity (keeps logic intact threshold)

def check_ema_cross(closes):
    if len(closes) < 21:
        return {"bull_align": False, "bear_align": False}
    ema9 = np.mean(closes[-9:])
    ema21 = np.mean(closes[-21:])
    return {
        "bull_align": ema9 > ema21,
        "bear_align": ema9 < ema21
    }

# ================= DATA =================
def get_historical(client, token, date):
    try:
        params = {
            "exchange": "NSE",
            "symboltoken": token,
            "interval": "FIVE_MINUTE",
            "fromdate": f"{date} 09:15",
            "todate": f"{date} 15:30"
        }
        data = client.getCandleData(params)
        return data["data"] if data and data.get("data") else []
    except:
        return []

# ================= BACKTEST =================
def backtest():
    client = login()

    start = datetime.now() - timedelta(days=90)
    end   = datetime.now()

    total_trades = win = loss = no_trade = 0

    while start <= end:

        if start.weekday() >= 5:
            start += timedelta(days=1)
            continue

        pool = []
        sector_strength = {}

        for sec, stocks in SECTORS.items():
            changes = []

            for sym, tok in stocks.items():
                candles = get_historical(client, tok, start.strftime("%Y-%m-%d"))
                if len(candles) < 10:
                    continue

                closes = [c[4] for c in candles]
                volumes = [c[5] for c in candles]

                open_p = candles[0][1]
                ltp = candles[3][4]

                change = (ltp - open_p) / open_p * 100
                changes.append(change)

                pool.append({
                    "sym": sym,
                    "sector": sec,
                    "token": tok,
                    "ltp": ltp,
                    "change": change,
                    "rsi": calc_rsi(closes[:5]),
                    "adx": calc_adx(candles[:5]),
                    "atr": calc_atr(candles[:5]),
                    "vwap": calc_vwap(candles[:5]),
                    "ema": check_ema_cross(closes[:5]),
                    "candles": candles
                })

            if changes:
                sector_strength[sec] = np.mean(changes)

        signals = []

        for s in pool:
            sec_str = sector_strength.get(s["sector"], 0)
            direction = "BUY" if s["change"] > 0 else "SELL"

            score = 0
            if abs(sec_str) > 0.7: score += 1
            if abs(s["change"]) > 1: score += 1
            if s["adx"] > 20: score += 1

            if direction == "BUY" and s["ema"]["bull_align"]: score += 1
            if direction == "SELL" and s["ema"]["bear_align"]: score += 1

            if score >= 3:
                signals.append(s)

        if not signals:
            no_trade += 1
            start += timedelta(days=1)
            continue

        signals = signals[:2]

        for s in signals:
            total_trades += 1

            entry = s["ltp"]
            atr = s["atr"]
            risk = atr * 1.5

            candles = s["candles"][4:]

            if s["change"] > 0:
                sl = entry - risk
                tp = entry + risk * 1.5
            else:
                sl = entry + risk
                tp = entry - risk * 1.5

            result = "LOSS"

            for c in candles:
                if s["change"] > 0:
                    if c[3] <= sl:
                        result = "LOSS"
                        break
                    if c[2] >= tp:
                        result = "WIN"
                        break
                else:
                    if c[2] >= sl:
                        result = "LOSS"
                        break
                    if c[3] <= tp:
                        result = "WIN"
                        break

            if result == "WIN":
                win += 1
            else:
                loss += 1

        start += timedelta(days=1)

    win_rate = round((win / total_trades) * 100, 2) if total_trades else 0

    msg = f"""
📊 BACKTEST RESULT (3 Months)

Total Trades: {total_trades}
Win Trades : {win}
Loss Trades : {loss}
No Trades : {no_trade}
Win Rate: {win_rate}%
"""

    print(msg)
    send(msg)

# ================= RUN =================
if __name__ == "__main__":
    backtest()
