import pandas as pd
from datetime import datetime
from SmartApi import SmartConnect
import pyotp
import requests
import time
import pytz
import os

# ===== ENV =====
API_KEY = os.getenv("API_KEY")
CLIENT_ID = os.getenv("CLIENT_ID")
PASSWORD = os.getenv("PASSWORD")
TOTP_SECRET = os.getenv("TOTP_SECRET")

TG_TOKEN = os.getenv("TG_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# ===== TELEGRAM =====
def send(msg):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg}
        )
        print("Telegram:", r.text)
    except Exception as e:
        print("Telegram error:", e)

# ===== LOGIN =====
def login():
    try:
        obj = SmartConnect(api_key=API_KEY)

        totp = pyotp.TOTP(TOTP_SECRET).now()
        data = obj.generateSession(CLIENT_ID, PASSWORD, totp)

        if data and data.get("status"):
            obj.setAccessToken(data['data']['jwtToken'])
            obj.setRefreshToken(data['data']['refreshToken'])
            obj.feed_token = data['data']['feedToken']

            print("✅ Login Success")
            send("✅ Login Success")

            return obj

    except Exception as e:
        print("Login error:", e)
        send("❌ Login Failed")

    return None

# ===== START =====
obj = login()
if obj is None:
    exit()

send("📊 Bot Started...")

# ===== SIMPLE TEST CALL =====
try:
    data = obj.ltpData("NSE", "RELIANCE", "2885")
    price = data['data']['ltp']

    send(f"✅ LTP Fetch Success\nRELIANCE: {price}")

except Exception as e:
    print("API error:", e)
    send("❌ API Failed")

# ===== TEST LOOP =====
send("🚀 Monitoring Started")

while True:
    try:
        data = obj.ltpData("NSE", "RELIANCE", "2885")
        ltp = data['data']['ltp']

        print("LTP:", ltp)

    except Exception as e:
        print("Loop error:", e)

    time.sleep(5)
