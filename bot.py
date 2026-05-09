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
CANDLE_DELAY   = 1.1    
MAX_RETRIES    = 3      
RETRY_BACKOFF  = 2.0    

# =====================================================
# TELEGRAM FUNCTION 
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
    return now_ist().weekday() < 5   

def scan_window_open(now):
    t = (now.hour, now.minute)
    return (9, 25) <= t < (15, 25)

def is_rate_limit_error(e):
    msg = str(e).lower()
    return "exceeding access rate" in msg or "access denied" in msg

# =====================================================
# LOGIN
# =====================================================
def login():
    try:
        obj  = SmartConnect(api_key=API_KEY)
        totp = pyotp.TOTP(TOTP_SECRET).now()
        data = obj.generateSession(CLIENT_ID, PASSWORD, totp)

        if not data or data.get("status") is False:
            send(f"Login failed — generateSession returned: {data}")
            return None

        refresh_token = data["data"]["refreshToken"]
        obj.getfeedToken()
        profile = obj.getProfile(refresh_token)

        if not profile or not profile.get("data"):
            return None

        obj.generateToken(refresh_token)
        print("[login] Angel One login successful")
        return obj

    except Exception as e:
        print(f"[login] Exception: {e}")
        return None

# =====================================================
# SECTOR → STOCK → TOKEN MAP
# =====================================================
SECTORS = {
    "BANK": {"HDFCBANK": "1333", "ICICIBANK": "4963", "SBIN": "3045", "AXISBANK": "5900", "KOTAKBANK": "1922"},
    "IT": {"TCS": "11536", "INFY": "1594", "HCLTECH": "7229", "TECHM": "13538", "WIPRO": "3787"},
    "AUTO": {"TATAMOTORS": "3456", "MARUTI": "10999", "M&M": "2031", "BAJAJ-AUTO": "16669"},
    "PHARMA": {"SUNPHARMA": "3351", "CIPLA": "694", "DRREDDY": "881", "DIVISLAB": "10940"},
    "FMCG": {"ITC": "1660", "HINDUNILVR": "1394", "NESTLEIND": "17963"},
    "METAL": {"TATASTEEL": "3499", "JSWSTEEL": "11723", "HINDALCO": "1363"},
    "ENERGY": {"RELIANCE": "2885", "ONGC": "2475", "NTPC": "11630", "POWERGRID": "14977"},
    "NBFC": {"BAJFINANCE": "317", "BAJAJFINSV": "16675"},
    "INFRA": {"LT": "11483", "ADANIPORTS": "15083"},
}

def _fetch_candles(token, from_str, to_str):
    return API_OBJECT.getCandleData({
        "exchange": "NSE", "symboltoken": token,
        "interval": "FIVE_MINUTE", "fromdate": from_str, "todate": to_str,
    })

def get_data(token, symbol=""):
    global API_OBJECT
    now   = now_ist()
    start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    from_str = start.strftime("%Y-%m-%d %H:%M")
    to_str   = now.strftime("%Y-%m-%d %H:%M")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = _fetch_candles(token, from_str, to_str)
            if response and response.get("errorCode") == "AG8001":
                API_OBJECT = login()
                if API_OBJECT is None: return pd.DataFrame()
                response = _fetch_candles(token, from_str, to_str)

            if response and response.get("data"):
                return pd.DataFrame(response["data"], columns=["time", "open", "high", "low", "close", "volume"])
            return pd.DataFrame()

        except Exception as e:
            if is_rate_limit_error(e):
                time.sleep(RETRY_BACKOFF * attempt)
            else:
                return pd.DataFrame()
    return pd.DataFrame()

# =====================================================
# TRADE GRADING SYSTEM
# =====================================================
def grade_trade(stock_pct, sector_pct):
    """
    Grades the setup based on momentum strength.
    Returns (Grade, Description, Rank)
    """
    s_chg = abs(stock_pct)
    sec_chg = abs(sector_pct)

    if s_chg >= 1.50 and sec_chg >= 0.75:
        return "A+", "High Recommendation (Strong Momentum)", 3
    elif s_chg >= 0.75 and sec_chg >= 0.40:
        return "B", "Good Setup (Human Logic & Chart Check Advised)", 2
    elif s_chg >= 0.25 and sec_chg >= 0.20:
        return "C", "Borderline Setup (Strict Human Logic Required)", 1
    else:
        return "D", "Ignore (Weak Setup)", 0

# =====================================================
# MARKET SCANNER
# =====================================================
def scan_market():
    market_data = []
    sector_raw  = {}

    for sector, stocks in SECTORS.items():
        for symbol, token in stocks.items():
            df = get_data(token, symbol)
            if len(df) < 4:
                time.sleep(CANDLE_DELAY)
                continue

            open_price   = df.iloc[0]["open"]
            latest_close = df.iloc[-1]["close"]
            if open_price == 0:
                time.sleep(CANDLE_DELAY)
                continue

            change_pct = ((latest_close - open_price) / open_price) * 100
            sector_raw.setdefault(sector, []).append(change_pct)
            
            market_data.append({
                "symbol": symbol, "sector": sector,
                "change": change_pct, "ltp": latest_close,
            })
            time.sleep(CANDLE_DELAY)

    if not market_data:
        return None, "No data available"

    sector_avg = {s: sum(v) / len(v) for s, v in sector_raw.items()}

    # Grade all stocks
    graded_signals = []
    for s in market_data:
        sec_change = sector_avg.get(s["sector"], 0)
        grade, desc, rank = grade_trade(s["change"], sec_change)
        
        # Only keep A+, B, and C trades. Drop D trades.
        if rank > 0: 
            s["grade"] = grade
            s["grade_desc"] = desc
            s["rank"] = rank
            s["sec_change"] = sec_change
            graded_signals.append(s)

    if not graded_signals:
        return None, "No setups met A+, B, or C criteria at this time."

    # Sort primarily by Grade Rank (A+ first), then by the highest % change
    graded_signals.sort(key=lambda x: (x["rank"], abs(x["change"])), reverse=True)
    return graded_signals[0], None

def option_pick(price, direction):
    atm = round(price / 50) * 50
    return f"{atm} CE" if direction == "BUY" else f"{atm} PE"

def build_alert(signal, direction, option):
    # Emojis for quick visual scanning on Telegram
    grade_emoji = {"A+": "🚀", "B": "⚖️", "C": "⚠️"}
    emoji = grade_emoji.get(signal["grade"], "📈")

    return (
        f"{emoji} <b>TRADE ALERT: Grade {signal['grade']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Status:</b> {signal['grade_desc']}\n"
        f"<b>Action:</b> {'🟢 BUY' if direction == 'BUY' else '🔴 SHORT'}\n"
        f"<b>Stock:</b> {signal['symbol']} ({signal['sector']})\n"
        f"<b>LTP:</b> ₹{round(signal['ltp'], 2)}\n"
        f"<b>Stock Move:</b> {round(signal['change'], 2)}%\n"
        f"<b>Sector Move:</b> {round(signal['sec_change'], 2)}%\n"
        f"<b>Target Option:</b> {option}\n"
        f"<b>Time:</b> {now_ist().strftime('%H:%M IST')}"
    )

# =====================================================
# MAIN BOT LOOP
# =====================================================
def main():
    global API_OBJECT
    if not is_trading_day(): return

    API_OBJECT = login()
    if API_OBJECT is None: return

    send("Trading bot started — scanning dynamically between 09:25 and 15:25")
    
    traded = False
    no_trade_reason = "No signal generated today"

    while True:
        now = now_ist()

        if scan_window_open(now) and not traded:
            print(f"[{now.strftime('%H:%M')}] Scanning market for A+/B/C trades...")
            signal, reason = scan_market()

            if signal:
                direction = "BUY" if signal["change"] > 0 else "SHORT"
                option    = option_pick(signal["ltp"], direction)
                send(build_alert(signal, direction, option))
                print(f"[{now.strftime('%H:%M')}] Found {signal['grade']} Trade. Alert sent.")
                traded = True  # Stop scanning after alerting the best trade
            else:
                no_trade_reason = reason or "Scan returned no valid setups"
                print(f"[{now.strftime('%H:%M')}] {no_trade_reason}")

        if (now.hour == 15 and now.minute >= 30) or now.hour > 15:
            if not traded: send(f"Market closed. No trades taken today.\nReason: {no_trade_reason}")
            break

        time.sleep(120)

if __name__ == "__main__":
    main()
