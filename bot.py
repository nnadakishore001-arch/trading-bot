import requests
import time
from datetime import datetime
from SmartApi import SmartConnect
import pyotp

# ===== LOGIN =====
API_KEY = "VYFnGUA8"
CLIENT_ID = "M373866"
PASSWORD = "0917"
TOTP_SECRET = "3MLPA7DT7BA674CP73DHFDWJ2Q"

totp = pyotp.TOTP(TOTP_SECRET).now()

obj = SmartConnect(api_key=API_KEY)
obj.generateSession(CLIENT_ID, PASSWORD, totp)

print("Login Success")

# ===== TELEGRAM =====
TOKEN = "8706462182:AAHt5JMZ5tfMUjfKTYncwcfHZCflpQY9hHA"
CHAT_ID = "890425913"

def send(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": msg})

# ===== STOCKS =====
TOKENS = {
    "RELIANCE": "2885",
    "TCS": "11536"
}

levels = {}

send("📊 Bot Started...")

# ===== WAIT FOR 9:20 =====
while True:
    now = datetime.now().strftime("%H:%M")
    if now >= "09:20":
        break
    time.sleep(5)

# ===== CAPTURE LEVELS =====
for stock, token in TOKENS.items():
    data = obj.ltpData("NSE", stock, token)
    price = data['data']['ltp']

    levels[stock] = {
        "high": price + 2,
        "low": price - 2
    }

send("✅ Levels Captured")

# ===== LIVE MONITOR =====
while True:
    for stock, token in TOKENS.items():
        try:
            ltp = obj.ltpData("NSE", stock, token)['data']['ltp']

            high = levels[stock]["high"]
            low = levels[stock]["low"]

            if ltp > high:
                send(f"🚀 BUY {stock}\nAbove: {high}\nLTP: {ltp}")
                levels.pop(stock)

            elif ltp < low:
                send(f"🔻 SELL {stock}\nBelow: {low}\nLTP: {ltp}")
                levels.pop(stock)

        except:
            pass

    time.sleep(5)
if __name__ == "__main__":
    run_930_screener()
