
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
API_KEY     = os.getenv("API_KEY")
CLIENT_ID   = os.getenv("CLIENT_ID")
PASSWORD    = os.getenv("PASSWORD")
TOTP_SECRET = os.getenv("TOTP_SECRET")
TG_TOKEN    = os.getenv("TG_TOKEN")
CHAT_ID     = os.getenv("CHAT_ID")

# =====================================================
# GLOBAL SETTINGS
# =====================================================
API_OBJECT = None
IST        = pytz.timezone("Asia/Kolkata")
CANDLE_DELAY   = 1.1    
MAX_RETRIES    = 3      
RETRY_BACKOFF  = 2.0    

def send(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"})
    except Exception as e:
        pass # Silent failure for Telegram to keep console clean

def now_ist():
    return datetime.now(IST)

def is_trading_day():
    return now_ist().weekday() < 5   

def scan_window_open(now):
    t = (now.hour, now.minute)
    return (9, 25) <= t < (15, 25)

def login():
    try:
        obj  = SmartConnect(api_key=API_KEY)
        totp = pyotp.TOTP(TOTP_SECRET).now()
        data = obj.generateSession(CLIENT_ID, PASSWORD, totp)
        if not data or data.get("status") is False: return None
        refresh_token = data["data"]["refreshToken"]
        obj.getfeedToken()
        obj.generateToken(refresh_token)
        return obj
    except:
        return None

# =====================================================
# SECTOR MAP
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

def get_data(token):
    global API_OBJECT
    now = now_ist()
    start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    from_str, to_str = start.strftime("%Y-%m-%d %H:%M"), now.strftime("%Y-%m-%d %H:%M")
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            res = API_OBJECT.getCandleData({"exchange": "NSE", "symboltoken": token, "interval": "FIVE_MINUTE", "fromdate": from_str, "todate": to_str})
            if res and res.get("data"):
                return pd.DataFrame(res["data"], columns=["time", "open", "high", "low", "close", "volume"])
            if res and res.get("errorCode") == "AG8001":
                API_OBJECT = login()
            time.sleep(RETRY_BACKOFF)
        except:
            continue
    return pd.DataFrame()

def grade_trade(stock_pct, sector_pct):
    s_chg, sec_chg = abs(stock_pct), abs(sector_pct)
    if s_chg >= 1.50 and sec_chg >= 0.75: return "A+", "High Recommendation (Strong Momentum)", 3
    if s_chg >= 0.75 and sec_chg >= 0.40: return "B", "Good Setup (Human Logic & Chart Check Advised)", 2
    if s_chg >= 0.25 and sec_chg >= 0.20: return "C", "Borderline Setup (Strict Human Logic Required)", 1
    return "D", None, 0

def scan_market():
    market_data, sector_raw = [], {}
    for sector, stocks in SECTORS.items():
        for symbol, token in stocks.items():
            df = get_data(token)
            if len(df) < 4 or df.iloc[0]["open"] == 0:
                time.sleep(CANDLE_DELAY)
                continue
            change_pct = ((df.iloc[-1]["close"] - df.iloc[0]["open"]) / df.iloc[0]["open"]) * 100
            sector_raw.setdefault(sector, []).append(change_pct)
            market_data.append({"symbol": symbol, "sector": sector, "change": change_pct, "ltp": df.iloc[-1]["close"]})
            time.sleep(CANDLE_DELAY)

    if not market_data: return None
    sector_avg = {s: sum(v) / len(v) for s, v in sector_raw.items()}
    graded = []
    for s in market_data:
        grade, desc, rank = grade_trade(s["change"], sector_avg.get(s["sector"], 0))
        if rank > 0:
            s.update({"grade": grade, "grade_desc": desc, "rank": rank, "sec_change": sector_avg[s["sector"]]})
            graded.append(s)
    
    if not graded: return None
    graded.sort(key=lambda x: (x["rank"], abs(x["change"])), reverse=True)
    return graded[0]

def main():
    global API_OBJECT

    if not is_trading_day():
        return

    API_OBJECT = login()

    if not API_OBJECT:
        return

    # Startup message
    print(f"[{now_ist().strftime('%H:%M')}] Bot is live and scanning silently...")
    send("<b>Bot Live</b> - Scanning for A+, B, and C grade trades.")

    # =====================================================
    # UPDATED TRADE CONTROL
    # =====================================================
    trades_taken = 0
    MAX_TRADES_PER_DAY = 2

    while True:

        now = now_ist()

        # =====================================================
        # ACTIVE SCAN WINDOW
        # =====================================================
        if scan_window_open(now) and trades_taken < MAX_TRADES_PER_DAY:

            signal = scan_market()

            if signal:

                direction = "BUY" if signal["change"] > 0 else "SHORT"

                atm = round(signal["ltp"] / 50) * 50

                option = f"{atm} {'CE' if direction == 'BUY' else 'PE'}"

                emoji = {
                    "A+": "🚀",
                    "B": "⚖️",
                    "C": "⚠️"
                }.get(signal["grade"], "📈")

                alert = (
                    f"{emoji} <b>Grade {signal['grade']} Alert</b>\n"
                    f"<b>Stock:</b> {signal['symbol']}\n"
                    f"<b>Move:</b> {round(signal['change'], 2)}% "
                    f"(Sec: {round(signal['sec_change'], 2)}%)\n"
                    f"<b>Option:</b> {option}\n"
                    f"<b>Note:</b> {signal['grade_desc']}"
                )

                send(alert)

                # =====================================================
                # UPDATED TRADE COUNTER
                # =====================================================
                trades_taken += 1

                print(
                    f"[{now.strftime('%H:%M')}] "
                    f"Trade Found: {signal['symbol']} "
                    f"({signal['grade']}) | "
                    f"Trade {trades_taken}/{MAX_TRADES_PER_DAY}"
                )

        # =====================================================
        # MARKET CLOSE
        # =====================================================
        if (now.hour == 15 and now.minute >= 30) or now.hour > 15:

            send("Market closed - bot stopping.")

            break

        time.sleep(120)


if __name__ == "__main__":
    main()
