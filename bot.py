import os
import pandas as pd
import pyotp
import requests
from datetime import datetime, timedelta, time as dtime
from SmartApi import SmartConnect
import pytz
import time

# =====================================================
# ENV VARIABLES (Fill these in your environment)
# =====================================================
API_KEY     = os.getenv("API_KEY")
CLIENT_ID   = os.getenv("CLIENT_ID")
PASSWORD    = os.getenv("PASSWORD")
TOTP_SECRET = os.getenv("TOTP_SECRET")
TG_TOKEN    = os.getenv("TG_TOKEN")
CHAT_ID     = os.getenv("CHAT_ID")

# =====================================================
# BACKTEST CONFIGURATION
# =====================================================
DAYS_TO_BACKTEST = 30      # How many trading days to look back
IST = pytz.timezone("Asia/Kolkata")

# Strategy Settings (Matches your Live Code)
EMA_FAST = 9
EMA_SLOW = 21
VOLUME_SPIKE = 2.0
STOCK_THRESHOLD = 0.25
SECTOR_THRESHOLD = 0.20
SL_PCT = 0.30
TARGET_PCT = 0.60
CANDLE_DELAY = 1.1

# =====================================================
# SECTOR MAP (Matches your Live Code)
# =====================================================
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

API_OBJECT = None

# =====================================================
# TELEGRAM NOTIFICATION
# =====================================================
def send_bt_msg(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": f"📊 [BACKTEST]\n{msg}"}
        )
    except Exception as e:
        print("Telegram Error:", e)

# =====================================================
# LOGIN & DATA FETCHING
# =====================================================
def login():
    global API_OBJECT
    obj = SmartConnect(api_key=API_KEY)
    data = obj.generateSession(CLIENT_ID, PASSWORD, pyotp.TOTP(TOTP_SECRET).now())
    if data.get("status"):
        obj.generateToken(data["data"]["refreshToken"])
        return obj
    return None

def fetch_historical_data(token, date_str):
    """Fetches full day 5-min candles."""
    try:
        res = API_OBJECT.getCandleData({
            "exchange": "NSE",
            "symboltoken": token,
            "interval": "FIVE_MINUTE",
            "fromdate": f"{date_str} 09:15",
            "todate": f"{date_str} 15:30"
        })
        if res.get("data"):
            df = pd.DataFrame(res["data"], columns=["time", "open", "high", "low", "close", "volume"])
            df["time"] = pd.to_datetime(df["time"]).dt.tz_convert("Asia/Kolkata")
            return df
    except:
        pass
    return pd.DataFrame()

# =====================================================
# INDICATORS
# =====================================================
def compute_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def check_signal(df, sector_avg_move):
    if len(df) < EMA_SLOW + 2: return None
    
    ema9 = compute_ema(df["close"], EMA_FAST)
    ema21 = compute_ema(df["close"], EMA_SLOW)
    
    # Crossover logic
    crossover = None
    if ema9.iloc[-2] <= ema21.iloc[-2] and ema9.iloc[-1] > ema21.iloc[-1]: crossover = "BUY"
    if ema9.iloc[-2] >= ema21.iloc[-2] and ema9.iloc[-1] < ema21.iloc[-1]: crossover = "SHORT"
    
    if not crossover: return None

    # Volume & Reversal logic
    avg_vol = df["volume"].iloc[:-1].mean()
    vol_spike = df["volume"].iloc[-1] >= VOLUME_SPIKE * avg_vol
    
    candle = df.iloc[-1]
    body = abs(candle["close"] - candle["open"])
    rng = (candle["high"] - candle["low"])
    no_reversal = (body / rng) >= 0.30 if rng != 0 else False

    # Threshold checks
    open_price = df.iloc[0]["open"]
    move = ((candle["close"] - open_price) / open_price) * 100
    
    if abs(move) >= STOCK_THRESHOLD and abs(sector_avg_move) >= SECTOR_THRESHOLD and vol_spike and no_reversal:
        return crossover
    return None

# =====================================================
# MAIN BACKTEST ENGINE
# =====================================================
def run_backtest():
    global API_OBJECT
    API_OBJECT = login()
    if not API_OBJECT: 
        print("Login Failed")
        return

    # Determine trading days
    backtest_days = []
    curr = datetime.now(IST).date()
    while len(backtest_days) < DAYS_TO_BACKTEST:
        curr -= timedelta(days=1)
        if curr.weekday() < 5: backtest_days.append(curr)
    
    backtest_days.sort()
    all_trades = []

    send_bt_msg(f"Starting Backtest for last {DAYS_TO_BACKTEST} days...")

    for day in backtest_days:
        date_str = day.strftime("%Y-%m-%d")
        print(f"Processing {date_str}...")
        
        # 1. Fetch data for all stocks for this day
        day_cache = {}
        for sector, stocks in SECTORS.items():
            for symbol, token in stocks.items():
                df = fetch_historical_data(token, date_str)
                if not df.empty:
                    day_cache[symbol] = {"df": df, "sector": sector}
                time.sleep(CANDLE_DELAY)

        # 2. Simulate the day (09:25 to 15:25)
        # We step 5 minutes at a time
        time_steps = pd.date_range(f"{date_str} 09:25", f"{date_str} 15:25", freq="5min", tz="Asia/Kolkata")
        
        day_trades_count = 0
        
        for current_time in time_steps:
            # Calculate sector averages for this specific time step
            sector_moves = {}
            for sym, data in day_cache.items():
                df_slice = data["df"][data["df"]["time"] <= current_time]
                if not df_slice.empty:
                    move = ((df_slice.iloc[-1]["close"] - df_slice.iloc[0]["open"]) / df_slice.iloc[0]["open"]) * 100
                    sector_moves.setdefault(data["sector"], []).append(move)
            
            sector_avgs = {sec: sum(m)/len(m) for sec, m in sector_moves.items()}

            # Check signals for each stock
            for sym, data in day_cache.items():
                df_slice = data["df"][data["df"]["time"] <= current_time]
                signal = check_signal(df_slice, sector_avgs.get(data["sector"], 0))
                
                if signal:
                    # SIMULATE TRADE
                    entry_price = df_slice.iloc[-1]["close"]
                    entry_time = current_time.strftime("%H:%M")
                    
                    sl = entry_price * (1 - SL_PCT/100) if signal == "BUY" else entry_price * (1 + SL_PCT/100)
                    tgt = entry_price * (1 + TARGET_PCT/100) if signal == "BUY" else entry_price * (1 - TARGET_PCT/100)
                    
                    # Track outcome in future candles
                    future_df = data["df"][data["df"]["time"] > current_time]
                    outcome = "EOD"
                    exit_price = entry_price
                    exit_time = "15:25"

                    for _, row in future_df.iterrows():
                        if signal == "BUY":
                            if row["low"] <= sl: outcome = "SL"; exit_price = sl; exit_time = row["time"].strftime("%H:%M"); break
                            if row["high"] >= tgt: outcome = "TARGET"; exit_price = tgt; exit_time = row["time"].strftime("%H:%M"); break
                        else:
                            if row["high"] >= sl: outcome = "SL"; exit_price = sl; exit_time = row["time"].strftime("%H:%M"); break
                            if row["low"] <= tgt: outcome = "TARGET"; exit_price = tgt; exit_time = row["time"].strftime("%H:%M"); break
                        exit_price = row["close"]

                    pnl = ((exit_price - entry_price) / entry_price) * 100 if signal == "BUY" else ((entry_price - exit_price) / entry_price) * 100
                    
                    trade_info = {
                        "day": date_str, "sym": sym, "dir": signal, "entry": entry_price, 
                        "time": entry_time, "outcome": outcome, "pnl": pnl, "exit_t": exit_time
                    }
                    
                    all_trades.append(trade_info)
                    
                    # Notify Telegram
                    send_bt_msg(
                        f"📅 Date: {date_str}\n"
                        f"🔔 Signal: {signal} {sym}\n"
                        f"🕒 Entry: {entry_time} @ ₹{round(entry_price, 2)}\n"
                        f"🏁 Result: {outcome} at {exit_time}\n"
                        f"💰 P&L: {round(pnl, 2)}%"
                    )
                    day_trades_count += 1
                    # Note: We simulate as if the bot keeps scanning, but you can add a 'break' 
                    # if your live bot only takes 1 trade per stock per day.

    # Final Summary
    if all_trades:
        total_pnl = sum([t['pnl'] for t in all_trades])
        wins = len([t for t in all_trades if t['pnl'] > 0])
        summary = (
            f"🏁 BACKTEST COMPLETE 🏁\n"
            f"Total Trades: {len(all_trades)}\n"
            f"Win Rate: {round((wins/len(all_trades))*100, 2)}%\n"
            f"Total P&L: {round(total_pnl, 2)}%"
        )
        send_bt_msg(summary)
    else:
        send_bt_msg("Backtest finished: No signals found in this period.")

if __name__ == "__main__":
    run_backtest()
