import os
import pandas as pd
from datetime import datetime
from SmartApi import SmartConnect
import pyotp

# ==============================
# 🔐 ENV VARIABLES (SECURE)
# ==============================
API_KEY     = os.getenv("API_KEY")
CLIENT_ID   = os.getenv("CLIENT_ID")
PASSWORD    = os.getenv("PASSWORD")
TOTP_SECRET = os.getenv("TOTP_SECRET")

# (Optional - not used in backtest but kept for consistency)
TG_TOKEN    = os.getenv("TG_TOKEN")
CHAT_ID     = os.getenv("CHAT_ID")

SYMBOL = "RELIANCE"
TOKEN  = "2885"

# ==============================
# VALIDATION (IMPORTANT)
# ==============================
if not all([API_KEY, CLIENT_ID, PASSWORD, TOTP_SECRET]):
    raise ValueError("❌ Missing environment variables. Please set API_KEY, CLIENT_ID, PASSWORD, TOTP_SECRET")

# ==============================
# LOGIN
# ==============================
def login():
    obj = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    data = obj.generateSession(CLIENT_ID, PASSWORD, totp)

    if not data or data.get("status") is False:
        raise Exception(f"Login failed: {data}")

    obj.getfeedToken()
    return obj

api = login()

# ==============================
# FETCH DATA
# ==============================
def get_data():
    to_date   = datetime.now().strftime("%Y-%m-%d %H:%M")
    from_date = (datetime.now() - pd.Timedelta(days=30)).strftime("%Y-%m-%d %H:%M")

    data = api.getCandleData({
        "exchange": "NSE",
        "symboltoken": TOKEN,
        "interval": "FIVE_MINUTE",
        "fromdate": from_date,
        "todate": to_date
    })

    df = pd.DataFrame(data["data"],
        columns=["time","open","high","low","close","volume"]
    )

    df["time"] = pd.to_datetime(df["time"])
    return df

# ==============================
# INDICATORS
# ==============================
def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1/period).mean()
    avg_loss = loss.ewm(alpha=1/period).mean()

    rs = avg_gain / avg_loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))

def vol_ratio(df):
    avg = df["volume"].iloc[:-1].mean()
    return df["volume"].iloc[-1] / avg if avg > 0 else 0

def calc_atr(df, period=14):
    high = df["high"]
    low  = df["low"]
    close = df["close"]

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)

    return tr.rolling(period).mean().iloc[-1]

# ==============================
# STRATEGY LOGIC
# ==============================
def breakout_signal(df):
    if len(df) < 5:
        return None

    prev_high = df["high"].iloc[-2]
    prev_low  = df["low"].iloc[-2]
    close     = df["close"].iloc[-1]

    if close > prev_high:
        return "BUY"
    elif close < prev_low:
        return "SHORT"
    return None

def strong_candle(df):
    c = df.iloc[-1]
    body = abs(c["close"] - c["open"])
    rng  = c["high"] - c["low"]

    if rng == 0:
        return False

    return (body / rng) > 0.6

def rsi_momentum(rsi, direction):
    if direction == "BUY":
        return 55 < rsi < 75
    else:
        return 25 < rsi < 45

def get_entry(df, direction):
    return df["high"].iloc[-2] if direction == "BUY" else df["low"].iloc[-2]

def build_sl_target(entry, direction, atr):
    if pd.isna(atr) or atr == 0:
        sl_pct = 0.3
        tgt_pct = 0.6
    else:
        sl_pct = (1.2 * atr / entry) * 100
        tgt_pct = (2.5 * atr / entry) * 100

    if direction == "BUY":
        sl = entry * (1 - sl_pct/100)
        target = entry * (1 + tgt_pct/100)
    else:
        sl = entry * (1 + sl_pct/100)
        target = entry * (1 - tgt_pct/100)

    return sl, target

# ==============================
# BACKTEST
# ==============================
def backtest():
    df = get_data()

    trades = []
    balance = 10000

    for i in range(30, len(df)-1):
        sub_df = df.iloc[:i+1]

        signal = breakout_signal(sub_df)
        if not signal:
            continue

        rsi = calc_rsi(sub_df["close"]).iloc[-1]
        vol = vol_ratio(sub_df)

        if vol < 2.5:
            continue

        if not rsi_momentum(rsi, signal):
            continue

        if not strong_candle(sub_df):
            continue

        entry = get_entry(sub_df, signal)
        atr   = calc_atr(sub_df)

        sl, target = build_sl_target(entry, signal, atr)

        next_candle = df.iloc[i+1]

        result = None
        pnl = 0

        if signal == "BUY":
            if next_candle["low"] <= sl:
                result = "LOSS"
                pnl = -1
            elif next_candle["high"] >= target:
                result = "WIN"
                pnl = 2.5
        else:
            if next_candle["high"] >= sl:
                result = "LOSS"
                pnl = -1
            elif next_candle["low"] <= target:
                result = "WIN"
                pnl = 2.5

        if result:
            balance *= (1 + pnl/100)

            trades.append({
                "time": df.iloc[i]["time"],
                "signal": signal,
                "entry": entry,
                "result": result,
                "pnl%": pnl,
                "balance": balance
            })

    total = len(trades)
    wins  = len([t for t in trades if t["result"] == "WIN"])

    win_rate = (wins / total * 100) if total > 0 else 0

    print("\n========== BACKTEST RESULT ==========")
    print("Total Trades :", total)
    print("Win Rate     :", round(win_rate, 2), "%")
    print("Final Balance:", round(balance, 2))
    print("====================================")

    return pd.DataFrame(trades)

# ==============================
# RUN
# ==============================
if __name__ == "__main__":
    df = backtest()
    df.to_excel("backtest_results.xlsx", index=False)
    print("✅ Saved: backtest_results.xlsx")
