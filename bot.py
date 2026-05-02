import pandas as pd
import numpy as np
import requests
import pyotp
import time
from datetime import datetime, timedelta
from SmartApi import SmartConnect

# ==============================================================================
# CONFIG
# ==============================================================================
TOKEN_TG    = "8691427620:AAF5vkJmHqETtm2TyhEd6CLdozCPsa57ATg"
CHAT_ID     = "890425913"
API_KEY     = "VYFnGUA8"
CLIENT_ID   = "M373866"
PASSWORD    = "0917"
TOTP_SECRET = "3MLPA7DT7BA674CP73DHFDWJ2Q"

BT_DAYS  = 45
BT_END   = datetime.now()
BT_START = BT_END - timedelta(days=BT_DAYS)

# ==============================================================================
# TELEGRAM
# ==============================================================================
def send(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN_TG}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg},
            timeout=10
        )
    except:
        pass

# ==============================================================================
# SESSION MANAGEMENT (FIXED)
# ==============================================================================
_obj = None
LAST_LOGIN = 0

def login():
    global _obj, LAST_LOGIN

    obj = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()

    data = obj.generateSession(CLIENT_ID, PASSWORD, totp)

    if not data.get("status"):
        raise Exception(f"Login failed: {data}")

    jwt_token     = data["data"]["jwtToken"]
    refresh_token = data["data"]["refreshToken"]

    obj.setAccessToken(jwt_token)
    obj.setRefreshToken(refresh_token)

    # VERY IMPORTANT
    obj.generateToken(refresh_token)

    time.sleep(1)  # stabilize session

    _obj = obj
    LAST_LOGIN = time.time()

    print("✅ LOGIN SUCCESS")
    return obj


def ensure_session():
    global LAST_LOGIN, _obj
    if time.time() - LAST_LOGIN > 600:  # 10 min refresh
        print("🔄 Refreshing session...")
        _obj = login()


def safe_call(api_func, *args, **kwargs):
    global _obj

    for i in range(3):
        try:
            ensure_session()
            res = api_func(*args, **kwargs)

            if isinstance(res, dict) and res.get("errorCode") == "AG8001":
                print("⚠️ Token expired → Re-login")
                _obj = login()
                continue

            return res

        except Exception as e:
            print(f"Retry {i+1}: {e}")
            time.sleep(2)

    return None


# ==============================================================================
# DATA FETCH (FIXED)
# ==============================================================================
def fetch_history(token, start, end):
    all_rows = []
    current  = start

    while current < end:
        ensure_session()

        chunk_end = min(current + timedelta(days=5), end)

        params = {
            "exchange": "NSE",
            "symboltoken": token,
            "interval": "FIVE_MINUTE",
            "fromdate": current.strftime("%Y-%m-%d 09:15"),
            "todate": chunk_end.strftime("%Y-%m-%d 15:30"),
        }

        res = safe_call(lambda: _obj.getCandleData(params))

        if res and res.get("data"):
            all_rows.extend(res["data"])

        current = chunk_end + timedelta(days=1)
        time.sleep(0.3)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows,
        columns=["time","open","high","low","close","volume"])

    df["time"] = pd.to_datetime(df["time"])
    df["date"] = df["time"].dt.date

    for col in ["open","high","low","close","volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna().sort_values("time").reset_index(drop=True)

    return df


# ==============================================================================
# TEST RUN
# ==============================================================================
if __name__ == "__main__":

    login()

    send("🚀 Backtest started...")

    print("Fetching sample stock (HCLTECH)...")

    df = fetch_history("7229", BT_START, BT_END)

    if df.empty:
        print("❌ No data fetched")
    else:
        print("✅ Data fetched:", len(df), "rows")
        print(df.head())

    send("✅ Data fetch completed")
