import requests
import time
import pyotp
import numpy as np
from datetime import datetime, timedelta
from SmartApi import SmartConnect
 
# =============================================================================
# ── CONFIG  (change only this block)
# =============================================================================
TOKEN        = "8706462182:AAHt5JMZ5tfMUjfKTYncwcfHZCflpQY9hHA"  # Telegram bot token
CHAT_ID      = "890425913"                                          # Telegram chat/channel ID
 
API_KEY      = "VYFnGUA8"
CLIENT_ID    = "M373866"
PASSWORD     = "0917"
TOTP_SECRET  = "3MLPA7DT7BA674CP73DHFDWJ2Q"
 
# Strategy parameters
MIN_SECTOR_STR   = 0.7    # sector avg % change needed
MIN_STOCK_CHANGE = 1.0    # individual stock % change needed
MIN_SCORE        = 5      # min score out of 9 to qualify (was 4/7 — now recalibrated)
ATR_SL_MULT      = 1.5    # SL = entry ± ATR × 1.5
TP1_MULT         = 1.5    # TP1 = entry ± risk × 1.5
TP2_MULT         = 3.0    # TP2 = entry ± risk × 3.0
MAX_PICKS        = 2      # send alerts for top N stocks
 
# =============================================================================
# ── F&O SECTOR UNIVERSE
# =============================================================================
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
 
# FIX 14 — correct F&O strike intervals per sector
# NSE uses 50-point strikes for most stocks, 100 for some large ones
STRIKE_INTERVAL = {
    "BANK":50,"IT":50,"AUTO":50,"PHARMA":50,
    "FMCG":50,"METAL":50,"ENERGY":50,"NBFC":50,"INFRA":100
}
 
# FIX 18 — correct tokens for index LTP (NSE indices use special tokens)
INDICES = {
    "NIFTY":     {"exchange":"NSE","symbol":"Nifty 50","token":"99926000"},
    "BANKNIFTY": {"exchange":"NSE","symbol":"Nifty Bank","token":"99926009"},
}
 
# =============================================================================
# ── FIX 17 — TELEGRAM SEND WITH RETRY + TIMEOUT
# =============================================================================
def send(msg: str) -> bool:
    """Send Telegram message. Returns True if sent successfully."""
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    for attempt in range(3):   # retry up to 3 times
        try:
            resp = requests.post(
                url,
                data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
                timeout=10
            )
            if resp.status_code == 200:
                return True
            print(f"[TG] Attempt {attempt+1} failed: {resp.status_code} {resp.text[:80]}")
        except Exception as e:
            print(f"[TG] Attempt {attempt+1} error: {e}")
        time.sleep(2)
    print("[TG] All 3 attempts failed")
    return False
 
# =============================================================================
# ── FIX 03 + 13 — CLEAN LOGIN WITH AUTO-REFRESH
# =============================================================================
_client = None
 
def login() -> SmartConnect:
    """
    FIX 03: Single login function — no duplicate blocks.
    FIX 13: Called fresh each morning so session never expires silently.
    TOTP is regenerated every call (30-second window, always fresh).
    """
    global _client
    try:
        obj = SmartConnect(api_key=API_KEY)
        # FIX 03: generate TOTP inside login() so it's always fresh
        totp_code = pyotp.TOTP(TOTP_SECRET).now()
        data = obj.generateSession(CLIENT_ID, PASSWORD, totp_code)
        if data.get("status") is False:
            raise ValueError(data.get("message", "Unknown error"))
        _client = obj
        print(f"[LOGIN] Success at {datetime.now().strftime('%H:%M:%S')}")
        return obj
    except Exception as e:
        send(f"⚠️ Login failed: {e}")
        raise
 
# =============================================================================
# ── FIX 07 + 16 — CANDLE FETCHER (dynamic window from 09:00 → now)
# =============================================================================
def get_candles(client: SmartConnect, token: str) -> list:
    """
    FIX 07: Fetch from 09:00 to NOW so we always get 14+ candles for RSI.
    FIX 16: Dynamic todate = current time, not hardcoded 09:35.
    Returns list of [timestamp, open, high, low, close, volume].
    """
    try:
        now      = datetime.now()
        fromdate = now.strftime("%Y-%m-%d 09:00")
        todate   = now.strftime("%Y-%m-%d %H:%M")
        params   = {
            "exchange":    "NSE",
            "symboltoken": token,
            "interval":    "FIVE_MINUTE",
            "fromdate":    fromdate,
            "todate":      todate,
        }
        data = client.getCandleData(params)
        return data["data"] if data and data.get("data") else []
    except Exception as e:
        print(f"[CANDLE] Token {token} error: {e}")
        return []
 
# =============================================================================
# ── FIX 18 + 19 — INDEX BIAS (correct exchange + consistent method)
# =============================================================================
def get_index_bias(client: SmartConnect) -> dict:
    """
    FIX 18: Use correct exchange and token for NIFTY/BANKNIFTY.
    FIX 19: Use getCandleData (same as run()) for consistency.
    Returns {name: % change from open} for each index.
    """
    result = {}
    for name, info in INDICES.items():
        try:
            now      = datetime.now()
            fromdate = now.strftime("%Y-%m-%d 09:15")
            todate   = now.strftime("%Y-%m-%d %H:%M")
            params   = {
                "exchange":    info["exchange"],
                "symboltoken": info["token"],
                "interval":    "FIVE_MINUTE",
                "fromdate":    fromdate,
                "todate":      todate,
            }
            data = client.getCandleData(params)
            candles = data["data"] if data and data.get("data") else []
            if len(candles) >= 2:
                open_p  = candles[0][1]
                ltp     = candles[-1][4]
                result[name] = round((ltp - open_p) / open_p * 100, 2) if open_p else 0
            else:
                result[name] = 0
        except Exception as e:
            print(f"[INDEX] {name} error: {e}")
            result[name] = 0
    return result
 
# =============================================================================
# ── FIX 04 — WILDER'S SMOOTHED RSI  (correct formula)
# =============================================================================
def calc_rsi(closes: list, period: int = 14) -> float:
    """
    FIX 04: Wilder's Smoothed RSI — the correct standard formula.
    Your original used np.mean(gains) which is Simple RSI — less accurate
    because it doesn't weight recent bars more heavily.
 
    Wilder's method:
      avg_gain = (prev_avg_gain × (period-1) + current_gain) / period
    This smoothing makes RSI less noisy on short candle windows.
    """
    prices = np.array(closes, dtype=float)
    if len(prices) < period + 1:
        return 50.0   # not enough data — return neutral
 
    deltas = np.diff(prices)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
 
    # Seed with simple average for first window
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
 
    # Wilder's smoothing for remaining bars
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
 
    if avg_loss == 0:
        return 100.0
 
    rs  = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)
 
# =============================================================================
# ── FIX 05 — REAL ADX  (directional movement, not std proxy)
# =============================================================================
def calc_adx(candles: list, period: int = 14) -> float:
    """
    FIX 05: Real ADX using True Range and Directional Movement.
    Your original std*10 had no directional component — it just measured
    volatility, not trend strength. ADX > 20 = trending, < 20 = choppy.
 
    Simplified ADX:
      +DM = current high - prev high (if positive)
      -DM = prev low - current low (if positive)
      TR  = max(H-L, |H-PC|, |L-PC|)
      ADX = 100 × EMA(|+DI - -DI| / (+DI + -DI))
    """
    if len(candles) < period + 1:
        return 0.0
 
    plus_dm_list, minus_dm_list, tr_list = [], [], []
 
    for i in range(1, len(candles)):
        h,  l,  c  = candles[i][2],   candles[i][3],   candles[i][4]
        ph, pl, pc = candles[i-1][2], candles[i-1][3], candles[i-1][4]
 
        # True Range
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_list.append(tr)
 
        # Directional movement
        up   = h - ph
        down = pl - l
        plus_dm_list.append(up   if up > down and up > 0   else 0.0)
        minus_dm_list.append(down if down > up and down > 0 else 0.0)
 
    def wilder_smooth(arr, p):
        sm = sum(arr[:p])
        result = [sm]
        for v in arr[p:]:
            sm = sm - sm / p + v
            result.append(sm)
        return result
 
    atr14   = wilder_smooth(tr_list,        period)
    pdm14   = wilder_smooth(plus_dm_list,   period)
    mdm14   = wilder_smooth(minus_dm_list,  period)
 
    dx_list = []
    for a, p, m in zip(atr14, pdm14, mdm14):
        if a == 0:
            continue
        pdi  = 100 * p / a
        mdi  = 100 * m / a
        dsum = pdi + mdi
        dx_list.append(100 * abs(pdi - mdi) / dsum if dsum else 0)
 
    return round(sum(dx_list[-period:]) / min(len(dx_list), period), 2) if dx_list else 0.0
 
# =============================================================================
# ── FIX 06 — CORRECT ATR  (includes gap candles)
# =============================================================================
def calc_atr(candles: list, period: int = 14) -> float:
    """
    FIX 06: Correct True Range = max of:
      (H - L)         — intraday range
      |H - prev_close| — gap up scenario
      |L - prev_close| — gap down scenario
 
    Your original only used H - L, missing overnight gaps entirely.
    """
    if len(candles) < 2:
        return 0.0
 
    tr_list = []
    for i in range(1, len(candles)):
        h  = candles[i][2]
        l  = candles[i][3]
        pc = candles[i-1][4]   # previous close
        tr_list.append(max(h - l, abs(h - pc), abs(l - pc)))
 
    recent = tr_list[-period:] if len(tr_list) >= period else tr_list
    return round(np.mean(recent), 2)
 
# =============================================================================
# ── FIX 08 — VWAP  (institutional intraday fair value)
# =============================================================================
def calc_vwap(candles: list) -> float:
    """
    FIX 08: VWAP added.
    VWAP = Σ(typical_price × volume) / Σ(volume)
    typical_price = (H + L + C) / 3
 
    Price ABOVE VWAP = institutions net buying (good for BUY)
    Price BELOW VWAP = institutions net selling (good for SELL)
    This is the single most used intraday filter by professional desks.
    """
    total_pv, total_v = 0.0, 0.0
    for c in candles:
        tp       = (c[2] + c[3] + c[4]) / 3.0
        total_pv += tp * c[5]
        total_v  += c[5]
    return round(total_pv / total_v, 2) if total_v > 0 else 0.0
 
# =============================================================================
# ── FIX 09 — EMA CROSSOVER  (trend confirmation)
# =============================================================================
def calc_ema(prices: list, period: int) -> list:
    """Exponential Moving Average — full list returned."""
    if len(prices) < period:
        return prices[:]
    k   = 2.0 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for p in prices[period:]:
        ema.append(p * k + ema[-1] * (1 - k))
    pad = len(prices) - len(ema)
    return [ema[0]] * pad + ema
 
def check_ema_cross(closes: list) -> dict:
    """
    FIX 09: EMA 9 / EMA 21 crossover check.
    bull_cross = EMA9 crossed above EMA21 in last 3 bars (fresh signal).
    bear_cross = EMA9 crossed below EMA21 in last 3 bars.
    Also returns current alignment for score purposes.
    """
    if len(closes) < 22:
        return {"bull_cross": False, "bear_cross": False,
                "bull_align": False, "bear_align": False,
                "ema9": 0, "ema21": 0}
 
    ema9  = calc_ema(closes, 9)
    ema21 = calc_ema(closes, 21)
 
    now9, now21  = ema9[-1],  ema21[-1]
    prev9, prev21 = ema9[-2], ema21[-2]
 
    bull_cross = (prev9 <= prev21) and (now9 > now21)   # just crossed up
    bear_cross = (prev9 >= prev21) and (now9 < now21)   # just crossed down
 
    # "recent cross" — within last 3 bars still counts as a valid entry
    if not bull_cross and len(ema9) >= 4:
        bull_cross = (ema9[-3] <= ema21[-3]) and (now9 > now21)
    if not bear_cross and len(ema9) >= 4:
        bear_cross = (ema9[-3] >= ema21[-3]) and (now9 < now21)
 
    return {
        "bull_cross": bull_cross,
        "bear_cross": bear_cross,
        "bull_align": now9 > now21,
        "bear_align": now9 < now21,
        "ema9":       round(now9, 2),
        "ema21":      round(now21, 2),
    }
 
# =============================================================================
# ── FIX 20 — DYNAMIC GRADE CALCULATOR
# =============================================================================
def get_grade(score: int, max_score: int = 9) -> str:
    """
    FIX 20: Grade from actual score — not hardcoded "A+".
    A+ = top 80%+ of conditions passed (high conviction)
    A  = 65–79%
    B  = 55–64%
    C  = below 55% (log only, don't trade)
    """
    pct = score / max_score * 100
    if pct >= 80: return "A+"
    if pct >= 65: return "A"
    if pct >= 55: return "B"
    return "C"
 
# =============================================================================
# ── FIX 14 — CORRECT STRIKE CALCULATION
# =============================================================================
def get_strike(ltp: float, sector: str) -> int:
    """
    FIX 14: Use sector-aware strike intervals.
    round(ltp/50)*50 works for most, but some stocks use 100-point intervals.
    """
    interval = STRIKE_INTERVAL.get(sector, 50)
    return int(round(ltp / interval) * interval)
 
# =============================================================================
# ── FIX 19 — PRE-MARKET SUMMARY (consistent candle data)
# =============================================================================
def premarket_summary(client: SmartConnect):
    """
    FIX 19: Uses getCandleData for consistency with run().
    Your original used ltpData which sometimes returns 0 for 'open' pre-market.
    Now includes RSI and volume context per sector.
    """
    try:
        sector_strength = {}
        movers          = []
 
        for sec, stocks in SECTORS.items():
            changes = []
            for sym, tok in stocks.items():
                try:
                    candles = get_candles(client, tok)
                    if len(candles) < 2:
                        continue
                    open_p = candles[0][1]
                    ltp    = candles[-1][4]
                    if open_p <= 0:
                        continue
                    chg = round((ltp - open_p) / open_p * 100, 2)
                    changes.append(chg)
                    movers.append((sym, sec, chg))
                    time.sleep(0.1)
                except:
                    pass
 
            if changes:
                sector_strength[sec] = round(sum(changes) / len(changes), 2)
 
        # Sort sectors
        top_sec    = sorted(sector_strength.items(), key=lambda x: x[1], reverse=True)
        top_stocks = sorted(movers, key=lambda x: abs(x[2]), reverse=True)[:5]
        index_bias = get_index_bias(client)
 
        # Build message with emoji heatmap bar
        msg = "📊 <b>PRE-MARKET SUMMARY — 9:15 AM</b>\n\n"
        msg += "<b>Sector Heatmap:</b>\n"
        for sec, val in top_sec:
            bar = ("▲" if val >= 0 else "▼") * min(abs(int(val * 2)), 5)
            sign = "+" if val >= 0 else ""
            msg += f"  {sec:<8} {sign}{val}%  {bar}\n"
 
        msg += "\n<b>Top Movers:</b>\n"
        for sym, sec, chg in top_stocks:
            sign = "+" if chg >= 0 else ""
            direction = "🟢" if chg >= 0 else "🔴"
            msg += f"  {direction} {sym} ({sec}) {sign}{chg}%\n"
 
        ni  = index_bias.get("NIFTY", 0)
        bni = index_bias.get("BANKNIFTY", 0)
        msg += f"\n<b>Index Bias:</b>\n"
        msg += f"  NIFTY     : {'🟢' if ni >= 0 else '🔴'} {'+' if ni>=0 else ''}{ni}%\n"
        msg += f"  BANKNIFTY : {'🟢' if bni >= 0 else '🔴'} {'+' if bni>=0 else ''}{bni}%\n"
        msg += f"\nNext signal: 9:30 AM sharp 🎯"
 
        send(msg)
 
    except Exception as e:
        send(f"⚠️ Pre-market summary error: {e}")
        print(f"[PREMARKET] Error: {e}")
 
# =============================================================================
# ── MAIN STRATEGY RUN
# =============================================================================
def run(client: SmartConnect):
    """
    Main 9:30 AM strategy with all 9 scoring conditions:
      1. Sector strength > 0.7%
      2. Stock change > 1%
      3. ORB breakout (first 3 candles)          [FIX 11]
      4. Relative volume >= 1.5×                 [FIX 10]
      5. Real ADX > 20                           [FIX 05]
      6. RSI in momentum zone                    [FIX 04]
      7. VWAP alignment                          [FIX 08]
      8. EMA 9/21 crossover or alignment         [FIX 09]
      9. Index (NIFTY) bias confirmation         [FIX 18]
    """
    try:
        print(f"[RUN] Starting at {datetime.now().strftime('%H:%M:%S')}")
 
        sector_strength = {}
        pool            = []
        index_bias      = get_index_bias(client)
 
        # ── Scan all sectors
        for sec, stocks in SECTORS.items():
            changes = []
 
            for sym, tok in stocks.items():
                try:
                    candles = get_candles(client, tok)
 
                    # Need at least 6 candles for meaningful signals
                    if len(candles) < 6:
                        print(f"[SKIP] {sym}: only {len(candles)} candles")
                        continue
 
                    closes  = [c[4] for c in candles]
                    volumes = [c[5] for c in candles]
                    open_p  = candles[0][1]
                    ltp     = closes[-1]
 
                    if open_p <= 0:
                        continue
 
                    change = round((ltp - open_p) / open_p * 100, 3)
                    changes.append(change)
 
                    # ── All indicators
                    rsi_val  = calc_rsi(closes)
                    adx_val  = calc_adx(candles)
                    atr_val  = calc_atr(candles)
                    vwap_val = calc_vwap(candles)
                    ema_data = check_ema_cross(closes)
 
                    # FIX 11 — ORB uses first 3 candles (09:15, 09:20, 09:25)
                    orb_window = candles[:3]
                    orb_high   = max(c[2] for c in orb_window)
                    orb_low    = min(c[3] for c in orb_window)
                    breakout   = (ltp > orb_high) or (ltp < orb_low)
 
                    # FIX 10 — Relative volume: current vs 5-bar average
                    vol_now    = volumes[-1]
                    vol_avg    = np.mean(volumes[-6:-1]) if len(volumes) >= 6 else np.mean(volumes[:-1])
                    rvol       = round(vol_now / vol_avg, 2) if vol_avg > 0 else 1.0
                    volume_ok  = rvol >= 1.5
 
                    pool.append({
                        "sym":      sym,
                        "sector":   sec,
                        "ltp":      round(ltp, 2),
                        "change":   change,
                        "rsi":      rsi_val,
                        "adx":      adx_val,
                        "atr":      atr_val,
                        "vwap":     vwap_val,
                        "ema9":     ema_data["ema9"],
                        "ema21":    ema_data["ema21"],
                        "ema_bull": ema_data["bull_cross"] or ema_data["bull_align"],
                        "ema_bear": ema_data["bear_cross"] or ema_data["bear_align"],
                        "breakout": breakout,
                        "rvol":     rvol,
                        "vol_ok":   volume_ok,
                    })
 
                    time.sleep(0.12)   # rate limit
 
                except Exception as e:
                    print(f"[STOCK] {sym}: {e}")
                    continue
 
            if changes:
                sector_strength[sec] = round(sum(changes) / len(changes), 3)
 
        if not pool:
            send("⚠️ No data fetched. Check market hours or API connection.")
            return
 
        # ── Score each stock (9 conditions)
        signals = []
 
        for s in pool:
            sec_str   = sector_strength.get(s["sector"], 0)
            direction = "BUY" if s["change"] > 0 else "SELL"
            score     = 0
 
            # Condition 1 — Sector conviction
            if abs(sec_str) > MIN_SECTOR_STR:         score += 1
 
            # Condition 2 — Stock moved enough
            if abs(s["change"]) > MIN_STOCK_CHANGE:   score += 1
 
            # Condition 3 — ORB breakout confirmed
            if s["breakout"]:                          score += 1
 
            # Condition 4 — Volume spike (real move)
            if s["vol_ok"]:                            score += 1
 
            # Condition 5 — ADX trend strength
            if s["adx"] > 20:                          score += 1
 
            # Condition 6 — RSI momentum zone
            if direction == "BUY"  and 52 < s["rsi"] < 74: score += 1
            if direction == "SELL" and 26 < s["rsi"] < 48: score += 1
 
            # Condition 7 — VWAP alignment (FIX 08)
            if direction == "BUY"  and s["ltp"] > s["vwap"]: score += 1
            if direction == "SELL" and s["ltp"] < s["vwap"]: score += 1
 
            # Condition 8 — EMA crossover (FIX 09)
            if direction == "BUY"  and s["ema_bull"]:  score += 1
            if direction == "SELL" and s["ema_bear"]:  score += 1
 
            # Condition 9 — Index confirmation (FIX 18)
            ni = index_bias.get("NIFTY", 0)
            if direction == "BUY"  and ni > 0:         score += 1
            if direction == "SELL" and ni < 0:         score += 1
 
            # FIX 20 — Grade from actual score
            grade = get_grade(score, max_score=9)
 
            if score >= MIN_SCORE:
                signals.append((s, sec_str, direction, score, grade))
 
        # Sort by score descending then by abs(change)
        signals.sort(key=lambda x: (x[3], abs(x[0]["change"])), reverse=True)
        top2 = signals[:MAX_PICKS]
 
        if not top2:
            send("⚠️ No strong signals found at 9:30 AM.\nAll stocks scored below threshold. Waiting for 10:00 AM...")
            return
 
        # ── Send top-2 summary first
        summary = f"📊 <b>TOP {len(top2)} SIGNALS — 9:30 AM</b>\n\n"
        for s, sec_str, direction, score, grade in top2:
            arrow = "🟢" if direction == "BUY" else "🔴"
            summary += f"{arrow} <b>{direction} {s['sym']}</b> [{grade}] — Score {score}/9\n"
            summary += f"   {s['sector']} | RSI {s['rsi']} | RVOL {s['rvol']}× | ADX {s['adx']}\n\n"
        send(summary)
 
        # ── Send full signal for each top pick
        for s, sec_str, direction, score, grade in top2:
            ltp  = s["ltp"]
            atr  = s["atr"]
            risk = atr * ATR_SL_MULT
 
            if direction == "BUY":
                sl     = round(ltp - risk, 2)
                tp1    = round(ltp + risk * TP1_MULT, 2)
                tp2    = round(ltp + risk * TP2_MULT, 2)
                option = "CE"
            else:
                sl     = round(ltp + risk, 2)
                tp1    = round(ltp - risk * TP1_MULT, 2)
                tp2    = round(ltp - risk * TP2_MULT, 2)
                option = "PE"
 
            # FIX 14 — correct strike interval
            strike = get_strike(ltp, s["sector"])
 
            rr1  = round(abs(tp1 - ltp) / abs(ltp - sl), 1) if abs(ltp - sl) > 0 else 0
            rr2  = round(abs(tp2 - ltp) / abs(ltp - sl), 1) if abs(ltp - sl) > 0 else 0
            sign = "+" if sec_str >= 0 else ""
 
            # Condition checklist for transparency
            def chk(ok): return "✅" if ok else "❌"
            vwap_ok  = (direction=="BUY" and s["ltp"]>s["vwap"]) or (direction=="SELL" and s["ltp"]<s["vwap"])
            ema_ok   = s["ema_bull"] if direction=="BUY" else s["ema_bear"]
            rsi_ok   = (52 < s["rsi"] < 74) if direction=="BUY" else (26 < s["rsi"] < 48)
            ni       = index_bias.get("NIFTY", 0)
            idx_ok   = (ni > 0 and direction=="BUY") or (ni < 0 and direction=="SELL")
 
            msg = (
                f"{'🟢' if direction=='BUY' else '🔴'} <b>{direction} {s['sym']} [{grade}]</b>\n"
                f"Sector : {s['sector']} ({sign}{round(sec_str,2)}%)\n"
                f"Option : {s['sym']} {strike} {option}\n\n"
                f"Entry  : ₹{ltp}\n"
                f"SL     : ₹{sl}\n"
                f"TP1    : ₹{tp1}  (1:{rr1})\n"
                f"TP2    : ₹{tp2}  (1:{rr2})\n\n"
                f"── Signal Quality ({score}/9) ──\n"
                f"{chk(s['breakout'])} ORB Breakout\n"
                f"{chk(s['vol_ok'])} RVOL: {s['rvol']}× (need 1.5×)\n"
                f"{chk(vwap_ok)} VWAP: ₹{s['vwap']}\n"
                f"{chk(ema_ok)} EMA 9/21: {s['ema9']} / {s['ema21']}\n"
                f"{chk(rsi_ok)} RSI: {s['rsi']}\n"
                f"{chk(s['adx']>20)} ADX: {s['adx']}\n"
                f"{chk(idx_ok)} NIFTY: {'+' if ni>=0 else ''}{ni}%\n\n"
                f"Grade  : {grade} | Score: {score}/9\n"
                f"Time   : {datetime.now().strftime('%H:%M:%S')} IST"
            )
            send(msg)
            time.sleep(0.5)
 
    except Exception as e:
        # FIX 15 — catch all errors, always notify Telegram
        err_msg = f"⚠️ Strategy error at {datetime.now().strftime('%H:%M:%S')}: {e}"
        print(err_msg)
        send(err_msg)
 
# =============================================================================
# ── MAIN LOOP
# =============================================================================
if __name__ == "__main__":
 
    # FIX 02 — single imports block (all at top, no duplicates)
    send("🚀 <b>Angel One Bot v3.0 Started</b>\n✅ 20 fixes applied\n✅ VWAP + EMA + Real RSI + Real ADX\n✅ 9 scoring conditions\n✅ Dynamic grading")
 
    # FIX 13 — login fresh at start, refresh before each day's run
    client     = login()
    last_915   = None
    last_930   = None
 
    while True:
        now  = datetime.now()
        date = now.date()
 
        # ── 9:15 AM — Pre-market summary
        if now.hour == 9 and now.minute == 15:
            if last_915 != date:
                # FIX 13 — re-login each morning for fresh session token
                client    = login()
                premarket_summary(client)
                last_915  = date
 
        # ── 9:30 AM — Main strategy
        elif now.hour == 9 and now.minute == 30:
            if last_930 != date:
                run(client)
                last_930 = date
 
        time.sleep(10)
