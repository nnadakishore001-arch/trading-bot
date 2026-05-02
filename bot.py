import pandas as pd
import requests
from datetime import datetime, timedelta
from SmartApi import SmartConnect
import pyotp

# ===== TELEGRAM =====
TOKEN = "8691427620:AAF5vkJmHqETtm2TyhEd6CLdozCPsa57ATg"
CHAT_ID = "890425913"

def send(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": msg})

# ===== LOGIN =====
API_KEY = "VYFnGUA8"
CLIENT_ID = "M373866"
PASSWORD = "0917"
TOTP_SECRET = "3MLPA7DT7BA674CP73DHFDWJ2Q"

totp = pyotp.TOTP(TOTP_SECRET).now()
obj = SmartConnect(api_key=API_KEY)
obj.generateSession(CLIENT_ID, PASSWORD, totp)

send("🚀 Starting FULL F&O Backtest...")

# ===== FULL F&O STOCK LIST (SECTOR-WISE) =====
SECTORS = {

    "BANK": {
        "HDFCBANK": "1333",
        "ICICIBANK": "4963",
        "SBIN": "3045",
        "AXISBANK": "5900",
        "KOTAKBANK": "1922"
    },

    "IT": {
        "TCS": "11536",
        "INFY": "1594",
        "HCLTECH": "7229",
        "TECHM": "13538",
        "WIPRO": "3787"
    },

    "AUTO": {
        "TATAMOTORS": "3456",
        "MARUTI": "10999",
        "M&M": "2031",
        "BAJAJ-AUTO": "16669",
        "EICHERMOT": "910"
    },

    "FMCG": {
        "ITC": "1660",
        "HINDUNILVR": "1394",
        "NESTLEIND": "17963",
        "DABUR": "772"
    },

    "PHARMA": {
        "SUNPHARMA": "3351",
        "DRREDDY": "881",
        "CIPLA": "694",
        "DIVISLAB": "10940"
    },

    "METAL": {
        "TATASTEEL": "3499",
        "JSWSTEEL": "11723",
        "HINDALCO": "1363"
    },

    "ENERGY": {
        "RELIANCE": "2885",
        "ONGC": "2475",
        "NTPC": "11630",
        "POWERGRID": "14977"
    },

    "NBFC": {
        "BAJFINANCE": "317",
        "BAJAJFINSV": "16675",
        "SBICARD": "17971"
    },

    "INFRA": {
        "L&T": "11483",
        "ADANIPORTS": "15083"
    }
}

# ===== DATE RANGE =====
end_date = datetime.now()
start_date = end_date - timedelta(days=150)

def get_data(token):
    params = {
        "exchange": "NSE",
        "symboltoken": token,
        "interval": "FIVE_MINUTE",
        "fromdate": start_date.strftime("%Y-%m-%d 09:15"),
        "todate": end_date.strftime("%Y-%m-%d 15:30")
    }

    data = obj.getCandleData(params)

    if not data['data']:
        return pd.DataFrame()

    df = pd.DataFrame(data['data'], columns=["time","open","high","low","close","volume"])
    df['time'] = pd.to_datetime(df['time'])
    df['date'] = df['time'].dt.date
    return df

# ===== LOAD DATA =====
market_data = {}

for sector, stocks in SECTORS.items():
    for sym, token in stocks.items():
        try:
            df = get_data(token)
            if not df.empty:
                market_data[sym] = {"sector": sector, "df": df}
        except:
            pass

send(f"✅ Loaded {len(market_data)} F&O Stocks")

# ===== BACKTEST =====
results = []
daily_picks = {}

all_dates = sorted(set(
    d for v in market_data.values()
    for d in v["df"]["date"]
))

for day in all_dates:

    sector_strength = {}
    stock_moves = []

    for sym, data in market_data.items():

        df = data["df"]
        sector = data["sector"]

        day_df = df[df['date'] == day]

        if day_df.empty:
            continue

        open_915 = day_df[day_df['time'].dt.strftime("%H:%M") == "09:15"]
        candle_930 = day_df[day_df['time'].dt.strftime("%H:%M") == "09:30"]

        if open_915.empty or candle_930.empty:
            continue

        open_price = open_915.iloc[0]['open']
        price_930 = candle_930.iloc[0]['close']

        change = ((price_930 - open_price) / open_price) * 100

        stock_moves.append((sym, sector, change, price_930))

        sector_strength.setdefault(sector, []).append(change)

    sector_strength = {k: sum(v)/len(v) for k, v in sector_strength.items() if v}
    strong_sectors = {k: v for k, v in sector_strength.items() if v > 0.5}

    filtered = [
        (sym, sec, chg, price)
        for sym, sec, chg, price in stock_moves
        if sec in strong_sectors and chg > 0.8
    ]

    if not filtered:
        continue

    filtered.sort(key=lambda x: x[2], reverse=True)
    top_stocks = filtered[:2]

    daily_picks[day] = top_stocks

    for sym, sec, chg, entry in top_stocks:

        df = market_data[sym]["df"]
        day_df = df[df['date'] == day]

        exit_candle = day_df[day_df['time'].dt.strftime("%H:%M") == "15:15"]

        if exit_candle.empty:
            continue

        exit_price = exit_candle.iloc[0]['close']
        pnl = ((exit_price - entry) / entry) * 100

        results.append({
            "date": day,
            "stock": sym,
            "sector": sec,
            "pnl%": pnl
        })

df_results = pd.DataFrame(results)

# ===== TELEGRAM OUTPUT =====
summary = f"""
📊 BACKTEST RESULT (5 Months)

Total Trades: {len(df_results)}
Win Rate: {round((df_results['pnl%'] > 0).mean()*100, 2)}%
Avg Return: {round(df_results['pnl%'].mean(), 2)}%
"""
send(summary)

top_trades = df_results.sort_values(by="pnl%", ascending=False).head(5)

msg = "🔥 Top Trades\n\n"
for _, row in top_trades.iterrows():
    msg += f"{row['stock']} | {row['date']} | {round(row['pnl%'],2)}%\n"

send(msg)
