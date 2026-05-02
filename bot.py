import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from SmartApi import SmartConnect
import pyotp
import requests
import time

# ===== CONFIG =====
API_KEY = "VYFnGUA8"
CLIENT_ID = "M373866"
PASSWORD = "0917"
TOTP_SECRET = "3MLPA7DT7BA674CP73DHFDWJ2Q"
TOKEN_TG = "8691427620:AAF5vkJmHqETtm2TyhEd6CLdozCPsa57ATg"
CHAT_ID = "890425913"

# ===== TELEGRAM =====
def send(msg):
    try:
        url = f"https://api.telegram.org/bot{TOKEN_TG}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
    except:
        pass

# ===== LOGIN FUNCTION =====
def login_service():
    obj = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    data = obj.generateSession(CLIENT_ID, PASSWORD, totp)
    if data['status']:
        obj.setAccessToken(data['data']['jwtToken'])
        return obj
    else:
        raise Exception(f"Login Failed: {data.get('message')}")

# ================= INDICATORS =================
def calc_rsi(closes, p=14):
    if len(closes) < p+1: return 50
    delta = np.diff(closes)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    avg_g = np.mean(gains[:p])
    avg_l = np.mean(losses[:p])
    for i in range(p, len(gains)):
        avg_g = (avg_g * (p - 1) + gains[i]) / p
        avg_l = (avg_l * (p - 1) + losses[i]) / p
    return 100 - 100 / (1 + avg_g / avg_l) if avg_l != 0 else 100

def calc_macd(closes):
    if len(closes) < 27: return {"bull": False, "bear": False}
    s = pd.Series(closes)
    macd_line = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    signal = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal
    return {"bull": hist.iloc[-1] > 0 and hist.iloc[-2] <= 0, 
            "bear": hist.iloc[-1] < 0 and hist.iloc[-2] >= 0}

# ================= BACKTEST ENGINE =================
def run_backtest(start_dt, end_dt):
    try:
        client = login_service()
        print(f"🚀 Backtest session started for {start_dt.date()} to {end_dt.date()}")
        
        # Stock to test: SBIN
        token_to_test = "3045" 
        
        params = {
            "exchange": "NSE",
            "symboltoken": token_to_test,
            "interval": "FIVE_MINUTE",
            "fromdate": start_dt.strftime("%Y-%m-%d 09:15"),
            "todate": end_dt.strftime("%Y-%m-%d 15:30")
        }
        
        res = client.getCandleData(params)
        if not res.get("data"):
            print("❌ No data found.")
            return

        df = pd.DataFrame(res["data"], columns=["time","open","high","low","close","volume"])
        df['time'] = pd.to_datetime(df['time'])
        df['date'] = df['time'].dt.date
        
        results = []
        days = df['date'].unique()
        
        for day in days:
            day_df = df[df['date'] == day].copy().sort_values("time")
            if len(day_df) < 20: continue # Ensure enough data for indicators
            
            # 9:30 AM Entry Point (5th candle of 5-min interval)
            entry_candle = day_df.iloc[4] 
            closes_for_rsi = day_df.iloc[:5]['close'].values
            closes_for_macd = day_df.iloc[:20]['close'].values # Increased window for MACD
            
            # Logic Scoring
            score = 0
            rsi_val = calc_rsi(closes_for_rsi)
            macd = calc_macd(closes_for_macd)
            
            if 52 < rsi_val < 74: score += 2
            if macd["bull"]: score += 2
            
            if score >= 4:
                entry_price = entry_candle['close']
                exit_price = day_df.iloc[-1]['close'] 
                pnl = ((exit_price - entry_price) / entry_price) * 100
                results.append(pnl)
                print(f"✅ {day}: PnL {pnl:.2f}% (RSI: {round(rsi_val,1)})")

        # Stats
        if results:
            win_rate = len([x for x in results if x > 0]) / len(results) * 100
            summary = (f"📊 BACKTEST RESULT\nTrades: {len(results)}\n"
                       f"Win Rate: {win_rate:.2f}%\nAvg Return: {np.mean(results):.2f}%")
            print(summary)
            send(summary)
        else:
            print("No trades met criteria.")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    # Define 3 month window
    backtest_start = datetime(2026, 2, 1)
    backtest_end = datetime(2026, 4, 30)
    run_backtest(backtest_start, backtest_end)
