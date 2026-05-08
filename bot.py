
import os
import pandas as pd
import pyotp
import requests
from datetime import datetime
from SmartApi import SmartConnect
import pytz
import time

# =====================================================
# ENV VARIABLES  (never change these)
# =====================================================
API_KEY     = os.getenv("API_KEY")
CLIENT_ID   = os.getenv("CLIENT_ID")
PASSWORD    = os.getenv("PASSWORD")
TOTP_SECRET = os.getenv("TOTP_SECRET")

TG_TOKEN = os.getenv("TG_TOKEN")
CHAT_ID  = os.getenv("CHAT_ID")

# =====================================================
# GLOBAL STATE
# =====================================================
API_OBJECT = None
IST        = pytz.timezone("Asia/Kolkata")

# =====================================================
# TELEGRAM FUNCTION  (never change this)
# =====================================================
def send(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg}
        )
    except Exception as e:
        print("Telegram Error:", e)

# =====================================================
# HELPERS
# =====================================================
def now_ist():
    return datetime.now(IST)

def is_trading_day():
    return now_ist().weekday() < 5   # Mon–Fri only

def scan_window_open(now):
    """
    Only scan between 09:25 and 15:25.
    Before 09:25 there are fewer than 4 completed candles.
    After 15:25 the market books are too thin to act on.
    """
    t = (now.hour, now.minute)
    return (9, 25) <= t < (15, 25)

# =====================================================
# LOGIN
# =====================================================
# ROOT CAUSE OF YOUR AG8001 ERROR — THREE BUGS FIXED HERE:
#
# BUG 1: Token order was wrong.
#   Old code called setAccessToken(jwtToken) BEFORE generateSession
#   returned, then immediately called setRefreshToken + getfeedToken.
#   The SDK's own generateSession() already sets all three tokens
#   internally. When you manually set them again AFTER in a different
#   order you can overwrite the internal state and corrupt the auth
#   header that getProfile reads. Confirmed by SmartAPI source:
#   generateSession() calls self.setAccessToken(jwtToken) then
#   self.setRefreshToken(refreshToken) then self.setFeedToken internally.
#
# BUG 2: generateToken(refreshToken) was never called after login.
#   The official Angel One README and test suite both call
#   smartApi.generateToken(refreshToken) right after getProfile.
#   This refreshes the jwtToken pool so subsequent API calls
#   (getCandleData, etc.) don't hit AG8001 mid-session.
#
# BUG 3: Profile validation checked the wrong key.
#   Old code checked profile.get("status") but the SDK wraps the
#   response — the correct key is profile["data"]["clientcode"].
#   If "data" is missing or None the old code passed validation
#   and proceeded with a broken session object.
#
# FIX: Follow the exact official sequence from Angel One's own
#   README and test/api_test.py:
#     1. generateSession  → SDK auto-sets all tokens internally
#     2. getfeedToken     → just for storage, SDK already set it
#     3. getProfile(refreshToken) → validate session is live
#     4. generateToken(refreshToken) → refresh the JWT pool
# =====================================================
def login():
    """
    Authenticate with Angel One SmartAPI using TOTP.
    Returns a live SmartConnect object, or None on failure.
    Follows the exact official sequence from Angel One's README.
    """
    try:
        obj  = SmartConnect(api_key=API_KEY)
        totp = pyotp.TOTP(TOTP_SECRET).now()

        # STEP 1 — Generate session
        # The SDK internally calls setAccessToken + setRefreshToken
        # + setFeedToken. Do NOT override them manually afterwards.
        data = obj.generateSession(CLIENT_ID, PASSWORD, totp)

        if not data or data.get("status") is False:
            send(f"Login failed — generateSession returned: {data}")
            return None

        # STEP 2 — Store tokens locally (SDK already set them internally,
        # these are just for our reference / re-login logic)
        auth_token    = data["data"]["jwtToken"]
        refresh_token = data["data"]["refreshToken"]
        feed_token    = obj.getfeedToken()

        # STEP 3 — Validate session by calling getProfile
        # Must use refresh_token (raw string, not "Bearer …" prefixed)
        profile = obj.getProfile(refresh_token)

        if (
            not profile
            or not profile.get("data")
            or not profile["data"].get("clientcode")
        ):
            send(f"Profile validation failed — response: {profile}")
            return None

        # STEP 4 — Refresh the JWT pool (critical: prevents AG8001
        # mid-session on getCandleData calls)
        obj.generateToken(refresh_token)

        client_name = profile["data"].get("name", CLIENT_ID)
        send(
            f"Angel One login successful\n"
            f"Client: {client_name} ({profile['data']['clientcode']})\n"
            f"Exchanges: {', '.join(profile['data'].get('exchanges', []))}"
        )
        return obj

    except Exception as e:
        send(f"Login error: {e}")
        print(f"[login] Exception: {e}")
        return None

# =====================================================
# SECTOR → STOCK → TOKEN MAP
# =====================================================
SECTORS = {
    "BANK": {
        "HDFCBANK":  "1333",
        "ICICIBANK": "4963",
        "SBIN":      "3045",
        "AXISBANK":  "5900",
        "KOTAKBANK": "1922",
    },
    "IT": {
        "TCS":     "11536",
        "INFY":    "1594",
        "HCLTECH": "7229",
        "TECHM":   "13538",
        "WIPRO":   "3787",
    },
    "AUTO": {
        "TATAMOTORS": "3456",
        "MARUTI":     "10999",
        "M&M":        "2031",
        "BAJAJ-AUTO": "16669",
    },
    "PHARMA": {
        "SUNPHARMA": "3351",
        "CIPLA":     "694",
        "DRREDDY":   "881",
        "DIVISLAB":  "10940",
    },
    "FMCG": {
        "ITC":        "1660",
        "HINDUNILVR": "1394",
        "NESTLEIND":  "17963",
    },
    "METAL": {
        "TATASTEEL": "3499",
        "JSWSTEEL":  "11723",
        "HINDALCO":  "1363",
    },
    "ENERGY": {
        "RELIANCE":  "2885",
        "ONGC":      "2475",
        "NTPC":      "11630",
        "POWERGRID": "14977",
    },
    "NBFC": {
        "BAJFINANCE": "317",
        "BAJAJFINSV": "16675",
    },
    "INFRA": {
        "LT":         "11483",
        "ADANIPORTS": "15083",
    },
}

# =====================================================
# CANDLE DATA FETCH
# =====================================================
def _fetch_candles(token, from_str, to_str):
    return API_OBJECT.getCandleData({
        "exchange":    "NSE",
        "symboltoken": token,
        "interval":    "FIVE_MINUTE",
        "fromdate":    from_str,
        "todate":      to_str,
    })


def get_data(token, symbol=""):
    """
    Fetch 5-minute candles from 09:15 today until now.
    Auto re-login on AG8001 session expiry.
    """
    global API_OBJECT

    try:
        now   = now_ist()
        start = now.replace(hour=9, minute=15, second=0, microsecond=0)

        from_str = start.strftime("%Y-%m-%d %H:%M")
        to_str   = now.strftime("%Y-%m-%d %H:%M")

        response = _fetch_candles(token, from_str, to_str)

        # Session expired mid-scan: re-login and retry once
        if response and response.get("errorCode") == "AG8001":
            send("Session expired mid-scan — re-logging in")
            API_OBJECT = login()
            if API_OBJECT is None:
                return pd.DataFrame()
            response = _fetch_candles(token, from_str, to_str)

        if response and response.get("data"):
            return pd.DataFrame(
                response["data"],
                columns=["time", "open", "high", "low", "close", "volume"],
            )

    except Exception as e:
        print(f"[get_data] Error — {symbol} ({token}): {e}")

    return pd.DataFrame()

# =====================================================
# MARKET SCANNER
# =====================================================
def scan_market():
    """
    Scans all 35 stocks across 9 sectors.

    Thresholds:
      Stock move  >= 0.25% from day-open
      Sector avg  >= 0.20% (sector must confirm the move)

    Returns (best_signal_dict, None) or (None, reason_str).
    """
    market_data = []
    sector_raw  = {}

    for sector, stocks in SECTORS.items():
        for symbol, token in stocks.items():

            df = get_data(token, symbol)

            if len(df) < 4:
                time.sleep(0.7)
                continue

            open_price   = df.iloc[0]["open"]
            latest_close = df.iloc[-1]["close"]

            if open_price == 0:
                time.sleep(0.7)
                continue

            change_pct = ((latest_close - open_price) / open_price) * 100

            sector_raw.setdefault(sector, []).append(change_pct)
            market_data.append({
                "symbol": symbol,
                "sector": sector,
                "change": change_pct,
                "ltp":    latest_close,
            })

            time.sleep(0.7)   # Respect Angel One rate limits

    if not market_data:
        return None, "No market data — all stocks had fewer than 4 candles"

    sector_avg = {s: sum(v) / len(v) for s, v in sector_raw.items()}

    signals = [
        s for s in market_data
        if s["sector"] in sector_avg
        and abs(s["change"]) >= 0.25
        and abs(sector_avg[s["sector"]]) >= 0.20
    ]

    if not signals:
        return None, "Low momentum — no stock+sector combo crossed thresholds"

    signals.sort(key=lambda x: abs(x["change"]), reverse=True)
    return signals[0], None

# =====================================================
# OPTION STRIKE PICKER
# =====================================================
def option_pick(price, direction):
    """
    Round to nearest 50 — standard for NIFTY/BANKNIFTY strike intervals.
    direction: "BUY" -> CE, "SHORT" -> PE
    """
    atm = round(price / 50) * 50
    return f"{atm} CE" if direction == "BUY" else f"{atm} PE"

# =====================================================
# TRADE ALERT FORMATTER
# =====================================================
def build_alert(signal, direction, option):
    return (
        f"TRADE ALERT\n\n"
        f"Signal   : {direction}\n"
        f"Stock    : {signal['symbol']}\n"
        f"Sector   : {signal['sector']}\n"
        f"LTP      : Rs.{round(signal['ltp'], 2)}\n"
        f"Move     : {round(signal['change'], 2)}%\n"
        f"Strike   : {option}\n"
        f"Time     : {now_ist().strftime('%H:%M IST')}"
    )

# =====================================================
# MAIN BOT LOOP
# =====================================================
def main():
    global API_OBJECT

    if not is_trading_day():
        send("Today is not a trading day (weekend). Bot will not start.")
        return

    API_OBJECT = login()
    if API_OBJECT is None:
        return

    send("Trading bot started — waiting for scan window (09:25)")

    traded                 = False
    no_trade_reason        = "No signal generated today"
    market_open_msg_sent   = False
    market_closed_msg_sent = False

    while True:
        now = now_ist()

        # One-time market-open notification at 09:20
        if now.hour == 9 and now.minute >= 20 and not market_open_msg_sent:
            send("Market open — live scanning will begin at 09:25")
            market_open_msg_sent = True

        # Active scan: 09:25–15:25, only if no trade taken yet
        if scan_window_open(now) and not traded:
            print(f"[{now.strftime('%H:%M')}] Scanning market...")
            signal, reason = scan_market()

            if signal:
                direction = "BUY" if signal["change"] > 0 else "SHORT"
                option    = option_pick(signal["ltp"], direction)
                send(build_alert(signal, direction, option))
                print(f"[{now.strftime('%H:%M')}] Trade alert sent.")
                traded = True
            else:
                no_trade_reason = reason or "Scan returned no reason"
                print(f"[{now.strftime('%H:%M')}] No signal: {no_trade_reason}")

        # Market close at 15:30
        if (
            (now.hour == 15 and now.minute >= 30) or now.hour > 15
        ) and not market_closed_msg_sent:

            if not traded:
                send(f"No trade today\nReason: {no_trade_reason}")

            send(f"Market closed — bot stopped\nTime: {now.strftime('%H:%M IST')}")
            market_closed_msg_sent = True
            break

        time.sleep(120)


# =====================================================
# ENTRY POINT
# =====================================================
if __name__ == "__main__":
    main()
