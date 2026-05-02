import pandas as pd
from datetime import datetime, timedelta
from SmartApi import SmartConnect
import pyotp

# ===== LOGIN =====
API_KEY = "VYFnGUA8"
CLIENT_ID = "M373866"
PASSWORD = "0917"
TOTP_SECRET = "3MLPA7DT7BA674CP73DHFDWJ2Q"

totp = pyotp.TOTP(TOTP_SECRET).now()
obj = SmartConnect(api_key=API_KEY)
obj.generateSession(CLIENT_ID, PASSWORD, totp)

print("Login Success")

# ===== F&O STOCKS (EXPANDED) =====
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
        "BAJAJ-AUTO": "16669"
    },
    "FMCG": {
        "ITC": "1660",
        "HINDUNILVR": "1394",
        "NESTLEIND": "17963"
    },
    "ENERGY": {
        "RELIANCE": "2885",
        "ONGC": "2475"
    }
}

# ===== DATE RANGE =====
end_date = datetime.now()
start_date = end_date - timedelta(days=90)

# ===== FETCH DATA =====
def get_data(token):
    params = {
        "exchange": "NSE",
        "symboltoken": token,
        "interval": "FIVE_MINUTE",
        "fromdate": start_date.strftime("%Y-%m-%d 09:15"),
        "todate": end_date.strftime("%Y-%m-%d 15:30")
    }

    data = obj.getCandleData(params)
    df = pd.DataFrame(data['data'], columns=["time","open","high","low","close","volume"])
    df['time'] = pd.to_datetime(df['time'])
    return df

# ===== LOAD ALL DATA =====
market_data = {}

for sector, stocks in SECTORS.items():
    for sym, token in stocks.items():
        try:
            market_data[sym] = {
                "sector": sector,
                "df": get_data(token)
            }
        except:
            pass

# ===== BACKTEST =====
results = []

# Get all dates
all_dates = sorted(set(
    d for v in market_data.values()
    for d in v["df"]["time"].dt.date
))

for day in all_dates:

    sector_strength = {}
    stock_moves = []

    for sym, data in market_data.items():
        df = data["df"]
        sector = data["sector"]

        day_df = df[df['time'].dt.date == day]

        if day_df.empty:
            continue

        # 9:15 open
        open_candle = day_df[day_df['time'].dt.strftime("%H:%M") == "09:15"]
        candle_930 = day_df[day_df['time'].dt.strftime("%H:%M") == "09:30"]

        if open_candle.empty or candle_930.empty:
            continue

        open_price = open_candle.iloc[0]['open']
        price_930 = candle_930.iloc[0]['close']

        change = ((price_930 - open_price) / open_price) * 100

        stock_moves.append((sym, sector, change, price_930))

        if sector not in sector_strength:
            sector_strength[sector] = []

        sector_strength[sector].append(change)

    # ===== CALCULATE SECTOR AVG =====
    for sec in sector_strength:
        sector_strength[sec] = sum(sector_strength[sec]) / len(sector_strength[sec])

    # ===== FILTER STRONG SECTORS =====
    strong_sectors = {k: v for k, v in sector_strength.items() if v > 0.5}

    if not strong_sectors:
        continue

    # ===== FILTER STOCKS =====
    filtered = [
        (sym, sec, chg, price)
        for sym, sec, chg, price in stock_moves
        if sec in strong_sectors and chg > 0.8
    ]

    # ===== PICK TOP 2 =====
    filtered.sort(key=lambda x: x[2], reverse=True)
    selected = filtered[:2]

    # ===== EXIT AT 3:15 =====
    for sym, sec, chg, entry in selected:

        df = market_data[sym]["df"]
        day_df = df[df['time'].dt.date == day]

        exit_candle = day_df[day_df['time'].dt.strftime("%H:%M") == "15:15"]

        if exit_candle.empty:
            continue

        exit_price = exit_candle.iloc[0]['close']

        pnl = ((exit_price - entry) / entry) * 100

        results.append({
            "date": day,
            "stock": sym,
            "sector": sec,
            "entry": entry,
            "exit": exit_price,
            "pnl%": pnl
        })

# ===== RESULTS =====
df_results = pd.DataFrame(results)

print("\n===== BACKTEST RESULT =====")
print("Total Trades:", len(df_results))
print("Win Rate:", (df_results['pnl%'] > 0).mean() * 100)
print("Average Return:", df_results['pnl%'].mean())

print("\nSample Trades:")
print(df_results.head())
