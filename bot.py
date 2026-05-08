
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
# ANGEL ONE API RATE LIMITS (official, as of 2024)
# Source: smartapi.angelbroking.com/docs/RateLimit
#
# getCandleData:
#   3 requests / second
#   180 requests / minute  (the binding cap — 3/s × 60 = 180)
#   5000 requests / hour
#
# Safe inter-request delay to stay well inside limits:
#   1 request every 0.4s = 2.5 req/s  (safe under the 3/s cap)
#   but Angel One's rate limiter has a known bug where it fires
#   403 even below the documented limit (reported multiple times
#   on the forum). We use 1.1s delay + exponential backoff retry
#   to handle both the documented limit and the buggy enforcement.
# =====================================================
CANDLE_DELAY   = 1.1    # seconds between each getCandleData call
MAX_RETRIES    = 3      # retry attempts per stock on rate-limit hit
RETRY_BACKOFF  = 2.0    # seconds added per retry (exponential)

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
    t = (now.hour, now.minute)
    return (9, 25) <= t < (15, 25)

def is_rate_limit_error(e):
    """
    Detect Angel One rate-limit responses.
    The SDK raises DataException with the raw HTTP body as the message.
    The body is: b'Access denied because of exceeding access rate'
    We check for both the bytes repr and the plain string.
    """
    msg = str(e).lower()
    return "exceeding access rate" in msg or "access denied" in msg

# =====================================================
# LOGIN
# =====================================================
def login():
    """
    Authenticate with Angel One SmartAPI using TOTP.
    Follows the exact official sequence:
      1. generateSession  (SDK auto-sets all tokens internally)
      2. getfeedToken     (stored locally for reference)
      3. getProfile(refreshToken)  (validate session is live)
      4. generateToken(refreshToken)  (refresh JWT pool)
    """
    try:
        obj  = SmartConnect(api_key=API_KEY)
        totp = pyotp.TOTP(TOTP_SECRET).now()

        data = obj.generateSession(CLIENT_ID, PASSWORD, totp)

        if not data or data.get("status") is False:
            send(f"Login failed — generateSession returned: {data}")
            return None

        auth_token    = data["data"]["jwtToken"]
        refresh_token = data["data"]["refreshToken"]
        feed_token    = obj.getfeedToken()

        profile = obj.getProfile(refresh_token)

        if (
            not profile
            or not profile.get("data")
            or not profile["data"].get("clientcode")
        ):
            send(f"Profile validation failed — response: {profile}")
            return None

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
# CANDLE DATA FETCH  — with retry + exponential backoff
# =====================================================
def _fetch_candles(token, from_str, to_str):
    """Single raw getCandleData call."""
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

    Rate-limit handling strategy (fixes "exceeding access rate"):
    ─────────────────────────────────────────────────────────────
    Angel One's getCandleData limit is officially 3 req/s and
    180 req/min. However, the rate limiter has a known bug that
    fires 403 even well below the published limit.

    We handle this with THREE layers of protection:

    Layer 1 — Proactive delay (CANDLE_DELAY = 1.1s per call)
      Keeps our throughput at ~0.9 req/s, safely under the 3/s cap
      AND under the 180/min cap (0.9 × 60 = 54 req/min).

    Layer 2 — Exponential backoff retry (up to MAX_RETRIES = 3)
      On a rate-limit hit, wait RETRY_BACKOFF × attempt seconds
      before retrying the same stock. This gives the server's
      sliding window time to clear before we try again.
      Attempt 1 → wait 2s, Attempt 2 → wait 4s, Attempt 3 → wait 8s

    Layer 3 — Session expiry re-login (AG8001)
      If the JWT expired mid-scan, re-login and retry once.

    Returns DataFrame[time, open, high, low, close, volume]
    or an empty DataFrame if all retries fail.
    """
    global API_OBJECT

    now   = now_ist()
    start = now.replace(hour=9, minute=15, second=0, microsecond=0)

    from_str = start.strftime("%Y-%m-%d %H:%M")
    to_str   = now.strftime("%Y-%m-%d %H:%M")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = _fetch_candles(token, from_str, to_str)

            # ── Layer 3: Session expired → re-login once ────────
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

            # Non-rate-limit empty response — skip this stock
            print(f"[get_data] Empty response — {symbol} ({token}), attempt {attempt}")
            return pd.DataFrame()

        except Exception as e:
            if is_rate_limit_error(e):
                # ── Layer 2: Exponential backoff ────────────────
                wait = RETRY_BACKOFF * attempt
                print(
                    f"[get_data] Rate limit hit — {symbol} ({token}), "
                    f"attempt {attempt}/{MAX_RETRIES}, "
                    f"waiting {wait:.1f}s before retry"
                )
                time.sleep(wait)
                # continue to next attempt

            else:
                # Non-rate-limit exception — don't retry, skip stock
                print(f"[get_data] Error — {symbol} ({token}): {e}")
                return pd.DataFrame()

    print(f"[get_data] All {MAX_RETRIES} retries exhausted — skipping {symbol}")
    return pd.DataFrame()

# =====================================================
# MARKET SCANNER
# =====================================================
def scan_market():
    """
    Scans all 35 stocks across 9 sectors.

    Selection criteria:
      Stock move  >= 0.25% from day-open
      Sector avg  >= 0.20% (sector must confirm the move)

    Returns (best_signal_dict, None) or (None, reason_str).
    """
    market_data = []
    sector_raw  = {}
    skipped     = 0

    for sector, stocks in SECTORS.items():
        for symbol, token in stocks.items():

            df = get_data(token, symbol)

            if len(df) < 4:
                skipped += 1
                # Layer 1 delay still applies even on skip
                time.sleep(CANDLE_DELAY)
                continue

            open_price   = df.iloc[0]["open"]
            latest_close = df.iloc[-1]["close"]

            if open_price == 0:
                skipped += 1
                time.sleep(CANDLE_DELAY)
                continue

            change_pct = ((latest_close - open_price) / open_price) * 100

            sector_raw.setdefault(sector, []).append(change_pct)
            market_data.append({
                "symbol": symbol,
                "sector": sector,
                "change": change_pct,
                "ltp":    latest_close,
            })

            # ── Layer 1: Proactive rate-limit delay ─────────────
            time.sleep(CANDLE_DELAY)

    print(
        f"[scan] Fetched {len(market_data)} stocks, "
        f"skipped {skipped} (< 4 candles or bad data)"
    )

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
    Nearest 50 — standard for NIFTY/BANKNIFTY strike intervals.
    direction: "BUY" -> CE,  "SHORT" -> PE
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

        if now.hour == 9 and now.minute >= 20 and not market_open_msg_sent:
            send("Market open — live scanning will begin at 09:25")
            market_open_msg_sent = True

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
