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

# ========= TOKENS =========
STOCKS = {
    "RELIANCE": "2885",
    "TCS": "11536"
}

# ========= FETCH =========
def get_data(obj, token):
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

    return pd.DataFrame()

# ========= BACKTEST =========
def run_backtest(obj):

    nifty_df = get_data(obj, NIFTY)
    if len(nifty_df) < 4:
        send("❌ Not enough index data")
        return

    nifty_dir = 1 if nifty_df.iloc[3]['close'] > nifty_df.iloc[0]['open'] else -1

    best = None

    for sym, token in STOCKS.items():
        df = get_data(obj, token)

        if len(df) < 10:
            continue

        open_p = df.iloc[0]['open']
        entry = df.iloc[3]['close']
        change = ((entry - open_p) / open_p) * 100

        if abs(change) < 0.7:
            continue

        if (change > 0 and nifty_dir < 0) or (change < 0 and nifty_dir > 0):
            continue

        if not best or abs(change) > abs(best["change"]):
            best = {
                "sym": sym,
                "entry": entry,
                "df": df,
                "change": change
            }

    if not best:
        send("❌ No trade setup today")
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
        f"Stock: {best['sym']}\n"
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
