"""
╔══════════════════════════════════════════════════════════════╗
║           BACKTEST BOT — 1 MONTH HISTORICAL REPLAY          ║
║  Same strategy as production. Replays day-by-day, sends     ║
║  real Telegram alerts with [BACKTEST] prefix so you can     ║
║  track accuracy live without risking real capital.          ║
╚══════════════════════════════════════════════════════════════╝

HOW IT WORKS:
  • Iterates over every trading day in the past 30 days
  • For each day, replays 5-min candles from 09:25 → 15:15
    in configurable time-steps (REPLAY_STEP_MIN)
  • At each step, runs the EXACT same strategy filters
  • Sends Telegram alert when signal fires (with [BACKTEST] tag)
  • After each day, checks SL/Target hit using remaining candles
  • Sends end-of-day result + cumulative P&L to Telegram
  • Saves everything to backtest_results.db for analysis

REQUIREMENTS:
  pip install pandas pyotp requests smartapi-python pytz
"""

import os
import sqlite3
import pandas as pd
import pyotp
import requests
from datetime import datetime, timedelta, time as dtime
from SmartApi import SmartConnect
import pytz
import time

# ─────────────────────────────────────────────
# ENV VARIABLES  (same as production)
# ─────────────────────────────────────────────
API_KEY     = os.getenv("API_KEY")
CLIENT_ID   = os.getenv("CLIENT_ID")
PASSWORD    = os.getenv("PASSWORD")
TOTP_SECRET = os.getenv("TOTP_SECRET")
TG_TOKEN    = os.getenv("TG_TOKEN")
CHAT_ID     = os.getenv("CHAT_ID")

# ─────────────────────────────────────────────
# BACKTEST CONFIG
# ─────────────────────────────────────────────
BACKTEST_DAYS    = 30          # how many calendar days to look back
REPLAY_STEP_MIN  = 15          # simulate scanning every N minutes
CANDLE_DELAY     = 1.1         # keep same rate-limit delay
MAX_RETRIES      = 3
RETRY_BACKOFF    = 2.0
DB_PATH          = "backtest_results.db"

# ─────────────────────────────────────────────
# STRATEGY SETTINGS  (identical to production)
# ─────────────────────────────────────────────
STOCK_THRESHOLD    = 0.30
SECTOR_THRESHOLD   = 0.25
EMA_FAST           = 9
EMA_SLOW           = 21
VOLUME_SPIKE       = 2.5
RSI_PERIOD         = 14
RSI_BUY_MIN        = 55
RSI_BUY_MAX        = 75
RSI_SHORT_MIN      = 25
RSI_SHORT_MAX      = 45
MIN_CANDLES_NEEDED = 25
SL_MULT            = 1.5       # ATR multiplier for stop-loss
TGT_MULT           = 2.5       # ATR multiplier for target
SL_PCT_FALLBACK    = 0.30
TGT_PCT_FALLBACK   = 0.60

# ─────────────────────────────────────────────
# GLOBALS
# ─────────────────────────────────────────────
API_OBJECT = None
IST        = pytz.timezone("Asia/Kolkata")

# ─────────────────────────────────────────────
# NSE HOLIDAYS
# ─────────────────────────────────────────────
NSE_HOLIDAYS = {
    datetime(2025, 1, 26).date(), datetime(2025, 2, 26).date(),
    datetime(2025, 3, 14).date(), datetime(2025, 3, 31).date(),
    datetime(2025, 4, 10).date(), datetime(2025, 4, 14).date(),
    datetime(2025, 4, 18).date(), datetime(2025, 5, 1).date(),
    datetime(2025, 8, 15).date(), datetime(2025, 8, 27).date(),
    datetime(2025, 10, 2).date(), datetime(2025, 10, 21).date(),
    datetime(2025, 10, 22).date(), datetime(2025, 11, 5).date(),
    datetime(2025, 12, 25).date(),
    datetime(2026, 1, 26).date(), datetime(2026, 3, 20).date(),
    datetime(2026, 4, 2).date(),  datetime(2026, 4, 3).date(),
    datetime(2026, 4, 14).date(), datetime(2026, 5, 1).date(),
    datetime(2026, 8, 15).date(), datetime(2026, 8, 26).date(),
    datetime(2026, 10, 2).date(), datetime(2026, 12, 25).date(),
}

# ─────────────────────────────────────────────
# STRIKE INTERVALS
# ─────────────────────────────────────────────
STRIKE_INTERVALS = {
    "HDFCBANK": 50,  "ICICIBANK": 50,  "SBIN": 50,    "AXISBANK": 50,
    "KOTAKBANK": 50, "TCS": 50,        "INFY": 50,    "HCLTECH": 50,
    "TECHM": 50,     "WIPRO": 10,      "TATAMOTORS": 50, "MARUTI": 100,
    "M&M": 50,       "BAJAJ-AUTO": 50, "SUNPHARMA": 50,  "CIPLA": 50,
    "DRREDDY": 50,   "DIVISLAB": 100,  "ITC": 5,         "HINDUNILVR": 50,
    "NESTLEIND": 50, "TATASTEEL": 10,  "JSWSTEEL": 50,   "HINDALCO": 10,
    "RELIANCE": 50,  "ONGC": 10,       "NTPC": 10,       "POWERGRID": 10,
    "BAJFINANCE": 50,"BAJAJFINSV": 50, "LT": 100,        "ADANIPORTS": 50,
}

# ─────────────────────────────────────────────
# SECTORS
# ─────────────────────────────────────────────
SECTORS = {
    "BANK":   {"HDFCBANK":"1333","ICICIBANK":"4963","SBIN":"3045","AXISBANK":"5900","KOTAKBANK":"1922"},
    "IT":     {"TCS":"11536","INFY":"1594","HCLTECH":"7229","TECHM":"13538","WIPRO":"3787"},
    "AUTO":   {"TATAMOTORS":"3456","MARUTI":"10999","M&M":"2031","BAJAJ-AUTO":"16669"},
    "PHARMA": {"SUNPHARMA":"3351","CIPLA":"694","DRREDDY":"881","DIVISLAB":"10940"},
    "FMCG":   {"ITC":"1660","HINDUNILVR":"1394","NESTLEIND":"17963"},
    "METAL":  {"TATASTEEL":"3499","JSWSTEEL":"11723","HINDALCO":"1363"},
    "ENERGY": {"RELIANCE":"2885","ONGC":"2475","NTPC":"11630","POWERGRID":"14977"},
    "NBFC":   {"BAJFINANCE":"317","BAJAJFINSV":"16675"},
    "INFRA":  {"LT":"11483","ADANIPORTS":"15083"},
}

# ══════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bt_trades (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date   TEXT,
            signal_time  TEXT,
            symbol       TEXT,
            sector       TEXT,
            direction    TEXT,
            entry_price  REAL,
            recommended  TEXT,
            strike       TEXT,
            sl_price     REAL,
            target_price REAL,
            sl_pct       REAL,
            tgt_pct      REAL,
            rsi          REAL,
            vol_ratio    REAL,
            atr          REAL,
            result       TEXT,
            exit_price   REAL,
            exit_time    TEXT,
            pnl_pct      REAL
        )
    """)
    conn.commit()
    conn.close()

def save_trade(t):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO bt_trades
        (trade_date,signal_time,symbol,sector,direction,entry_price,
         recommended,strike,sl_price,target_price,sl_pct,tgt_pct,
         rsi,vol_ratio,atr,result,exit_price,exit_time,pnl_pct)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        t["date"], t["signal_time"], t["symbol"], t["sector"],
        t["direction"], t["entry"],
        t["recommended"], t["strike"],
        t["sl"], t["target"], t["sl_pct"], t["tgt_pct"],
        t["rsi"], t["vol_ratio"], t["atr"] or 0,
        t["result"], t["exit_price"], t["exit_time"], t["pnl_pct"],
    ))
    conn.commit()
    conn.close()

def get_summary():
    conn  = sqlite3.connect(DB_PATH)
    rows  = conn.execute(
        "SELECT result, COUNT(*), AVG(pnl_pct) FROM bt_trades GROUP BY result"
    ).fetchall()
    conn.close()
    stats = {}
    for result, cnt, avg_pnl in rows:
        stats[result] = {"count": cnt, "avg_pnl": round(avg_pnl or 0, 2)}
    return stats

# ══════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════
def send(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"[telegram] {e}")

# ══════════════════════════════════════════════
# LOGIN
# ══════════════════════════════════════════════
def login():
    global API_OBJECT
    try:
        obj  = SmartConnect(api_key=API_KEY)
        totp = pyotp.TOTP(TOTP_SECRET).now()
        data = obj.generateSession(CLIENT_ID, PASSWORD, totp)
        if not data or data.get("status") is False:
            print(f"[login] Failed: {data}")
            return False
        rf = data["data"]["refreshToken"]
        obj.getfeedToken()
        obj.getProfile(rf)
        obj.generateToken(rf)
        API_OBJECT = obj
        print("[login] OK")
        return True
    except Exception as e:
        print(f"[login] Exception: {e}")
        return False

# ══════════════════════════════════════════════
# CANDLE FETCH — historical range
# ══════════════════════════════════════════════
def fetch_day_candles(token, symbol, trade_date):
    """
    Fetches ALL 5-min candles for a specific historical date.
    Returns DataFrame or empty DataFrame on failure.
    """
    global API_OBJECT
    from_str = f"{trade_date} 09:15"
    to_str   = f"{trade_date} 15:30"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = API_OBJECT.getCandleData({
                "exchange": "NSE", "symboltoken": token,
                "interval": "FIVE_MINUTE",
                "fromdate": from_str, "todate": to_str,
            })
            if resp and resp.get("errorCode") == "AG8001":
                print("[session] Expired — re-logging")
                if not login():
                    return pd.DataFrame()
                continue
            if resp and resp.get("data"):
                df = pd.DataFrame(
                    resp["data"],
                    columns=["time","open","high","low","close","volume"]
                )
                df["time"] = pd.to_datetime(df["time"])
                return df
            return pd.DataFrame()
        except Exception as e:
            msg = str(e).lower()
            if "exceeding access rate" in msg or "access denied" in msg:
                wait = RETRY_BACKOFF * attempt
                print(f"[rate_limit] {symbol} attempt {attempt}, wait {wait}s")
                time.sleep(wait)
            else:
                print(f"[fetch] {symbol} {trade_date}: {e}")
                return pd.DataFrame()
    return pd.DataFrame()

# ══════════════════════════════════════════════
# INDICATORS  (identical to production)
# ══════════════════════════════════════════════
def compute_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def compute_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    ag    = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    al    = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs    = ag / al.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))

def has_ema_crossover(df):
    if len(df) < EMA_SLOW + 2:
        return None
    ef = compute_ema(df["close"], EMA_FAST)
    es = compute_ema(df["close"], EMA_SLOW)
    if ef.iloc[-2] <= es.iloc[-2] and ef.iloc[-1] > es.iloc[-1]:
        return "BUY"
    if ef.iloc[-2] >= es.iloc[-2] and ef.iloc[-1] < es.iloc[-1]:
        return "SHORT"
    return None

def get_volume_ratio(df):
    if len(df) < 3:
        return 0
    avg = df["volume"].iloc[:-1].mean()
    return df["volume"].iloc[-1] / avg if avg > 0 else 0

def last_candle_reversal(df):
    if df.empty:
        return False
    c   = df.iloc[-1]
    rng = c["high"] - c["low"]
    return (abs(c["close"] - c["open"]) / rng < 0.30) if rng > 0 else True

def higher_highs_lower_lows(df, direction, lookback=5):
    if len(df) < lookback + 1:
        return True
    if direction == "BUY":
        h = df["high"].iloc[-lookback:].values
        return all(h[i] <= h[i+1] for i in range(len(h)-1))
    l = df["low"].iloc[-lookback:].values
    return all(l[i] >= l[i+1] for i in range(len(l)-1))

def no_big_gap(df, gap_pct=0.50):
    if df.empty:
        return True
    c   = df.iloc[-1]
    rng = abs(c["close"] - c["open"]) / c["open"] * 100 if c["open"] > 0 else 0
    return rng <= gap_pct

def get_atr(df, period=14):
    if len(df) < period + 1:
        return None
    h, l, c = df["high"], df["low"], df["close"]
    p = c.shift(1)
    tr = pd.concat([h - l, (h - p).abs(), (l - p).abs()], axis=1).max(axis=1)
    val = tr.rolling(period).mean().iloc[-1]
    return float(val) if pd.notna(val) else None

# ══════════════════════════════════════════════
# STRIKE RECOMMENDATION  (identical to production)
# ══════════════════════════════════════════════
def recommend_strike(symbol, ltp, direction, atr, rsi, vol_ratio):
    interval = STRIKE_INTERVALS.get(symbol, 50)
    opt_type = "CE" if direction == "BUY" else "PE"
    atm      = round(ltp / interval) * interval
    itm      = (atm - interval) if direction == "BUY" else (atm + interval)

    atr_pct      = (atr / ltp * 100) if atr and ltp > 0 else 999
    strong_vol   = vol_ratio >= 3.5
    rsi_momentum = (60 <= rsi <= 72) if direction == "BUY" else (28 <= rsi <= 40)
    low_atr      = atr_pct <= 1.0
    use_itm      = strong_vol and rsi_momentum and low_atr

    recommended = "ITM" if use_itm else "ATM"
    strike_val  = itm  if use_itm else atm
    reasons     = []
    if not strong_vol:   reasons.append(f"Vol {round(vol_ratio,1)}× < 3.5×")
    if not rsi_momentum: reasons.append(f"RSI {round(rsi,1)} not peak")
    if not low_atr:      reasons.append(f"ATR {round(atr_pct,2)}% > 1%")
    reason = "; ".join(reasons) if reasons else "All ITM criteria met"

    return {
        "recommended":  recommended,
        "strike_label": f"{strike_val} {opt_type}",
        "atm_strike":   f"{atm} {opt_type}",
        "itm_strike":   f"{itm} {opt_type}",
        "reason":       reason,
    }

# ══════════════════════════════════════════════
# TRADE PARAM BUILDER
# ══════════════════════════════════════════════
def build_params(ltp, direction, atr):
    if atr and atr > 0:
        sl_d  = round(SL_MULT  * atr, 2)
        tgt_d = round(TGT_MULT * atr, 2)
        sl     = round(ltp - sl_d,  2) if direction == "BUY" else round(ltp + sl_d,  2)
        target = round(ltp + tgt_d, 2) if direction == "BUY" else round(ltp - tgt_d, 2)
        sl_pct  = round(sl_d  / ltp * 100, 2)
        tgt_pct = round(tgt_d / ltp * 100, 2)
    else:
        sl      = round(ltp * (1 - SL_PCT_FALLBACK/100),  2) if direction == "BUY" else round(ltp * (1 + SL_PCT_FALLBACK/100),  2)
        target  = round(ltp * (1 + TGT_PCT_FALLBACK/100), 2) if direction == "BUY" else round(ltp * (1 - TGT_PCT_FALLBACK/100), 2)
        sl_pct, tgt_pct = SL_PCT_FALLBACK, TGT_PCT_FALLBACK
    return sl, target, sl_pct, tgt_pct

# ══════════════════════════════════════════════
# SIMULATE ONE TIME-SLICE
# Checks all strategy filters on candles UP TO `upto_idx`
# ══════════════════════════════════════════════
def check_signal(df_slice, symbol, sector, open_price):
    """
    Runs all 8 strategy filters on a slice of candles.
    Returns signal dict or None.
    """
    if len(df_slice) < MIN_CANDLES_NEEDED:
        return None

    if open_price == 0:
        return None

    close_p    = df_slice.iloc[-1]["close"]
    change     = ((close_p - open_price) / open_price) * 100
    rsi_series = compute_rsi(df_slice["close"], RSI_PERIOD)
    rsi        = rsi_series.iloc[-1]
    crossover  = has_ema_crossover(df_slice)
    vol_ratio  = get_volume_ratio(df_slice)
    atr        = get_atr(df_slice)

    if crossover is None:                                          return None
    if abs(change) < STOCK_THRESHOLD:                             return None
    if vol_ratio < VOLUME_SPIKE:                                  return None
    if crossover == "BUY"   and not (RSI_BUY_MIN   <= rsi <= RSI_BUY_MAX):   return None
    if crossover == "SHORT" and not (RSI_SHORT_MIN  <= rsi <= RSI_SHORT_MAX): return None
    if last_candle_reversal(df_slice):                            return None
    if not higher_highs_lower_lows(df_slice, crossover):         return None
    if not no_big_gap(df_slice):                                  return None
    if crossover == "BUY"   and change <= 0:                     return None
    if crossover == "SHORT" and change >= 0:                     return None

    sl, target, sl_pct, tgt_pct = build_params(close_p, crossover, atr)
    strike_rec = recommend_strike(symbol, close_p, crossover, atr, rsi, vol_ratio)

    return {
        "symbol":    symbol,
        "sector":    sector,
        "direction": crossover,
        "ltp":       close_p,
        "change":    change,
        "rsi":       rsi,
        "vol_ratio": vol_ratio,
        "atr":       atr,
        "sl":        sl,
        "target":    target,
        "sl_pct":    sl_pct,
        "tgt_pct":   tgt_pct,
        "strike_rec":strike_rec,
    }

# ══════════════════════════════════════════════
# SIMULATE EXIT — scan remaining candles for SL/Target hit
# ══════════════════════════════════════════════
def simulate_exit(df_full, signal_idx, direction, sl, target):
    """
    After signal fires at candle `signal_idx`, walk forward through
    remaining candles and check if SL or Target is hit first.
    Returns (result, exit_price, exit_time).
    """
    remaining = df_full.iloc[signal_idx + 1:]
    for _, row in remaining.iterrows():
        hi, lo = row["high"], row["low"]
        ts = str(row["time"])
        if direction == "BUY":
            if lo <= sl:
                return "LOSS", sl, ts
            if hi >= target:
                return "WIN",  target, ts
        else:
            if hi >= sl:
                return "LOSS", sl, ts
            if lo <= target:
                return "WIN",  target, ts
    # Neither hit → use last close as exit
    last = df_full.iloc[-1]
    exit_p  = last["close"]
    exit_ts = str(last["time"])
    entry   = signal_idx  # just for context
    ltp     = df_full.iloc[signal_idx]["close"]
    pnl     = ((exit_p - ltp) / ltp) * 100 if direction == "BUY" else ((ltp - exit_p) / ltp) * 100
    result  = "WIN" if pnl > 0 else "LOSS"
    return result, exit_p, exit_ts

# ══════════════════════════════════════════════
# ALERT FORMATTERS
# ══════════════════════════════════════════════
def fmt_signal(signal, trade_date, signal_time):
    rec = signal["strike_rec"]
    d   = signal["direction"]
    rr  = round(signal["tgt_pct"] / signal["sl_pct"], 1) if signal["sl_pct"] else "N/A"
    if rec["recommended"] == "ITM":
        strike_block = (
            f"🎯 <b>Recommended: ITM</b>\n"
            f"  Strike  : {rec['itm_strike']}\n"
            f"  ATM alt : {rec['atm_strike']}\n"
            f"  Reason  : {rec['reason']}"
        )
    else:
        strike_block = (
            f"🎯 <b>Recommended: ATM</b>\n"
            f"  Strike  : {rec['atm_strike']}\n"
            f"  ITM alt : {rec['itm_strike']} (avoid)\n"
            f"  Reason  : {rec['reason']}"
        )
    return (
        f"🔁 <b>[BACKTEST] TRADE ALERT</b>\n"
        f"📅 {trade_date}  ⏰ {signal_time}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Direction : {'🟢 BUY (CE)' if d == 'BUY' else '🔴 SHORT (PE)'}\n"
        f"Stock     : <b>{signal['symbol']}</b> ({signal['sector']})\n"
        f"LTP       : ₹{round(signal['ltp'], 2)}\n"
        f"Day Move  : {round(signal['change'], 2)}%\n"
        f"\n{strike_block}\n\n"
        f"<b>Levels</b>\n"
        f"Entry  : ₹{round(signal['ltp'], 2)}\n"
        f"SL     : ₹{signal['sl']} (−{signal['sl_pct']}%)\n"
        f"Target : ₹{signal['target']} (+{signal['tgt_pct']}%)\n"
        f"ATR 14 : ₹{round(signal['atr'], 2) if signal['atr'] else 'N/A'}\n"
        f"R:R    : 1 : {rr}\n\n"
        f"<b>Signal Quality</b>\n"
        f"RSI    : {round(signal['rsi'], 1)}\n"
        f"Volume : {round(signal['vol_ratio'], 1)}× avg\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

def fmt_result(trade, pnl_pct):
    emoji  = "✅" if trade["result"] == "WIN" else "❌"
    return (
        f"{emoji} <b>[BACKTEST] {trade['result']}</b>\n"
        f"Symbol : {trade['symbol']}  {trade['direction']}\n"
        f"Entry  : ₹{trade['entry']}  →  Exit: ₹{trade['exit_price']}\n"
        f"P&amp;L    : {'+' if pnl_pct >= 0 else ''}{round(pnl_pct, 2)}%\n"
        f"Exit at: {trade['exit_time']}"
    )

def fmt_day_summary(date_str, day_trades, cum_trades):
    wins   = sum(1 for t in day_trades if t["result"] == "WIN")
    losses = len(day_trades) - wins
    pnl    = sum(t["pnl_pct"] for t in day_trades)
    wr     = round(wins / len(day_trades) * 100) if day_trades else 0

    cum_w  = sum(1 for t in cum_trades if t["result"] == "WIN")
    cum_l  = len(cum_trades) - cum_w
    cum_wr = round(cum_w / len(cum_trades) * 100) if cum_trades else 0
    cum_pnl= sum(t["pnl_pct"] for t in cum_trades)

    return (
        f"📊 <b>[BACKTEST] Day Summary — {date_str}</b>\n"
        f"Trades  : {len(day_trades)}  |  W: {wins}  L: {losses}\n"
        f"Win Rate: {wr}%\n"
        f"Day P&amp;L : {'+' if pnl >= 0 else ''}{round(pnl, 2)}%\n"
        f"─────────────────────\n"
        f"<b>Cumulative so far</b>\n"
        f"Trades  : {len(cum_trades)}  |  W: {cum_w}  L: {cum_l}\n"
        f"Win Rate: {cum_wr}%\n"
        f"Total P&amp;L: {'+' if cum_pnl >= 0 else ''}{round(cum_pnl, 2)}%"
    )

def fmt_final_summary(all_trades):
    wins   = sum(1 for t in all_trades if t["result"] == "WIN")
    losses = len(all_trades) - wins
    total  = len(all_trades)
    wr     = round(wins / total * 100) if total > 0 else 0
    avg_win  = round(sum(t["pnl_pct"] for t in all_trades if t["result"] == "WIN")  / max(wins, 1),  2)
    avg_loss = round(sum(t["pnl_pct"] for t in all_trades if t["result"] == "LOSS") / max(losses, 1), 2)
    total_pnl= round(sum(t["pnl_pct"] for t in all_trades), 2)
    rr       = round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else "∞"

    return (
        f"🏁 <b>[BACKTEST] FINAL REPORT — 30 Days</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Total Signals : {total}\n"
        f"Wins          : {wins}  ({wr}%)\n"
        f"Losses        : {losses}\n"
        f"Total P&amp;L      : {'+' if total_pnl >= 0 else ''}{total_pnl}%\n"
        f"Avg Win       : +{avg_win}%\n"
        f"Avg Loss      : {avg_loss}%\n"
        f"Actual R:R    : 1 : {rr}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{'✅ Strategy TARGET MET (≥70%)' if wr >= 70 else '⚠️ Below 70% — review filters'}"
    )

# ══════════════════════════════════════════════
# MAIN BACKTEST ENGINE
# ══════════════════════════════════════════════
def run_backtest():
    init_db()

    if not login():
        print("[backtest] Login failed. Exiting.")
        return

    send(
        f"🔁 <b>[BACKTEST STARTED]</b>\n"
        f"Period  : Last {BACKTEST_DAYS} calendar days\n"
        f"Replay  : Every {REPLAY_STEP_MIN} min per day\n"
        f"Filters : EMA {EMA_FAST}/{EMA_SLOW} + RSI + Vol ×{VOLUME_SPIKE}\n"
        f"SL/TGT  : ATR×{SL_MULT} / ATR×{TGT_MULT}\n"
        f"Watch Telegram for daily results..."
    )

    # Build list of trading days
    today       = datetime.now(IST).date()
    start_date  = today - timedelta(days=BACKTEST_DAYS)
    trading_days = []
    d = start_date
    while d < today:
        if d.weekday() < 5 and d not in NSE_HOLIDAYS:
            trading_days.append(d)
        d += timedelta(days=1)

    print(f"[backtest] {len(trading_days)} trading days to process")

    all_trades = []

    for trade_date in trading_days:
        date_str   = trade_date.strftime("%Y-%m-%d")
        day_trades = []
        alerted    = set()   # symbols already alerted today

        print(f"\n[backtest] ── {date_str} ──")
        send(f"📅 <b>[BACKTEST]</b> Starting day: {date_str}")

        # Fetch all candles for this day for every stock
        day_cache = {}
        for sector, stocks in SECTORS.items():
            for symbol, token in stocks.items():
                df = fetch_day_candles(token, symbol, date_str)
                time.sleep(CANDLE_DELAY)
                if not df.empty:
                    day_cache[(sector, symbol)] = df

        if not day_cache:
            print(f"[backtest] No data for {date_str} — skipping")
            continue

        # Replay time-steps from 09:25 to 15:15
        scan_start = datetime.strptime(f"{date_str} 09:25", "%Y-%m-%d %H:%M")
        scan_end   = datetime.strptime(f"{date_str} 15:15", "%Y-%m-%d %H:%M")
        step       = timedelta(minutes=REPLAY_STEP_MIN)
        current_ts = scan_start

        while current_ts <= scan_end:
            ts_str     = current_ts.strftime("%H:%M")
            sector_changes: dict = {}

            # Collect sector averages at this timestamp
            for (sector, symbol), df in day_cache.items():
                df_slice = df[df["time"] <= pd.Timestamp(current_ts)]
                if df_slice.empty or df_slice.iloc[0]["open"] == 0:
                    continue
                chg = ((df_slice.iloc[-1]["close"] - df_slice.iloc[0]["open"])
                       / df_slice.iloc[0]["open"]) * 100
                sector_changes.setdefault(sector, []).append(chg)

            sector_avg = {s: sum(v)/len(v) for s, v in sector_changes.items()}

            for (sector, symbol), df in day_cache.items():
                if symbol in alerted:
                    continue

                df_slice = df[df["time"] <= pd.Timestamp(current_ts)]
                if df_slice.empty:
                    continue

                open_p = df_slice.iloc[0]["open"]

                # Sector filter
                if abs(sector_avg.get(sector, 0)) < SECTOR_THRESHOLD:
                    continue

                sig = check_signal(df_slice, symbol, sector, open_p)
                if sig is None:
                    continue

                # Signal fired — find its candle index in full df
                sig_idx = len(df[df["time"] <= pd.Timestamp(current_ts)]) - 1

                # Simulate exit on remaining candles
                result, exit_p, exit_ts = simulate_exit(
                    df, sig_idx, sig["direction"], sig["sl"], sig["target"]
                )

                entry   = sig["ltp"]
                pnl_pct = round(
                    ((exit_p - entry) / entry * 100) if sig["direction"] == "BUY"
                    else ((entry - exit_p) / entry * 100), 2
                )

                trade = {
                    "date":        date_str,
                    "signal_time": ts_str,
                    "symbol":      symbol,
                    "sector":      sector,
                    "direction":   sig["direction"],
                    "entry":       entry,
                    "recommended": sig["strike_rec"]["recommended"],
                    "strike":      sig["strike_rec"]["strike_label"],
                    "sl":          sig["sl"],
                    "target":      sig["target"],
                    "sl_pct":      sig["sl_pct"],
                    "tgt_pct":     sig["tgt_pct"],
                    "rsi":         sig["rsi"],
                    "vol_ratio":   sig["vol_ratio"],
                    "atr":         sig["atr"],
                    "result":      result,
                    "exit_price":  exit_p,
                    "exit_time":   exit_ts,
                    "pnl_pct":     pnl_pct,
                }

                save_trade(trade)
                day_trades.append(trade)
                all_trades.append(trade)
                alerted.add(symbol)

                # Send Telegram alert + result
                send(fmt_signal(sig, date_str, ts_str))
                time.sleep(1)
                send(fmt_result(trade, pnl_pct))
                time.sleep(0.5)

                print(
                    f"  [{ts_str}] {symbol} {sig['direction']} "
                    f"entry={entry} → {result} exit={exit_p} pnl={pnl_pct}%"
                )

            current_ts += step

        # End of day summary
        send(fmt_day_summary(date_str, day_trades, all_trades))
        time.sleep(2)

    # Final report
    send(fmt_final_summary(all_trades))

    # Print DB summary
    stats = get_summary()
    print("\n[backtest] ══ COMPLETE ══")
    for result, s in stats.items():
        print(f"  {result}: {s['count']} trades, avg P&L: {s['avg_pnl']}%")


# ══════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════
if __name__ == "__main__":
    run_backtest()
