import os
import sqlite3
import pandas as pd
import pyotp
import requests
from datetime import datetime, timedelta, time as dtime
from SmartApi import SmartConnect
import pytz
import time

# ─────────────────────────────────────────────────────
# ENV
# ─────────────────────────────────────────────────────
API_KEY     = os.getenv("API_KEY")
CLIENT_ID   = os.getenv("CLIENT_ID")
PASSWORD    = os.getenv("PASSWORD")
TOTP_SECRET = os.getenv("TOTP_SECRET")
TG_TOKEN    = os.getenv("TG_TOKEN")
CHAT_ID     = os.getenv("CHAT_ID")

# ─────────────────────────────────────────────────────
# BACKTEST CONFIG
# ─────────────────────────────────────────────────────
BACKTEST_DAYS      = 30
REPLAY_STEP_MIN    = 15      # simulate scan every 15 min
MAX_TRADES_PER_DAY = 2
DB_PATH            = "backtest_results.db"

CANDLE_DELAY  = 1.1
MAX_RETRIES   = 3
RETRY_BACKOFF = 2.0

# ─────────────────────────────────────────────────────
# STRATEGY (must match production exactly)
# ─────────────────────────────────────────────────────
MIN_SCORE   = 8
MIN_CANDLES = 25
EMA_FAST    = 9
EMA_SLOW    = 21
RSI_PERIOD  = 14

VOL_HIGH  = 3.0;  VOL_LOW  = 2.0
SEC_HIGH  = 0.40; SEC_LOW  = 0.25
BODY_MIN  = 0.60
RSI_BUY_LO = 55;  RSI_BUY_HI  = 70
RSI_SHT_LO = 30;  RSI_SHT_HI  = 45

SL_MULT = 1.5;  TGT_MULT = 2.5
SL_FB   = 0.30; TGT_FB   = 0.60

# ─────────────────────────────────────────────────────
# GLOBALS
# ─────────────────────────────────────────────────────
API_OBJECT = None
IST        = pytz.timezone("Asia/Kolkata")

# ─────────────────────────────────────────────────────
# NSE HOLIDAYS
# ─────────────────────────────────────────────────────
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

STRIKE_INTERVALS = {
    "HDFCBANK": 50,  "ICICIBANK": 50,  "SBIN": 50,       "AXISBANK": 50,
    "KOTAKBANK": 50, "TCS": 50,        "INFY": 50,       "HCLTECH": 50,
    "TECHM": 50,     "WIPRO": 10,      "TATAMOTORS": 50, "MARUTI": 100,
    "M&M": 50,       "BAJAJ-AUTO": 50, "SUNPHARMA": 50,  "CIPLA": 50,
    "DRREDDY": 50,   "DIVISLAB": 100,  "ITC": 5,         "HINDUNILVR": 50,
    "NESTLEIND": 50, "TATASTEEL": 10,  "JSWSTEEL": 50,   "HINDALCO": 10,
    "RELIANCE": 50,  "ONGC": 10,       "NTPC": 10,       "POWERGRID": 10,
    "BAJFINANCE": 50,"BAJAJFINSV": 50, "LT": 100,        "ADANIPORTS": 50,
}

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

# ══════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════
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
            score        INTEGER,
            score_detail TEXT,
            entry_price  REAL,
            recommended  TEXT,
            strike       TEXT,
            sl_price     REAL,
            target_price REAL,
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
        (trade_date,signal_time,symbol,sector,direction,score,score_detail,
         entry_price,recommended,strike,sl_price,target_price,
         result,exit_price,exit_time,pnl_pct)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        t["date"], t["time"], t["symbol"], t["sector"], t["direction"],
        t["score"], t["score_detail"],
        t["entry"], t["recommended"], t["strike"],
        t["sl"], t["target"],
        t["result"], t["exit_p"], t["exit_time"], t["pnl"],
    ))
    conn.commit()
    conn.close()

# ══════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════
def send(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"[tg] {e}")

# ══════════════════════════════════════════════════════
# LOGIN
# ══════════════════════════════════════════════════════
def login():
    global API_OBJECT
    try:
        obj  = SmartConnect(api_key=API_KEY)
        totp = pyotp.TOTP(TOTP_SECRET).now()
        data = obj.generateSession(CLIENT_ID, PASSWORD, totp)
        if not data or data.get("status") is False:
            print(f"[login] Failed: {data}"); return False
        rf = data["data"]["refreshToken"]
        obj.getfeedToken()
        obj.getProfile(rf)
        obj.generateToken(rf)
        API_OBJECT = obj
        print("[login] OK"); return True
    except Exception as e:
        print(f"[login] {e}"); return False

# ══════════════════════════════════════════════════════
# CANDLE FETCH — historical date
# ══════════════════════════════════════════════════════
def fetch_day(token, symbol, date_str):
    global API_OBJECT
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = API_OBJECT.getCandleData({
                "exchange": "NSE", "symboltoken": token,
                "interval": "FIVE_MINUTE",
                "fromdate": f"{date_str} 09:15",
                "todate":   f"{date_str} 15:30",
            })
            if resp and resp.get("errorCode") == "AG8001":
                login(); continue
            if resp and resp.get("data"):
                df = pd.DataFrame(resp["data"],
                    columns=["time","open","high","low","close","volume"])
                df["time"] = pd.to_datetime(df["time"])
                return df
            return pd.DataFrame()
        except Exception as e:
            m = str(e).lower()
            if "exceeding access rate" in m or "access denied" in m:
                time.sleep(RETRY_BACKOFF * attempt)
            else:
                print(f"[fetch] {symbol} {date_str}: {e}")
                return pd.DataFrame()
    return pd.DataFrame()

# ══════════════════════════════════════════════════════
# INDICATORS (identical to production)
# ══════════════════════════════════════════════════════
def ema(s, p):
    return s.ewm(span=p, adjust=False).mean()

def rsi_calc(s, p=14):
    d = s.diff()
    g = d.clip(lower=0); l = -d.clip(upper=0)
    ag = g.ewm(alpha=1/p, min_periods=p, adjust=False).mean()
    al = l.ewm(alpha=1/p, min_periods=p, adjust=False).mean()
    return 100 - (100 / (1 + ag / al.replace(0, 1e-9)))

def ema_cross(df):
    if len(df) < EMA_SLOW + 2: return None
    ef = ema(df["close"], EMA_FAST); es = ema(df["close"], EMA_SLOW)
    if ef.iloc[-2] <= es.iloc[-2] and ef.iloc[-1] > es.iloc[-1]: return "BUY"
    if ef.iloc[-2] >= es.iloc[-2] and ef.iloc[-1] < es.iloc[-1]: return "SHORT"
    return None

def vr(df):
    if len(df) < 3: return 0.0
    avg = df["volume"].iloc[:-1].mean()
    return float(df["volume"].iloc[-1] / avg) if avg > 0 else 0.0

def body_ratio(df):
    c = df.iloc[-1]; r = c["high"] - c["low"]
    return abs(c["close"] - c["open"]) / r if r > 0 else 0.0

def calc_atr(df, p=14):
    if len(df) < p + 1: return None
    h, l, c = df["high"], df["low"], df["close"]
    tr  = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    val = tr.rolling(p).mean().iloc[-1]
    return float(val) if pd.notna(val) else None

def is_doji(df):      return body_ratio(df) < 0.25
def is_spike(df):
    c = df.iloc[-1]
    return abs(c["close"]-c["open"])/c["open"]*100 > 0.50 if c["open"] > 0 else False

def trend_ok(df, direction, lb=5):
    if len(df) < lb+1: return True
    if direction == "BUY":
        h = df["high"].iloc[-lb:].values
        return all(h[i] <= h[i+1] for i in range(len(h)-1))
    l = df["low"].iloc[-lb:].values
    return all(l[i] >= l[i+1] for i in range(len(l)-1))

# ══════════════════════════════════════════════════════
# SCORER (identical to production)
# ══════════════════════════════════════════════════════
def score_signal(df, direction, sec_avg, vol_r, rsi_val):
    pts = 2; detail = ["EMA+2"]          # EMA cross already confirmed

    if direction == "BUY"   and RSI_BUY_LO <= rsi_val <= RSI_BUY_HI:
        pts += 2; detail.append("RSI+2")
    elif direction == "SHORT" and RSI_SHT_LO <= rsi_val <= RSI_SHT_HI:
        pts += 2; detail.append("RSI+2")
    else:
        detail.append("RSI+0")

    if vol_r >= VOL_HIGH:   pts += 2; detail.append(f"Vol+2({round(vol_r,1)}x)")
    elif vol_r >= VOL_LOW:  pts += 1; detail.append(f"Vol+1({round(vol_r,1)}x)")
    else:                             detail.append(f"Vol+0({round(vol_r,1)}x)")

    abs_s = abs(sec_avg)
    if abs_s >= SEC_HIGH:   pts += 2; detail.append(f"Sec+2({round(abs_s,2)}%)")
    elif abs_s >= SEC_LOW:  pts += 1; detail.append(f"Sec+1({round(abs_s,2)}%)")
    else:                             detail.append(f"Sec+0({round(abs_s,2)}%)")

    if body_ratio(df) >= BODY_MIN: pts += 2; detail.append("Body+2")
    else:                                    detail.append("Body+0")

    return pts, " | ".join(detail)

# ══════════════════════════════════════════════════════
# TRADE PARAMS
# ══════════════════════════════════════════════════════
def build_params(ltp, direction, a):
    if a and a > 0:
        sd = round(SL_MULT*a, 2); td = round(TGT_MULT*a, 2)
        sl     = round(ltp-sd, 2) if direction=="BUY" else round(ltp+sd, 2)
        target = round(ltp+td, 2) if direction=="BUY" else round(ltp-td, 2)
    else:
        sl     = round(ltp*(1-SL_FB/100),  2) if direction=="BUY" else round(ltp*(1+SL_FB/100),  2)
        target = round(ltp*(1+TGT_FB/100), 2) if direction=="BUY" else round(ltp*(1-TGT_FB/100), 2)
    return sl, target

def recommend_strike(symbol, ltp, direction, a, rsi_val, vol_r):
    iv  = STRIKE_INTERVALS.get(symbol, 50)
    opt = "CE" if direction=="BUY" else "PE"
    atm = round(ltp/iv)*iv
    itm = (atm-iv) if direction=="BUY" else (atm+iv)
    ap  = (a/ltp*100) if a and ltp>0 else 999
    use = (vol_r>=3.5) and ((60<=rsi_val<=72) if direction=="BUY" else (28<=rsi_val<=40)) and (ap<=1.0)
    rec = "ITM" if use else "ATM"
    val = itm if use else atm
    return rec, f"{val} {opt}"

# ══════════════════════════════════════════════════════
# EXIT SIMULATOR
# ══════════════════════════════════════════════════════
def simulate_exit(df_full, sig_idx, direction, sl, target):
    for _, row in df_full.iloc[sig_idx+1:].iterrows():
        hi, lo, ts = row["high"], row["low"], str(row["time"])
        if direction == "BUY":
            if lo <= sl:     return "LOSS", sl,     ts
            if hi >= target: return "WIN",  target, ts
        else:
            if hi >= sl:     return "LOSS", sl,     ts
            if lo <= target: return "WIN",  target, ts
    last  = df_full.iloc[-1]
    ep    = last["close"]
    entry = df_full.iloc[sig_idx]["close"]
    pnl   = (ep-entry)/entry*100 if direction=="BUY" else (entry-ep)/entry*100
    return ("WIN" if pnl > 0 else "LOSS"), ep, str(last["time"])

# ══════════════════════════════════════════════════════
# FORMATTERS
# ══════════════════════════════════════════════════════
def score_bar(s):
    return f"{'█'*s}{'░'*(10-s)} {s}/10"

def fmt_alert(t, date_str, ts):
    d = t["direction"]
    return (
        f"🔁 <b>[BACKTEST] TRADE ALERT</b>\n"
        f"📅 {date_str}  ⏰ {ts}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Direction : {'🟢 BUY (CE)' if d=='BUY' else '🔴 SHORT (PE)'}\n"
        f"Stock     : <b>{t['symbol']}</b>  ({t['sector']})\n"
        f"LTP       : ₹{round(t['entry'],2)}\n"
        f"\n"
        f"<b>Score</b>\n"
        f"<code>{score_bar(t['score'])}</code>\n"
        f"<code>{t['score_detail']}</code>\n"
        f"\n"
        f"Strike    : {t['recommended']} → {t['strike']}\n"
        f"SL        : ₹{t['sl']}\n"
        f"Target    : ₹{t['target']}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

def fmt_result(t):
    e   = "✅" if t["result"]=="WIN" else "❌"
    pnl = t["pnl"]
    return (
        f"{e} <b>[BACKTEST] {t['result']}</b>\n"
        f"{t['symbol']}  Entry ₹{t['entry']}  →  Exit ₹{t['exit_p']}\n"
        f"P&amp;L : {'+' if pnl>=0 else ''}{round(pnl,2)}%  |  Exit: {t['exit_time'][:16]}"
    )

def fmt_day(date_str, day_trades, all_trades):
    w  = sum(1 for t in day_trades if t["result"]=="WIN")
    l  = len(day_trades)-w
    dp = sum(t["pnl"] for t in day_trades)
    wr = round(w/len(day_trades)*100) if day_trades else 0
    cw = sum(1 for t in all_trades if t["result"]=="WIN")
    ct = len(all_trades)
    cwr= round(cw/ct*100) if ct else 0
    cp = sum(t["pnl"] for t in all_trades)
    return (
        f"📊 <b>[BACKTEST] {date_str}</b>\n"
        f"Trades: {len(day_trades)}  W:{w}  L:{l}  WR:{wr}%  P&amp;L:{'+' if dp>=0 else ''}{round(dp,2)}%\n"
        f"─── Cumulative ───\n"
        f"Trades:{ct}  WR:{cwr}%  P&amp;L:{'+' if cp>=0 else ''}{round(cp,2)}%"
    )

def fmt_final(all_trades):
    if not all_trades:
        return "🏁 [BACKTEST] No trades fired in 30 days — check MIN_SCORE threshold"
    w    = sum(1 for t in all_trades if t["result"]=="WIN")
    l    = len(all_trades)-w
    tot  = len(all_trades)
    wr   = round(w/tot*100)
    wins = [t["pnl"] for t in all_trades if t["result"]=="WIN"]
    loss = [t["pnl"] for t in all_trades if t["result"]=="LOSS"]
    aw   = round(sum(wins)/max(len(wins),1),  2)
    al   = round(sum(loss)/max(len(loss),1),  2)
    tp   = round(sum(t["pnl"] for t in all_trades), 2)
    rr   = round(abs(aw/al), 2) if al != 0 else "∞"
    verdict = "✅ Target met (≥70%)" if wr >= 70 else ("⚠️ Close — tune slightly" if wr >= 60 else "❌ Below target — review score threshold")
    return (
        f"🏁 <b>[BACKTEST] FINAL — 30 days</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Total trades  : {tot}  (avg {round(tot/20,1)}/day)\n"
        f"Wins          : {w}  ({wr}%)\n"
        f"Losses        : {l}\n"
        f"Total P&amp;L     : {'+' if tp>=0 else ''}{tp}%\n"
        f"Avg win       : +{aw}%\n"
        f"Avg loss      : {al}%\n"
        f"Actual R:R    : 1 : {rr}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{verdict}"
    )

# ══════════════════════════════════════════════════════
# MAIN BACKTEST ENGINE
# ══════════════════════════════════════════════════════
def run_backtest():
    init_db()
    if not login():
        print("[backtest] Login failed"); return

    today       = datetime.now(IST).date()
    start       = today - timedelta(days=BACKTEST_DAYS)
    trading_days= [d for d in (start + timedelta(i) for i in range(BACKTEST_DAYS))
                   if d.weekday() < 5 and d not in NSE_HOLIDAYS and d < today]

    send(
        f"🔁 <b>[BACKTEST STARTED]</b>\n"
        f"Period   : {start} → {today}\n"
        f"Days     : {len(trading_days)} trading days\n"
        f"Strategy : Confluence ≥{MIN_SCORE}/10  |  Max {MAX_TRADES_PER_DAY}/day\n"
        f"Sit back — day summaries incoming..."
    )

    all_trades = []

    for trade_date in trading_days:
        date_str   = trade_date.strftime("%Y-%m-%d")
        day_trades = []
        alerted    = set()

        print(f"\n══ {date_str} ══")

        # Fetch all candles for the day
        cache = {}
        for sector, stocks in SECTORS.items():
            for symbol, token in stocks.items():
                df = fetch_day(token, symbol, date_str)
                time.sleep(CANDLE_DELAY)
                if not df.empty:
                    cache[(sector, symbol)] = df

        if not cache:
            print(f"  No data — skipping"); continue

        # Replay time-steps 09:25 → 14:45 in REPLAY_STEP_MIN steps
        step     = timedelta(minutes=REPLAY_STEP_MIN)
        cur_ts   = datetime.strptime(f"{date_str} 09:25", "%Y-%m-%d %H:%M")
        end_ts   = datetime.strptime(f"{date_str} 14:45", "%Y-%m-%d %H:%M")
        day_count= 0

        while cur_ts <= end_ts and day_count < MAX_TRADES_PER_DAY:
            ts_str = cur_ts.strftime("%H:%M")

            # Sector averages at this timestamp
            sec_changes: dict = {}
            for (sector, symbol), df in cache.items():
                sl = df[df["time"] <= pd.Timestamp(cur_ts)]
                if sl.empty or sl.iloc[0]["open"] == 0: continue
                chg = (sl.iloc[-1]["close"]-sl.iloc[0]["open"])/sl.iloc[0]["open"]*100
                sec_changes.setdefault(sector, []).append(chg)
            sec_avg = {s: sum(v)/len(v) for s, v in sec_changes.items()}

            candidates = []

            for (sector, symbol), df in cache.items():
                if symbol in alerted: continue
                sl = df[df["time"] <= pd.Timestamp(cur_ts)]
                if len(sl) < MIN_CANDLES: continue

                op = sl.iloc[0]["open"]
                if op == 0: continue
                change = (sl.iloc[-1]["close"]-op)/op*100

                cross   = ema_cross(sl)
                if cross is None:              continue
                if is_doji(sl):               continue
                if is_spike(sl):              continue
                if not trend_ok(sl, cross):   continue
                if cross=="BUY"  and change<=0: continue
                if cross=="SHORT" and change>=0: continue

                sa      = sec_avg.get(sector, 0)
                vol_r   = vr(sl)
                rsi_val = rsi_calc(sl["close"], RSI_PERIOD).iloc[-1]
                a       = calc_atr(sl)

                score, detail = score_signal(sl, cross, sa, vol_r, rsi_val)
                if score < MIN_SCORE: continue

                sl_p, target = build_params(sl.iloc[-1]["close"], cross, a)
                rec, strike  = recommend_strike(symbol, sl.iloc[-1]["close"], cross, a, rsi_val, vol_r)

                candidates.append({
                    "symbol": symbol, "sector": sector,
                    "direction": cross, "score": score, "score_detail": detail,
                    "entry": sl.iloc[-1]["close"],
                    "sl": sl_p, "target": target,
                    "recommended": rec, "strike": strike,
                    "rsi": rsi_val, "vol_ratio": vol_r,
                    "df_full": df,
                    "sig_idx": len(sl)-1,
                })

            # Take top scored candidates
            candidates.sort(key=lambda x: x["score"], reverse=True)
            for cand in candidates:
                if day_count >= MAX_TRADES_PER_DAY: break
                if cand["symbol"] in alerted:       continue

                result, exit_p, exit_time = simulate_exit(
                    cand["df_full"], cand["sig_idx"],
                    cand["direction"], cand["sl"], cand["target"]
                )
                pnl = round(
                    ((exit_p-cand["entry"])/cand["entry"]*100) if cand["direction"]=="BUY"
                    else ((cand["entry"]-exit_p)/cand["entry"]*100), 2
                )

                trade = {
                    "date": date_str, "time": ts_str,
                    "symbol": cand["symbol"], "sector": cand["sector"],
                    "direction": cand["direction"],
                    "score": cand["score"], "score_detail": cand["score_detail"],
                    "entry": cand["entry"],
                    "recommended": cand["recommended"], "strike": cand["strike"],
                    "sl": cand["sl"], "target": cand["target"],
                    "result": result, "exit_p": exit_p,
                    "exit_time": exit_time, "pnl": pnl,
                }

                save_trade(trade)
                day_trades.append(trade)
                all_trades.append(trade)
                alerted.add(cand["symbol"])
                day_count += 1

                send(fmt_alert(trade, date_str, ts_str))
                time.sleep(0.5)
                send(fmt_result(trade))
                time.sleep(0.5)

                print(f"  [{ts_str}] {cand['symbol']} {cand['direction']} score={cand['score']} → {result} pnl={pnl}%")

            cur_ts += step

        send(fmt_day(date_str, day_trades, all_trades))
        time.sleep(1)

    send(fmt_final(all_trades))
    print("\n[backtest] Complete")


if __name__ == "__main__":
    run_backtest()
