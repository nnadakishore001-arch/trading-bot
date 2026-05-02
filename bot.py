import pandas as pd
import numpy as np
import requests
import pyotp
import time
from datetime import datetime, timedelta
from SmartApi import SmartConnect

# ==============================================================================
# ── CONFIG — change only this block
# ==============================================================================
TOKEN_TG     = "8691427620:AAF5vkJmHqETtm2TyhEd6CLdozCPsa57ATg"
CHAT_ID      = "890425913"

API_KEY      = "VYFnGUA8"
CLIENT_ID    = "M373866"
PASSWORD     = "0917"
TOTP_SECRET  = "3MLPA7DT7BA674CP73DHFDWJ2Q"

# Strategy params
MIN_SCORE        = 6      # minimum out of 9 to place trade
MIN_SECTOR_STR   = 0.7    # sector avg % change threshold
MIN_STOCK_CHANGE = 1.0    # individual stock % change threshold
RVOL_MIN         = 1.5    # relative volume minimum multiplier
RSI_BULL_LO      = 52     # RSI lower bound for BUY
RSI_BULL_HI      = 74     # RSI upper bound for BUY (above = overbought)
RSI_BEAR_LO      = 26     # RSI lower bound for SELL (below = oversold)
RSI_BEAR_HI      = 48     # RSI upper bound for SELL
ATR_SL_MULT      = 1.5    # SL = EMA21 or ATR×this (whichever is tighter)
TP1_MULT         = 1.5    # TP1 = risk × 1.5
TP2_MULT         = 3.0    # TP2 = risk × 3.0
MAX_PICKS        = 2      # max signals per session

ENTRY_AFTER      = "09:30"   # no entries before this (market settles)
ENTRY_BEFORE     = "14:30"   # no new entries after this
FORCE_CLOSE      = "15:15"   # close all open positions by this time

# ==============================================================================
# ── SECTORS  (Angel One NSE tokens)
# ==============================================================================
SECTORS = {
    "BANK":   {"HDFCBANK":"1333","ICICIBANK":"4963","SBIN":"3045",
               "AXISBANK":"5900","KOTAKBANK":"1922","INDUSINDBK":"5258"},
    "IT":     {"TCS":"11536","INFY":"1594","HCLTECH":"7229",
               "TECHM":"13538","WIPRO":"3787","LTIM":"17818"},
    "AUTO":   {"TATAMOTORS":"3456","MARUTI":"10999","M&M":"2031",
               "BAJAJ-AUTO":"16669","EICHERMOT":"910"},
    "PHARMA": {"SUNPHARMA":"3351","CIPLA":"694","DRREDDY":"881","DIVISLAB":"10940"},
    "FMCG":   {"ITC":"1660","HINDUNILVR":"1394","NESTLEIND":"17963","BRITANNIA":"547"},
    "METAL":  {"TATASTEEL":"3499","JSWSTEEL":"11723","HINDALCO":"1363","VEDL":"3063"},
    "ENERGY": {"RELIANCE":"2885","ONGC":"2475","NTPC":"11630","POWERGRID":"14977"},
    "NBFC":   {"BAJFINANCE":"317","BAJAJFINSV":"16675","CHOLAFIN":"685"},
    "INFRA":  {"LT":"11483","ADANIPORTS":"15083","ADANIENT":"25"},
}

INDICES = {
    "NIFTY":     {"exchange":"NSE","token":"99926000"},
    "BANKNIFTY": {"exchange":"NSE","token":"99926009"},
}

# ==============================================================================
# ── TELEGRAM
# ==============================================================================
def send(msg: str) -> None:
    """Send Telegram message with 3-attempt retry."""
    url = f"https://api.telegram.org/bot{TOKEN_TG}/sendMessage"
    for attempt in range(3):
        try:
            r = requests.post(
                url,
                data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
                timeout=10
            )
            if r.status_code == 200:
                return
        except Exception as e:
            print(f"[TG attempt {attempt+1}] {e}")
        time.sleep(2)

# ==============================================================================
# ── LOGIN + AUTO TOKEN REFRESH
# ==============================================================================
_obj = None   # global client handle

def login() -> SmartConnect:
    """Login to Angel One. Called at startup and on token expiry."""
    global _obj
    obj   = SmartConnect(api_key=API_KEY)
    totp  = pyotp.TOTP(TOTP_SECRET).now()
    data  = obj.generateSession(CLIENT_ID, PASSWORD, totp)
    if not data.get("status"):
        raise Exception(f"Login failed: {data.get('message','unknown')}")
    obj.setAccessToken(data["data"]["jwtToken"])
    _obj = obj
    print(f"[LOGIN] OK at {datetime.now().strftime('%H:%M:%S')}")
    return obj


def safe_api(func, *args, **kwargs):
    """
    Wrapper that retries any API call and re-logins on token expiry (AG8001).
    Keeps your original excellent token-refresh pattern.
    """
    global _obj
    for attempt in range(3):
        try:
            result = func(*args, **kwargs)
            # Angel One token expired → refresh
            if isinstance(result, dict) and result.get("errorCode") == "AG8001":
                print("[AUTH] Token expired → re-login")
                _obj = login()
                continue
            return result
        except Exception as e:
            print(f"[API attempt {attempt+1}] {e}")
            time.sleep(1)
    return None

# ==============================================================================
# ── CANDLE FETCHER
# ==============================================================================
def get_candles(token: str, from_str: str = None, to_str: str = None) -> pd.DataFrame:
    """
    Fetch 5-minute OHLCV candles from Angel One.
    Default window: 09:00 today → now (ensures 14+ bars for RSI).
    Returns pandas DataFrame with columns: time, open, high, low, close, volume.
    """
    now      = datetime.now()
    fromdate = from_str or now.strftime("%Y-%m-%d 09:00")
    todate   = to_str   or now.strftime("%Y-%m-%d %H:%M")

    params = {
        "exchange":    "NSE",
        "symboltoken": token,
        "interval":    "FIVE_MINUTE",
        "fromdate":    fromdate,
        "todate":      todate,
    }
    res = safe_api(_obj.getCandleData, params)
    if not res or not res.get("data"):
        return pd.DataFrame()

    df = pd.DataFrame(res["data"], columns=["time","open","high","low","close","volume"])
    df["time"] = pd.to_datetime(df["time"])
    for col in ["open","high","low","close","volume"]:
        df[col] = pd.to_numeric(df[col])
    return df.sort_values("time").reset_index(drop=True)


def get_index_bias() -> dict:
    """Fetch % change of NIFTY and BANKNIFTY from open."""
    result = {}
    for name, info in INDICES.items():
        try:
            now = datetime.now()
            params = {
                "exchange": info["exchange"], "symboltoken": info["token"],
                "interval": "FIVE_MINUTE",
                "fromdate": now.strftime("%Y-%m-%d 09:15"),
                "todate":   now.strftime("%Y-%m-%d %H:%M"),
            }
            res = safe_api(_obj.getCandleData, params)
            if res and res.get("data") and len(res["data"]) >= 2:
                o = res["data"][0][1]
                c = res["data"][-1][4]
                result[name] = round((c - o) / o * 100, 2) if o else 0
            else:
                result[name] = 0
        except:
            result[name] = 0
    return result

# ==============================================================================
# ── LEADING INDICATORS  (entry signals)
# ==============================================================================

def calc_rsi(closes: np.ndarray, period: int = 14) -> float:
    """
    LEADING — Wilder's Smoothed RSI.
    Entry zone: 52–74 (BUY) or 26–48 (SELL).
    Catches momentum BEFORE price has fully extended.
    """
    if len(closes) < period + 1:
        return 50.0
    delta    = np.diff(closes)
    gains    = np.where(delta > 0, delta, 0.0)
    losses   = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    return round(100 - 100 / (1 + avg_gain / avg_loss), 2) if avg_loss else 100.0


def calc_macd(closes: np.ndarray) -> dict:
    """
    LEADING — MACD Histogram cross.
    Histogram turning positive = momentum shift to bullish (before price confirms).
    Histogram turning negative = momentum shift to bearish.
    This is a LEADING signal because it reacts to rate-of-change, not price level.
    """
    def ema(arr, p):
        k, e = 2/(p+1), [arr[0]]
        for v in arr[1:]: e.append(v*k + e[-1]*(1-k))
        return np.array(e)

    if len(closes) < 27:
        return {"hist": 0, "hist_prev": 0, "bull_cross": False, "bear_cross": False}

    fast   = ema(closes, 12)
    slow   = ema(closes, 26)
    line   = fast - slow
    signal = ema(line, 9)
    hist   = line - signal

    # Histogram cross: was negative/zero, now positive = bullish momentum starting
    bull_cross = hist[-1] > 0 and hist[-2] <= 0
    bear_cross = hist[-1] < 0 and hist[-2] >= 0

    return {
        "hist":       round(float(hist[-1]), 4),
        "hist_prev":  round(float(hist[-2]), 4),
        "bull_cross": bull_cross,
        "bear_cross": bear_cross,
        # Also useful: histogram trending up (not just crossing)
        "hist_rising":  hist[-1] > hist[-2],
        "hist_falling": hist[-1] < hist[-2],
    }


def calc_obv(closes: np.ndarray, volumes: np.ndarray) -> dict:
    """
    LEADING — On-Balance Volume vs its EMA.
    OBV rises BEFORE price when smart money accumulates quietly.
    OBV above its EMA = accumulation (BUY signal).
    OBV below its EMA = distribution (SELL signal).
    """
    obv    = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i-1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])

    obv_arr = np.array(obv)
    period  = min(10, len(obv_arr) - 1)
    k       = 2 / (period + 1)
    obv_ema = [obv_arr[0]]
    for v in obv_arr[1:]:
        obv_ema.append(v * k + obv_ema[-1] * (1 - k))

    return {
        "obv":       obv_arr[-1],
        "obv_ema":   obv_ema[-1],
        "bull":      obv_arr[-1] > obv_ema[-1],   # accumulation
        "bear":      obv_arr[-1] < obv_ema[-1],   # distribution
    }


def calc_rvol(volumes: np.ndarray) -> float:
    """
    LEADING — Relative Volume.
    Current bar volume vs 5-bar average.
    RVOL ≥ 1.5 = real breakout with participation (not a fake move).
    RVOL < 1.0 = low conviction — skip this signal.
    """
    if len(volumes) < 3:
        return 1.0
    avg = np.mean(volumes[-6:-1]) if len(volumes) >= 6 else np.mean(volumes[:-1])
    return round(volumes[-1] / avg, 2) if avg > 0 else 1.0


def calc_orb(df_day: pd.DataFrame) -> dict:
    """
    LEADING — Opening Range Breakout.
    Uses first 3 candles (09:15–09:30) to define the opening range.
    Price closing ABOVE range high = bull breakout (early trend signal).
    Price closing BELOW range low  = bear breakout.
    """
    orb_window = df_day.iloc[:3]
    orb_high   = orb_window["high"].max()
    orb_low    = orb_window["low"].min()
    ltp        = df_day.iloc[-1]["close"]
    return {
        "high":       orb_high,
        "low":        orb_low,
        "bull_break": ltp > orb_high,
        "bear_break": ltp < orb_low,
        "any_break":  ltp > orb_high or ltp < orb_low,
    }

# ==============================================================================
# ── LAGGING INDICATORS  (stoploss placement)
# ==============================================================================

def calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    """
    LAGGING — Average True Range.
    Used for SL buffer: SL = entry - ATR×1.5 (BUY) or + ATR×1.5 (SELL).
    ATR adapts SL to the stock's actual daily volatility.
    HDFCBANK with ATR=8 gets SL=12pts. TATAMOTORS ATR=25 gets SL=37.5pts.
    Fixed % SL would have been wrong for both.
    """
    if len(df) < 2:
        return 0.0
    tr = []
    for i in range(1, len(df)):
        h, l, pc = df.iloc[i]["high"], df.iloc[i]["low"], df.iloc[i-1]["close"]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    return round(np.mean(tr[-period:]), 2) if tr else 0.0


def calc_ema_series(closes: np.ndarray, period: int) -> np.ndarray:
    """EMA series for full array — returns numpy array."""
    k, ema = 2/(period+1), [closes[0]]
    for v in closes[1:]: ema.append(v*k + ema[-1]*(1-k))
    return np.array(ema)


def calc_supertrend(df: pd.DataFrame, period: int = 10, mult: float = 3.0) -> dict:
    """
    LAGGING — Supertrend line as dynamic SL.
    Once in a trade, SL trails the Supertrend line.
    Supertrend flipping direction = exit signal (trend reversal confirmed).

    Used as SL reference, NOT as entry signal (it's too slow for entry).
    """
    if len(df) < period + 1:
        return {"line": df.iloc[-1]["close"], "bull": True, "bear": False}

    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values

    # Compute ATR for Supertrend
    tr = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
          for i in range(1, len(closes))]
    atr_st = np.convolve(tr, np.ones(period)/period, mode='valid')

    ub, lb   = [], []
    direction = []
    st_line  = []

    start_idx = len(closes) - len(atr_st)
    for i, atr_v in enumerate(atr_st):
        idx  = i + start_idx
        hl2  = (highs[idx] + lows[idx]) / 2
        ub.append(hl2 + mult * atr_v)
        lb.append(hl2 - mult * atr_v)

        if i == 0:
            direction.append(1)   # start neutral
            st_line.append(ub[0])
            continue

        prev_dir = direction[-1]
        prev_st  = st_line[-1]

        if closes[idx] > prev_st:
            cur_dir = -1   # bullish (Angel One convention: -1 = bull)
            st_line.append(lb[i])
        elif closes[idx] < prev_st:
            cur_dir = 1    # bearish
            st_line.append(ub[i])
        else:
            cur_dir = prev_dir
            st_line.append(prev_st)

        direction.append(cur_dir)

    return {
        "line": round(st_line[-1], 2),
        "bull": direction[-1] == -1,
        "bear": direction[-1] == 1,
    }


def calc_vwap(df: pd.DataFrame) -> float:
    """
    LAGGING — VWAP as context filter.
    Price above VWAP = institutions net long (supports BUY entries).
    Price below VWAP = institutions net short (supports SELL entries).
    Used as a filter, not a trigger — lagging because it accumulates all day.
    """
    tp  = (df["high"] + df["low"] + df["close"]) / 3
    return round((tp * df["volume"]).sum() / df["volume"].sum(), 2) \
           if df["volume"].sum() > 0 else 0.0


def calc_ema21_sl(df: pd.DataFrame, direction: str) -> float:
    """
    LAGGING — EMA 21 as dynamic SL floor.
    For BUY: SL = max(ATR-based SL, EMA21) — price shouldn't close below EMA21
    For SELL: SL = min(ATR-based SL, EMA21) — price shouldn't close above EMA21

    EMA21 moves with price, so SL tightens as trade goes in your favour.
    This is the classic 'trailing EMA stop' used by professional traders.
    """
    if len(df) < 21:
        return df.iloc[-1]["close"]
    ema21 = calc_ema_series(df["close"].values, 21)
    return round(float(ema21[-1]), 2)


def calc_adx(df: pd.DataFrame, period: int = 14) -> float:
    """
    LAGGING — ADX for trend strength confirmation.
    > 20 = trending market (signals reliable)
    < 20 = choppy/sideways (skip all signals)
    ADX is lagging because it measures what ALREADY happened.
    """
    if len(df) < period + 2:
        return 0.0
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    pdm, mdm, tr_list = [], [], []
    for i in range(1, len(c)):
        pdm.append(max(h[i]-h[i-1], 0) if h[i]-h[i-1] > l[i-1]-l[i] else 0)
        mdm.append(max(l[i-1]-l[i], 0) if l[i-1]-l[i] > h[i]-h[i-1] else 0)
        tr_list.append(max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])))
    def ws(arr):
        s = sum(arr[:period])
        r = [s]
        for v in arr[period:]: r.append(r[-1]-r[-1]/period+v); return r
    atr14 = ws(tr_list); pdm14 = ws(pdm); mdm14 = ws(mdm)
    dx = [100*abs(p/a-m/a)/(p/a+m/a) if a and (p/a+m/a) else 0
          for p,m,a in zip(pdm14,mdm14,atr14)]
    return round(sum(dx[-period:])/period, 2) if dx else 0.0

# ==============================================================================
# ── SCORING + GRADE ENGINE  (9 conditions)
# ==============================================================================

def score_signal(df: pd.DataFrame, direction: str,
                 sector_str: float, index_bias: dict) -> dict:
    """
    Master scoring function — evaluates all 9 conditions.

    LEADING conditions (entry triggers):
      L1 — MACD histogram bullish/bearish cross
      L2 — OBV above/below EMA (smart money confirmation)
      L3 — RSI in momentum zone (not overbought or oversold)
      L4 — Relative Volume ≥ 1.5× (real participation)
      L5 — ORB breakout (opening range cleared)

    LAGGING conditions (context filters):
      G1 — Sector strength > 0.7% (tail wind)
      G2 — VWAP side alignment
      G3 — ADX > 20 (trending day)
      G4 — NIFTY index bias alignment

    Returns dict with score (0-9), grade, individual results, SL levels.
    """
    closes  = df["close"].values
    volumes = df["volume"].values
    ltp     = float(closes[-1])

    # Compute all indicators
    rsi_v    = calc_rsi(closes)
    macd_v   = calc_macd(closes)
    obv_v    = calc_obv(closes, volumes)
    rvol_v   = calc_rvol(volumes)
    orb_v    = calc_orb(df)
    vwap_v   = calc_vwap(df)
    atr_v    = calc_atr(df)
    adx_v    = calc_adx(df)
    ema21    = calc_ema21_sl(df, direction)
    st_v     = calc_supertrend(df)

    # ── LEADING conditions
    is_bull = direction == "BUY"

    L1_macd  = macd_v["bull_cross"] or macd_v["hist_rising"]  if is_bull else \
               macd_v["bear_cross"] or macd_v["hist_falling"]
    L2_obv   = obv_v["bull"]   if is_bull else obv_v["bear"]
    L3_rsi   = RSI_BULL_LO < rsi_v < RSI_BULL_HI if is_bull else \
               RSI_BEAR_LO < rsi_v < RSI_BEAR_HI
    L4_rvol  = rvol_v >= RVOL_MIN
    L5_orb   = orb_v["bull_break"] if is_bull else orb_v["bear_break"]

    # ── LAGGING / context conditions
    G1_sect  = abs(sector_str) > MIN_SECTOR_STR
    G2_vwap  = ltp > vwap_v if is_bull else ltp < vwap_v
    G3_adx   = adx_v > 20
    G4_index = index_bias.get("NIFTY", 0) > 0 if is_bull else \
               index_bias.get("NIFTY", 0) < 0

    score = sum([L1_macd, L2_obv, L3_rsi, L4_rvol, L5_orb,
                 G1_sect, G2_vwap, G3_adx, G4_index])

    # ── Dynamic grade
    if score >= 8:   grade = "A+"
    elif score >= 6: grade = "A"
    elif score >= 5: grade = "B"
    else:            grade = "C"

    # ── SL calculation: LAGGING indicators define the floor
    # BUY SL  = max of (EMA21, Supertrend, entry - ATR×mult)
    # SELL SL = min of (EMA21, Supertrend, entry + ATR×mult)
    atr_sl  = atr_v * ATR_SL_MULT
    if is_bull:
        sl_atr  = round(ltp - atr_sl, 2)
        sl_ema  = ema21              # price below EMA21 = trend broken
        sl_st   = st_v["line"] if st_v["bull"] else sl_atr
        # Take the HIGHEST of the three (tightest valid stop)
        sl      = round(max(sl_atr, sl_ema, sl_st), 2)
        # Sanity: SL must be below entry
        if sl >= ltp: sl = round(ltp - atr_sl, 2)
    else:
        sl_atr  = round(ltp + atr_sl, 2)
        sl_ema  = ema21
        sl_st   = st_v["line"] if st_v["bear"] else sl_atr
        # Take the LOWEST of the three (tightest valid stop)
        sl      = round(min(sl_atr, sl_ema, sl_st), 2)
        if sl <= ltp: sl = round(ltp + atr_sl, 2)

    risk = abs(ltp - sl)
    tp1  = round(ltp + risk * TP1_MULT, 2) if is_bull else round(ltp - risk * TP1_MULT, 2)
    tp2  = round(ltp + risk * TP2_MULT, 2) if is_bull else round(ltp - risk * TP2_MULT, 2)

    return {
        "score":   score,
        "grade":   grade,
        "valid":   score >= MIN_SCORE and grade in ("A+", "A"),
        "ltp":     round(ltp, 2),
        "sl":      sl,
        "tp1":     tp1,
        "tp2":     tp2,
        "atr":     atr_v,
        "rsi":     rsi_v,
        "adx":     adx_v,
        "rvol":    rvol_v,
        "vwap":    vwap_v,
        "ema21":   ema21,
        "st_line": st_v["line"],
        "orb_h":   orb_v["high"],
        "orb_l":   orb_v["low"],
        # Individual condition results for transparency
        "L1_macd": L1_macd, "L2_obv": L2_obv,  "L3_rsi": L3_rsi,
        "L4_rvol": L4_rvol, "L5_orb": L5_orb,
        "G1_sect": G1_sect, "G2_vwap": G2_vwap, "G3_adx": G3_adx, "G4_idx": G4_index,
    }

# ==============================================================================
# ── ALERT FORMATTER
# ==============================================================================
def format_alert(sym: str, sector: str, direction: str,
                 sec_str: float, sig: dict) -> str:
    """Build the full Telegram alert message."""

    def chk(ok): return "✅" if ok else "❌"
    sign  = "+" if sec_str >= 0 else ""
    arrow = "🟢" if direction == "BUY" else "🔴"
    option = "CE" if direction == "BUY" else "PE"
    strike = int(round(sig["ltp"] / 50) * 50)
    rr1    = round(abs(sig["tp1"] - sig["ltp"]) / max(abs(sig["ltp"] - sig["sl"]), 0.01), 1)
    rr2    = round(abs(sig["tp2"] - sig["ltp"]) / max(abs(sig["ltp"] - sig["sl"]), 0.01), 1)

    return (
        f"{arrow} <b>{direction} {sym} [{sig['grade']}]</b>\n"
        f"Sector : {sector} ({sign}{round(sec_str,2)}%)\n"
        f"Option : {sym} {strike} {option}\n\n"
        f"Entry  : ₹{sig['ltp']}\n"
        f"SL     : ₹{sig['sl']}  ← (EMA21/ST/ATR lagging SL)\n"
        f"TP1    : ₹{sig['tp1']}  (1:{rr1})\n"
        f"TP2    : ₹{sig['tp2']}  (1:{rr2})\n\n"
        f"── LEADING (Entry) ──\n"
        f"{chk(sig['L1_macd'])} MACD histogram cross\n"
        f"{chk(sig['L2_obv'])} OBV vs EMA (smart money)\n"
        f"{chk(sig['L3_rsi'])} RSI: {sig['rsi']} (zone {RSI_BULL_LO}-{RSI_BULL_HI})\n"
        f"{chk(sig['L4_rvol'])} RVOL: {sig['rvol']}× (need {RVOL_MIN}×)\n"
        f"{chk(sig['L5_orb'])} ORB breakout ({round(sig['orb_h'],1)}/{round(sig['orb_l'],1)})\n\n"
        f"── LAGGING (SL context) ──\n"
        f"{chk(sig['G2_vwap'])} VWAP: ₹{sig['vwap']}\n"
        f"  EMA21 : ₹{sig['ema21']}\n"
        f"  ST    : ₹{sig['st_line']}\n"
        f"  ATR   : {sig['atr']} pts\n"
        f"{chk(sig['G3_adx'])} ADX: {sig['adx']}\n"
        f"{chk(sig['G4_idx'])} NIFTY bias\n"
        f"{chk(sig['G1_sect'])} Sector strength\n\n"
        f"Score  : {sig['score']}/9 | Grade: {sig['grade']}\n"
        f"Time   : {datetime.now().strftime('%H:%M:%S')} IST"
    )

# ==============================================================================
# ── PRE-MARKET SUMMARY  (9:15 AM)
# ==============================================================================
def premarket_summary():
    """Quick sector scan + index bias before market open."""
    try:
        movers, sector_vals = [], {}

        for sec, stocks in SECTORS.items():
            changes = []
            for sym, tok in stocks.items():
                df = get_candles(tok)
                if df.empty or len(df) < 2: continue
                o, c = df.iloc[0]["open"], df.iloc[-1]["close"]
                if o <= 0: continue
                chg = round((c - o) / o * 100, 2)
                changes.append(chg)
                movers.append((sym, sec, chg))
                time.sleep(0.1)
            if changes: sector_vals[sec] = round(sum(changes)/len(changes), 2)

        top_sec    = sorted(sector_vals.items(), key=lambda x: x[1], reverse=True)
        top_stocks = sorted(movers, key=lambda x: abs(x[2]), reverse=True)[:5]
        idx        = get_index_bias()

        msg  = "📊 <b>PRE-MARKET — 9:15 AM</b>\n\n"
        msg += "<b>Sectors:</b>\n"
        for s, v in top_sec:
            bar  = ("▲" if v >= 0 else "▼") * min(abs(int(v * 2)), 5)
            msg += f"  {s:<8} {'+' if v>=0 else ''}{v}%  {bar}\n"
        msg += "\n<b>Top Movers:</b>\n"
        for sym, sec, chg in top_stocks:
            msg += f"  {'🟢' if chg>=0 else '🔴'} {sym} {'+' if chg>=0 else ''}{chg}%\n"
        ni, bni = idx.get("NIFTY",0), idx.get("BANKNIFTY",0)
        msg += f"\n<b>NIFTY:</b> {'🟢' if ni>=0 else '🔴'} {'+' if ni>=0 else ''}{ni}%"
        msg += f"\n<b>BANKNIFTY:</b> {'🟢' if bni>=0 else '🔴'} {'+' if bni>=0 else ''}{bni}%"
        msg += f"\n\n🎯 Signal scan at 9:30 AM"
        send(msg)
    except Exception as e:
        send(f"⚠️ Pre-market error: {e}")
        print(f"[PREMARKET] {e}")

# ==============================================================================
# ── MAIN STRATEGY  (9:30 AM)
# ==============================================================================
def run():
    """
    Main live strategy execution.
    Entry gate: 6+ out of 9 conditions.
    SL: EMA21 / Supertrend / ATR-based (whichever is tightest valid stop).
    """
    try:
        now_str = datetime.now().strftime("%H:%M")
        if now_str < ENTRY_AFTER:
            send(f"⏰ Too early — waiting until {ENTRY_AFTER}")
            return
        if now_str > ENTRY_BEFORE:
            send(f"🔒 Entry window closed ({ENTRY_BEFORE}) — no new trades")
            return

        print(f"[RUN] Starting at {datetime.now().strftime('%H:%M:%S')}")
        index_bias      = get_index_bias()
        sector_strength = {}
        pool            = []

        # ── Fetch + compute for each stock
        for sec, stocks in SECTORS.items():
            sec_changes = []
            for sym, tok in stocks.items():
                try:
                    df = get_candles(tok)
                    if df.empty or len(df) < 6:
                        continue
                    o   = float(df.iloc[0]["open"])
                    ltp = float(df.iloc[-1]["close"])
                    if o <= 0: continue
                    chg = round((ltp - o) / o * 100, 3)
                    sec_changes.append(chg)

                    if abs(chg) < MIN_STOCK_CHANGE:
                        continue

                    direction = "BUY" if chg > 0 else "SELL"
                    sig       = score_signal(df, direction,
                                             0,   # sector_str filled below
                                             index_bias)
                    pool.append({
                        "sym": sym, "sector": sec,
                        "change": chg, "direction": direction,
                        "df": df, "sig": sig,
                    })
                    time.sleep(0.12)
                except Exception as e:
                    print(f"[{sym}] {e}")
            if sec_changes:
                sector_strength[sec] = round(sum(sec_changes)/len(sec_changes), 3)

        if not pool:
            send("⚠️ No data fetched. Check connection.")
            return

        # Re-score with actual sector strength now known
        signals = []
        for s in pool:
            sec_str = sector_strength.get(s["sector"], 0)
            df      = s["df"]
            sig     = score_signal(df, s["direction"], sec_str, index_bias)
            if sig["valid"]:
                signals.append((s["sym"], s["sector"], s["direction"],
                                 sec_str, sig))

        # Sort: grade order then score then magnitude
        grade_order = {"A+": 0, "A": 1, "B": 2, "C": 3}
        signals.sort(key=lambda x: (grade_order[x[4]["grade"]], -x[4]["score"]))
        top2 = signals[:MAX_PICKS]

        if not top2:
            # Send heatmap even if no signals
            hm  = "📊 <b>Sector Heatmap 9:30 AM</b>\n"
            for sec, val in sorted(sector_strength.items(), key=lambda x: x[1], reverse=True):
                bar = ("▲" if val>=0 else "▼") * min(abs(int(val*2)), 5)
                hm += f"  {sec:<8} {'+' if val>=0 else ''}{val}%  {bar}\n"
            send(hm + "\n⚠️ No signals passed 6/9 threshold today.")
            return

        # Send sector heatmap
        hm = "📊 <b>Sector Heatmap + Signals 9:30 AM</b>\n"
        for sec, val in sorted(sector_strength.items(), key=lambda x: x[1], reverse=True):
            bar = ("▲" if val>=0 else "▼") * min(abs(int(val*2)), 5)
            hm += f"  {sec:<8} {'+' if val>=0 else ''}{val}%  {bar}\n"
        send(hm)

        # Send summary of top picks
        summary = f"🎯 <b>TOP {len(top2)} SIGNALS</b>\n\n"
        for sym, sec, direction, sec_str, sig in top2:
            arrow = "🟢" if direction == "BUY" else "🔴"
            summary += (f"{arrow} <b>{direction} {sym}</b> [{sig['grade']}] "
                        f"— {sig['score']}/9\n"
                        f"   RSI {sig['rsi']} | RVOL {sig['rvol']}× | ADX {sig['adx']}\n\n")
        send(summary)

        # Send full signal for each
        for sym, sec, direction, sec_str, sig in top2:
            alert = format_alert(sym, sec, direction, sec_str, sig)
            send(alert)
            time.sleep(0.5)

    except Exception as e:
        msg = f"⚠️ run() error: {e}"
        print(msg)
        send(msg)

# ==============================================================================
# ── MAIN LOOP
# ==============================================================================
if __name__ == "__main__":
    _obj = login()
    send(
        "🚀 <b>Angel One Bot v4.0 Live</b>\n\n"
        "LEADING indicators → Entry:\n"
        "  MACD cross, OBV, RSI, RVOL, ORB\n\n"
        "LAGGING indicators → SL:\n"
        "  EMA21, Supertrend, ATR×1.5\n\n"
        f"Entry gate: {MIN_SCORE}/9 conditions\n"
        f"Window: {ENTRY_AFTER} – {ENTRY_BEFORE}\n"
        f"Force close: {FORCE_CLOSE}"
    )

    last_premarket = None
    last_run       = None

    while True:
        now  = datetime.now()
        date = now.date()
        t    = now.strftime("%H:%M")

        # 9:15 AM pre-market
        if t == "09:15" and last_premarket != date:
            _obj = login()   # fresh session each morning
            premarket_summary()
            last_premarket = date

        # 9:30 AM main scan
        elif t == "09:30" and last_run != date:
            run()
            last_run = date

        # 3:15 PM force-close reminder
        elif t == FORCE_CLOSE:
            send(f"⏰ <b>{FORCE_CLOSE} — Square off all positions now!</b>")

        time.sleep(10)
