
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
    """
    Returns False on weekends.
    NOTE: Add known NSE holidays manually if needed.
    """
    return now_ist().weekday() < 5          # Mon-Fri only

def market_is_open(now):
    """True during NSE live hours: 09:15 to 15:30."""
    t = (now.hour, now.minute)
    return (9, 15) <= t < (15, 30)

def scan_window_open(now):
    """
    Start scanning only after 09:25 so at least 4 completed
    5-min candles exist (09:15, 09:20, 09:25 + partial).
    Stop scanning 5 minutes before close to avoid thin books.
    """
    t = (now.hour, now.minute)
    return (9, 25) <= t < (15, 25)

# =====================================================
# LOGIN
# =====================================================
def login():
    """
    Authenticate with Angel One SmartAPI using TOTP.
    Returns a live SmartConnect object or None on failure.
    """
    try:
        obj  = SmartConnect(api_key=API_KEY)
        totp = pyotp.TOTP(TOTP_SECRET).now()
        data = obj.generateSession(CLIENT_ID, PASSWORD, totp)

        if not data or not data.get("status"):
            send("Login Failed - Check credentials / TOTP secret")
            return None

        auth_token    = data["data"]["jwtToken"]
        refresh_token = data["data"]["refreshToken"]
        feed_token    = obj.getfeedToken()

        obj.setAccessToken(auth_token)
        obj.setRefreshToken(refresh_token)
        obj.feed_token = feed_token

        profile = obj.getProfile(refresh_token)
        if not profile.get("status"):
            send("Profile validation failed")
            return None

        send("Angel One login successful")
        return obj

    except Exception as e:
        send(f"Login error: {e}")
        return None

# =====================================================
# SECTOR TO STOCK TO TOKEN MAP
# All tokens verified against NSE symbol master (2024)
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
    """Inner helper — makes one getCandleData API call."""
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
    Auto re-login on AG8001 (session expired).
    Returns DataFrame[time, open, high, low, close, volume]
    or an empty DataFrame on any failure.
    """
    global API_OBJECT

    try:
        now   = now_ist()
        start = now.replace(hour=9, minute=15, second=0, microsecond=0)

        from_str = start.strftime("%Y-%m-%d %H:%M")
        to_str   = now.strftime("%Y-%m-%d %H:%M")

        response = _fetch_candles(token, from_str, to_str)

        # Session expired: re-login and retry once
        if response and response.get("errorCode") == "AG8001":
            send("Session expired - re-logging in")
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
        print(f"[get_data] Error - {symbol} ({token}): {e}")

    return pd.DataFrame()

# =====================================================
# MARKET SCANNER
# =====================================================
def scan_market():
    """
    Scans all 35 stocks across 9 sectors.

    Selection criteria:
      Stock move  >= 0.25 % from day-open
      Sector avg  >= 0.20 % (sector must confirm the move)

    Returns (best_signal_dict, None)  on success
            (None, reason_str)        on no signal.
    """
    market_data = []
    sector_raw  = {}          # sector -> [change_pct, ...]

    for sector, stocks in SECTORS.items():
        for symbol, token in stocks.items():

            df = get_data(token, symbol)

            # Need at least 4 completed candles (~20 min of data)
            if len(df) < 4:
                time.sleep(0.7)
                continue

            open_price   = df.iloc[0]["open"]
            latest_close = df.iloc[-1]["close"]

            # Guard against bad tick data with zero open price
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

            # Respect Angel One rate limits (~1.4 req/s safe ceiling)
            time.sleep(0.7)

    if not market_data:
        return None, "No market data fetched - all stocks had fewer than 4 candles"

    # Sector strength: simple average of all member moves
    sector_avg = {
        s: sum(v) / len(v)
        for s, v in sector_raw.items()
    }

    # Filter: stock AND its sector must both cross thresholds
    signals = [
        s for s in market_data
        if s["sector"] in sector_avg
        and abs(s["change"]) >= 0.25
        and abs(sector_avg[s["sector"]]) >= 0.20
    ]

    if not signals:
        return None, "Low momentum - no stock+sector combo crossed thresholds"

    # Pick the single strongest mover
    signals.sort(key=lambda x: abs(x["change"]), reverse=True)
    return signals[0], None

# =====================================================
# OPTION STRIKE PICKER
# =====================================================
def option_pick(price, direction):
    """
    Returns ATM option strike string.

    Rounding:
      Nearest 50  - standard for NIFTY/BANKNIFTY correlated strikes.
      Change to nearest 100 for stocks like RELIANCE or SBIN
      that trade on wider strike intervals.

    direction: "BUY"   -> CE (call, bullish view)
               "SHORT" -> PE (put, bearish view)
    """
    atm = round(price / 50) * 50

    if direction == "BUY":
        return f"{atm} CE"
    return f"{atm} PE"

# =====================================================
# TRADE ALERT FORMATTER
# =====================================================
def build_alert(signal, direction, option):
    """Builds the Telegram message string for a trade alert."""
    return (
        f"TRADE ALERT\n\n"
        f"Signal    : {direction}\n"
        f"Stock     : {signal['symbol']}\n"
        f"Sector    : {signal['sector']}\n"
        f"LTP       : Rs.{round(signal['ltp'], 2)}\n"
        f"Move      : {round(signal['change'], 2)}%\n"
        f"Strike    : {option}\n"
        f"Time      : {now_ist().strftime('%H:%M IST')}"
    )

# =====================================================
# MAIN BOT LOOP
# =====================================================
def main():
    global API_OBJECT

    # Weekend / holiday guard
    if not is_trading_day():
        send("Today is not a trading day (weekend). Bot will not start.")
        print("Not a trading day. Exiting.")
        return

    # Login
    API_OBJECT = login()
    if API_OBJECT is None:
        return

    send("Trading bot started - waiting for scan window (09:25)")

    # Per-session state (resets automatically on each run)
    traded                 = False
    no_trade_reason        = "No signal generated today"
    market_open_msg_sent   = False
    market_closed_msg_sent = False

    # Main event loop
    while True:
        now = now_ist()

        # One-time market-open notification
        if (
            now.hour == 9
            and now.minute >= 20
            and not market_open_msg_sent
        ):
            send("Market open - live scanning will begin at 09:25")
            market_open_msg_sent = True

        # Active scan window: 09:25 to 15:25, only if not yet traded
        if scan_window_open(now) and not traded:

            print(f"[{now.strftime('%H:%M')}] Scanning market...")
            signal, reason = scan_market()

            if signal:
                direction = "BUY" if signal["change"] > 0 else "SHORT"
                option    = option_pick(signal["ltp"], direction)
                message   = build_alert(signal, direction, option)

                send(message)
                print(f"[{now.strftime('%H:%M')}] Alert sent.")

                traded = True

            else:
                no_trade_reason = reason or "Scan returned no reason"
                print(f"[{now.strftime('%H:%M')}] No signal: {no_trade_reason}")

        # Market close: 15:30 or later
        market_over = (
            (now.hour == 15 and now.minute >= 30)
            or now.hour > 15
        )

        if market_over and not market_closed_msg_sent:

            if not traded:
                send(
                    f"No trade today\n"
                    f"Reason: {no_trade_reason}"
                )

            send(
                f"Market closed - bot stopped\n"
                f"Time: {now.strftime('%H:%M IST')}"
            )

            market_closed_msg_sent = True
            break

        # Wait 2 minutes before next scan attempt
        time.sleep(120)


# =====================================================
# ENTRY POINT
# =====================================================
if __name__ == "__main__":
    main()
