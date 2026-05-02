import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from SmartApi import SmartConnect
import pyotp
import requests
import time

# ========= TELEGRAM =========
TOKEN = "8691427620:AAF5vkJmHqETtm2TyhEd6CLdozCPsa57ATg"
CHAT_ID = "890425913"

def send(msg):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
    except:
        pass

# ========= LOGIN =========
API_KEY = "VYFnGUA8"
CLIENT_ID = "M373866"
PASSWORD = "0917"
TOTP_SECRET = "3MLPA7DT7BA674CP73DHFDWJ2Q"

def login():
    for _ in range(5):
        try:
            obj = SmartConnect(api_key=API_KEY)
            totp = pyotp.TOTP(TOTP_SECRET).now()
            data = obj.generateSession(CLIENT_ID, PASSWORD, totp)

            if data.get("status"):
                obj.setAccessToken(data['data']['jwtToken'])
                return obj
        except:
            time.sleep(2)
    raise Exception("Login Failed")

# Backtest window — Angel One stores max ~60 days of 5-min data
# Use 45 days to stay safely within the window
BT_DAYS         = 45
BT_END          = datetime.now()
BT_START        = BT_END - timedelta(days=BT_DAYS)
 
# Strategy thresholds (must match live bot exactly)
MIN_SCORE       = 6
MIN_SECTOR_STR  = 0.7
MIN_STOCK_CHANGE= 1.0
RVOL_MIN        = 1.5
RSI_BULL_LO, RSI_BULL_HI = 52, 74
RSI_BEAR_LO, RSI_BEAR_HI = 26, 48
ATR_SL_MULT     = 1.5
TP1_MULT        = 1.5
TP2_MULT        = 3.0
MAX_PICKS_DAY   = 2     # max trades per day (same as live)
ENTRY_TIME      = "09:30"
EXIT_TIME       = "15:15"
 
# ==============================================================================
# ── SECTORS
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
 
NIFTY_TOKEN = "99926000"
 
# ==============================================================================
# ── TELEGRAM
# ==============================================================================
def send(msg: str) -> None:
    url = f"https://api.telegram.org/bot{TOKEN_TG}/sendMessage"
    for _ in range(3):
        try:
            r = requests.post(url,
                data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
                timeout=10)
            if r.status_code == 200:
                return
        except:
            pass
        time.sleep(2)
 
# ==============================================================================
# ── LOGIN + SAFE API CALL
# ==============================================================================
_obj = None
 
def login():
    global _obj
    obj  = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    data = obj.generateSession(CLIENT_ID, PASSWORD, totp)
    if not data.get("status"):
        raise Exception(f"Login failed: {data.get('message')}")
    obj.setAccessToken(data["data"]["jwtToken"])
    _obj = obj
    print(f"[LOGIN] OK — {datetime.now().strftime('%H:%M:%S')}")
    return obj
 
def safe_call(func, *args, **kwargs):
    global _obj
    for attempt in range(3):
        try:
            res = func(*args, **kwargs)
            if isinstance(res, dict) and res.get("errorCode") == "AG8001":
                print("[TOKEN] Expired → re-login")
                _obj = login()
                continue
            return res
        except Exception as e:
            print(f"[API attempt {attempt+1}] {e}")
            time.sleep(1)
    return None
 
# ==============================================================================
# ── DATA FETCH — chunks of 5 days (Angel One rate limit safe)
# ==============================================================================
def fetch_history(token: str, start: datetime, end: datetime) -> pd.DataFrame:
    """
    Fetch full historical 5-min data in 5-day chunks.
    Handles token expiry and retries automatically.
    Returns clean DataFrame sorted by time.
    """
    all_rows  = []
    current   = start
 
    while current < end:
        chunk_end = min(current + timedelta(days=5), end)
        params    = {
            "exchange":    "NSE",
            "symboltoken": token,
            "interval":    "FIVE_MINUTE",
            "fromdate":    current.strftime("%Y-%m-%d 09:15"),
            "todate":      chunk_end.strftime("%Y-%m-%d 15:30"),
        }
        res = safe_call(_obj.getCandleData, params)
        if res and res.get("data"):
            all_rows.extend(res["data"])
        current = chunk_end + timedelta(days=1)
        time.sleep(0.25)   # rate limit
 
    if not all_rows:
        return pd.DataFrame()
 
    df = pd.DataFrame(all_rows,
                      columns=["time","open","high","low","close","volume"])
    df["time"]   = pd.to_datetime(df["time"])
    df["date"]   = df["time"].dt.date
    df["h_min"]  = df["time"].dt.strftime("%H:%M")   # for time filtering
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna().sort_values("time").reset_index(drop=True)
    return df
 
# ==============================================================================
# ── INDICATORS (same functions as live bot — must be identical)
# ==============================================================================
 
def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50.0
    delta = np.diff(np.array(closes, dtype=float))
    gains = np.where(delta > 0, delta, 0.0)
    losses= np.where(delta < 0, -delta, 0.0)
    ag, al = np.mean(gains[:period]), np.mean(losses[:period])
    for i in range(period, len(gains)):
        ag = (ag*(period-1)+gains[i])/period
        al = (al*(period-1)+losses[i])/period
    return round(100 - 100/(1+ag/al), 2) if al else 100.0
 
def calc_ema_arr(prices, period):
    k, e = 2/(period+1), [prices[0]]
    for p in prices[1:]: e.append(p*k + e[-1]*(1-k))
    return np.array(e)
 
def calc_macd(closes):
    if len(closes) < 27:
        return {"hist": 0, "hist_prev": 0, "bull_cross": False,
                "bear_cross": False, "hist_rising": False, "hist_falling": False}
    c    = np.array(closes, dtype=float)
    fast = calc_ema_arr(c, 12)
    slow = calc_ema_arr(c, 26)
    line = fast - slow
    sig  = calc_ema_arr(line, 9)
    hist = line - sig
    return {
        "hist":        round(float(hist[-1]), 4),
        "hist_prev":   round(float(hist[-2]), 4),
        "bull_cross":  hist[-1] > 0 and hist[-2] <= 0,
        "bear_cross":  hist[-1] < 0 and hist[-2] >= 0,
        "hist_rising": hist[-1] > hist[-2],
        "hist_falling":hist[-1] < hist[-2],
    }
 
def calc_obv(closes, volumes):
    c, v = np.array(closes, dtype=float), np.array(volumes, dtype=float)
    obv  = [0.0]
    for i in range(1, len(c)):
        if c[i] > c[i-1]:   obv.append(obv[-1]+v[i])
        elif c[i] < c[i-1]: obv.append(obv[-1]-v[i])
        else:                obv.append(obv[-1])
    oa = np.array(obv)
    p  = min(10, len(oa)-1)
    k  = 2/(p+1); oe = [oa[0]]
    for x in oa[1:]: oe.append(x*k+oe[-1]*(1-k))
    return {"bull": oa[-1] > oe[-1], "bear": oa[-1] < oe[-1]}
 
def calc_rvol(volumes):
    v = np.array(volumes, dtype=float)
    if len(v) < 3: return 1.0
    avg = np.mean(v[-6:-1]) if len(v) >= 6 else np.mean(v[:-1])
    return round(v[-1]/avg, 2) if avg > 0 else 1.0
 
def calc_vwap(df_slice):
    tp = (df_slice["high"]+df_slice["low"]+df_slice["close"])/3
    tv = (tp * df_slice["volume"]).sum()
    vt = df_slice["volume"].sum()
    return round(tv/vt, 2) if vt > 0 else 0.0
 
def calc_atr(df_slice, period=14):
    if len(df_slice) < 2: return 0.0
    tr = []
    for i in range(1, len(df_slice)):
        h  = df_slice.iloc[i]["high"]
        l  = df_slice.iloc[i]["low"]
        pc = df_slice.iloc[i-1]["close"]
        tr.append(max(h-l, abs(h-pc), abs(l-pc)))
    return round(np.mean(tr[-period:]), 2) if tr else 0.0
 
def calc_adx(df_slice, period=14):
    if len(df_slice) < period+2: return 0.0
    h = df_slice["high"].values
    l = df_slice["low"].values
    c = df_slice["close"].values
    pdm, mdm, trl = [], [], []
    for i in range(1, len(c)):
        pdm.append(max(h[i]-h[i-1],0) if h[i]-h[i-1]>l[i-1]-l[i] else 0)
        mdm.append(max(l[i-1]-l[i],0) if l[i-1]-l[i]>h[i]-h[i-1] else 0)
        trl.append(max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])))
    def ws(a):
        s=[sum(a[:period])];
        [s.append(s[-1]-s[-1]/period+v) for v in a[period:]]
        return s
    a14=ws(trl); p14=ws(pdm); m14=ws(mdm)
    dx=[100*abs(p/a-m/a)/(p/a+m/a) if a and (p/a+m/a) else 0
        for p,m,a in zip(p14,m14,a14)]
    return round(sum(dx[-period:])/period, 2) if dx else 0.0
 
def calc_ema21_val(closes):
    if len(closes) < 21: return closes[-1]
    return round(float(calc_ema_arr(np.array(closes, dtype=float), 21)[-1]), 2)
 
def get_grade(score):
    if score >= 8: return "A+"
    if score >= 6: return "A"
    if score >= 5: return "B"
    return "C"
 
# ==============================================================================
# ── SIGNAL SCORER (identical logic to live bot)
# ==============================================================================
def score_bar(df_up_to_bar: pd.DataFrame, direction: str,
              sec_str: float, nifty_chg: float) -> dict:
    """Score a single entry bar using all 9 conditions."""
    closes  = df_up_to_bar["close"].values
    volumes = df_up_to_bar["volume"].values
    ltp     = float(closes[-1])
    is_bull = direction == "BUY"
 
    rsi_v  = calc_rsi(closes)
    macd_v = calc_macd(closes)
    obv_v  = calc_obv(closes, volumes)
    rvol_v = calc_rvol(volumes)
    vwap_v = calc_vwap(df_up_to_bar)
    atr_v  = calc_atr(df_up_to_bar)
    adx_v  = calc_adx(df_up_to_bar)
    ema21  = calc_ema21_val(closes)
 
    orb_h  = df_up_to_bar.iloc[:3]["high"].max() if len(df_up_to_bar) >= 3 else ltp
    orb_l  = df_up_to_bar.iloc[:3]["low"].min()  if len(df_up_to_bar) >= 3 else ltp
    orb_b  = ltp > orb_h if is_bull else ltp < orb_l
 
    L1 = macd_v["bull_cross"] or macd_v["hist_rising"]  if is_bull else \
         macd_v["bear_cross"] or macd_v["hist_falling"]
    L2 = obv_v["bull"]  if is_bull else obv_v["bear"]
    L3 = RSI_BULL_LO < rsi_v < RSI_BULL_HI if is_bull else \
         RSI_BEAR_LO < rsi_v < RSI_BEAR_HI
    L4 = rvol_v >= RVOL_MIN
    L5 = orb_b
    G1 = abs(sec_str) > MIN_SECTOR_STR
    G2 = ltp > vwap_v if is_bull else ltp < vwap_v
    G3 = adx_v > 20
    G4 = nifty_chg > 0 if is_bull else nifty_chg < 0
 
    score = sum([L1, L2, L3, L4, L5, G1, G2, G3, G4])
    grade = get_grade(score)
 
    # ATR-based SL using EMA21 as floor
    risk  = atr_v * ATR_SL_MULT
    if is_bull:
        sl  = round(max(ltp - risk, ema21), 2)
        if sl >= ltp: sl = round(ltp - risk, 2)
        tp1 = round(ltp + risk * TP1_MULT, 2)
        tp2 = round(ltp + risk * TP2_MULT, 2)
    else:
        sl  = round(min(ltp + risk, ema21), 2)
        if sl <= ltp: sl = round(ltp + risk, 2)
        tp1 = round(ltp - risk * TP1_MULT, 2)
        tp2 = round(ltp - risk * TP2_MULT, 2)
 
    return {
        "score": score, "grade": grade,
        "valid": score >= MIN_SCORE and grade in ("A+","A"),
        "ltp": ltp, "sl": sl, "tp1": tp1, "tp2": tp2,
        "atr": atr_v, "rsi": rsi_v, "adx": adx_v,
        "rvol": rvol_v, "vwap": vwap_v, "ema21": ema21,
    }
 
# ==============================================================================
# ── TRADE SIMULATOR — check SL/TP on future candles
# ==============================================================================
def simulate_trade(df_future: pd.DataFrame, direction: str,
                   entry: float, sl: float, tp1: float, tp2: float) -> dict:
    """
    Walk forward through candles after entry.
    Exit rule: 50% at TP1, remaining 50% at TP2 or SL.
    Time filter: only check candles between 09:15 and 15:15.
    Returns result, exit_price, pnl_pct, pnl_pts, exit_reason.
    """
    tp1_hit   = False
    exit_p    = entry
    result    = "TIMEOUT"    # default: closed at 3:15
 
    for _, row in df_future.iterrows():
        # Time filter — no after-hours simulation
        t = row["time"].strftime("%H:%M")
        if t < "09:15" or t > "15:15":
            continue
 
        h, l = row["high"], row["low"]
 
        if direction == "BUY":
            if not tp1_hit and h >= tp1:
                tp1_hit = True   # 50% booked at TP1
            if tp1_hit and h >= tp2:
                exit_p = tp2; result = "TP2"; break
            if l <= sl:
                exit_p = sl; result = "SL"
                # If TP1 already hit, partial win
                if tp1_hit: result = "TP1+SL"
                break
        else:
            if not tp1_hit and l <= tp1:
                tp1_hit = True
            if tp1_hit and l <= tp2:
                exit_p = tp2; result = "TP2"; break
            if h >= sl:
                exit_p = sl; result = "SL"
                if tp1_hit: result = "TP1+SL"
                break
 
    # TIMEOUT — force close at last available price
    if result == "TIMEOUT":
        exit_p  = float(df_future.iloc[-1]["close"])
        result  = "TIMEOUT"
 
    pnl_pts = round((exit_p - entry) * (1 if direction=="BUY" else -1), 2)
    pnl_pct = round(pnl_pts / entry * 100, 3) if entry else 0
 
    # Blended PnL for partial exits
    if result == "TP1+SL":
        pnl_pts = round(((tp1 - entry)*0.5 + (sl - entry)*0.5) *
                        (1 if direction=="BUY" else -1), 2)
        pnl_pct = round(pnl_pts/entry*100, 3)
 
    return {
        "result":    result,
        "exit_price":round(exit_p, 2),
        "pnl_pts":   pnl_pts,
        "pnl_pct":   pnl_pct,
        "is_win":    result in ("TP1","TP2","TP1+SL"),
    }
 
# ==============================================================================
# ── MAIN BACKTEST LOOP
# ==============================================================================
def run_backtest():
    print(f"\n{'='*55}")
    print(f"  BACKTEST: {BT_START.strftime('%d %b %Y')} → {BT_END.strftime('%d %b %Y')}")
    print(f"  Stocks  : {sum(len(v) for v in SECTORS.values())}")
    print(f"  Score gate: {MIN_SCORE}/9 | SL: ATR×{ATR_SL_MULT}")
    print(f"{'='*55}\n")
 
    send(f"<b>Backtest started</b>\n"
         f"Range: {BT_START.strftime('%d %b')} → {BT_END.strftime('%d %b %Y')}\n"
         f"Stocks: {sum(len(v) for v in SECTORS.values())} | Gate: {MIN_SCORE}/9\n"
         f"Fetching data... (~15 min)")
 
    # ── Step 1: Fetch all historical data
    market = {}
    total_stocks = sum(len(v) for v in SECTORS.values())
    fetched = 0
 
    for sec, stocks in SECTORS.items():
        for sym, tok in stocks.items():
            fetched += 1
            print(f"[{fetched}/{total_stocks}] Fetching {sym}...", end=" ")
            df = fetch_history(tok, BT_START, BT_END)
            if not df.empty:
                market[sym] = {"df": df, "sector": sec}
                print(f"{len(df)} rows")
            else:
                print("NO DATA")
            time.sleep(0.2)
 
    # Fetch NIFTY for index bias
    print("Fetching NIFTY...")
    nifty_df = fetch_history(NIFTY_TOKEN, BT_START, BT_END)
 
    send(f"✅ Loaded {len(market)}/{total_stocks} stocks\nRunning backtest...")
 
    # ── Step 2: Get all trading dates
    dates = sorted(set(d for v in market.values()
                       for d in v["df"]["date"].unique()))
    print(f"\nTrading days found: {len(dates)}")
 
    # ── Step 3: Day-by-day simulation
    trades     = []   # all trade records
    equity_pts = 0.0  # cumulative PnL in points
 
    for day in dates:
        day_pool        = []
        sector_strength = {}
        nifty_chg       = 0.0
 
        # Get NIFTY change for this day
        if not nifty_df.empty:
            nd = nifty_df[nifty_df["date"] == day]
            if len(nd) >= 2:
                nifty_chg = round(
                    (float(nd.iloc[-1]["close"]) - float(nd.iloc[0]["open"]))
                    / float(nd.iloc[0]["open"]) * 100, 2
                ) if nd.iloc[0]["open"] > 0 else 0
 
        # Compute sector strength for this day
        for sym, data in market.items():
            dd = data["df"][data["df"]["date"] == day].sort_values("time")
            if len(dd) < 4: continue
            o = float(dd.iloc[0]["open"])
            c = float(dd.iloc[3]["close"])   # 9:30 bar close (iloc[3])
            if o <= 0: continue
            chg = round((c - o) / o * 100, 3)
            sec = data["sector"]
            sector_strength.setdefault(sec, []).append(chg)
 
        sector_strength = {k: round(sum(v)/len(v), 3)
                           for k, v in sector_strength.items()}
 
        # Score each stock at 9:30 AM bar
        for sym, data in market.items():
            dd  = data["df"][data["df"]["date"] == day].sort_values("time")
            sec = data["sector"]
 
            # Need enough bars for indicators
            if len(dd) < 8: continue
 
            # Entry bar = first bar >= 09:30
            # Find the bar index where time >= 09:30
            entry_mask = dd["h_min"] >= ENTRY_TIME
            if not entry_mask.any(): continue
            entry_idx  = dd[entry_mask].index[0]
            entry_pos  = dd.index.get_loc(entry_idx)
 
            # Use all candles up to and including entry bar
            df_entry = dd.iloc[:entry_pos + 1]
            if len(df_entry) < 6: continue
 
            o   = float(dd.iloc[0]["open"])
            ltp = float(df_entry.iloc[-1]["close"])
            if o <= 0: continue
 
            chg       = round((ltp - o) / o * 100, 3)
            direction = "BUY" if chg > 0 else "SELL"
 
            if abs(chg) < MIN_STOCK_CHANGE: continue
 
            sec_str = sector_strength.get(sec, 0)
            sig     = score_bar(df_entry, direction, sec_str, nifty_chg)
 
            if sig["valid"]:
                # Future candles for trade simulation
                df_future = dd.iloc[entry_pos + 1:]
                day_pool.append({
                    "sym":      sym,
                    "sector":   sec,
                    "date":     str(day),
                    "direction":direction,
                    "sec_str":  sec_str,
                    "sig":      sig,
                    "df_future":df_future,
                    "change":   chg,
                })
 
        if not day_pool: continue
 
        # Pick top MAX_PICKS_DAY by grade then score
        grade_rank = {"A+":0,"A":1,"B":2,"C":3}
        day_pool.sort(key=lambda x: (
            grade_rank[x["sig"]["grade"]], -x["sig"]["score"],
            -abs(x["change"])
        ))
        picks = day_pool[:MAX_PICKS_DAY]
 
        for p in picks:
            sig   = p["sig"]
            trade = simulate_trade(
                p["df_future"], p["direction"],
                sig["ltp"], sig["sl"], sig["tp1"], sig["tp2"]
            )
            equity_pts += trade["pnl_pts"]
 
            record = {
                "date":      p["date"],
                "sym":       p["sym"],
                "sector":    p["sector"],
                "direction": p["direction"],
                "grade":     sig["grade"],
                "score":     sig["score"],
                "entry":     sig["ltp"],
                "sl":        sig["sl"],
                "tp1":       sig["tp1"],
                "tp2":       sig["tp2"],
                "result":    trade["result"],
                "exit_price":trade["exit_price"],
                "pnl_pts":   trade["pnl_pts"],
                "pnl_pct":   trade["pnl_pct"],
                "is_win":    trade["is_win"],
                "equity":    round(equity_pts, 2),
                # indicator snapshot
                "rsi":       sig["rsi"],
                "adx":       sig["adx"],
                "rvol":      sig["rvol"],
                "atr":       sig["atr"],
            }
            trades.append(record)
            print(f"  {p['date']} {p['direction']} {p['sym']} [{sig['grade']}] "
                  f"Score:{sig['score']}/9 → {trade['result']} {trade['pnl_pts']:+.1f}pts")
 
    # ── Step 4: Compute statistics
    df_t = pd.DataFrame(trades)
 
    if df_t.empty:
        send("❌ Backtest: No valid trades found in date range")
        return
 
    total        = len(df_t)
    wins         = df_t["is_win"].sum()
    losses       = total - wins
    win_rate     = round(wins / total * 100, 1) if total else 0
    total_pts    = round(df_t["pnl_pts"].sum(), 1)
    avg_win_pts  = round(df_t[df_t["is_win"]]["pnl_pts"].mean(), 2)  if wins   else 0
    avg_loss_pts = round(df_t[~df_t["is_win"]]["pnl_pts"].mean(), 2) if losses else 0
    profit_factor= round(abs(df_t[df_t["is_win"]]["pnl_pts"].sum() /
                             df_t[~df_t["is_win"]]["pnl_pts"].sum()), 2) \
                   if losses and df_t[~df_t["is_win"]]["pnl_pts"].sum() != 0 else 0
    # Max drawdown
    peak, dd, cur = 0, 0, 0
    for p in df_t["pnl_pts"]:
        cur  += p
        peak  = max(peak, cur)
        dd    = min(dd, cur - peak)
    max_dd = round(dd, 1)
 
    # Grade breakdown
    grade_stats = {}
    for g in ["A+","A","B"]:
        gdf = df_t[df_t["grade"] == g]
        if len(gdf) == 0: continue
        gw  = gdf["is_win"].sum()
        grade_stats[g] = {
            "total": len(gdf),
            "wins":  int(gw),
            "wr":    round(gw/len(gdf)*100, 1),
            "pts":   round(gdf["pnl_pts"].sum(), 1),
        }
 
    # Sector breakdown
    sector_stats = {}
    for sec in df_t["sector"].unique():
        sdf = df_t[df_t["sector"] == sec]
        sw  = sdf["is_win"].sum()
        sector_stats[sec] = {
            "total": len(sdf),
            "wins":  int(sw),
            "wr":    round(sw/len(sdf)*100, 1),
        }
 
    # Result distribution
    result_counts = df_t["result"].value_counts().to_dict()
 
    # Sample trade log (first 20)
    log_lines = []
    for _, row in df_t.head(20).iterrows():
        sign = "+" if row["pnl_pts"] >= 0 else ""
        log_lines.append(
            f"{row['date']} | {row['direction']} {row['sym']} [{row['grade']}] "
            f"| {row['result']} | {sign}{row['pnl_pts']}pts"
        )
 
    # ── Step 5: Save CSV
    csv_path = "backtest_results.csv"
    df_t.to_csv(csv_path, index=False)
    print(f"\n[SAVED] {csv_path}")
 
    # ── Step 6: Print + Telegram results
    summary = f"""
{'='*55}
BACKTEST RESULTS
Range  : {BT_START.strftime('%d %b %Y')} → {BT_END.strftime('%d %b %Y')}
Days   : {len(dates)} trading days
{'='*55}
OVERALL
  Trades       : {total}
  Wins         : {wins}
  Losses       : {losses}
  Win Rate     : {win_rate}%  {'✅ TARGET MET' if win_rate >= 70 else '⚠️ Below 70% target'}
  Total PnL    : {total_pts:+.1f} pts
  Avg Win      : {avg_win_pts:+.2f} pts
  Avg Loss     : {avg_loss_pts:+.2f} pts
  Profit Factor: {profit_factor}  {'✅' if profit_factor >= 1.3 else '⚠️'}
  Max Drawdown : {max_dd:.1f} pts  {'✅' if abs(max_dd) < 200 else '⚠️ High'}
{'='*55}
GRADE BREAKDOWN
"""
    for g, gs in grade_stats.items():
        summary += (f"  [{g}] {gs['total']} trades | {gs['wins']}W "
                    f"| {gs['wr']}% WR | {gs['pts']:+.1f}pts\n")
 
    summary += f"\nRESULT TYPES\n"
    for r, cnt in result_counts.items():
        summary += f"  {r:<12} : {cnt}\n"
 
    summary += f"\nSECTOR WIN RATES\n"
    for sec, ss in sorted(sector_stats.items(),
                           key=lambda x: x[1]["wr"], reverse=True):
        summary += f"  {sec:<8} : {ss['total']} trades | {ss['wr']}% WR\n"
 
    print(summary)
 
    # Send to Telegram in parts (message length limit)
    tg_main = (
        f"<b>📊 BACKTEST RESULTS</b>\n"
        f"Range: {BT_START.strftime('%d %b')} → {BT_END.strftime('%d %b %Y')}\n"
        f"Days: {len(dates)} | Stocks: {len(market)}\n\n"
        f"<b>Overall</b>\n"
        f"Trades  : {total}\n"
        f"Wins    : {wins} | Losses: {losses}\n"
        f"Win Rate: <b>{win_rate}%</b> "
        f"{'✅' if win_rate >= 70 else '⚠️ below 70%'}\n"
        f"PnL     : {total_pts:+.1f} pts\n"
        f"Avg Win : {avg_win_pts:+.2f} pts\n"
        f"Avg Loss: {avg_loss_pts:+.2f} pts\n"
        f"Prof.Factor: {profit_factor} {'✅' if profit_factor>=1.3 else '⚠️'}\n"
        f"Max DD  : {max_dd:.1f} pts\n\n"
        f"<b>Grade Breakdown</b>\n"
    )
    for g, gs in grade_stats.items():
        tg_main += f"[{g}] {gs['total']}T | {gs['wr']}% WR | {gs['pts']:+.1f}pts\n"
 
    tg_main += f"\n<b>Sector Win Rates</b>\n"
    for sec, ss in sorted(sector_stats.items(),
                           key=lambda x: x[1]["wr"], reverse=True):
        tg_main += f"{sec:<8}: {ss['wr']}% ({ss['total']} trades)\n"
 
    send(tg_main)
    time.sleep(1)
 
    send("🔥 <b>Sample Trades (first 20)</b>\n" + "\n".join(log_lines))
 
    # Final verdict
    verdict = "✅ SYSTEM READY FOR LIVE" if (win_rate >= 65 and profit_factor >= 1.2) \
              else "⚠️ NEEDS TUNING BEFORE LIVE"
    send(f"\n<b>{verdict}</b>\n"
         f"Win rate: {win_rate}% (need ≥65%)\n"
         f"PF: {profit_factor} (need ≥1.2)\n"
         f"Results saved: backtest_results.csv")
 
    print(f"\n[DONE] {verdict}")
    return df_t
 
# ==============================================================================
# ── RUN
# ==============================================================================
if __name__ == "__main__":
    _obj = login()
    send("🚀 <b>Backtest v1.0 Starting</b>\n"
         f"Range: {BT_START.strftime('%d %b')} → {BT_END.strftime('%d %b %Y')}\n"
         "Fetching real data from Angel One...\nThis takes ~15 minutes.")
    run_backtest()
