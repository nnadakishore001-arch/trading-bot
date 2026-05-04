import pandas as pd
from datetime import datetime
from SmartApi import SmartConnect
import pyotp
import requests
import time
import pytz
import os

# ========= ENV =========
API_KEY = os.getenv("API_KEY")
CLIENT_ID = os.getenv("CLIENT_ID")
PASSWORD = os.getenv("PASSWORD")
TOTP_SECRET = os.getenv("TOTP_SECRET")

TG_TOKEN = os.getenv("TG_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

if not all([API_KEY, CLIENT_ID, PASSWORD, TOTP_SECRET, TG_TOKEN, CHAT_ID]):
    raise Exception("❌ Missing Environment Variables")

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
    for _ in range(3):
        try:
            obj = SmartConnect(api_key=API_KEY)
            totp = pyotp.TOTP(TOTP_SECRET).now()
            data = obj.generateSession(CLIENT_ID, PASSWORD, totp)

            if data and data.get("status"):
                obj.setAccessToken(data['data']['jwtToken'])
                obj.setRefreshToken(data['data']['refreshToken'])
                obj.feed_token = data['data']['feedToken']

                send("✅ Login Success")
                time.sleep(3)

                try:
                    obj.getProfile(obj.getAccessToken())
                except:
                    pass

                return obj
        except:
            pass
        time.sleep(5)

    return None

# ========= CONFIG =========
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

INDICES = {
    "NIFTY": "99926000",
    "BANKNIFTY": "99926009"
}

# ========= API =========
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
            df = pd.DataFrame(res["data"],
                columns=["time","open","high","low","close","volume"])
            return df

    except:
        pass

    return pd.DataFrame()

# ========= OPTION =========
def get_option(price, change, direction):
    atm = round(price / 100) * 100

    if abs(change) > 1.5:
        strike = atm - 100 if direction == "BUY" else atm + 100
        tag = "ITM"
    else:
        strike = atm
        tag = "ATM"

    opt = "CE" if direction == "BUY" else "PE"
    return strike, opt, tag

# ========= STRATEGY =========
def run_strategy(obj):

    market = []
    sector_strength = {}

    n_df = get_data(obj, INDICES["NIFTY"])
    b_df = get_data(obj, INDICES["BANKNIFTY"])

    if len(n_df) < 4 or len(b_df) < 4:
        send("⚠️ Index data not ready yet")
        return

    nifty_dir = 1 if n_df.iloc[3]['close'] > n_df.iloc[0]['open'] else -1
    bank_dir = 1 if b_df.iloc[3]['close'] > b_df.iloc[0]['open'] else -1

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

            time.sleep(1)

    if not market:
        send("⚠️ No market data available")
        return

    sector_strength = {k: sum(v)/len(v) for k,v in sector_strength.items()}

    signals = []

    for s in market:
        sec = s["sector"]
        change = s["change"]

        if abs(change) < 0.7:
            continue

        if sec == "BANK":
            if (change > 0 and bank_dir < 0) or (change < 0 and bank_dir > 0):
                continue
        else:
            if (change > 0 and nifty_dir < 0) or (change < 0 and nifty_dir > 0):
                continue

        if abs(sector_strength.get(sec,0)) < 0.3:
            continue

        signals.append(s)

    if not signals:
        send("❌ No trade")
        return

    signals.sort(key=lambda x: abs(x["change"]), reverse=True)

    top2 = signals[:2]
    final_stock = top2[0]

    # ========= OUTPUT =========
    msg = "📊 Sector Heatmap\n\n"

    for sec, val in sector_strength.items():
        bars = "🟢"*min(int(abs(val)*2),5) if val > 0 else "🔴"*min(int(abs(val)*2),5)
        msg += f"{sec} {round(val,2)}% {bars}\n"

    msg += "\n🔥 Top Picks (F&O)\n\n"

    for s in top2:
        direction = "BUY" if s["change"] > 0 else "SELL"
        strike, opt, tag = get_option(s["ltp"], s["change"], direction)

        msg += (
            f"{direction} {s['sym']}\n"
            f"Sector: {s['sector']}\n"
            f"LTP: {round(s['ltp'],2)}\n"
            f"Move: {round(s['change'],2)}%\n"
            f"Option: {strike} {opt} ({tag})\n\n"
        )

    direction = "BUY" if final_stock["change"] > 0 else "SELL"
    strike, opt, tag = get_option(final_stock["ltp"], final_stock["change"], direction)

    msg += "🎯 Final Stock Recommendation\n\n"
    msg += (
        f"{direction} {final_stock['sym']}\n"
        f"Sector: {final_stock['sector']}\n"
        f"LTP: {round(final_stock['ltp'],2)}\n"
        f"Move: {round(final_stock['change'],2)}%\n"
        f"Option: {strike} {opt} ({tag})\n"
    )

    send(msg)

# ========= MAIN =========
def main():
    obj = login()
    if obj is None:
        send("❌ Login Failed")
        return

    ist = pytz.timezone("Asia/Kolkata")

    while True:
        now = datetime.now(ist)

        if now.hour == 9 and now.minute == 15:
            send("📊 Market Open — Preparing Scan")
            time.sleep(60)

        if now.hour == 9 and now.minute == 30:
            send("⚙️ Running Strategy")
            run_strategy(obj)
            time.sleep(60)

        time.sleep(20)

if __name__ == "__main__":
    main()
