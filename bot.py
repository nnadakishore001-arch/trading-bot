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
BACKTEST_DAYS    = 15
MAX_TRADES_DAILY = 2       # Limit to top 2 high-probability setups
STOCK_MIN_MOVE   = 0.40    # Slightly higher to ensure trend strength
STOCK_MAX_MOVE   = 2.00    # Prevent buying into exhaustion
SECTOR_THRESHOLD = 0.25    
STOP_LOSS_PCT    = 0.70    # Hard SL
TARGET_PCT       = 1.50    # Target
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
    try:
        res = API.getCandleData({"exchange": "NSE", "symboltoken": token, "interval": "FIVE_MINUTE",
                                 "fromdate": f"{date_str} 09:15", "todate": f"{date_str} 15:30"})
        if res.get("data"):
            df = pd.DataFrame(res["data"], columns=["time", "open", "high", "low", "close", "volume"])
            df["time"] = pd.to_datetime(df["time"]).dt.tz_convert("Asia/Kolkata")
            return df
    except: return pd.DataFrame()

def compute_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

# ─────────────────────────────────────────────
# ENGINE
# ─────────────────────────────────────────────
API = login()
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

    # Simulation Window
    time_steps = pd.date_range(f"{date_str} 09:25", f"{date_str} 15:15", freq="5min", tz="Asia/Kolkata")
    daily_trades = 0
    active_symbols = set()

    for current_ts in time_steps:
        if daily_trades >= MAX_TRADES_DAILY: break
        
        # Sector confirmation at current timestamp
        sector_moves = {}
        for sym, data in day_cache.items():
            df_slice = data["df"][data["df"]["time"] <= current_ts]
            if not df_slice.empty:
                move = ((df_slice.iloc[-1]["close"] - df_slice.iloc[0]["open"]) / df_slice.iloc[0]["open"]) * 100
                sector_moves.setdefault(data["sector"], []).append(move)
        sec_avgs = {sec: sum(m)/len(m) for sec, m in sector_moves.items()}

        # Scan each stock
        for sym, data in day_cache.items():
            if daily_trades >= MAX_TRADES_DAILY: break
            if sym in active_symbols: continue

            df_s = data["df"][data["df"]["time"] <= current_ts]
            if len(df_s) < 22: continue # Ensure enough data for EMA21

            ema9 = compute_ema(df_s["close"], 9)
            ema21 = compute_ema(df_s["close"], 21)
            
            # 1. EMA Crossover check
            crossover = None
            if ema9.iloc[-2] <= ema21.iloc[-2] and ema9.iloc[-1] > ema21.iloc[-1]: crossover = "BUY"
            elif ema9.iloc[-2] >= ema21.iloc[-2] and ema9.iloc[-1] < ema21.iloc[-1]: crossover = "SHORT"

            if not crossover: continue

            # 2. Logic Filters
            ltp = df_s.iloc[-1]["close"]
            move = ((ltp - df_s.iloc[0]["open"]) / df_s.iloc[0]["open"]) * 100
            sec_move = sec_avgs.get(data["sector"], 0)
            
            # Reversal check (Body > 30% of range)
            candle = df_s.iloc[-1]
            body_range_ratio = abs(candle["close"] - candle["open"]) / (candle["high"] - candle["low"]) if (candle["high"]-candle["low"]) !=0 else 0

            if abs(move) >= STOCK_MIN_MOVE and abs(move) <= STOCK_MAX_MOVE and abs(sec_move) >= SECTOR_THRESHOLD and body_range_ratio > 0.3:
                # ENTRY FOUND
                sl = ltp * (1 - STOP_LOSS_PCT/100) if crossover == "BUY" else ltp * (1 + STOP_LOSS_PCT/100)
                tgt = ltp * (1 + TARGET_PCT/100) if crossover == "BUY" else ltp * (1 - TARGET_PCT/100)
                
                # Track Outcome
                future = data["df"][data["df"]["time"] > current_ts]
                exit_p = ltp
                for _, row in future.iterrows():
                    if crossover == "BUY":
                        if row["low"] <= sl: exit_p = sl; break
                        if row["high"] >= tgt: exit_p = tgt; break
                    else:
                        if row["high"] >= sl: exit_p = sl; break
                        if row["low"] <= tgt: exit_p = tgt; break
                    exit_p = row["close"]

                pnl = ((exit_p - ltp) / ltp * 100) if crossover == "BUY" else ((ltp - exit_p) / ltp * 100)
                all_trades.append({"Date": date_str, "Time": current_ts.strftime("%H:%M"), "Sym": sym, "Dir": crossover, "Entry": round(ltp,2), "Exit": round(exit_p,2), "PnL": round(pnl,2)})
                daily_trades += 1
                active_symbols.add(sym)

# ─────────────────────────────────────────────
# OUTPUT
# ─────────────────────────────────────────────
if all_trades:
    res = pd.DataFrame(all_trades)
    wins = len(res[res["PnL"] > 0])
    summary = "            BACKTEST RESULTS              \n"
    summary += "=========================================\n"
    summary += "      Date Entry Time      Symbol Direction  Entry Price  Exit Price  PnL %\n"
    for _, r in res.iterrows():
        summary += f"{r['Date']}      {r['Time']}  {r['Sym']:>10}  {r['Dir']:>5}  {r['Entry']:>11}  {r['Exit']:>10}  {r['PnL']:>5}\n"
    summary += f"\n--- Summary ---\nTotal Trades : {len(res)}\nWins         : {wins}\nLosses       : {len(res)-wins}\nWin Rate     : {round(wins/len(res)*100,2)}%\nTotal PnL %  : {round(res['PnL'].sum(),2)}%"
    print(summary)
    send_telegram(summary)
