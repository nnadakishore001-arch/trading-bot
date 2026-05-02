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
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"  # ✅ FIXED HERE
    requests.post(url, data={"chat_id": CHAT_ID, "text": msg})

# ===== LOGIN =====
def login():
    totp = pyotp.TOTP(TOTP_SECRET).now()
    obj = SmartConnect(api_key=API_KEY)
    obj.generateSession(CLIENT_ID, PASSWORD, totp)
    return obj

# ===== F&O STOCKS =====
SECTORS = {
    "BANK": {
        "HDFCBANK": "1333",
        "ICICIBANK": "4963",
        "SBIN": "3045",
        "AXISBANK": "5900",
        "KOTAKBANK": "1922"
    },
    "IT": {
        "TCS": "11536",
        "INFY": "1594",
        "HCLTECH": "7229",
        "WIPRO": "3787",
        "TECHM": "13538"
    },
    "AUTO": {
        "TATAMOTORS": "3456",
        "MARUTI": "10999",
        "M&M": "2031",
        "BAJAJ-AUTO": "16669"
    },
    "FMCG": {
        "ITC": "1660",
        "HINDUNILVR": "1394",
        "NESTLEIND": "17963"
    },
    "ENERGY": {
        "RELIANCE": "2885",
        "ONGC": "2475"
    }
}

# ===== GET PRICE CHANGE =====
def get_change(obj, symbol, token):
    data = obj.ltpData("NSE", symbol, token)['data']
    ltp = data['ltp']
    open_price = data['open']
    change = ((ltp - open_price) / open_price) * 100
    return ltp, change

# ===== HEATMAP =====
def format_heatmap(sector_data):
    sorted_sec = sorted(sector_data.items(), key=lambda x: x[1], reverse=True)

    msg = "📊 Sector Heatmap\n\n"

    for sec, pct in sorted_sec:
        bars = int(abs(pct) * 3)

        if pct > 0:
            bar = "🟢" * bars
        else:
            bar = "🔴" * bars

        msg += f"{sec:<8} {pct:+.2f}%  {bar}\n"

    return msg

# ===== MAIN LOGIC =====
def run_screener():
    send("🚀 Running F&O Sector Screener...")

    obj = login()

    sector_strength = {}
    stock_data = []

    for sector, stocks in SECTORS.items():
        changes = []

        for sym, tok in stocks.items():
            try:
                ltp, change = get_change(obj, sym, tok)

                if abs(change) < 0.8:
                    continue

                changes.append(change)
                stock_data.append((sym, tok, change, ltp, sector))

            except:
                pass

        if changes:
            sector_strength[sector] = sum(changes) / len(changes)

    send(format_heatmap(sector_strength))

    filtered = []

    for sym, tok, change, ltp, sector in stock_data:
        if sector_strength.get(sector, 0) > 0.5:
            filtered.append((sym, tok, change, ltp, sector))

    filtered.sort(key=lambda x: x[2], reverse=True)
    top_stocks = filtered[:2]

    msg = "\n🔥 Top Picks (F&O)\n\n"

    for sym, tok, change, ltp, sector in top_stocks:
        direction = "BUY" if change > 0 else "SELL"

        msg += f"{direction} {sym}\n"
        msg += f"Sector: {sector}\n"
        msg += f"LTP: {ltp}\n"
        msg += f"Move: {change:.2f}%\n\n"

    send(msg)

# ===== RUN =====
if __name__ == "__main__":
    run_screener()
