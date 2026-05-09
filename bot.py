import os
import pandas as pd
import pyotp
import requests
from datetime import datetime, timedelta
from SmartApi import SmartConnect
import pytz
import time

# ─────────────────────────────────────────────
# CONFIGURATION & ENV
# ─────────────────────────────────────────────
API_KEY     = os.getenv("API_KEY")
CLIENT_ID   = os.getenv("CLIENT_ID")
PASSWORD    = os.getenv("PASSWORD")
TOTP_SECRET = os.getenv("TOTP_SECRET")
TG_TOKEN    = os.getenv("TG_TOKEN")
CHAT_ID     = os.getenv("CHAT_ID")

# BACKTEST SETTINGS
BACKTEST_DAYS    = 30
STOCK_MIN_MOVE   = 0.35    # Min % move from open
STOCK_MAX_MOVE   = 1.80    # Max % move (Prevents buying at the absolute top)
SECTOR_THRESHOLD = 0.25    # Stronger confirmation
STOP_LOSS_PCT    = 0.70    # Hard SL to prevent big account losses
TARGET_PCT       = 1.50    # Realistic Intraday Target
CANDLE_DELAY     = 1.1     # Safe API delay

IST = pytz.timezone("Asia/Kolkata")

# ─────────────────────────────────────────────
# SECTORS & SYMBOLS
# ─────────────────────────────────────────────
SECTORS = {
    "BANK": {"HDFCBANK":"1333","ICICIBANK":"4963","SBIN":"3045","AXISBANK":"5900","KOTAKBANK":"1922"},
    "IT": {"TCS":"11536","INFY":"1594","HCLTECH":"7229","TECHM":"13538","WIPRO":"3787"},
    "AUTO": {"TATAMOTORS":"3456","MARUTI":"10999","M&M":"2031","BAJAJ-AUTO":"16669"},
    "PHARMA": {"SUNPHARMA":"3351","CIPLA":"694","DRREDDY":"881","DIVISLAB":"10940"},
    "FMCG": {"ITC":"1660","HINDUNILVR":"1394","NESTLEIND":"17963"},
    "METAL": {"TATASTEEL":"3499","JSWSTEEL":"11723","HINDALCO":"1363"},
    "ENERGY": {"RELIANCE":"2885","ONGC":"2475","NTPC":"11630","POWERGRID":"14977"},
    "NBFC": {"BAJFINANCE":"317","BAJAJFINSV":"16675"},
    "INFRA": {"LT":"11483","ADANIPORTS":"15083"},
}

# ─────────────────────────────────────────────
# CORE FUNCTIONS
# ─────────────────────────────────────────────
def send_telegram(msg):
    """Sends monospaced formatted table to Telegram."""
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": f"<pre>{msg}</pre>",
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"Telegram Error: {e}")

def login():
    try:
        obj = SmartConnect(api_key=API_KEY)
        totp = pyotp.TOTP(TOTP_SECRET).now()
        data = obj.generateSession(CLIENT_ID, PASSWORD, totp)
        if data.get("status"):
            obj.generateToken(data["data"]["refreshToken"])
            return obj
    except: return None

def fetch_data(token, date_str):
    try:
        res = API.getCandleData({
            "exchange": "NSE", "symboltoken": token, "interval": "FIVE_MINUTE",
            "fromdate": f"{date_str} 09:15", "todate": f"{date_str} 15:30"
        })
        if res.get("data"):
            df = pd.DataFrame(res["data"], columns=["time", "open", "high", "low", "close", "volume"])
            df["time"] = pd.to_datetime(df["time"]).dt.tz_convert("Asia/Kolkata")
            return df
    except: pass
    return pd.DataFrame()

# ─────────────────────────────────────────────
# START BACKTEST
# ─────────────────────────────────────────────
API = login()
if not API:
    print("Login Failed")
    exit()

trading_days = []
curr_date = datetime.now(IST).date()
while len(trading_days) < BACKTEST_DAYS:
    curr_date -= timedelta(days=1)
    if curr_date.weekday() < 5: trading_days.append(curr_date.strftime("%Y-%m-%d"))

all_trades = []
print(f"Starting Sniper Backtest for {BACKTEST_DAYS} days...")

for date_str in reversed(trading_days):
    day_cache = {}
    print(f"Processing: {date_str}")
    
    for sec, stocks in SECTORS.items():
        for sym, token in stocks.items():
            df = fetch_data(token, date_str)
            if not df.empty: day_cache[sym] = {"df": df, "sector": sec}
            time.sleep(CANDLE_DELAY)

    # Replay logic at exactly 09:45
    current_time = pd.Timestamp(f"{date_str} 09:45", tz="Asia/Kolkata")
    sector_moves = {}
    
    for sym, data in day_cache.items():
        df_slice = data["df"][data["df"]["time"] <= current_time]
        if not df_slice.empty:
            move = ((df_slice.iloc[-1]["close"] - df_slice.iloc[0]["open"]) / df_slice.iloc[0]["open"]) * 100
            sector_moves.setdefault(data["sector"], []).append(move)
    
    sector_avgs = {sec: sum(m)/len(m) for sec, m in sector_moves.items()}

    # Scan for Signal
    day_signal_found = False
    for sym, data in day_cache.items():
        if day_signal_found: break
        
        df_slice = data["df"][data["df"]["time"] <= current_time]
        if df_slice.empty: continue
        
        open_p = df_slice.iloc[0]["open"]
        ltp = df_slice.iloc[-1]["close"]
        move = ((ltp - open_p) / open_p) * 100
        sec_move = sector_avgs.get(data["sector"], 0)

        # Filters
        if abs(move) >= STOCK_MIN_MOVE and abs(move) <= STOCK_MAX_MOVE and abs(sec_move) >= SECTOR_THRESHOLD:
            direction = "BUY" if move > 0 else "SHORT"
            sl = ltp * (1 - STOP_LOSS_PCT/100) if direction == "BUY" else ltp * (1 + STOP_LOSS_PCT/100)
            tgt = ltp * (1 + TARGET_PCT/100) if direction == "BUY" else ltp * (1 - TARGET_PCT/100)
            
            # Walk forward to find exit
            future = data["df"][data["df"]["time"] > current_time]
            exit_p = ltp
            for _, row in future.iterrows():
                if direction == "BUY":
                    if row["low"] <= sl: exit_p = sl; break
                    if row["high"] >= tgt: exit_p = tgt; break
                else:
                    if row["high"] >= sl: exit_p = sl; break
                    if row["low"] <= tgt: exit_p = tgt; break
                exit_p = row["close"] # EOD Exit

            pnl = ((exit_p - ltp) / ltp * 100) if direction == "BUY" else ((ltp - exit_p) / ltp * 100)
            
            all_trades.append({
                "Date": date_str, "Time": "09:45", "Symbol": sym, 
                "Dir": direction, "Entry": round(ltp, 2), "Exit": round(exit_p, 2), 
                "PnL": round(pnl, 2)
            })
            day_signal_found = True

# ─────────────────────────────────────────────
# FORMAT FINAL REPORT
# ─────────────────────────────────────────────
if all_trades:
    df_res = pd.DataFrame(all_trades)
    
    # Calculate Stats
    total = len(df_res)
    wins = len(df_res[df_res["PnL"] > 0])
    losses = total - wins
    win_rate = round((wins / total) * 100, 2)
    total_pnl = round(df_res["PnL"].sum(), 2)
    
    # Format Table Header
    report = "            BACKTEST RESULTS              \n"
    report += "=========================================\n"
    report += "      Date Entry Time      Symbol Direction  Entry Price  Exit Price  PnL %\n"
    
    # Add Rows
    for _, r in df_res.iterrows():
        report += f"{r['Date']}      {r['Time']}  {r['Symbol']:>10}  {r['Dir']:>5}  {r['Entry']:>11}  {r['Exit']:>10}  {r['PnL']:>5}\n"
    
    # Add Summary
    report += f"\n--- Summary ---\n"
    report += f"Total Trades : {total}\n"
    report += f"Wins         : {wins}\n"
    report += f"Losses       : {losses}\n"
    report += f"Win Rate     : {win_rate}%\n"
    report += f"Total PnL %  : {total_pnl}%"
    
    # Final Send
    print(report)
    send_telegram(report)
else:
    print("No trades found in backtest.")
