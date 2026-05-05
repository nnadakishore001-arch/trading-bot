import os
import time
import requests
import pyotp
from SmartApi import SmartConnect

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
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg}
        )
        print("Telegram:", r.text)
    except Exception as e:
        print("Telegram error:", e)

# ========= LOGIN =========
def login():
    try:
        obj = SmartConnect(api_key=API_KEY)

        totp = pyotp.TOTP(TOTP_SECRET).now()
        print("TOTP:", totp)

        data = obj.generateSession(CLIENT_ID, PASSWORD, totp)
        print("Login Response:", data)

        if data and data.get("status"):
            obj.setAccessToken(data['data']['jwtToken'])
            obj.setRefreshToken(data['data']['refreshToken'])
            obj.feed_token = data['data']['feedToken']

            send("✅ Login Success")
            return obj
        else:
            send("❌ Login Failed")

    except Exception as e:
        print("Login error:", e)
        send("❌ Login Exception")

    return None

# ========= MAIN =========
def main():

    send("🚀 Bot Test Started")

    obj = login()

    if obj is None:
        return

    # ===== TEST LTP =====
    try:
        data = obj.ltpData("NSE", "RELIANCE", "2885")
        print("LTP DATA:", data)

        if data and data.get("data"):
            price = data['data']['ltp']
            send(f"✅ API Working\nRELIANCE LTP: {price}")
        else:
            send("❌ API returned no data")

    except Exception as e:
        print("API error:", e)
        send("❌ API Error")

    # ===== LOOP TEST =====
    send("📡 Live LTP Monitoring Started")

    while True:
        try:
            data = obj.ltpData("NSE", "RELIANCE", "2885")

            if data and data.get("data"):
                ltp = data['data']['ltp']
                print("Live LTP:", ltp)

        except Exception as e:
            print("Loop error:", e)

        time.sleep(10)

if __name__ == "__main__":
    main()
