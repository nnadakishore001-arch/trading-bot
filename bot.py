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
        send("✅ Backtest Login Success")
        return obj

    send("❌ Login Failed")
    return None

# ========= STOCK LIST =========
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
            df = pd.DataFrame(res["data"],
                columns=["time","open","high","low","close","volume"])
            return df

    except Exception as e:
        print("Data error:", e)

    return pd.DataFrame()

# ========= BACKTEST =========
def run_backtest(obj):

    best = None
    fallback = None
    total_checked = 0

    for sector, stocks in SECTORS.items():
        for sym, token in stocks.items():

            df = get_data(obj, token)

            if len(df) < 4:
                continue

            total_checked += 1

            open_p = df.iloc[0]['open']
            entry = df.iloc[3]['close']
            change = ((entry - open_p) / open_p) * 100

            print(sym, "change:", round(change, 2))  # DEBUG

            # -------- fallback (always track best) --------
            if not fallback or abs(change) > abs(fallback["change"]):
                fallback = {
                    "sym": sym,
                    "entry": entry,
                    "df": df,
                    "change": change,
                    "sector": sector
                }

            # -------- MAIN FILTER (RELAXED) --------
            if abs(change) < 0.4:
                continue

            if not best or abs(change) > abs(best["change"]):
                best = {
                    "sym": sym,
                    "entry": entry,
                    "df": df,
                    "change": change,
                    "sector": sector
                }

            time.sleep(0.7)

    # -------- FALLBACK --------
    if not best:
        send("⚠️ No strong trade → picking best available")
        best = fallback

    if not best:
        send("❌ No data available")
        return

    # ========= TRADE SIM =========
    entry = best["entry"]
    df = best["df"]

    direction = "BUY" if best["change"] > 0 else "SELL"

    sl = entry * 0.99 if direction == "BUY" else entry * 1.01
    tp = entry * 1.02 if direction == "BUY" else entry * 0.98

    result = "HOLD"

    for i in range(4, len(df)):
        high = df.iloc[i]['high']
        low = df.iloc[i]['low']

        if direction == "BUY":
            if high >= tp:
                result = "TARGET HIT"
                break
            elif low <= sl:
                result = "SL HIT"
                break
        else:
            if low <= tp:
                result = "TARGET HIT"
                break
            elif high >= sl:
                result = "SL HIT"
                break

    # ========= OUTPUT =========
    msg = (
        f"📊 TODAY BACKTEST RESULT\n\n"
        f"Stocks Scanned: {total_checked}\n\n"
        f"Stock: {best['sym']}\n"
        f"Sector: {best['sector']}\n"
        f"Direction: {direction}\n"
        f"Entry: {round(entry,2)}\n"
        f"Move: {round(best['change'],2)}%\n\n"
        f"SL: {round(sl,2)}\n"
        f"TP: {round(tp,2)}\n\n"
        f"Result: {result}"
    )

    send(msg)

# ========= MAIN =========
def main():
    obj = login()
    if obj:
        run_backtest(obj)

if __name__ == "__main__":
    main()
