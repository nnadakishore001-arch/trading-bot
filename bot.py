
import os
import pandas as pd
import pyotp
import requests
from datetime import datetime
from SmartApi import SmartConnect
import pytz
import time

# ========= ENV =========
API_KEY = os.getenv("API_KEY")
CLIENT_ID = os.getenv("CLIENT_ID")
PASSWORD = os.getenv("PASSWORD")
TOTP_SECRET = os.getenv("TOTP_SECRET")

TG_TOKEN = os.getenv("TG_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# ========= TELEGRAM =========
def send(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg}
        )
    except:
        pass

# ========= LOGIN =========
def login():
    obj = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    data = obj.generateSession(CLIENT_ID, PASSWORD, totp)

    if data and data.get("status"):
        obj.setAccessToken(data['data']['jwtToken'])
        obj.setRefreshToken(data['data']['refreshToken'])
        obj.feed_token = data['data']['feedToken']
        send("✅ Login Success")
        return obj

    send("❌ Login Failed")
    return None

# ========= STOCKS =========
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

# ========= FETCH =========
def get_data(obj, token):
    try:
        ist = pytz.timezone("Asia/Kolkata")
        now = datetime.now(ist)
        start = now.replace(hour=9, minute=15, second=0)

        res = obj.getCandleData({
            "exchange": "NSE",
            "symboltoken": token,
            "interval": "FIVE_MINUTE",
            "fromdate": start.strftime("%Y-%m-%d %H:%M"),
            "todate": now.strftime("%Y-%m-%d %H:%M")
        })

        if res and res.get("data"):
            return pd.DataFrame(res["data"],
                columns=["time","open","high","low","close","volume"])

    except:
        pass

    return pd.DataFrame()

# ========= INDEX (LTP BASED) =========
def get_index_direction(obj):
    try:
        nifty = obj.ltpData("NSE", "NIFTY", "99926000")['data']['ltp']
        bank = obj.ltpData("NSE", "BANKNIFTY", "99926009")['data']['ltp']

        return nifty, bank
    except:
        return None, None

# ========= STRATEGY =========
def scan_market(obj):

    market = []
    sector_strength = {}

    for sec, stocks in SECTORS.items():
        for sym, tok in stocks.items():

            df = get_data(obj, tok)
            if len(df) < 4:
                continue

            open_p = df.iloc[0]['open']
            close_930 = df.iloc[3]['close']
            change = ((close_930 - open_p) / open_p) * 100

            sector_strength.setdefault(sec, []).append(change)

            market.append({
                "sym": sym,
                "sector": sec,
                "change": change,
                "ltp": close_930
            })

            time.sleep(0.5)

    if not market:
        return None, "No data"

    # sector avg
    sector_strength = {k: sum(v)/len(v) for k,v in sector_strength.items()}

    # filter
    signals = []
    for s in market:
        if abs(s["change"]) >= 0.4 and abs(sector_strength[s["sector"]]) >= 0.3:
            signals.append(s)

    if not signals:
        return None, "Low momentum"

    signals.sort(key=lambda x: abs(x["change"]), reverse=True)

    return signals[0], None

# ========= OPTION =========
def option_pick(price, direction):
    atm = round(price / 100) * 100
    return f"{atm} {'CE' if direction=='BUY' else 'PE'}"

# ========= MAIN =========
def main():

    obj = login()
    if obj is None:
        return

    ist = pytz.timezone("Asia/Kolkata")

    send("🚀 Bot Running (Live Mode)")

    traded = False
    no_trade_reason = None

    while True:
        now = datetime.now(ist)

        # 9:20 update
        if now.hour == 9 and now.minute == 20:
            send("📊 Market Open — Scanning Starts")

        # trading window
        if 9 <= now.hour < 15:

            if not traded:
                signal, reason = scan_market(obj)

                if signal:
                    direction = "BUY" if signal["change"] > 0 else "SELL"
                    opt = option_pick(signal["ltp"], direction)

                    msg = (
                        f"🔥 TRADE ALERT\n\n"
                        f"{direction} {signal['sym']}\n"
                        f"Sector: {signal['sector']}\n"
                        f"LTP: {round(signal['ltp'],2)}\n"
                        f"Move: {round(signal['change'],2)}%\n"
                        f"Option: {opt}"
                    )

                    send(msg)
                    traded = True
                else:
                    no_trade_reason = reason

        # after market close
        if now.hour >= 15 and now.minute >= 30:
            if not traded:
                send(f"📉 No Trade Today\nReason: {no_trade_reason}")
            send("📊 Market Closed")
            break

        time.sleep(120)

if __name__ == "__main__":
    main()
