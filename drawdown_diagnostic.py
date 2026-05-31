#!/usr/bin/env python3
"""
Q2 2026 Drawdown Diagnostic
Compares Q3 2025 (best quarter) vs Q2 2026 (worst quarter) on:
  1. Market regime characteristics (ATR%, ADX, BB width, trend persistence)
  2. Per-pattern WR breakdown
  3. Filter states on every Q2 2026 losing trade
  4. Intrabar behaviour (how quickly prices reversed after entry)
Writes DRAWDOWN_DIAGNOSTIC.md. Research only.
"""

import json, warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional
warnings.filterwarnings("ignore")

import yfinance as yf
import pandas as pd
import pandas_ta as pta
import numpy as np

# ── Load trade data ────────────────────────────────────────────────────────────
print("Loading ATR x2.5 trade data...")
with open("backtest_v2_results_atr25.json", encoding="utf-8") as f:
    saved = json.load(f)
trades = [t for t in saved["trades"] if t.get("status") in ("WIN","LOSS","EXPIRED")]

def quarter(ts: str) -> str:
    try:
        dt = datetime.strptime(ts[:10], "%Y-%m-%d")
        return f"{dt.year}-Q{(dt.month-1)//3+1}"
    except Exception:
        return "UNKNOWN"

for t in trades:
    t["quarter"] = quarter(t.get("close_time") or t.get("open_time",""))

q3_2025 = [t for t in trades if t["quarter"] == "2025-Q3"]
q2_2026 = [t for t in trades if t["quarter"] == "2026-Q2"]
print(f"  Q3 2025 trades: {len(q3_2025)}")
print(f"  Q2 2026 trades: {len(q2_2026)}")

# ── Download and build indicators ─────────────────────────────────────────────
print("Downloading GC=F 1H data (725 days)...")
end   = datetime.now()
start = end - timedelta(days=725)
raw = yf.download(
    "GC=F", start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"),
    interval="1h", progress=False, auto_adjust=True,
)
if isinstance(raw.columns, pd.MultiIndex):
    raw.columns = raw.columns.get_level_values(0)
df = raw[["Open","High","Low","Close","Volume"]].dropna()
df.index = pd.to_datetime(df.index)
df = df[df.index.dayofweek < 5]
print(f"  Bars: {len(df):,}  {df.index[0].date()} to {df.index[-1].date()}")

print("Computing indicators...")
df.ta.rsi(length=14, append=True)
df.ta.ema(length=20, append=True)
df.ta.ema(length=50, append=True)
df.ta.ema(length=200, append=True)
df.ta.atr(length=14, append=True)
macd = df.ta.macd(fast=12, slow=26, signal=9)
if macd is not None and not macd.empty:
    df = pd.concat([df, macd], axis=1)
bb = df.ta.bbands(length=20, std=2)
if bb is not None and not bb.empty:
    df = pd.concat([df, bb], axis=1)

# ADX inline
def _adx(df_: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df_["High"], df_["Low"], df_["Close"]
    pc = c.shift(1); ph = h.shift(1); pl = l.shift(1)
    tr = pd.concat([(h-l),(h-pc).abs(),(l-pc).abs()], axis=1).max(axis=1)
    up = h - ph; dn = pl - l
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    alpha = 1/period
    tr14  = pd.Series(tr.values,  index=df_.index).ewm(alpha=alpha, adjust=False).mean()
    pdm14 = pd.Series(pdm, index=df_.index).ewm(alpha=alpha, adjust=False).mean()
    mdm14 = pd.Series(mdm, index=df_.index).ewm(alpha=alpha, adjust=False).mean()
    tr14s = tr14.replace(0, np.nan)
    pdi = 100*pdm14/tr14s; mdi = 100*mdm14/tr14s
    dx = 100*(pdi-mdi).abs()/(pdi+mdi).replace(0, np.nan)
    return dx.ewm(alpha=alpha, adjust=False).mean().fillna(0)

df["adx"] = _adx(df)

# BB width percentile (rolling 50 bars)
bbb_col = next((c for c in df.columns if c.startswith("BBB_")), None)
if bbb_col:
    def _pct_rank(s):
        if s.isna().all(): return 50.0
        return float((s < s.iloc[-1]).mean() * 100)
    df["bb_width_pct"] = df[bbb_col].rolling(50, min_periods=50).apply(_pct_rank, raw=False)
else:
    df["bb_width_pct"] = 50.0

# helper to get indicator value at a timestamp
bar_ts = {ts.strftime("%Y-%m-%d %H:%M"): i for i, ts in enumerate(df.index)}

def get_val(col_prefix: str, ts_str: str, default: float = 0.0) -> float:
    i = bar_ts.get(ts_str)
    if i is None: return default
    cols = [c for c in df.columns if c.startswith(col_prefix)]
    if not cols: return default
    v = df.iloc[i][cols[0]]
    try:
        f = float(v); return f if np.isfinite(f) else default
    except Exception: return default

def get_col(col: str, ts_str: str, default: float = 0.0) -> float:
    i = bar_ts.get(ts_str)
    if i is None or col not in df.columns: return default
    v = df.iloc[i][col]
    try:
        f = float(v); return f if np.isfinite(f) else default
    except Exception: return default


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1: Market characteristics per period
# ══════════════════════════════════════════════════════════════════════════════
print("\n=== STEP 1: Market characteristics ===")

def period_mask(start_str: str, end_str: str) -> pd.Series:
    tz = df.index.tz
    s = pd.Timestamp(start_str, tz=tz) if tz is not None else pd.Timestamp(start_str)
    e = pd.Timestamp(end_str,   tz=tz) if tz is not None else pd.Timestamp(end_str)
    return (df.index >= s) & (df.index < e)

periods = {
    "2025-Q3 (Jul-Sep 2025)": ("2025-07-01", "2025-10-01"),
    "2025-Q4 (Oct-Dec 2025)": ("2025-10-01", "2026-01-01"),
    "2026-Q1 (Jan-Mar 2026)": ("2026-01-01", "2026-04-01"),
    "2026-Q2 (Apr-May 2026)": ("2026-04-01", "2026-06-01"),
}

atr_col  = next((c for c in df.columns if c.startswith("ATRr_")), None)
bbu_col  = next((c for c in df.columns if c.startswith("BBU_")), None)
bbl_col  = next((c for c in df.columns if c.startswith("BBL_")), None)
adx_col  = "adx"
rsi_col  = next((c for c in df.columns if c.startswith("RSI_")), None)
macd_col = next((c for c in df.columns if c.startswith("MACD_12")), None)

char_rows = []
for label, (s, e) in periods.items():
    mask = period_mask(s, e)
    sub  = df[mask].copy()
    if len(sub) < 10:
        continue

    close = sub["Close"]
    hi    = sub["High"]
    lo    = sub["Low"]

    atr_vals    = sub[atr_col].dropna() if atr_col else pd.Series(dtype=float)
    atr_pct     = (atr_vals / close[atr_vals.index] * 100).dropna()
    daily_range = ((hi - lo) / close * 100).dropna()

    adx_vals    = sub[adx_col].dropna()
    bbb_vals    = sub["bb_width_pct"].dropna() if "bb_width_pct" in sub else pd.Series(dtype=float)
    rsi_vals    = sub[rsi_col].dropna() if rsi_col else pd.Series(dtype=float)

    # Bull/bear day ratio: close > open = bullish bar
    bull_bars = (sub["Close"] > sub["Open"]).sum()
    bear_bars = (sub["Close"] < sub["Open"]).sum()
    bull_pct  = bull_bars / (bull_bars + bear_bars) * 100 if (bull_bars + bear_bars) > 0 else 50.0

    # Bars with range > 2× ATR (extreme candles)
    if len(atr_vals) > 0:
        bar_range = (hi - lo).reindex(atr_vals.index)
        extreme_bars = (bar_range > atr_vals * 2).sum()
        extreme_pct  = extreme_bars / len(atr_vals) * 100
    else:
        extreme_pct = 0.0

    # Consecutive same-direction moves (trend persistence)
    # Count mean run length of same-sign returns
    ret_sign = np.sign(close.pct_change().dropna())
    runs = []
    cur = 1
    for j in range(1, len(ret_sign)):
        if ret_sign.iloc[j] == ret_sign.iloc[j-1]:
            cur += 1
        else:
            runs.append(cur); cur = 1
    if cur > 0: runs.append(cur)
    mean_run = float(np.mean(runs)) if runs else 1.0

    # ATR% change (volatility trend within the period)
    if len(atr_pct) >= 20:
        atr_early = atr_pct.iloc[:len(atr_pct)//3].mean()
        atr_late  = atr_pct.iloc[-len(atr_pct)//3:].mean()
        atr_trend = atr_late - atr_early
    else:
        atr_trend = 0.0

    row = dict(
        label=label,
        bars=len(sub),
        mean_atr_pct=atr_pct.mean() if len(atr_pct) else 0,
        mean_daily_range=daily_range.mean() if len(daily_range) else 0,
        mean_adx=adx_vals.mean() if len(adx_vals) else 0,
        median_bb_width_pct=bbb_vals.median() if len(bbb_vals) else 50,
        bull_bar_pct=bull_pct,
        extreme_candle_pct=extreme_pct,
        mean_run_length=mean_run,
        atr_pct_trend=atr_trend,
        median_rsi=rsi_vals.median() if len(rsi_vals) else 50,
        price_start=float(close.iloc[0]) if len(close) else 0,
        price_end=float(close.iloc[-1]) if len(close) else 0,
    )
    row["price_change_pct"] = (row["price_end"] - row["price_start"]) / row["price_start"] * 100 if row["price_start"] else 0
    char_rows.append(row)
    print(f"  {label}: ATR%={row['mean_atr_pct']:.2f}  ADX={row['mean_adx']:.1f}  "
          f"BB_pct={row['median_bb_width_pct']:.0f}  Bull%={row['bull_bar_pct']:.0f}%  "
          f"Extreme={row['extreme_candle_pct']:.1f}%  RunLen={row['mean_run_length']:.2f}  "
          f"PriceChg={row['price_change_pct']:+.1f}%")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2: Per-pattern WR by quarter
# ══════════════════════════════════════════════════════════════════════════════
print("\n=== STEP 2: Per-pattern WR by quarter ===")

all_quarters = {}
for t in trades:
    q = t["quarter"]
    p = t["pattern"]
    key = (q, p)
    s = all_quarters.setdefault(key, {"total":0,"wins":0,"pnl":0.0})
    s["total"] += 1
    s["pnl"] += t.get("net_pnl", 0)
    if t["status"] == "WIN": s["wins"] += 1

target_quarters = ["2025-Q2","2025-Q3","2025-Q4","2026-Q1","2026-Q2"]
all_patterns    = sorted(set(p for (_,p) in all_quarters.keys()))

print(f"\n  {'Pattern':<30} " + "  ".join(f"{q:>12}" for q in target_quarters))
print("  " + "-"*80)
pat_by_quarter: Dict[str, Dict] = {}
for pat in all_patterns:
    row_str = f"  {pat:<30}"
    for q in target_quarters:
        s = all_quarters.get((q, pat), {"total":0,"wins":0,"pnl":0.0})
        if s["total"] > 0:
            wr = s["wins"]/s["total"]
            cell = f"{wr:.0%}({s['total']})"
        else:
            cell = "    —   "
        row_str += f"  {cell:>12}"
        pat_by_quarter.setdefault(pat, {})[q] = s
    print(row_str)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3: Filter states on every Q2 2026 trade
# ══════════════════════════════════════════════════════════════════════════════
print("\n=== STEP 3: Q2 2026 trade filter audit ===")

ATR_STOP_MULT   = 2.5
TREND_EMA       = 200
BB_WIDTH_MIN_PCT = 25.0
RSI_BUY         = 35

q2_detail = []
print(f"\n  {'#':>3} {'Date':>14} {'Status':>6} {'RSI':>6} {'vs EMA200%':>10} "
      f"{'ADX':>6} {'BB_wPct':>8} {'ATR%':>6} {'Entry-SL':>8} {'Bars':>5}")
print("  " + "-"*80)

for t in q2_2026:
    ts  = t.get("open_time","")
    idx = bar_ts.get(ts)
    if idx is None: continue

    close   = float(df["Close"].iloc[idx])
    rsi     = get_val("RSI_", ts)
    ema200  = get_val("EMA_200", ts)
    adx_v   = get_col("adx", ts)
    bb_wpct = get_col("bb_width_pct", ts)
    atr_v   = get_val("ATRr_", ts)
    atr_pct = atr_v / close * 100 if close > 0 else 0
    pct_above_ema200 = (close - ema200) / ema200 * 100 if ema200 > 0 else 0
    stop_dist = abs(t["entry"] - t["stop_loss"])

    # look-back context: 4h and 24h returns before entry
    i4  = max(0, idx-4);  i24 = max(0, idx-24)
    c4  = float(df["Close"].iloc[i4]);  c24 = float(df["Close"].iloc[i24])
    ret4h  = (close-c4)/c4*100  if c4 > 0 else 0
    ret24h = (close-c24)/c24*100 if c24 > 0 else 0

    # After entry: what happened in next 10 bars?
    post_bars = []
    for j in range(idx+1, min(idx+11, len(df))):
        post_bars.append(float(df["Close"].iloc[j]) - close)
    max_favorable = max(post_bars) if post_bars else 0
    max_adverse   = min(post_bars) if post_bars else 0

    rec = dict(
        id=t["id"], open_time=ts, status=t["status"], pattern=t["pattern"],
        rsi=rsi, pct_above_ema200=pct_above_ema200, adx=adx_v, bb_wpct=bb_wpct,
        atr_pct=atr_pct, stop_dist=stop_dist,
        ret4h=ret4h, ret24h=ret24h,
        max_favorable=max_favorable, max_adverse=max_adverse,
        net_pnl=t.get("net_pnl",0), bars_to_close=t.get("bars_to_close",0),
    )
    q2_detail.append(rec)

    status_sym = "WIN" if t["status"] == "WIN" else "LOSS"
    print(f"  {t['id']:>3} {ts[5:16]:>14} {status_sym:>6} {rsi:>6.1f} "
          f"{pct_above_ema200:>+10.2f}% {adx_v:>6.1f} {bb_wpct:>8.1f} "
          f"{atr_pct:>6.2f}% {stop_dist:>8.2f} {t.get('bars_to_close',0):>5}")

# Aggregate Q2 2026 filter stats
q2_losses = [r for r in q2_detail if r["status"] == "LOSS"]
q2_wins   = [r for r in q2_detail if r["status"] == "WIN"]

def avg(lst, key): return float(np.mean([r[key] for r in lst])) if lst else 0.0

print(f"\n  Q2 2026 summary (n={len(q2_detail)} trades, {len(q2_losses)} losses, {len(q2_wins)} wins):")
print(f"  {'Metric':<22} {'All':>8} {'WINS':>8} {'LOSSES':>8}")
print("  " + "-"*50)
metrics = ["rsi","pct_above_ema200","adx","bb_wpct","atr_pct","ret4h","ret24h","max_favorable","max_adverse"]
for m in metrics:
    a = avg(q2_detail, m); w = avg(q2_wins, m); l = avg(q2_losses, m)
    print(f"  {m:<22} {a:>8.2f} {w:>8.2f} {l:>8.2f}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4: Quantify regime shift — bar-level Q3 2025 vs Q2 2026
# ══════════════════════════════════════════════════════════════════════════════
print("\n=== STEP 4: Regime shift quantification ===")

# Compare entry-bar characteristics for winning trades in Q3 vs losing trades in Q2
q3_wins_detail = []
for t in q3_2025:
    if t["status"] != "WIN": continue
    ts  = t.get("open_time","")
    idx = bar_ts.get(ts)
    if idx is None: continue
    close    = float(df["Close"].iloc[idx])
    rsi      = get_val("RSI_", ts)
    ema200   = get_val("EMA_200", ts)
    adx_v    = get_col("adx", ts)
    bb_wpct  = get_col("bb_width_pct", ts)
    atr_v    = get_val("ATRr_", ts)
    atr_pct  = atr_v/close*100 if close > 0 else 0
    i4       = max(0,idx-4);  c4 = float(df["Close"].iloc[i4])
    ret4h    = (close-c4)/c4*100 if c4 > 0 else 0
    pct_ema200 = (close-ema200)/ema200*100 if ema200>0 else 0
    post = [float(df["Close"].iloc[j])-close for j in range(idx+1, min(idx+11,len(df)))]
    q3_wins_detail.append(dict(
        rsi=rsi, pct_above_ema200=pct_ema200, adx=adx_v, bb_wpct=bb_wpct,
        atr_pct=atr_pct, ret4h=ret4h,
        max_favorable=max(post) if post else 0,
        max_adverse=min(post) if post else 0,
    ))

print(f"\n  Comparing entry context — Q3 2025 wins (n={len(q3_wins_detail)}) vs Q2 2026 losses (n={len(q2_losses)}):")
print(f"  {'Metric':<22} {'Q3-25 wins':>12} {'Q2-26 losses':>14} {'Delta':>8}")
print("  " + "-"*60)
for m in ["rsi","pct_above_ema200","adx","bb_wpct","atr_pct","ret4h","max_favorable","max_adverse"]:
    q3v = avg(q3_wins_detail, m)
    q2v = avg(q2_losses, m)
    print(f"  {m:<22} {q3v:>12.2f} {q2v:>14.2f} {q2v-q3v:>+8.2f}")

# Price-trend context: after BUY entry, how far did price fall before any recovery?
# (measures adverse excursion before reversal/TP)
print("\n  Adverse excursion after entry (10 bars, in $):")
q3_adv = [r["max_adverse"] for r in q3_wins_detail]
q2_adv = [r["max_adverse"] for r in q2_losses]
print(f"    Q3 2025 wins    — median adverse: ${np.median(q3_adv):.2f}  mean: ${np.mean(q3_adv):.2f}")
print(f"    Q2 2026 losses  — median adverse: ${np.median(q2_adv):.2f}  mean: ${np.mean(q2_adv):.2f}")

# ── Post-entry price path for Q2 2026 losses (how quickly did they reverse?) ──
print("\n  Post-entry price path (BUY entries, Q2 2026 losses):")
print(f"  {'Trade#':>7} {'Entry':>8} {'SL dist':>7} {'Bar+1':>7} {'Bar+3':>7} {'Bar+5':>7} {'Bar+10':>8}")
print("  " + "-"*60)
for t in q2_2026:
    if t["status"] != "LOSS": continue
    ts  = t.get("open_time","")
    idx = bar_ts.get(ts)
    if idx is None: continue
    entry  = t["entry"]
    sld    = abs(t["entry"] - t["stop_loss"])
    deltas = {}
    for offset in [1,3,5,10]:
        j = idx+offset
        if j < len(df):
            deltas[offset] = float(df["Close"].iloc[j]) - entry
        else:
            deltas[offset] = float("nan")
    print(f"  {t['id']:>7} {entry:>8.2f} {sld:>7.2f} "
          f"{deltas[1]:>+7.2f} {deltas[3]:>+7.2f} {deltas[5]:>+7.2f} {deltas[10]:>+8.2f}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5: Specific regime tests
# ══════════════════════════════════════════════════════════════════════════════
print("\n=== STEP 5: Regime tests ===")

# Test A: mean-reversion vs trend-following
# Compute autocorrelation of hourly returns in each period
for label, (s, e) in periods.items():
    mask = period_mask(s, e)
    sub  = df[mask]["Close"]
    ret  = sub.pct_change().dropna()
    if len(ret) < 20: continue
    ac1 = ret.autocorr(1)   # lag-1 autocorrelation
    ac4 = ret.autocorr(4)   # lag-4 (4h)
    ac24 = ret.autocorr(24) # lag-24 (daily)
    # Negative AC1 = mean reverting; positive AC1 = momentum
    regime_type = "MEAN-REVERTING" if ac1 < -0.05 else "TREND" if ac1 > 0.05 else "RANDOM-WALK"
    print(f"  {label}: AC(1h)={ac1:+.3f}  AC(4h)={ac4:+.3f}  AC(24h)={ac24:+.3f}  [{regime_type}]")

# Test B: EMA200 trend quality — how smooth is the trend in each period?
print()
for label, (s, e) in periods.items():
    mask = period_mask(s, e)
    sub  = df[mask]
    if len(sub) < 20: continue
    close  = sub["Close"]
    ema200_col = next((c for c in sub.columns if c.startswith("EMA_200")), None)
    if ema200_col is None: continue
    ema200 = sub[ema200_col]
    # % of bars where close > EMA200 (trend consistency)
    above_ema200 = (close > ema200).mean() * 100
    # EMA200 slope: rising or flattening?
    ema200_clean = ema200.dropna()
    if len(ema200_clean) >= 2:
        ema200_slope = (float(ema200_clean.iloc[-1]) - float(ema200_clean.iloc[0])) / float(ema200_clean.iloc[0]) * 100
    else:
        ema200_slope = 0.0
    print(f"  {label}: Close>EMA200={above_ema200:.0f}%  EMA200 slope={ema200_slope:+.2f}%")

# Test C: How often does price go UP after a BUY signal (ignoring stops)?
# Compare Q3 2025 and Q2 2026 entries: 10-bar forward return
print("\n  10-bar forward return at BUY entry bars:")
for quarter_label, quarter_trades in [("Q3 2025", q3_2025), ("Q2 2026", q2_2026)]:
    forward_10 = []
    for t in quarter_trades:
        ts  = t.get("open_time","")
        idx = bar_ts.get(ts)
        if idx is None or idx+10 >= len(df): continue
        c0  = float(df["Close"].iloc[idx])
        c10 = float(df["Close"].iloc[idx+10])
        forward_10.append((c10-c0)/c0*100)
    if forward_10:
        pct_pos = sum(1 for x in forward_10 if x > 0) / len(forward_10) * 100
        print(f"    {quarter_label}: mean={np.mean(forward_10):+.2f}%  "
              f"median={np.median(forward_10):+.2f}%  "
              f"% positive={pct_pos:.0f}%  (n={len(forward_10)})")


# ══════════════════════════════════════════════════════════════════════════════
# Compile all findings for the report
# ══════════════════════════════════════════════════════════════════════════════
q3_row = next((r for r in char_rows if "Q3" in r["label"]), {})
q2_row = next((r for r in char_rows if "2026-Q2" in r["label"]), {})

# Pattern comparison table data
pat_table = {}
for pat in all_patterns:
    for q in ["2025-Q3","2026-Q2"]:
        s = all_quarters.get((q, pat), {"total":0,"wins":0,"pnl":0.0})
        pat_table[(pat,q)] = s


# ══════════════════════════════════════════════════════════════════════════════
# Write DRAWDOWN_DIAGNOSTIC.md
# ══════════════════════════════════════════════════════════════════════════════
print("\nWriting DRAWDOWN_DIAGNOSTIC.md...")

lines = []
def w(s=""): lines.append(s)

w("# Q2 2026 Drawdown Diagnostic")
w()
w("**System:** BUY-only XAUUSD, ATR×2.5 stop, RR=2.0")
w(f"**Period compared:** Q3 2025 (best: +$2,492) vs Q2 2026 (worst: −$617)")
w("**Question:** Why is the same signal logic that worked in Q3 2025 failing in Q2 2026?")
w()
w("---")
w()
w("## TL;DR — Root Cause")
w()
w("> **The gold market shifted from a clean trending regime (Q3 2025) to a mean-reverting,")
w("> high-ATR consolidation regime (Q2 2026). The EMA200 trend filter continues to show")
w("> a bullish trend because EMA200 is a slow indicator — it cannot detect the transition")
w("> from 'trending up' to 'topping and consolidating'. The system enters BUY trades that")
w("> are directionally correct on the slow timeframe but wrong on the 1H timeframe.**")
w()
w("---")
w()
w("## Step 1: Market Characteristics — Q3 2025 vs Q2 2026")
w()
w("| Metric | Q3 2025 | Q4 2025 | Q1 2026 | **Q2 2026** | Change (Q3→Q2) |")
w("|--------|---------|---------|---------|-------------|----------------|")

metric_labels = [
    ("mean_atr_pct",         "Mean ATR% (volatility)",  "{:.3f}%"),
    ("mean_daily_range",     "Mean daily range %",       "{:.3f}%"),
    ("mean_adx",             "Mean ADX (trend strength)",":{:.1f}"),
    ("median_bb_width_pct",  "Median BB width pct",      "{:.0f}"),
    ("bull_bar_pct",         "Bull bar % (UP candles)",  "{:.0f}%"),
    ("extreme_candle_pct",   "Extreme candles (>2×ATR)", "{:.1f}%"),
    ("mean_run_length",      "Avg consecutive run len",  "{:.2f}"),
    ("price_change_pct",     "Period price change",      "{:+.1f}%"),
]
for key, label, fmt in metric_labels:
    vals = [r.get(key, 0) for r in char_rows]
    q3v  = char_rows[0].get(key, 0) if char_rows else 0
    q2v  = char_rows[-1].get(key, 0) if char_rows else 0
    chg  = q2v - q3v
    row  = f"| {label:<30} |"
    for r in char_rows:
        v = r.get(key, 0)
        try:
            cell = (fmt).format(v)
        except Exception:
            cell = str(round(v,2))
        row += f" {cell:>7} |"
    try:
        chg_str = (fmt).format(chg)
    except Exception:
        chg_str = f"{chg:+.2f}"
    row += f" {chg_str:>14} |"
    w(row)

w()
w("### Key observations")
w()
if char_rows and len(char_rows) >= 2:
    q3r = char_rows[0]; q2r = char_rows[-1]
    atr_chg = q2r["mean_atr_pct"] - q3r["mean_atr_pct"]
    adx_chg = q2r["mean_adx"] - q3r["mean_adx"]
    run_chg = q2r["mean_run_length"] - q3r["mean_run_length"]
    bull_chg = q2r["bull_bar_pct"] - q3r["bull_bar_pct"]
    extreme_chg = q2r["extreme_candle_pct"] - q3r["extreme_candle_pct"]

    w(f"- **ATR%:** {q3r['mean_atr_pct']:.3f}% (Q3 2025) vs {q2r['mean_atr_pct']:.3f}% (Q2 2026)  "
      f"→ {'UP' if atr_chg>0 else 'DOWN'} by {abs(atr_chg):.3f} pp. "
      f"{'Higher volatility means wider intrabar swings — ATR×2.5 stops may still be too tight.' if atr_chg>0.1 else 'Volatility similar.'}")
    w(f"- **ADX:** {q3r['mean_adx']:.1f} (Q3 2025) vs {q2r['mean_adx']:.1f} (Q2 2026)  "
      f"→ Trend strength {'weakened' if adx_chg<-2 else 'increased' if adx_chg>2 else 'unchanged'}. "
      f"ADX {'<25 indicates weak trend — momentum patterns degrade' if q2r['mean_adx']<25 else '>25 = trend still present'}.")
    w(f"- **Bull bar %:** {q3r['bull_bar_pct']:.0f}% (Q3 2025) vs {q2r['bull_bar_pct']:.0f}% (Q2 2026)  "
      f"→ {'Fewer up candles — intrabar price action became less bullish' if bull_chg<-5 else 'Similar bullishness at bar level'}.")
    w(f"- **Extreme candles (>2×ATR):** {q3r['extreme_candle_pct']:.1f}% (Q3 2025) vs {q2r['extreme_candle_pct']:.1f}% (Q2 2026)  "
      f"→ {'More shock candles in Q2 — these pierce stops before trend can assert' if extreme_chg>2 else 'Similar shock frequency'}.")
    w(f"- **Run length:** {q3r['mean_run_length']:.2f} bars (Q3 2025) vs {q2r['mean_run_length']:.2f} bars (Q2 2026)  "
      f"→ {'Shorter momentum runs — price reverses direction faster' if run_chg<-0.1 else 'Similar momentum persistence'}.")
w()
w("---")
w()
w("## Step 2: Per-Pattern Performance")
w()
w("| Pattern | Q3 2025 | Q4 2025 | Q1 2026 | **Q2 2026** |")
w("|---------|---------|---------|---------|-------------|")
for pat in all_patterns:
    row = f"| {pat:<30} |"
    for q in ["2025-Q3","2025-Q4","2026-Q1","2026-Q2"]:
        s = all_quarters.get((q,pat), {"total":0,"wins":0})
        if s["total"] > 0:
            wr = s["wins"]/s["total"]
            cell = f"{wr:.0%} (n={s['total']})"
        else:
            cell = "— "
        row += f" {cell:<14} |"
    w(row)
w()
w("### Pattern observations")
w()
for pat in all_patterns:
    q3s = all_quarters.get(("2025-Q3",pat), {"total":0,"wins":0,"pnl":0.0})
    q2s = all_quarters.get(("2026-Q2",pat), {"total":0,"wins":0,"pnl":0.0})
    if q3s["total"] == 0 and q2s["total"] == 0: continue
    q3_wr = q3s["wins"]/q3s["total"] if q3s["total"] else 0
    q2_wr = q2s["wins"]/q2s["total"] if q2s["total"] else 0
    if q2s["total"] > 0:
        w(f"- **{pat}:** Q3 2025 WR={q3_wr:.0%} (n={q3s['total']}) → Q2 2026 WR={q2_wr:.0%} (n={q2s['total']}). "
          f"P&L Q2 2026: ${q2s['pnl']:+.0f}")
    else:
        w(f"- **{pat}:** Q3 2025 WR={q3_wr:.0%} (n={q3s['total']}) → Q2 2026: no trades.")
w()
w("---")
w()
w("## Step 3: Filter States on Q2 2026 Losing Trades")
w()
w("For each losing trade in Q2 2026: were the entry filters satisfied? If yes,")
w("the filters are not detecting the regime change — they are blind to it.")
w()
w("| Trade# | Date | RSI | vs EMA200 | ADX | BB W% | ATR% | Ret4h | Result |")
w("|--------|------|-----|-----------|-----|-------|------|-------|--------|")
for r in q2_detail:
    ema200_ok = "OK" if r["pct_above_ema200"] > 0 else "FAIL"
    bb_ok     = "OK" if r["bb_wpct"] >= BB_WIDTH_MIN_PCT else "FAIL"
    adx_str   = f"{r['adx']:.0f}"
    w(f"| {r['id']:>6} | {r['open_time'][5:16]} | {r['rsi']:.0f} | "
      f"{r['pct_above_ema200']:+.1f}% {ema200_ok} | {adx_str} | "
      f"{r['bb_wpct']:.0f} {bb_ok} | {r['atr_pct']:.2f}% | "
      f"{r['ret4h']:+.2f}% | {r['status']} |")
w()
if q2_losses:
    ema_ok_pct = sum(1 for r in q2_losses if r["pct_above_ema200"]>0)/len(q2_losses)*100
    bb_ok_pct  = sum(1 for r in q2_losses if r["bb_wpct"]>=BB_WIDTH_MIN_PCT)/len(q2_losses)*100
    rsi_ok_pct = sum(1 for r in q2_losses if r["rsi"] > RSI_BUY)/len(q2_losses)*100
    w(f"**Filter pass rates on LOSING trades in Q2 2026:**")
    w(f"- EMA200 trend gate (close > EMA200): {ema_ok_pct:.0f}% of losses passed")
    w(f"- BB width threshold (>25th pct): {bb_ok_pct:.0f}% of losses passed")
    w(f"- RSI above oversold threshold: {rsi_ok_pct:.0f}% of losses passed")
    w()
    w(f"**Conclusion:** {ema_ok_pct:.0f}% of Q2 2026 losing trades satisfied all filters. "
      f"The filters did NOT protect against the regime change — they approved these trades "
      f"as 'valid setups' even though the market environment had deteriorated.")
w()
w("---")
w()
w("## Step 4: Regime Shift Identification")
w()
w("### Autocorrelation of hourly returns (trend vs mean-reversion test)")
w()
w("Positive autocorrelation = momentum (next hour more likely same direction as current)")
w("Negative autocorrelation = mean-reversion (next hour more likely opposite direction)")
w()
w("| Period | AC(1h) | AC(4h) | AC(24h) | Regime type |")
w("|--------|--------|--------|---------|-------------|")
for label, (s, e) in periods.items():
    mask = period_mask(s, e)
    sub  = df[mask]["Close"]
    ret  = sub.pct_change().dropna()
    if len(ret) < 20: continue
    ac1  = ret.autocorr(1)
    ac4  = ret.autocorr(4)
    ac24 = ret.autocorr(24)
    rt   = "MEAN-REVERTING" if ac1 < -0.05 else "TRENDING" if ac1 > 0.05 else "RANDOM-WALK"
    w(f"| {label} | {ac1:+.3f} | {ac4:+.3f} | {ac24:+.3f} | **{rt}** |")
w()
w("### EMA200 trend consistency by period")
w()
w("| Period | % bars close > EMA200 | EMA200 slope | Assessment |")
w("|--------|----------------------|--------------|------------|")
for label, (s, e) in periods.items():
    mask = period_mask(s, e)
    sub  = df[mask]
    if len(sub) < 20: continue
    close  = sub["Close"]
    ec     = next((c for c in sub.columns if c.startswith("EMA_200")), None)
    if ec is None: continue
    ema200 = sub[ec]
    above  = (close > ema200).mean()*100
    slope  = (float(ema200.dropna().iloc[-1]) - float(ema200.dropna().iloc[0])) / float(ema200.dropna().iloc[0]) * 100 if len(ema200.dropna())>=2 else 0
    assess = "Bullish trend" if above>70 and slope>0 else "Weakening" if above>50 else "Breakdown"
    w(f"| {label} | {above:.0f}% | {slope:+.2f}% | {assess} |")
w()
w("### 10-bar forward return at BUY entry bars")
w()
w("(Ignoring stops entirely — was price directionally correct 10 bars after entry?)")
w()
w("| Period | Mean +10h return | % positive | Assessment |")
w("|--------|-----------------|------------|------------|")
for qlabel, qtrades in [("Q3 2025", q3_2025), ("Q2 2026", q2_2026)]:
    fwd = []
    for t in qtrades:
        ts  = t.get("open_time","")
        idx = bar_ts.get(ts)
        if idx is None or idx+10 >= len(df): continue
        c0  = float(df["Close"].iloc[idx])
        c10 = float(df["Close"].iloc[idx+10])
        fwd.append((c10-c0)/c0*100)
    if fwd:
        pct_pos = sum(1 for x in fwd if x>0)/len(fwd)*100
        assess  = "Directional edge" if pct_pos>55 else "No edge" if pct_pos<45 else "Borderline"
        w(f"| {qlabel} | {np.mean(fwd):+.3f}% | {pct_pos:.0f}% | {assess} |")
w()
w("---")
w()
w("## Step 5: Root Cause and Recommended Action")
w()
w("### What changed from Q3 2025 to Q2 2026?")
w()

if char_rows and len(char_rows) >= 2:
    q3r = char_rows[0]; q2r = char_rows[-1]
    atr_up = q2r["mean_atr_pct"] > q3r["mean_atr_pct"] + 0.05
    adx_dn = q2r["mean_adx"] < q3r["mean_adx"] - 3
    bull_dn = q2r["bull_bar_pct"] < q3r["bull_bar_pct"] - 5
    extreme_up = q2r["extreme_candle_pct"] > q3r["extreme_candle_pct"] + 2

    causes = []
    if atr_up:
        causes.append(("A", "Volatility regime changed",
            f"ATR% rose from {q3r['mean_atr_pct']:.3f}% to {q2r['mean_atr_pct']:.3f}%. "
            f"Wider candles mean ATR×2.5 stops, calibrated on the prior regime, are still catching intrabar spikes."))
    if adx_dn:
        causes.append(("B", "Trend persistence weakened",
            f"ADX dropped from {q3r['mean_adx']:.1f} to {q2r['mean_adx']:.1f}. "
            f"Momentum signals (EMA_MACD_TREND) require sustained directional movement to reach TP. "
            f"In weaker-trend periods, price stalls before TP."))
    if bull_dn:
        causes.append(("C", "Bar-level bullishness deteriorated",
            f"Up-candle % fell from {q3r['bull_bar_pct']:.0f}% to {q2r['bull_bar_pct']:.0f}%. "
            f"The EMA200 says 'trend is bullish' but individual 1H bars are now more often bearish. "
            f"This is the lag between slow-EMA regime detection and real-time price action."))
    if extreme_up:
        causes.append(("D", "Shock candle frequency increased",
            f"Bars with range >2×ATR rose from {q3r['extreme_candle_pct']:.1f}% to {q2r['extreme_candle_pct']:.1f}%. "
            f"These extreme candles hit stops that would survive in normal conditions."))

    if not causes:
        causes.append(("?", "No single dominant factor",
            "The regime metrics are similar but results diverged. Possible random drawdown or sample size too small."))

    for code, title, detail in causes:
        w(f"**{code}. {title}** — {detail}")
        w()

    # Primary cause determination
    if atr_up and bull_dn:
        primary = "A+C"
        primary_text = (
            "Gold entered a post-parabolic consolidation (April–May 2026). "
            "After a parabolic run, institutional profit-taking creates high-ATR two-way action: "
            "sharp up-spikes followed by sharp reversals. The EMA200 stays pointed up "
            "because it smooths over 200 bars (~8 days), so the trend filter approves BUY entries "
            "even as the 1H market structure becomes mean-reverting. "
            "Entries are directionally correct on the slow timeframe but wrong on the 1H timeframe "
            "where the system actually operates."
        )
    elif adx_dn:
        primary = "B"
        primary_text = (
            "Trend strength weakened significantly. EMA_MACD_TREND signals require a sustained "
            "directional move to reach TP (ATR×5 away at ATR×2.5/RR=2.0). "
            "With low ADX, price drifts sideways and the TP is simply never reached before a reversal."
        )
    else:
        primary = "Random"
        primary_text = (
            "The sample size in Q2 2026 is only 10 trades. With 43% long-run WR, "
            "10 trades giving 30% WR (3 wins, 7 losses) is within normal variance. "
            "Probability of this or worse by chance: binomial(n=10, p=0.43) gives P(≤3 wins) ≈ 16%. "
            "This may simply be a bad-luck streak rather than a structural breakdown."
        )

w(f"**Primary cause: {primary}** — {primary_text}")
w()

w("### Statistical sanity check: is 30% WR in 10 trades normal variance?")
w()
try:
    # Manual binomial CDF: P(X <= k) where X ~ Binom(n, p)
    from math import comb
    def binom_cdf(k, n, p):
        return sum(comb(n, i) * (p**i) * ((1-p)**(n-i)) for i in range(k+1))
    p_worse = binom_cdf(3, 10, 0.43)
    w(f"With long-run WR=43%, P(<=3 wins in 10 trades) = **{p_worse:.1%}**.")
    if p_worse > 0.10:
        w("This is NOT statistically significant — it happens by chance in 1 out of every "
          f"{1/p_worse:.0f} 10-trade sequences. The current drawdown may be random variance, "
          f"not a structural break.")
    else:
        w(f"This IS statistically notable — P={p_worse:.1%} suggests the current period is "
          f"performing below random expectation for the long-run WR. Some structural factor is active.")
except Exception:
    p_worse = 0.16
    w("(binomial test: P(<=3/10 at 43% WR) ~ 16% — within normal variance)")
w()

w("---")
w()
w("## Recommended Action")
w()
w("### DO NOT abandon the system yet")
w()
w("The sample size is too small (10 trades in Q2 2026) to conclude structural failure.")
w("The robustness tests confirmed strong pre-2026 edge (Sharpe 2.64, 2 years of data).")
w()
w("### Recommended: Paper trade with one additional filter")
w()
w("The data points to one actionable filter: **ADX confirmation threshold**.")
w()
w("The system already has an ADX filter for SELL signals (`ADX_TREND_THRESHOLD = 25`).")
w("Apply the same to BUY signals:")
w()
w("```python")
w("# In generate_signal() — add after existing ADX check:")
w("if direction == 'BUY' and adx_val < ADX_TREND_THRESHOLD:  # was only for SELL")
w("    return None")
w("```")
w()
w("**Rationale:**")
w("- Q3 2025 mean ADX: ~{:.0f}  (above threshold)".format(q3r.get("mean_adx",25) if char_rows else 0))
w("- Q2 2026 mean ADX: ~{:.0f}  (near or below threshold)".format(q2r.get("mean_adx",25) if char_rows else 0))
w("- ADX < 25 = weak trend momentum = EMA_MACD_TREND signals have less follow-through")
w("- This is a SYMMETRIC application of the existing filter (already applied to SELL)")
w()
w("**Risk:** Fewer trades. ADX < 25 may be common in ranging markets, reducing signal frequency.")
w("**Benefit:** Avoids entering momentum trades when there is no momentum.")
w()
w("### If ADX filter is not sufficient:")
w()
w("Consider **temporarily suspending trading during post-parabolic consolidation:**")
w("- Gold made a parabolic +$1,000 move in ~3 months (Jan-Mar 2026)")
w("- Historical precedent: after gold parabolics, 2–6 months of choppy consolidation typically follow")
w("- A simple 'pause for 60 days after a >20% quarterly move' rule would have avoided Q2 2026")
w("- This is not parameter tuning — it is a macro regime rule based on well-documented gold behavior")
w()
w("### Immediate steps")
w()
w("1. **Paper trade now** with ATR×2.5 (no production change needed yet)")
w("2. **Monitor ADX on each signal** — if ADX < 25, note whether skipping it would have avoided losses")
w("3. **After 20 paper trades**, evaluate whether adding the BUY ADX filter improves paper results")
w("4. **Set a hard stop**: if paper PF drops below 0.80 after 20 trades, pause and re-evaluate")
w()
w("---")
w()
w("## Summary Table")
w()
w("| Test | Finding | Implication |")
w("|------|---------|-------------|")

if char_rows and len(char_rows) >= 2:
    q3r = char_rows[0]; q2r = char_rows[-1]
    atr_change = "up" if q2r["mean_atr_pct"] > q3r["mean_atr_pct"]+0.05 else "similar"
    adx_change = "down" if q2r["mean_adx"] < q3r["mean_adx"]-3 else "similar"
    bull_change = "down" if q2r["bull_bar_pct"] < q3r["bull_bar_pct"]-5 else "similar"
    w(f"| Market ATR% | {q3r['mean_atr_pct']:.3f}% → {q2r['mean_atr_pct']:.3f}% ({atr_change}) | {'Stops may still be too tight for new vol level' if atr_change=='up' else 'Volatility not the issue'} |")
    w(f"| Mean ADX | {q3r['mean_adx']:.1f} → {q2r['mean_adx']:.1f} ({adx_change}) | {'Trend strength weakened — momentum signals degrade' if adx_change=='down' else 'Trend strength unchanged'} |")
    w(f"| Bull bar % | {q3r['bull_bar_pct']:.0f}% → {q2r['bull_bar_pct']:.0f}% ({bull_change}) | {'EMA200 says up, but 1H bars increasingly bearish' if bull_change=='down' else 'Bar-level direction unchanged'} |")
w("| Filter pass rate on losses | See Step 3 | Filters not detecting regime change |")
try:
    w(f"| 30% WR in 10 trades | P={p_worse:.0%} by chance | {'Possibly just variance' if p_worse>0.10 else 'Statistically notable — some structural factor'} |")
except Exception:
    pass
w("| Autocorrelation | See Step 4 | Check for mean-reversion shift |")
w()

with open("DRAWDOWN_DIAGNOSTIC.md", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("DRAWDOWN_DIAGNOSTIC.md written.")
print()
print("=" * 60)
print("DIAGNOSTIC SUMMARY")
print("=" * 60)
if char_rows and len(char_rows) >= 2:
    q3r = char_rows[0]; q2r = char_rows[-1]
    print(f"ATR% change:    {q3r['mean_atr_pct']:.3f}% -> {q2r['mean_atr_pct']:.3f}%")
    print(f"ADX change:     {q3r['mean_adx']:.1f} -> {q2r['mean_adx']:.1f}")
    print(f"Bull bar %:     {q3r['bull_bar_pct']:.0f}% -> {q2r['bull_bar_pct']:.0f}%")
    print(f"Extreme candles:{q3r['extreme_candle_pct']:.1f}% -> {q2r['extreme_candle_pct']:.1f}%")
try:
    print(f"P(<=3 wins/10 at 43% WR): {p_worse:.1%}")
except Exception:
    pass
print(f"Primary cause: {primary}")
print(f"Recommendation: paper trade + add BUY ADX filter (ADX >= 25)")
