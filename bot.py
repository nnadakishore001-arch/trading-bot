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

# ===== DEBUG ENV =====
print("ENV CHECK:")
print("API_KEY:", "OK" if API_KEY else "MISSING")
print("CLIENT_ID:", "OK" if CLIENT_ID else "MISSING")
print("PASSWORD:", "OK" if PASSWORD else "MISSING")
print("TOTP_SECRET:", "OK" if TOTP_SECRET else "MISSING")
print("TG_TOKEN:", "OK" if TG_TOKEN else "MISSING")
print("CHAT_ID:", "OK" if CHAT_ID else "MISSING")

if not all([API_KEY, CLIENT_ID, PASSWORD, TOTP_SECRET, TG_TOKEN, CHAT_ID]):
    raise Exception("❌ Missing Environment Variables")

# ===== TELEGRAM =====
def send(msg):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg}
        )
        print("Telegram Response:", r.text)
    except Exception as e:
        print("Telegram error:", e)

# ===== LOGIN =====
def login():
    try:
        obj = SmartConnect(api_key=API_KEY)

        totp = pyotp.TOTP(TOTP_SECRET).now()
        print("Generated TOTP:", totp)

        data = obj.generateSession(CLIENT_ID, PASSWORD, totp)
        print("Login Response:", data)

        if data and data.get("status"):
            obj.setAccessToken(data['data']['jwtToken'])
            obj.setRefreshToken(data['data']['refreshToken'])
            obj.feed_token = data['data']['feedToken']

            print("✅ Login Success")
            send("✅ Login Success")

            time.sleep(2)
            return obj
        else:
            print("❌ Login Failed:", data)
            send("❌ Login Failed")

    except Exception as e:
        print("Login Exception:", e)
        send("❌ Login Exception")

    return None

# ===== START =====
send("🚀 Bot Starting...")

obj = login()

if obj is None:
    print("Stopping due to login failure")
    exit()

# ===== TEST API =====
try:
    data = obj.ltpData("NSE", "RELIANCE", "2885")
    print("LTP DATA:", data)

    price = data['data']['ltp']
    send(f"✅ LTP Fetch Success\nRELIANCE: {price}")

except Exception as e:
    print("API ERROR:", e)
    send("❌ API Failed")

# ===== LOOP TEST =====
send("📡 Monitoring started")

while True:
    try:
        data = obj.ltpData("NSE", "RELIANCE", "2885")
        ltp = data['data']['ltp']

        print("Live LTP:", ltp)

    except Exception as e:
        print("Loop Error:", e)

    time.sleep(5)
