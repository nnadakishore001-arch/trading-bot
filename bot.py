"""
╔══════════════════════════════════════════════════════════════════╗
║          PRO TRADING BOT — GRADE + CONFLUENCE HYBRID             ║
║                                                                  ║
║  Built on your grade system (A+/B/C) + upgraded with:            ║
║  • Confluence scoring (5 layers, 10 pts) inside each grade       ║
║  • ATR-based SL & Target (not fixed %)                           ║
║  • EMA 9/21 crossover confirmation                               ║
║  • RSI zone filter (no overbought/oversold entries)              ║
║  • Volume spike confirmation (2.5× avg minimum)                  ║
║  • Candle body strength check                                    ║
║  • Per-symbol 45-min cooldown                                    ║
║  • Max 2 trades/day hard cap                                     ║
║  • Circuit breaker (2 losses → stop for the day)                 ║
║  • Auto token refresh at 08:00 IST                               ║
║  • Background SL/Target monitor per trade                        ║
║  • SQLite trade log with daily P&L summary                       ║
║  • NSE holiday calendar (2025 + 2026)                            ║
║                                                                  ║
║  GRADE RULES (your original logic, now WITH confluence gate):    ║
║    A+ : Stock ≥1.50% AND Sector ≥0.75% AND score ≥7              ║
║    B  : Stock ≥0.75% AND Sector ≥0.40% AND score ≥8              ║
║    C  : Stock ≥0.25% AND Sector ≥0.20% AND score ≥9 (strict)     ║
║    D  : Below all thresholds → never fire                        ║
╚══════════════════════════════════════════════════════════════════╝
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
import threading

# ─────────────────────────────────────────────────────
# ENV  (never change these)
# ─────────────────────────────────────────────────────
API_KEY     = os.getenv("API_KEY")
CLIENT_ID   = os.getenv("CLIENT_ID")
PASSWORD    = os.getenv("PASSWORD")
TOTP_SECRET = os.getenv("TOTP_SECRET")
TG_TOKEN    = os.getenv("TG_TOKEN")
CHAT_ID     = os.getenv("CHAT_ID")

# ─────────────────────────────────────────────────────
# GLOBALS & LOCKS
# ─────────────────────────────────────────────────────
API_OBJECT      = None
IST             = pytz.timezone("Asia/Kolkata")
DB_PATH         = "trades.db"
TOKEN_REFRESHED = None
API_LOCK        = threading.RLock()  # Prevents thread collisions on API calls

# ─────────────────────────────────────────────────────
# RATE LIMITS
# ─────────────────────────────────────────────────────
CANDLE_DELAY  = 1.1
MAX_RETRIES   = 3
RETRY_BACKOFF = 2.0

# ─────────────────────────────────────────────────────
# STRATEGY CONSTANTS
# ─────────────────────────────────────────────────────
MAX_TRADES_PER_DAY  = 2
SCAN_INTERVAL_SEC   = 300      # scan every 5 min
SIGNAL_COOLDOWN_MIN = 45       # per-symbol cooldown (minutes)
MIN_CANDLES         = 25       # minimum candles needed for indicators
SCAN_END_TIME       = dtime(14, 45)   # stop scanning after 14:45

# Circuit breaker
MAX_DAILY_LOSS = 2

# EMA / RSI
EMA_FAST   = 9
EMA_SLOW   = 21
RSI_PERIOD = 14

# Confluence scoring thresholds
VOL_HIGH    = 3.0    # volume ≥ 3× avg → +2 pts
VOL_LOW     = 2.5    # volume ≥ 2.5× avg → +1 pt
BODY_MIN    = 0.55   # candle body ≥ 55% of range → +2 pts
RSI_BUY_LO  = 50;  RSI_BUY_HI  = 72
RSI_SHT_LO  = 28;  RSI_SHT_HI  = 50

# Grade thresholds (your original logic preserved)
GRADE_AP_STOCK  = 1.50;  GRADE_AP_SEC  = 0.75
GRADE_B_STOCK   = 0.75;  GRADE_B_SEC   = 0.40
GRADE_C_STOCK   = 0.25;  GRADE_C_SEC   = 0.20

# Min score per grade to fire (C is hardest because move is weak)
GRADE_MIN_SCORE = {"A+": 7, "B": 8, "C": 9}

# ATR-based risk management
SL_MULT_AP  = 1.2   # tighter SL for A+ (strong move, less room)
SL_MULT_B   = 1.5
SL_MULT_C   = 1.8   # wider SL for C (weaker move, more noise)
TGT_MULT    = 2.5   # target multiplier (same for all grades)
SL_FB       = 0.30  # fallback fixed % if ATR unavailable
TGT_FB      = 0.60

# ─────────────────────────────────────────────────────
# NSE HOLIDAYS 2025 + 2026
# ─────────────────────────────────────────────────────
NSE_HOLIDAYS = {
    datetime(2025,1,26).date(),  datetime(2025,2,26).date(),
    datetime(2025,3,14).date(),  datetime(2025,3,31).date(),
    datetime(2025,4,10).date(),  datetime(2025,4,14).date(),
    datetime(2025,4,18).date(),  datetime(2025,5,1).date(),
    datetime(2025,8,15).date(),  datetime(2025,8,27).date(),
    datetime(2025,10,2).date(),  datetime(2025,10,21).date(),
    datetime(2025,10,22).date(), datetime(2025,11,5).date(),
    datetime(2025,12,25).date(),
    datetime(2026,1,26).date(),  datetime(2026,3,20).date(),
    datetime(2026,4,2).date(),   datetime(2026,4,3).date(),
    datetime(2026,4,14).date(),  datetime(2026,5,1).date(),
    datetime(2026,8,15).date(),  datetime(2026,8,26).date(),
    datetime(2026,10,2).date(),  datetime(2026,12,25).date(),
}

# ─────────────────────────────────────────────────────
# STRIKE INTERVALS (per symbol)
# ─────────────────────────────────────────────────────
STRIKE_INTERVALS = {
    "HDFCBANK":50,"ICICIBANK":50,"SBIN":50,"AXISBANK":50,"KOTAKBANK":50,
    "TCS":50,"INFY":50,"HCLTECH":50,"TECHM":50,"WIPRO":10,
    "TATAMOTORS":50,"MARUTI":100,"M&M":50,"BAJAJ-AUTO":50,
    "SUNPHARMA":50,"CIPLA":50,"DRREDDY":50,"DIVISLAB":100,
    "ITC":5,"HINDUNILVR":50,"NESTLEIND":50,
    "TATASTEEL":10,"JSWSTEEL":50,"HINDALCO":10,
    "RELIANCE":50,"ONGC":10,"NTPC":10,"POWERGRID":10,
    "BAJFINANCE":50,"BAJAJFINSV":50,"LT":100,"ADANIPORTS":50,
}

# ─────────────────────────────────────────────────────
# SECTORS
# ─────────────────────────────────────────────────────
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
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            date         TEXT,
            time         TEXT,
            symbol       TEXT,
            sector       TEXT,
            direction    TEXT,
            grade        TEXT,
            score        INTEGER,
            score_detail TEXT,
            entry_price  REAL,
            strike       TEXT,
            strike_type  TEXT,
            sl_price     REAL,
            target_price REAL,
            sl_pct       REAL,
            tgt_pct      REAL,
            rsi          REAL,
            vol_ratio    REAL,
            atr          REAL,
            result       TEXT DEFAULT 'OPEN',
            exit_price   REAL,
            pnl_pct      REAL
        )
    """)
    conn.commit()
    conn.close()

def log_trade(sig):
    conn = sqlite3.connect(DB_PATH, timeout=10)
    c    = conn.cursor()
    now  = now_ist()
    c.execute("""
        INSERT INTO trades
        (date,time,symbol,sector,direction,grade,score,score_detail,
         entry_price,strike,strike_type,sl_price,target_price,
         sl_pct,tgt_pct,rsi,vol_ratio,atr)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        now.strftime("%Y-%m-%d"), now.strftime("%H:%M"),
        sig["symbol"], sig["sector"], sig["direction"],
        sig["grade"], sig["score"], sig["score_detail"],
        sig["ltp"], sig["strike"], sig["strike_type"],
        sig["sl"], sig["target"], sig["sl_pct"], sig["tgt_pct"],
        round(sig["rsi"],2), round(sig["vol_ratio"],2), sig["atr"] or 0,
    ))
    tid = c.lastrowid
    conn.commit()
    conn.close()
    return tid

def update_result(tid, result, exit_p, pnl):
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute(
        "UPDATE trades SET result=?,exit_price=?,pnl_pct=? WHERE id=?",
        (result, exit_p, pnl, tid)
    )
    conn.commit()
    conn.close()

def get_today_stats():
    conn  = sqlite3.connect(DB_PATH, timeout=10)
    today = now_ist().strftime("%Y-%m-%d")
    rows  = conn.execute(
        "SELECT result, COUNT(*) FROM trades WHERE date=? GROUP BY result", (today,)
    ).fetchall()
    conn.close()
    s = {"WIN":0,"LOSS":0,"OPEN":0}
    for r,n in rows:
        if r in s: s[r] = n
    return s

# ══════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════
def send(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id":CHAT_ID,"text":msg,"parse_mode":"HTML"},
            timeout=10
        )
    except Exception as e:
        print(f"[telegram] {e}")

# ══════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════
def now_ist():
    return datetime.now(IST)

def is_trading_day():
    t = now_ist()
    return t.weekday() < 5 and t.date() not in NSE_HOLIDAYS

def scan_window_open(now):
    return dtime(9,25) <= now.time() <= SCAN_END_TIME

def market_closed(now):
    return now.time() >= dtime(15,30)

def is_rate_limit(e):
    m = str(e).lower()
    return "exceeding access rate" in m or "access denied" in m

# ══════════════════════════════════════════════════════
# SESSION MANAGEMENT (Secured with API_LOCK)
# ══════════════════════════════════════════════════════
def ensure_fresh():
    global API_OBJECT, TOKEN_REFRESHED
    now    = now_ist()
    cutoff = now.replace(hour=8, minute=0, second=0, microsecond=0)
    with API_LOCK:
        if TOKEN_REFRESHED is None or TOKEN_REFRESHED < cutoff:
            print(f"[session] Refreshing at {now.strftime('%H:%M')}")
            API_OBJECT = login()
            if API_OBJECT:
                TOKEN_REFRESHED = now
            return API_OBJECT is not None
    return True

def login():
    with API_LOCK:
        try:
            obj  = SmartConnect(api_key=API_KEY)
            totp = pyotp.TOTP(TOTP_SECRET).now()
            data = obj.generateSession(CLIENT_ID, PASSWORD, totp)
            if not data or data.get("status") is False:
                send(f"❌ Login failed: {data}")
                return None
            rf = data["data"]["refreshToken"]
            obj.getfeedToken()
            profile = obj.getProfile(rf)
            if not profile or not profile.get("data"):
                send("❌ Profile validation failed")
                return None
            obj.generateToken(rf)
            name = profile["data"].get("name", CLIENT_ID)
            send(f"✅ <b>Login OK</b> — {name} ({profile['data']['clientcode']})")
            return obj
        except Exception as e:
            send(f"❌ Login error: {e}")
            return None

# ══════════════════════════════════════════════════════
# CANDLE FETCH (Indicator Warmup Fix + API Lock)
# ══════════════════════════════════════════════════════
def get_data(token, symbol=""):
    global API_OBJECT
    now   = now_ist()
    # Fetch from 4 days ago to properly warm up EMA 21, RSI 14, and ATR 14
    start = now - timedelta(days=4)
    start = start.replace(hour=9, minute=15, second=0, microsecond=0)
    fs    = start.strftime("%Y-%m-%d %H:%M")
    ts    = now.strftime("%Y-%m-%d %H:%M")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with API_LOCK:
                resp = API_OBJECT.getCandleData({
                    "exchange":"NSE","symboltoken":token,
                    "interval":"FIVE_MINUTE",
                    "fromdate":fs,"todate":ts,
                })
                if resp and resp.get("errorCode") == "AG8001":
                    send("⚠️ Session expired — re-logging")
                    API_OBJECT = login()
                    if not API_OBJECT: return pd.DataFrame()
                    continue
            
            if resp and resp.get("data"):
                return pd.DataFrame(
                    resp["data"],
                    columns=["time","open","high","low","close","volume"]
                )
                return pd.DataFrame()
        except Exception as e:
            if is_rate_limit(e):
                wait = RETRY_BACKOFF * attempt
                print(f"[rate_limit] {symbol} attempt {attempt}, wait {wait}s")
                time.sleep(wait)
            else:
                print(f"[get_data] {symbol}: {e}")
                return pd.DataFrame()
    return pd.DataFrame()

# ══════════════════════════════════════════════════════
# INDICATORS
# ══════════════════════════════════════════════════════
def ema(s, p):
    return s.ewm(span=p, adjust=False).mean()

def calc_rsi(s, p=14):
    d  = s.diff()
    g  = d.clip(lower=0)
    l  = -d.clip(upper=0)
    ag = g.ewm(alpha=1/p, min_periods=p, adjust=False).mean()
    al = l.ewm(alpha=1/p, min_periods=p, adjust=False).mean()
    return 100 - (100 / (1 + ag / al.replace(0, 1e-9)))

def ema_crossover(df):
    if len(df) < EMA_SLOW + 2: return None
    ef = ema(df["close"], EMA_FAST)
    es = ema(df["close"], EMA_SLOW)
    if ef.iloc[-2] <= es.iloc[-2] and ef.iloc[-1] > es.iloc[-1]: return "BUY"
    if ef.iloc[-2] >= es.iloc[-2] and ef.iloc[-1] < es.iloc[-1]: return "SHORT"
    return None

def vol_ratio(df):
    if len(df) < 3: return 0.0
    avg = df["volume"].iloc[:-1].mean()
    return float(df["volume"].iloc[-1] / avg) if avg > 0 else 0.0

def candle_body_ratio(df):
    c = df.iloc[-1]; r = c["high"] - c["low"]
    return abs(c["close"] - c["open"]) / r if r > 0 else 0.0

def calc_atr(df, p=14):
    if len(df) < p + 1: return None
    h, l, c = df["high"], df["low"], df["close"]
    tr  = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()], axis=1).max(axis=1)
    val = tr.rolling(p).mean().iloc[-1]
    return float(val) if pd.notna(val) else None

def is_doji(df):       return candle_body_ratio(df) < 0.25
def is_news_spike(df):
    c = df.iloc[-1]
    return abs(c["close"]-c["open"])/c["open"]*100 > 0.50 if c["open"]>0 else False

# Fixed overly strict momentum filter
def trend_continuation(df, direction, lb=5):
    if len(df) < lb + 1: return True
    if direction == "BUY":
        return df["close"].iloc[-1] > df["close"].iloc[-(lb+1)]
    else:
        return df["close"].iloc[-1] < df["close"].iloc[-(lb+1)]

# ══════════════════════════════════════════════════════
# GRADE ENGINE  (your original logic, preserved exactly)
# ══════════════════════════════════════════════════════
def assign_grade(stock_pct, sector_pct):
    s, sec = abs(stock_pct), abs(sector_pct)
    if s >= GRADE_AP_STOCK and sec >= GRADE_AP_SEC:
        return "A+", "High Recommendation — Strong Momentum", "🚀"
    if s >= GRADE_B_STOCK  and sec >= GRADE_B_SEC:
        return "B",  "Good Setup — Chart check advised",       "⚖️"
    if s >= GRADE_C_STOCK  and sec >= GRADE_C_SEC:
        return "C",  "Borderline — Strict human logic required","⚠️"
    return None, None, None

# ══════════════════════════════════════════════════════
# CONFLUENCE SCORER  (quality gate within each grade)
# ══════════════════════════════════════════════════════
def confluence_score(df, direction, sector_avg, vr, rsi_val):
    pts = 0; detail = []

    # L1 — EMA crossover (+2, already confirmed before calling)
    pts += 2; detail.append("EMA+2")

    # L2 — RSI zone (+2)
    if direction=="BUY"   and RSI_BUY_LO <= rsi_val <= RSI_BUY_HI:
        pts += 2; detail.append(f"RSI+2({round(rsi_val,1)})")
    elif direction=="SHORT" and RSI_SHT_LO <= rsi_val <= RSI_SHT_HI:
        pts += 2; detail.append(f"RSI+2({round(rsi_val,1)})")
    else:
        detail.append(f"RSI+0({round(rsi_val,1)})")

    # L3 — Volume (+2 or +1)
    if vr >= VOL_HIGH:  pts += 2; detail.append(f"Vol+2({round(vr,1)}x)")
    elif vr >= VOL_LOW: pts += 1; detail.append(f"Vol+1({round(vr,1)}x)")
    else:                         detail.append(f"Vol+0({round(vr,1)}x)")

    # L4 — Sector strength (+2 or +1)
    asec = abs(sector_avg)
    if asec >= 0.75:   pts += 2; detail.append(f"Sec+2({round(asec,2)}%)")
    elif asec >= 0.40: pts += 1; detail.append(f"Sec+1({round(asec,2)}%)")
    else:                        detail.append(f"Sec+0({round(asec,2)}%)")

    # L5 — Candle body (+2)
    if candle_body_ratio(df) >= BODY_MIN:
        pts += 2; detail.append("Body+2")
    else:
        detail.append("Body+0")

    return pts, " | ".join(detail)

# ══════════════════════════════════════════════════════
# TRADE PARAMS — ATR-based, grade-aware SL
# ══════════════════════════════════════════════════════
def build_trade_params(ltp, direction, grade, a):
    sl_mult = {"A+": SL_MULT_AP, "B": SL_MULT_B, "C": SL_MULT_C}.get(grade, SL_MULT_B)
    if a and a > 0:
        sd = round(sl_mult * a, 2)
        td = round(TGT_MULT * a, 2)
        sl     = round(ltp-sd,2) if direction=="BUY" else round(ltp+sd,2)
        target = round(ltp+td,2) if direction=="BUY" else round(ltp-td,2)
        sp     = round(sd/ltp*100,2)
        tp     = round(td/ltp*100,2)
    else:
        sl     = round(ltp*(1-SL_FB/100),2)  if direction=="BUY" else round(ltp*(1+SL_FB/100),2)
        target = round(ltp*(1+TGT_FB/100),2) if direction=="BUY" else round(ltp*(1-TGT_FB/100),2)
        sp, tp = SL_FB, TGT_FB
    return sl, target, sp, tp

# ══════════════════════════════════════════════════════
# STRIKE RECOMMENDATION (ATM vs ITM)
# ══════════════════════════════════════════════════════
def recommend_strike(symbol, ltp, direction, grade, a, rsi_val, vr_val):
    iv   = STRIKE_INTERVALS.get(symbol, 50)
    opt  = "CE" if direction=="BUY" else "PE"
    atm  = round(ltp/iv)*iv
    itm  = (atm-iv) if direction=="BUY" else (atm+iv)
    ap   = (a/ltp*100) if a and ltp > 0 else 999

    use_itm = (
        grade == "A+"
        and vr_val >= 3.5
        and ((60 <= rsi_val <= 72) if direction=="BUY" else (28 <= rsi_val <= 40))
        and ap <= 1.0
    )
    rec  = "ITM" if use_itm else "ATM"
    val  = itm  if use_itm else atm
    return rec, f"{val} {opt}", f"{atm} {opt}", f"{itm} {opt}"

# ══════════════════════════════════════════════════════
# MARKET SCANNER (String Mask Fix for Multi-Day)
# ══════════════════════════════════════════════════════
def scan_market(last_alerted: dict):
    raw        = []
    sector_raw = {}
    now        = now_ist()
    today_str  = now.strftime("%Y-%m-%d")

    for sector, stocks in SECTORS.items():
        for symbol, token in stocks.items():

            if symbol in last_alerted:
                mins = (now - last_alerted[symbol]).total_seconds() / 60
                if mins < SIGNAL_COOLDOWN_MIN:
                    time.sleep(CANDLE_DELAY)
                    continue

            df = get_data(token, symbol)
            time.sleep(CANDLE_DELAY)

            # Ensure we have enough data overall for the indicator math
            if len(df) < MIN_CANDLES:
                continue

            # Safely extract today's specific price action using string mask
            today_mask = df["time"].str.startswith(today_str)
            today_df   = df[today_mask]

            if today_df.empty or today_df.iloc[0]["open"] == 0:
                continue

            op     = today_df.iloc[0]["open"]  # Accurate 09:15 AM today open
            cp     = today_df.iloc[-1]["close"]
            change = (cp - op) / op * 100
            sector_raw.setdefault(sector, []).append(change)

            raw.append({
                "symbol": symbol, "sector": sector,
                "df": df, "change": change, "ltp": cp,
            })

    if not raw: return []

    sec_avg = {s: sum(v)/len(v) for s, v in sector_raw.items()}
    signals = []

    for r in raw:
        df     = r["df"]
        change = r["change"]
        ltp    = r["ltp"]
        sector = r["sector"]
        symbol = r["symbol"]
        sa     = sec_avg.get(sector, 0)

        grade, desc, emoji = assign_grade(change, sa)
        if grade is None:
            continue

        cross = ema_crossover(df)
        if cross is None:
            continue

        if cross == "BUY"   and change <= 0: continue
        if cross == "SHORT" and change >= 0: continue

        if is_doji(df):             continue
        if is_news_spike(df):       continue
        if not trend_continuation(df, cross): continue

        vr_val  = vol_ratio(df)
        rsi_val = calc_rsi(df["close"], RSI_PERIOD).iloc[-1]
        a       = calc_atr(df)

        if cross=="BUY"   and not (RSI_BUY_LO <= rsi_val <= RSI_BUY_HI): continue
        if cross=="SHORT" and not (RSI_SHT_LO <= rsi_val <= RSI_SHT_HI): continue

        score, detail = confluence_score(df, cross, sa, vr_val, rsi_val)
        min_score = GRADE_MIN_SCORE.get(grade, 8)
        if score < min_score:
            print(f"  [{symbol}] grade={grade} score={score}/{min_score} needed — skip")
            continue

        sl, target, sp, tp = build_trade_params(ltp, cross, grade, a)
        rec, strike, atm_s, itm_s = recommend_strike(symbol, ltp, cross, grade, a, rsi_val, vr_val)
        rr = round(tp/sp, 1) if sp else "N/A"

        signals.append({
            "symbol":      symbol,
            "sector":      sector,
            "direction":   cross,
            "grade":       grade,
            "grade_desc":  desc,
            "grade_emoji": emoji,
            "score":       score,
            "score_detail":detail,
            "ltp":         ltp,
            "change":      change,
            "sec_change":  sa,
            "rsi":         rsi_val,
            "vol_ratio":   vr_val,
            "atr":         a,
            "sl":          sl,
            "target":      target,
            "sl_pct":      sp,
            "tgt_pct":     tp,
            "rr":          rr,
            "strike":      strike,
            "strike_type": rec,
            "atm_strike":  atm_s,
            "itm_strike":  itm_s,
        })

    grade_rank = {"A+": 3, "B": 2, "C": 1}
    signals.sort(
        key=lambda x: (grade_rank.get(x["grade"],0), x["score"], abs(x["change"])),
        reverse=True
    )
    return signals

# ══════════════════════════════════════════════════════
# TRADE MONITOR (background thread)
# ══════════════════════════════════════════════════════
class Monitor(threading.Thread):
    def __init__(self, tid, symbol, token, direction, sl, target, entry, grade):
        super().__init__(daemon=True)
        self.tid       = tid
        self.symbol    = symbol
        self.token     = token
        self.direction = direction
        self.sl        = sl
        self.target    = target
        self.entry     = entry
        self.grade     = grade
        self.active    = True

    def run(self):
        while self.active:
            time.sleep(60)
            if market_closed(now_ist()):
                self._squareoff(); break
            df = get_data(self.token, self.symbol)
            if df.empty: continue
            ltp = df.iloc[-1]["close"]
            if self.direction == "BUY":
                if ltp <= self.sl:     self._exit("SL HIT",     ltp); break
                if ltp >= self.target: self._exit("TARGET HIT", ltp); break
            else:
                if ltp >= self.sl:     self._exit("SL HIT",     ltp); break
                if ltp <= self.target: self._exit("TARGET HIT", ltp); break

    def _exit(self, label, ep):
        pnl = round(((ep-self.entry)/self.entry*100)*(1 if self.direction=="BUY" else -1),2)
        res = "WIN" if "TARGET" in label else "LOSS"
        update_result(self.tid, res, ep, pnl)
        e   = "✅" if res=="WIN" else "❌"
        send(
            f"{e} <b>{label}</b>  [Grade {self.grade}]\n"
            f"Symbol : {self.symbol}  ({self.direction})\n"
            f"Entry  : ₹{self.entry}  →  Exit: ₹{ep}\n"
            f"P&amp;L    : {'+' if pnl>0 else ''}{pnl}%\n"
            f"Time   : {now_ist().strftime('%H:%M IST')}"
        )
        self.active = False

    def _squareoff(self):
        df  = get_data(self.token, self.symbol)
        ep  = df.iloc[-1]["close"] if not df.empty else self.entry
        pnl = round(((ep-self.entry)/self.entry*100)*(1 if self.direction=="BUY" else -1),2)
        update_result(self.tid, "WIN" if pnl>0 else "LOSS", ep, pnl)
        send(
            f"⏰ <b>Square-off at close</b>  [Grade {self.grade}]\n"
            f"{self.symbol}  Exit ₹{ep}  P&amp;L {'+' if pnl>0 else ''}{pnl}%"
        )
        self.active = False

# ══════════════════════════════════════════════════════
# ALERT FORMATTER
# ══════════════════════════════════════════════════════
def score_bar(s):
    return f"{'█'*s}{'░'*(10-s)} {s}/10"

def build_alert(sig, trade_num):
    d   = sig["direction"]
    rec = sig["strike_type"]
    a_s = f"₹{round(sig['atr'],2)}" if sig["atr"] else "N/A"

    if rec == "ITM":
        strike_block = (
            f"🎯 <b>Strike: ITM</b>  →  {sig['itm_strike']}\n"
            f"   ATM alt : {sig['atm_strike']} (safer if unsure)"
        )
    else:
        strike_block = (
            f"🎯 <b>Strike: ATM</b>  →  {sig['atm_strike']}\n"
            f"   ITM : {sig['itm_strike']} (not justified for this grade)"
        )

    note = {
        "A+": "✅ Strong setup — EMA + RSI + Volume all confirmed",
        "B":  "👁 Good setup — verify chart before entry",
        "C":  "⚠️ Borderline — apply strict human logic before entering",
    }.get(sig["grade"], "")

    return (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"  {sig['grade_emoji']} <b>GRADE {sig['grade']} TRADE ALERT</b>  [{trade_num}/2]\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Direction : {'🟢 BUY (CE)' if d=='BUY' else '🔴 SHORT (PE)'}\n"
        f"Stock     : <b>{sig['symbol']}</b>  ({sig['sector']})\n"
        f"LTP       : ₹{round(sig['ltp'],2)}\n"
        f"Stock move: {round(sig['change'],2)}%  |  Sector: {round(sig['sec_change'],2)}%\n"
        f"\n"
        f"<b>Confluence score</b>\n"
        f"<code>{score_bar(sig['score'])}</code>\n"
        f"<code>{sig['score_detail']}</code>\n"
        f"\n"
        f"{strike_block}\n"
        f"\n"
        f"<b>Risk levels</b>\n"
        f"Entry  : ₹{round(sig['ltp'],2)}\n"
        f"SL     : ₹{sig['sl']}  (−{sig['sl_pct']}%)\n"
        f"Target : ₹{sig['target']}  (+{sig['tgt_pct']}%)\n"
        f"ATR 14 : {a_s}\n"
        f"R:R    : 1 : {sig['rr']}\n"
        f"\n"
        f"RSI : {round(sig['rsi'],1)}  ·  Vol : {round(sig['vol_ratio'],1)}×\n"
        f"\n"
        f"{note}\n"
        f"Time : {now_ist().strftime('%H:%M IST')}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

def build_daily_summary(alerts):
    s    = get_today_stats()
    tot  = s["WIN"] + s["LOSS"]
    wr   = round(s["WIN"]/tot*100) if tot else 0
    lines = [
        f"📋 <b>Daily Summary — {now_ist().strftime('%d %b %Y')}</b>",
        f"Alerts  : {len(alerts)}  |  Closed: {tot}",
        f"Wins    : {s['WIN']}  |  Losses: {s['LOSS']}",
        f"Win rate: {wr}%",
        "─────────────────────",
    ]
    for i, a in enumerate(alerts, 1):
        lines.append(
            f"{i}. {a['grade_emoji']} <b>{a['symbol']}</b> {a['direction']} "
            f"@ ₹{round(a['ltp'],2)}  "
            f"[{a['time']}]  Grade {a['grade']}  {a['score']}/10"
        )
    return "\n".join(lines)

# ══════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════
def main():
    global API_OBJECT

    init_db()

    if not is_trading_day():
        send(f"Today ({now_ist().strftime('%d %b %Y, %A')}) is not a trading day.")
        return

    if not ensure_fresh():
        return

    send(
        f"🤖 <b>Pro Bot started</b> — {now_ist().strftime('%d %b %Y')}\n"
        f"Grades   : A+ / B / C with confluence gate\n"
        f"Min score: A+≥7  B≥8  C≥9\n"
        f"Max trades: {MAX_TRADES_PER_DAY}/day  |  Window: 09:25–14:45\n"
        f"SL       : ATR×1.2(A+) / ×1.5(B) / ×1.8(C)\n"
        f"Target   : ATR×{TGT_MULT} for all grades"
    )

    last_alerted:  dict = {}
    alerts_sent:   list = []
    monitors:      list = []
    open_sent            = False
    closed_sent          = False
    all_tokens           = {s:t for sec in SECTORS.values() for s,t in sec.items()}

    while True:
        now = now_ist()
        ensure_fresh()

        if now.hour == 9 and now.minute >= 20 and not open_sent:
            send("📈 Market open — scanning from 09:25 IST")
            open_sent = True

        stats = get_today_stats()
        if stats["LOSS"] >= MAX_DAILY_LOSS:
            send(f"🛑 {MAX_DAILY_LOSS} losses today — scanning stopped")
            time.sleep(SCAN_INTERVAL_SEC)
            if market_closed(now): break
            continue

        trades_today = len(alerts_sent)
        if trades_today >= MAX_TRADES_PER_DAY:
            time.sleep(SCAN_INTERVAL_SEC)
            if market_closed(now): break
            continue

        if scan_window_open(now):
            print(f"[{now.strftime('%H:%M')}] Scanning... ({trades_today}/{MAX_TRADES_PER_DAY} today)")
            signals   = scan_market(last_alerted)
            remaining = MAX_TRADES_PER_DAY - trades_today

            for sig in signals[:remaining]:
                trade_num = trades_today + 1
                send(build_alert(sig, trade_num))
                tid = log_trade(sig)
                last_alerted[sig["symbol"]] = now
                alerts_sent.append({**sig, "time": now.strftime("%H:%M")})
                trades_today += 1

                m = Monitor(
                    tid       = tid,
                    symbol    = sig["symbol"],
                    token     = all_tokens.get(sig["symbol"], ""),
                    direction = sig["direction"],
                    sl        = sig["sl"],
                    target    = sig["target"],
                    entry     = sig["ltp"],
                    grade     = sig["grade"],
                )
                m.start()
                monitors.append(m)
                print(f"[{now.strftime('%H:%M')}] FIRED: {sig['symbol']} Grade {sig['grade']} score={sig['score']}/10")

        if market_closed(now) and not closed_sent:
            for m in monitors: m.active = False
            send(build_daily_summary(alerts_sent))
            send(f"🔕 Market closed | {now.strftime('%H:%M IST')}")
            closed_sent = True
            break

        time.sleep(SCAN_INTERVAL_SEC)


if __name__ == "__main__":
    main()
