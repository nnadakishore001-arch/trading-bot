
import os
import pandas as pd
import pyotp
import requests
from datetime import datetime
from SmartApi import SmartConnect
import pytz
import time

# =====================================================
# ENV VARIABLES
# =====================================================
API_KEY = os.getenv("API_KEY")
CLIENT_ID = os.getenv("CLIENT_ID")
PASSWORD = os.getenv("PASSWORD")
TOTP_SECRET = os.getenv("TOTP_SECRET")

TG_TOKEN = os.getenv("TG_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# =====================================================
# GLOBAL API OBJECT
# =====================================================
API_OBJECT = None

# =====================================================
# TELEGRAM FUNCTION
# =====================================================
def send(msg):

    try:

        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": msg
            }
        )

    except Exception as e:

        print("Telegram Error:", e)

# =====================================================
# LOGIN FUNCTION
# =====================================================
def login():

    try:

        obj = SmartConnect(api_key=API_KEY)

        totp = pyotp.TOTP(TOTP_SECRET).now()

        data = obj.generateSession(
            CLIENT_ID,
            PASSWORD,
            totp
        )

        if not data or not data.get("status"):

            send("❌ Login Failed")

            return None

        # =====================================================
        # TOKENS
        # =====================================================
        auth_token = data['data']['jwtToken']

        refresh_token = data['data']['refreshToken']

        feed_token = obj.getfeedToken()

        # =====================================================
        # SET TOKENS
        # =====================================================
        obj.setAccessToken(auth_token)

        obj.setRefreshToken(refresh_token)

        obj.feed_token = feed_token

        # =====================================================
        # PROFILE VALIDATION
        # =====================================================
        profile = obj.getProfile(refresh_token)

        if not profile.get("status"):

            send("❌ Profile Validation Failed")

            return None

        send("✅ Angel One Login Success")

        return obj

    except Exception as e:

        send(f"❌ Login Error: {e}")

        return None

# =====================================================
# SECTOR STOCKS
# =====================================================
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
        "TECHM": "13538",
        "WIPRO": "3787"
    },

    "AUTO": {
        "TATAMOTORS": "3456",
        "MARUTI": "10999",
        "M&M": "2031",
        "BAJAJ-AUTO": "16669"
    },

    "PHARMA": {
        "SUNPHARMA": "3351",
        "CIPLA": "694",
        "DRREDDY": "881",
        "DIVISLAB": "10940"
    },

    "FMCG": {
        "ITC": "1660",
        "HINDUNILVR": "1394",
        "NESTLEIND": "17963"
    },

    "METAL": {
        "TATASTEEL": "3499",
        "JSWSTEEL": "11723",
        "HINDALCO": "1363"
    },

    "ENERGY": {
        "RELIANCE": "2885",
        "ONGC": "2475",
        "NTPC": "11630",
        "POWERGRID": "14977"
    },

    "NBFC": {
        "BAJFINANCE": "317",
        "BAJAJFINSV": "16675"
    },

    "INFRA": {
        "LT": "11483",
        "ADANIPORTS": "15083"
    }
}

# =====================================================
# FETCH MARKET DATA
# =====================================================
def get_data(token):

    global API_OBJECT

    try:

        ist = pytz.timezone("Asia/Kolkata")

        now = datetime.now(ist)

        start = now.replace(
            hour=9,
            minute=15,
            second=0,
            microsecond=0
        )

        response = API_OBJECT.getCandleData({
            "exchange": "NSE",
            "symboltoken": token,
            "interval": "FIVE_MINUTE",
            "fromdate": start.strftime("%Y-%m-%d %H:%M"),
            "todate": now.strftime("%Y-%m-%d %H:%M")
        })

        # =====================================================
        # TOKEN EXPIRED → AUTO RE-LOGIN
        # =====================================================
        if (
            response and
            response.get("errorCode") == "AG8001"
        ):

            send("♻️ Session Expired — Re-Logging")

            API_OBJECT = login()

            if API_OBJECT is None:

                return pd.DataFrame()

            response = API_OBJECT.getCandleData({
                "exchange": "NSE",
                "symboltoken": token,
                "interval": "FIVE_MINUTE",
                "fromdate": start.strftime("%Y-%m-%d %H:%M"),
                "todate": now.strftime("%Y-%m-%d %H:%M")
            })

        if response and response.get("data"):

            return pd.DataFrame(
                response["data"],
                columns=[
                    "time",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume"
                ]
            )

    except Exception as e:

        print("Data Error:", e)

    return pd.DataFrame()

# =====================================================
# MARKET SCANNER
# =====================================================
def scan_market():

    market_data = []

    sector_strength = {}

    for sector, stocks in SECTORS.items():

        for symbol, token in stocks.items():

            df = get_data(token)

            if len(df) < 4:

                continue

            open_price = df.iloc[0]['open']

            latest_close = df.iloc[-1]['close']

            change_percent = (
                (latest_close - open_price) / open_price
            ) * 100

            sector_strength.setdefault(
                sector,
                []
            ).append(change_percent)

            market_data.append({
                "symbol": symbol,
                "sector": sector,
                "change": change_percent,
                "ltp": latest_close
            })

            time.sleep(0.5)

    if not market_data:

        return None, "No market data"

    # =====================================================
    # SECTOR STRENGTH
    # =====================================================
    sector_strength = {
        k: sum(v) / len(v)
        for k, v in sector_strength.items()
    }

    # =====================================================
    # FILTER STRONG STOCKS
    # =====================================================
    signals = []

    for stock in market_data:

        if (
            abs(stock["change"]) >= 0.25 and
            abs(sector_strength[stock["sector"]]) >= 0.20
        ):

            signals.append(stock)

    if not signals:

        return None, "Low momentum"

    # =====================================================
    # BEST SIGNAL
    # =====================================================
    signals.sort(
        key=lambda x: abs(x["change"]),
        reverse=True
    )

    return signals[0], None

# =====================================================
# OPTION PICKER
# =====================================================
def option_pick(price, direction):

    atm = round(price / 100) * 100

    if direction == "BUY":

        return f"{atm} CE"

    return f"{atm} PE"

# =====================================================
# MAIN BOT
# =====================================================
def main():

    global API_OBJECT

    API_OBJECT = login()

    if API_OBJECT is None:

        return

    ist = pytz.timezone("Asia/Kolkata")

    send("🚀 Live Trading Bot Started")

    traded = False

    no_trade_reason = None

    while True:

        now = datetime.now(ist)

        # =====================================================
        # MARKET OPEN MESSAGE
        # =====================================================
        if now.hour == 9 and now.minute == 20:

            send("📊 Market Open — Live Scanning Started")

        # =====================================================
        # LIVE MARKET HOURS
        # =====================================================
        if 9 <= now.hour < 15:

            if not traded:

                signal, reason = scan_market()

                if signal:

                    direction = (
                        "BUY"
                        if signal["change"] > 0
                        else "SELL"
                    )

                    option = option_pick(
                        signal["ltp"],
                        direction
                    )

                    message = (
                        f"🔥 TRADE ALERT\n\n"
                        f"Signal: {direction}\n"
                        f"Stock: {signal['symbol']}\n"
                        f"Sector: {signal['sector']}\n"
                        f"LTP: {round(signal['ltp'], 2)}\n"
                        f"Move: {round(signal['change'], 2)}%\n"
                        f"Option: {option}"
                    )

                    send(message)

                    traded = True

                else:

                    no_trade_reason = reason

        # =====================================================
        # MARKET CLOSE
        # =====================================================
        if (now.hour == 15 and now.minute >= 30) or now.hour > 15:

            if not traded:

                send(
                    f"📉 No Trade Today\n"
                    f"Reason: {no_trade_reason}"
                )

            send("📊 Market Closed — Bot Stopped")

            break

        # =====================================================
        # CHECK EVERY 2 MINUTES
        # =====================================================
        time.sleep(120)

# =====================================================
# START BOT
# =====================================================
if __name__ == "__main__":

    main()
