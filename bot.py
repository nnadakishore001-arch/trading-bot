import os
import pandas as pd
import pyotp
import requests
from datetime import datetime, timedelta
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

# =====================================================
# BACKTEST CONFIGURATION
# =====================================================
BACKTEST_DAYS = 30      # Number of previous trading days to test
CANDLE_DELAY  = 1.1     # Essential delay to prevent Angel One 403 blocks
MAX_RETRIES   = 3
RETRY_BACKOFF = 2.0

API_OBJECT = None
IST        = pytz.timezone("Asia/Kolkata")

# =====================================================
# SECTORS (Same as Live)
# =====================================================
SECTORS = {
    "BANK": {"HDFCBANK":"1333", "ICICIBANK":"4963", "SBIN":"3045", "AXISBANK":"5900", "KOTAKBANK":"1922"},
    "IT": {"TCS":"11536", "INFY":"1594", "HCLTECH":"7229", "TECHM":"13538", "WIPRO":"3787"},
    "AUTO": {"TATAMOTORS":"3456", "MARUTI":"10999", "M&M":"2031", "BAJAJ-AUTO":"16669"},
    "PHARMA": {"SUNPHARMA":"3351", "CIPLA":"694", "DRREDDY":"881", "DIVISLAB":"10940"},
    "FMCG": {"ITC":"1660", "HINDUNILVR":"1394", "NESTLEIND":"17963"},
    "METAL": {"TATASTEEL":"3499", "JSWSTEEL":"11723", "HINDALCO":"1363"},
    "ENERGY": {"RELIANCE":"2885", "ONGC":"2475", "NTPC":"11630", "POWERGRID":"14977"},
    "NBFC": {"BAJFINANCE":"317", "BAJAJFINSV":"16675"},
    "INFRA": {"LT":"11483", "ADANIPORTS":"15083"},
}

# =====================================================
# LOGIN
# =====================================================
def login():
    print("Logging into Angel One SmartAPI...")
    try:
        obj  = SmartConnect(api_key=API_KEY)
        totp = pyotp.TOTP(TOTP_SECRET).now()
        data = obj.generateSession(CLIENT_ID, PASSWORD, totp)
        if not data or data.get("status") is False:
            print("Login failed.")
            return None
            
        rf = data["data"]["refreshToken"]
        obj.getfeedToken()
        obj.getProfile(rf)
        obj.generateToken(rf)
        print("Login Successful!")
        return obj
    except Exception as e:
        print(f"Login error: {e}")
        return None

# =====================================================
# DATA FETCHING
# =====================================================
def fetch_historical_day(token, symbol, date_str):
    """Fetches a full single day of 5-min candles."""
    global API_OBJECT
    from_str = f"{date_str} 09:15"
    to_str   = f"{date_str} 15:30"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = API_OBJECT.getCandleData({
                "exchange": "NSE", "symboltoken": token,
                "interval": "FIVE_MINUTE", "fromdate": from_str, "todate": to_str
            })
            
            if resp and resp.get("data"):
                df = pd.DataFrame(resp["data"], columns=["time", "open", "high", "low", "close", "volume"])
                df["time"] = pd.to_datetime(df["time"])
                return df
            return pd.DataFrame()
            
        except Exception as e:
            msg = str(e).lower()
            if "exceeding access rate" in msg or "access denied" in msg:
                time.sleep(RETRY_BACKOFF * attempt)
            else:
                return pd.DataFrame()
    return pd.DataFrame()

# =====================================================
# BACKTEST ENGINE
# =====================================================
def run_backtest():
    global API_OBJECT
    API_OBJECT = login()
    if not API_OBJECT: return

    # 1. Determine the last N trading days
    trading_days = []
    current_date = datetime.now(IST).date() - timedelta(days=1)
    
    while len(trading_days) < BACKTEST_DAYS:
        if current_date.weekday() < 5: # Monday to Friday
            trading_days.append(current_date)
        current_date -= timedelta(days=1)
    
    trading_days.reverse() # Chronological order
    trade_log = []

    print(f"\nStarting Backtest for the last {BACKTEST_DAYS} trading days.")
    print("Warning: Fetching data takes time due to API rate limits (~1 min per day).")

    for target_date in trading_days:
        date_str = target_date.strftime("%Y-%m-%d")
        print(f"\n--- Processing Date: {date_str} ---")
        
        # Pre-fetch all data for the day to simulate live market
        day_data = {}
        for sector, stocks in SECTORS.items():
            for symbol, token in stocks.items():
                df = fetch_historical_day(token, symbol, date_str)
                if not df.empty:
                    day_data[symbol] = {"df": df, "sector": sector}
                time.sleep(CANDLE_DELAY) # Strict rate limiting

        if not day_data:
            print(f"No data available for {date_str}. Skipping.")
            continue

        # 2. Simulate time progressing from 09:25 to 15:25
        scan_times = pd.date_range(f"{date_str} 09:25", f"{date_str} 15:25", freq="5min", tz=IST)
        day_trade_executed = False

        for current_time in scan_times:
            if day_trade_executed: break

            market_data = []
            sector_raw  = {}

            # Evaluate each stock at exactly 'current_time'
            for symbol, info in day_data.items():
                df = info["df"]
                sector = info["sector"]
                
                # Filter data up to the current simulated minute
                df_up_to_now = df[df["time"] <= current_time]
                
                if len(df_up_to_now) < 4: continue
                
                open_price = df_up_to_now.iloc[0]["open"]
                latest_close = df_up_to_now.iloc[-1]["close"]
                
                if open_price == 0: continue
                
                change_pct = ((latest_close - open_price) / open_price) * 100
                sector_raw.setdefault(sector, []).append(change_pct)
                market_data.append({"symbol": symbol, "sector": sector, "change": change_pct, "ltp": latest_close})

            if not market_data: continue

            # Apply Strategy Logic
            sector_avg = {s: sum(v)/len(v) for s, v in sector_raw.items()}
            
            signals = [
                s for s in market_data
                if s["sector"] in sector_avg
                and abs(s["change"]) >= 0.25
                and abs(sector_avg[s["sector"]]) >= 0.20
            ]

            if signals:
                signals.sort(key=lambda x: abs(x["change"]), reverse=True)
                top_signal = signals[0]
                
                # Execute Trade
                entry_time = current_time
                entry_price = top_signal["ltp"]
                direction = "BUY" if top_signal["change"] > 0 else "SHORT"
                
                # Calculate Exit Price (Assuming intraday EOD exit at 15:15)
                full_df = day_data[top_signal["symbol"]]["df"]
                exit_row = full_df[full_df["time"] <= pd.Timestamp(f"{date_str} 15:15", tz=IST)].iloc[-1]
                exit_price = exit_row["close"]
                
                # Calculate PnL
                if direction == "BUY":
                    pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                else:
                    pnl_pct = ((entry_price - exit_price) / entry_price) * 100

                print(f"[{entry_time.strftime('%H:%M')}] SIGNAL FIRED: {direction} {top_signal['symbol']} at {entry_price:.2f}")
                print(f"[{exit_row['time'].strftime('%H:%M')}] EXIT {top_signal['symbol']} at {exit_price:.2f} | PnL: {pnl_pct:.2f}%")

                trade_log.append({
                    "Date": date_str,
                    "Entry Time": entry_time.strftime('%H:%M'),
                    "Symbol": top_signal["symbol"],
                    "Direction": direction,
                    "Entry Price": entry_price,
                    "Exit Price": exit_price,
                    "PnL %": round(pnl_pct, 2)
                })
                
                day_trade_executed = True # Only 1 trade per day, as per original script

        if not day_trade_executed:
            print("No signal generated for this day.")

    # 3. Print Final Results
    print("\n=========================================")
    print("           BACKTEST RESULTS              ")
    print("=========================================")
    if trade_log:
        results_df = pd.DataFrame(trade_log)
        print(results_df.to_string(index=False))
        
        wins = len(results_df[results_df["PnL %"] > 0])
        losses = len(results_df[results_df["PnL %"] <= 0])
        total_pnl = results_df["PnL %"].sum()
        
        print("\n--- Summary ---")
        print(f"Total Trades : {len(trade_log)}")
        print(f"Wins         : {wins}")
        print(f"Losses       : {losses}")
        print(f"Win Rate     : {(wins/len(trade_log))*100:.2f}%")
        print(f"Total PnL %  : {total_pnl:.2f}%")
    else:
        print("No trades were executed during the backtest period.")

if __name__ == "__main__":
    run_backtest()
