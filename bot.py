from datetime import datetime, timedelta
import numpy as np
from SmartApi import SmartConnect
import pyotp
import time

# ===== TELEGRAM =====
TOKEN = "8691427620:AAF5vkJmHqETtm2TyhEd6CLdozCPsa57ATg"
CHAT_ID = "890425913"

def send(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
 
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

# ================= IMPORT YOUR FUNCTIONS =================
# COPY THESE FROM YOUR ORIGINAL CODE (NO CHANGE)
# calc_rsi, calc_adx, calc_atr, calc_vwap, check_ema_cross, get_strike
# SECTORS, STRIKE_INTERVAL, etc.

# ================= FETCH HISTORICAL =================
def get_historical(client, token, date):
    try:
        fromdate = f"{date} 09:15"
        todate   = f"{date} 15:30"

        params = {
            "exchange": "NSE",
            "symboltoken": token,
            "interval": "FIVE_MINUTE",
            "fromdate": fromdate,
            "todate": todate
        }

        data = client.getCandleData(params)
        return data["data"] if data and data.get("data") else []
    except:
        return []

# ================= BACKTEST =================
def backtest():
    client = login()

    start_date = datetime.now() - timedelta(days=90)
    end_date   = datetime.now()

    total_trades = 0
    win = 0
    loss = 0
    no_trade = 0

    current_date = start_date

    while current_date <= end_date:

        # Skip weekends
        if current_date.weekday() >= 5:
            current_date += timedelta(days=1)
            continue

        pool = []
        sector_strength = {}

        for sec, stocks in SECTORS.items():
            changes = []

            for sym, tok in stocks.items():
                candles = get_historical(client, tok, current_date.strftime("%Y-%m-%d"))

                if len(candles) < 20:
                    continue

                closes = [c[4] for c in candles]
                volumes = [c[5] for c in candles]

                open_p = candles[0][1]
                ltp_930 = candles[3][4]  # 9:30 candle

                change = (ltp_930 - open_p) / open_p * 100
                changes.append(change)

                rsi = calc_rsi(closes[:4])
                adx = calc_adx(candles[:4])
                atr = calc_atr(candles[:4])
                vwap = calc_vwap(candles[:4])
                ema = check_ema_cross(closes[:4])

                orb_high = max(c[2] for c in candles[:3])
                orb_low  = min(c[3] for c in candles[:3])

                breakout = (ltp_930 > orb_high) or (ltp_930 < orb_low)

                vol_now = volumes[3]
                vol_avg = np.mean(volumes[:3])
                rvol = vol_now / vol_avg if vol_avg else 1

                pool.append({
                    "sym": sym,
                    "sector": sec,
                    "ltp": ltp_930,
                    "change": change,
                    "rsi": rsi,
                    "adx": adx,
                    "atr": atr,
                    "vwap": vwap,
                    "ema_bull": ema["bull_align"],
                    "ema_bear": ema["bear_align"],
                    "breakout": breakout,
                    "vol_ok": rvol >= 1.5
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
            if s["breakout"]: score += 1
            if s["vol_ok"]: score += 1
            if s["adx"] > 20: score += 1

            if direction == "BUY" and 52 < s["rsi"] < 74: score += 1
            if direction == "SELL" and 26 < s["rsi"] < 48: score += 1

            if direction == "BUY" and s["ltp"] > s["vwap"]: score += 1
            if direction == "SELL" and s["ltp"] < s["vwap"]: score += 1

            if direction == "BUY" and s["ema_bull"]: score += 1
            if direction == "SELL" and s["ema_bear"]: score += 1

            if score >= 5:
                signals.append(s)

        if not signals:
            no_trade += 1
            current_date += timedelta(days=1)
            continue

        signals = signals[:2]

        for s in signals:
            total_trades += 1

            entry = s["ltp"]
            atr = s["atr"]
            risk = atr * 1.5

            if s["change"] > 0:
                sl = entry - risk
                tp = entry + risk * 1.5
            else:
                sl = entry + risk
                tp = entry - risk * 1.5

            future_candles = candles[4:]

            result = None

            for c in future_candles:
                high = c[2]
                low = c[3]

                if s["change"] > 0:
                    if low <= sl:
                        result = "LOSS"
                        break
                    if high >= tp:
                        result = "WIN"
                        break
                else:
                    if high >= sl:
                        result = "LOSS"
                        break
                    if low <= tp:
                        result = "WIN"
                        break

            if result == "WIN":
                win += 1
            else:
                loss += 1

        current_date += timedelta(days=1)

    print("\n📊 BACKTEST RESULT (3 Months)\n")
    print(f"Total Trades: {total_trades}")
    print(f"Win Trades : {win}")
    print(f"Loss Trades : {loss}")
    print(f"No Trades : {no_trade}")
    print(f"Win Rate: {round((win/total_trades)*100,2) if total_trades else 0}%")

# ================= RUN =================
if __name__ == "__main__":
    backtest()
