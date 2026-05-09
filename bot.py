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
MAX_TRADES_DAILY = 2       
STOCK_MIN_MOVE   = 0.45    
STOCK_MAX_MOVE   = 1.90    
SECTOR_THRESHOLD = 0.25    
STOP_LOSS_PCT    = 0.70    
BE_TRIGGER_PCT   = 0.70    # Move SL to Entry when price hits this % profit
TARGET_PCT       = 1.50    
CANDLE_DELAY     = 1.1     

IST = pytz.timezone("Asia/Kolkata")

# ─────────────────────────────────────────────
# SYMBOLS MAP
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
# CORE HELPERS
# ─────────────────────────────────────────────
def send_telegram(msg):
    """Sends monospaced formatted table to Telegram."""
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": f"<pre>{msg}</pre>", "parse_mode": "HTML"}
    try: requests.post(url, data=payload, timeout=10)
    except: pass

def login():
    try:
        obj = SmartConnect(api_key=API_KEY)
        data = obj.generateSession(CLIENT_ID, PASSWORD, pyotp.TOTP(TOTP_SECRET).now())
        if data.get("status"):
            obj.generateToken(data["data"]["refreshToken"])
            return obj
    except: return None

def fetch_data(token, date_str):
    """Ensures no NoneType error is returned."""
    try:
        res = API.getCandleData({"exchange": "NSE", "symboltoken": token, "interval": "FIVE_MINUTE",
                                 "fromdate": f"{date_str} 09:15", "todate": f"{date_str} 15:30"})
        if res and res.get("status") and res.get("data"):
            df = pd.DataFrame(res["data"], columns=["time", "open", "high", "low", "close", "volume"])
            df["time"] = pd.to_datetime(df["time"]).dt.tz_convert("Asia/Kolkata")
            return df
    except: pass
    return pd.DataFrame()

def compute_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

# ─────────────────────────────────────────────
# EXECUTION ENGINE
# ─────────────────────────────────────────────
API = login()
if not API: exit()

trading_days = []
curr_date = datetime.now(IST).date()
while len(trading_days) < BACKTEST_DAYS:
    curr_date -= timedelta(days=1)
    if curr_date.weekday() < 5: trading_days.append(curr_date.strftime("%Y-%m-%d"))

all_trades = []

for date_str in reversed(trading_days):
    print(f"Backtesting Date: {date_str}")
    day_cache = {}
    for sec, stocks in SECTORS.items():
        for sym, token in stocks.items():
            df = fetch_data(token, date_str)
            if not df.empty: day_cache[sym] = {"df": df, "sector": sec}
            time.sleep(CANDLE_DELAY)

    time_steps = pd.date_range(f"{date_str} 09:25", f"{date_str} 15:15", freq="5min", tz="Asia/Kolkata")
    daily_trades = 0
    used_syms = set()

    for ts in time_steps:
        if daily_trades >= MAX_TRADES_DAILY: break
        
        # Sector moves at this minute
        sec_moves = {}
        for sym, data in day_cache.items():
            df_s = data["df"][data["df"]["time"] <= ts]
            if not df_s.empty:
                m = ((df_s.iloc[-1]["close"] - df_s.iloc[0]["open"]) / df_s.iloc[0]["open"]) * 100
                sec_moves.setdefault(data["sector"], []).append(m)
        sec_avgs = {sec: sum(v)/len(v) for sec, v in sec_moves.items()}

        for sym, data in day_cache.items():
            if daily_trades >= MAX_TRADES_DAILY or sym in used_syms: continue

            df_s = data["df"][data["df"]["time"] <= ts]
            if len(df_s) < 22: continue

            e9, e21 = compute_ema(df_s["close"], 9), compute_ema(df_s["close"], 21)
            side = None
            if e9.iloc[-2] <= e21.iloc[-2] and e9.iloc[-1] > e21.iloc[-1]: side = "BUY"
            elif e9.iloc[-2] >= e21.iloc[-2] and e9.iloc[-1] < e21.iloc[-1]: side = "SHORT"
            if not side: continue

            ltp = df_s.iloc[-1]["close"]
            move = ((ltp - df_s.iloc[0]["open"]) / df_s.iloc[0]["open"]) * 100
            
            # SunPharma Reversal Filter (Body Ratio > 0.35)
            candle = df_s.iloc[-1]
            c_rng = (candle["high"] - candle["low"])
            body_ratio = abs(candle["close"] - candle["open"]) / c_rng if c_rng != 0 else 0

            if abs(move) >= STOCK_MIN_MOVE and abs(move) <= STOCK_MAX_MOVE and \
               abs(sec_avgs.get(data["sector"], 0)) >= SECTOR_THRESHOLD and body_ratio > 0.35:
                
                # ENTRY FOUND
                entry = ltp
                sl = entry * (1 - STOP_LOSS_PCT/100) if side == "BUY" else entry * (1 + STOP_LOSS_PCT/100)
                be_trigger = entry * (1 + BE_TRIGGER_PCT/100) if side == "BUY" else entry * (1 - BE_TRIGGER_PCT/100)
                tgt = entry * (1 + TARGET_PCT/100) if side == "BUY" else entry * (1 - TARGET_PCT/100)
                
                is_be_active = False
                future = data["df"][data["df"]["time"] > ts]
                final_exit = entry

                for _, row in future.iterrows():
                    # Move SL to Entry if Break-Even Trigger hit
                    if not is_be_active:
                        if (side == "BUY" and row["high"] >= be_trigger) or (side == "SHORT" and row["low"] <= be_trigger):
                            is_be_active = True
                            sl = entry 

                    # Check SL/TGT/EOD
                    if side == "BUY":
                        if row["low"] <= sl: final_exit = sl; break
                        if row["high"] >= tgt: final_exit = tgt; break
                    else:
                        if row["high"] >= sl: final_exit = sl; break
                        if row["low"] <= tgt: final_exit = tgt; break
                    final_exit = row["close"]

                pnl = ((final_exit - entry) / entry * 100) if side == "BUY" else ((entry - final_exit) / entry * 100)
                all_trades.append({"Date": date_str, "Time": ts.strftime("%H:%M"), "Sym": sym, "Dir": side, "Entry": round(entry,2), "Exit": round(final_exit,2), "PnL": round(pnl,2)})
                daily_trades += 1; used_syms.add(sym)

# ─────────────────────────────────────────────
# FINAL FORMATTED OUTPUT
# ─────────────────────────────────────────────
if all_trades:
    res = pd.DataFrame(all_trades)
    wins = len(res[res["PnL"] > 0])
    total = len(res)
    
    # Table Formatting
    summary = "            BACKTEST RESULTS              \n"
    summary += "=========================================\n"
    summary += "      Date Entry Time      Symbol Direction  Entry Price  Exit Price  PnL %\n"
    for _, r in res.iterrows():
        summary += f"{r['Date']}      {r['Time']}  {r['Sym']:>10}  {r['Dir']:>9}  {r['Entry']:>11}  {r['Exit']:>10}  {r['PnL']:>5.2f}\n"
    
    summary += f"\n--- Summary ---\n"
    summary += f"Total Trades : {total}\n"
    summary += f"Wins         : {wins}\n"
    summary += f"Losses       : {total - wins}\n"
    summary += f"Win Rate     : {round(wins/total*100, 2)}%\n"
    summary += f"Total PnL %  : {round(res['PnL'].sum(), 2)}%"
    
    print(summary)
    send_telegram(summary)
else:
    print("No signals found in the backtest period.")
