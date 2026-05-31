#!/usr/bin/env python3
"""
sell_research.py — SELL approach validation (Phases 1-3)
Research-only: does NOT modify production gold_trading_agents.py

Approach A: Indicator mirror of BUY logic + NEW EMA200 slope requirement
Approach B: Bearish structure — lower-high + support break/retest rejection

Periods (daily GC=F bars):
  1. Bear 2011-2015      : 2011-09-01 to 2015-12-31
  2. Correction 2022     : 2022-03-01 to 2022-11-30
  3. Current 2026        : 2026-04-15 to today

Decision gate (deploy if ALL met, averaged across 3 periods):
  PF >= 1.3 | beats random WR by >= 5pp | PF > 1.0 in 2/3 periods
  Sharpe >= 1.0 | no single period worse than -$500
"""
import warnings
warnings.filterwarnings("ignore")

import json
import random
import numpy as np
import pandas as pd
import pandas_ta as pta   # noqa — registers .ta accessor
import yfinance as yf
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Tuple, Callable
from tabulate import tabulate

# ─── Parameters ───────────────────────────────────────────────────────────────
ACCOUNT_SIZE     = 10_000.0
MAX_RISK_PCT     = 0.01
MIN_RR           = 2.0
ATR_STOP_MULT    = 2.5          # matches deployed v6 config
GOLD_CONTRACT    = 100.0
MIN_LOT, MAX_LOT, LOT_STEP = 0.01, 10.0, 0.01
DAILY_LOSS_LIMIT = 300.0
SPREAD           = 0.30         # $/side (per spec)

# Approach A: indicator thresholds
RSI_SELL_SCORE  = 65.0          # RSI > 65 counts as SELL confluence
RSI_FLOOR_SELL  = 30.0          # block SELL if RSI <= 30 (mirror of BUY ceiling 70)
ADX_THRESH_A    = 25            # same as production
BB_WIDTH_MIN    = 25.0          # skip choppy markets (same as production)

# Approach B: structure thresholds
ADX_THRESH_B    = 20            # slightly looser — breakdowns can form below 25
LOOKBACK_SUPP   = 20            # bars for support level (20-day low)
RETEST_BARS     = 5             # how many bars back to look for a retest
SWING_LOOKBACK  = 40            # bars to scan for confirmed swing highs

# EMA200 slope requirement — the fix for Trade #4 class of errors
EMA200_SLOPE_BARS = 20          # daily bars ≈ 4 calendar weeks

ATR_VOLATILE_PCT  = 3.0         # daily bar: skip if ATR% > 3%
MAX_CONSEC_LOSS   = 2
WARMUP_BARS       = 220
WARMUP_DAYS       = 600         # calendar days prepended before test window start

MC_RUNS           = 200         # Monte Carlo iterations for random baseline
SYMBOL            = "GC=F"

PERIODS = [
    dict(name="Bear 2011-2015",    start="2011-09-01", end="2015-12-31"),
    dict(name="Correction 2022",   start="2022-03-01", end="2022-11-30"),
    dict(name="Current 2026",      start="2026-04-15", end=None),
]

# ─── Data download ─────────────────────────────────────────────────────────────
def fetch_daily(start_str: str, end_str: Optional[str]) -> pd.DataFrame:
    fetch_start = (datetime.strptime(start_str, "%Y-%m-%d") - timedelta(days=WARMUP_DAYS)
                   ).strftime("%Y-%m-%d")
    fetch_end   = end_str or date.today().strftime("%Y-%m-%d")
    print(f"  Fetching {SYMBOL} daily {fetch_start} -> {fetch_end} ...", end="", flush=True)
    df = yf.download(SYMBOL, start=fetch_start, end=fetch_end,
                     interval="1d", progress=False, auto_adjust=True)
    if df.empty:
        raise RuntimeError("No data returned from yfinance")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df = df[df.index.dayofweek < 5]
    print(f" {len(df):,} bars  ({df.index[0].date()} to {df.index[-1].date()})")
    return df


# ─── Indicators ────────────────────────────────────────────────────────────────
def _adx_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    pc, ph, pl = close.shift(1), high.shift(1), low.shift(1)
    tr  = pd.concat([(high - low), (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    up  = high - ph
    dn  = pl - low
    pdm = np.where((up > dn) & (up > 0),   up, 0.0)
    mdm = np.where((dn > up) & (dn > 0),   dn, 0.0)
    a   = 1.0 / period
    tr14  = pd.Series(tr.values,  index=df.index).ewm(alpha=a, adjust=False).mean()
    pdm14 = pd.Series(pdm,        index=df.index).ewm(alpha=a, adjust=False).mean()
    mdm14 = pd.Series(mdm,        index=df.index).ewm(alpha=a, adjust=False).mean()
    s   = tr14.replace(0, np.nan)
    pdi = 100 * pdm14 / s
    mdi = 100 * mdm14 / s
    dx  = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=a, adjust=False).mean().fillna(0.0)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.ta.rsi(length=14, append=True)
    df.ta.ema(length=20, append=True)
    df.ta.ema(length=50, append=True)
    df.ta.ema(length=200, append=True)
    df.ta.atr(length=14, append=True)

    for result in [df.ta.macd(fast=12, slow=26, signal=9),
                   df.ta.bbands(length=20, std=2)]:
        if result is not None and not result.empty:
            df = pd.concat([df, result], axis=1)

    df["adx"] = _adx_series(df)

    # EMA200 slope: change over EMA200_SLOPE_BARS bars (positive = rising, negative = falling)
    e200_col = next((c for c in df.columns if c.startswith("EMA_200")), None)
    df["ema200_slope"] = df[e200_col].diff(EMA200_SLOPE_BARS) if e200_col else 0.0

    # BB width percentile rank over last 50 bars
    bbb_col = next((c for c in df.columns if c.startswith("BBB_")), None)
    if bbb_col:
        def _pct(s: pd.Series) -> float:
            if s.isna().all():
                return 50.0
            return float((s < s.iloc[-1]).mean() * 100)
        df["bb_width_pct"] = df[bbb_col].rolling(50, min_periods=50).apply(_pct, raw=False)
    else:
        df["bb_width_pct"] = 50.0

    # Rename ATR column to a stable name
    atr_col = next((c for c in df.columns if (c.startswith("ATRr_") or c.startswith("ATR"))
                    and "mean" not in c and c != "adx"), None)
    if atr_col:
        df.rename(columns={atr_col: "_atr"}, inplace=True)

    return df


def _v(df: pd.DataFrame, prefix: str, i: int, default: float = 0.0) -> float:
    cols = [c for c in df.columns if c.startswith(prefix)]
    if not cols:
        return default
    try:
        v = float(df.iloc[i][cols[0]])
        return v if np.isfinite(v) else default
    except Exception:
        return default


# ─── Swing high helper (no look-ahead) ────────────────────────────────────────
def _swing_highs(df: pd.DataFrame, i: int, lookback: int = 40) -> Tuple[Optional[float], Optional[float]]:
    """Return (most_recent, prior) confirmed swing highs from bars strictly before i-1."""
    found: List[float] = []
    h = df["High"].values
    for j in range(i - 2, max(0, i - lookback), -1):
        if j > 0 and j + 1 < len(h):
            if h[j] > h[j - 1] and h[j] > h[j + 1]:
                found.append(float(h[j]))
        if len(found) == 2:
            break
    if len(found) >= 2:
        return found[0], found[1]
    if len(found) == 1:
        return found[0], None
    return None, None


# ─── Approach A: Indicator Mirror ──────────────────────────────────────────────
def signal_a(df: pd.DataFrame, i: int) -> Optional[dict]:
    """
    Mirror of the working BUY logic for the SELL side.
    Additions vs old EMA_MACD_TREND_SELL:
      - EMA200 SLOPE must be negative (EMA200 actively declining)
      - RSI floor blocks shorting into oversold (RSI <= 30)
    """
    close  = float(df["Close"].iloc[i])
    rsi    = _v(df, "RSI_", i)
    ema20  = _v(df, "EMA_20", i)
    ema50  = _v(df, "EMA_50", i)
    ema200 = _v(df, "EMA_200", i)
    atr    = _v(df, "_atr", i)
    macd_v = _v(df, "MACD_", i)
    macd_s = _v(df, "MACDs_", i)
    bb_u   = _v(df, "BBU_", i, close * 1.01)
    bb_l   = _v(df, "BBL_", i, close * 0.99)
    adx    = _v(df, "adx", i)
    slope  = float(df["ema200_slope"].iloc[i]) if "ema200_slope" in df.columns else 0.0
    bwp    = _v(df, "bb_width_pct", i, 50.0)

    if atr <= 0 or close <= 0:
        return None
    if (atr / close * 100) > ATR_VOLATILE_PCT:
        return None
    if bwp < BB_WIDTH_MIN:
        return None

    # EMA200 gate: price BELOW EMA200 AND EMA200 actively FALLING
    if ema200 <= 0:
        return None
    if not (close < ema200 - 0.3 * atr and slope < 0.0):
        return None

    # RSI floor: don't short into deeply oversold
    if rsi > 0 and rsi <= RSI_FLOOR_SELL:
        return None

    # ADX: confirmed trend momentum
    if adx < ADX_THRESH_A:
        return None

    # Confluence: mirror of the 5 BUY signals
    sell_r: List[str] = []
    if rsi > RSI_SELL_SCORE:
        sell_r.append(f"RSI overbought ({rsi:.1f})")
    if ema20 > 0 and close < ema20:
        sell_r.append("Price below EMA20")
    if ema20 > 0 and ema50 > 0 and ema20 < ema50:
        sell_r.append("EMA20<EMA50 downtrend")
    if (macd_v != 0 or macd_s != 0) and macd_v < macd_s:
        sell_r.append("MACD bearish")
    bb_range = bb_u - bb_l
    if bb_range > 0 and (close - bb_l) / bb_range > 0.8:
        sell_r.append(f"Near upper BB ({(close-bb_l)/bb_range:.0%})")

    if len(sell_r) < 3:
        return None

    sl   = round(close + atr * ATR_STOP_MULT, 2)
    dist = sl - close
    tp   = round(close - dist * MIN_RR, 2)
    if dist <= 0 or tp <= 0:
        return None

    return dict(approach="A", entry=close, sl=sl, tp=tp, atr=atr,
                count=len(sell_r), adx=adx, rsi=rsi, ema200=ema200, slope=slope)


# ─── Approach B: Bearish Structure ─────────────────────────────────────────────
def signal_b(df: pd.DataFrame, i: int) -> Optional[dict]:
    """
    Structure-based short: lower-high + support break with retest/rejection.
    Macro gate: EMA200 sloping down + close below EMA200.
    Entry is at the BREAKDOWN bar (close < 20-bar support after a retest from above).
    Stop = above most recent swing high (with ATR buffer); min = ATR*2.5.
    """
    if i < max(LOOKBACK_SUPP + RETEST_BARS + 2, SWING_LOOKBACK):
        return None

    close  = float(df["Close"].iloc[i])
    ema200 = _v(df, "EMA_200", i)
    atr    = _v(df, "_atr", i)
    adx    = _v(df, "adx", i)
    slope  = float(df["ema200_slope"].iloc[i]) if "ema200_slope" in df.columns else 0.0

    if atr <= 0 or close <= 0:
        return None
    if (atr / close * 100) > ATR_VOLATILE_PCT:
        return None

    # Macro confirmation: EMA200 sloping down AND close below EMA200
    if ema200 <= 0:
        return None
    if not (close < ema200 and slope < 0.0):
        return None

    # ADX: some directional movement present
    if adx < ADX_THRESH_B:
        return None

    # Lower high: most recent swing high < prior swing high
    sh1, sh2 = _swing_highs(df, i, SWING_LOOKBACK)
    if sh1 is None or sh2 is None:
        return None
    if sh1 >= sh2:
        return None  # no lower high, no structure

    # Support break + retest/rejection
    # support = 20-bar low of close, bars [i-21 .. i-1] (not current)
    closes = df["Close"].values
    window_start = max(0, i - LOOKBACK_SUPP - 1)
    support = float(np.min(closes[window_start:i]))

    # Retest: in the last RETEST_BARS bars (exclusive of current), price was ABOVE support
    retest_window = closes[max(0, i - RETEST_BARS):i]
    retest_occurred = bool(np.any(retest_window > support)) if len(retest_window) > 0 else False

    # Breakdown: current close is BELOW support
    if not (retest_occurred and close < support):
        return None

    # Stop: above the most recent swing high + 0.5*ATR buffer
    sl_structural = sh1 + atr * 0.5
    sl = round(max(sl_structural, close + atr * ATR_STOP_MULT), 2)
    dist = sl - close
    if dist <= 0:
        return None
    tp = round(close - dist * MIN_RR, 2)
    if tp <= 0:
        return None

    return dict(approach="B", entry=close, sl=sl, tp=tp, atr=atr,
                count=3, adx=adx, rsi=0.0, ema200=ema200, slope=slope,
                sh1=sh1, sh2=sh2, support=support)


# ─── Simulation engine ─────────────────────────────────────────────────────────
def simulate(df: pd.DataFrame,
             signal_fn: Callable,
             test_start_str: str) -> Tuple[List[dict], pd.Series]:
    """
    Bar-by-bar SELL simulation.
    - Pre-test bars run for equity/state calibration; trades only counted from test_start.
    - Conservative ambiguous-bar handling: if both SL and TP touch same bar, assume LOSS.
    - Spread ($0.30/side) applied as a separate P&L cost.
    Returns (test_trades, equity_series_for_test_period).
    """
    test_start = pd.Timestamp(test_start_str)
    n          = len(df)
    trades: List[dict] = []
    open_trade: Optional[dict] = None
    equity      = ACCOUNT_SIZE
    daily_loss  = 0.0
    daily_date  = ""
    consec_loss = 0
    monthly_pnl = 0.0
    monthly_mon = ""
    tid         = 0

    # Equity curve for test period only
    eq_dates:  List[pd.Timestamp] = []
    eq_values: List[float]        = []

    for i in range(WARMUP_BARS, n):
        bar   = df.index[i]
        bdate = bar.strftime("%Y-%m-%d")
        bmon  = bdate[:7]

        if bdate != daily_date:
            daily_loss = 0.0;  consec_loss = 0;  daily_date = bdate
        if bmon != monthly_mon:
            monthly_pnl = 0.0; monthly_mon = bmon

        hi    = float(df["High"].iloc[i])
        lo    = float(df["Low"].iloc[i])
        close = float(df["Close"].iloc[i])
        in_test = bar >= test_start

        # ── Close open position ──────────────────────────────────────────────
        if open_trade is not None:
            t = open_trade
            hit_sl = hi >= t["sl"]
            hit_tp = lo <= t["tp"]
            risk   = t["risk"]
            lot    = t["lot"]
            spread_cost = lot * GOLD_CONTRACT * SPREAD * 2

            if hit_sl or (hit_sl and hit_tp):   # SL wins on ambiguous bar
                pnl = round(-risk - spread_cost, 2)
                t.update(exit=t["sl"], pnl=pnl, status="LOSS", close_date=bdate)
                equity     += pnl
                daily_loss += abs(pnl)
                monthly_pnl += pnl
                consec_loss += 1
                if t["in_test"]:
                    trades.append(dict(t))
                open_trade = None
            elif hit_tp:
                pnl = round(risk * MIN_RR - spread_cost, 2)
                t.update(exit=t["tp"], pnl=pnl, status="WIN", close_date=bdate)
                equity     += pnl
                monthly_pnl += pnl
                consec_loss = 0
                if t["in_test"]:
                    trades.append(dict(t))
                open_trade = None

        if in_test:
            eq_dates.append(bar)
            eq_values.append(equity)

        if open_trade is not None:
            continue

        # ── Pre-signal guards ────────────────────────────────────────────────
        if daily_loss >= DAILY_LOSS_LIMIT:
            continue
        if consec_loss >= MAX_CONSEC_LOSS:
            continue

        sig = signal_fn(df, i)
        if sig is None:
            continue

        # Entry price with spread (SELL: we sell at bid = close - spread)
        entry = sig["entry"] - SPREAD
        sl    = sig["sl"]
        tp    = sig["tp"]
        dist  = sl - entry
        if dist <= 0:
            continue

        risk_cap    = min(equity * MAX_RISK_PCT, DAILY_LOSS_LIMIT - daily_loss)
        if risk_cap <= 0:
            continue
        raw_lot = risk_cap / (GOLD_CONTRACT * dist)
        lot     = max(MIN_LOT, min(MAX_LOT, round(raw_lot / LOT_STEP) * LOT_STEP))
        risk    = lot * GOLD_CONTRACT * dist

        tid += 1
        open_trade = dict(
            id=tid, open_date=bdate, approach=sig["approach"],
            entry=entry, sl=sl, tp=tp, lot=lot, risk=risk,
            adx=sig.get("adx", 0), rsi=sig.get("rsi", 0),
            ema200=sig.get("ema200", 0), slope=sig.get("slope", 0),
            exit=0.0, pnl=0.0, status="OPEN", close_date="",
            in_test=in_test,
        )

    # Expire any open trade at dataset end
    if open_trade is not None:
        t = open_trade
        spread_cost = t["lot"] * GOLD_CONTRACT * SPREAD * 2
        pnl = round((t["entry"] - close) * t["lot"] * GOLD_CONTRACT - spread_cost, 2)
        t.update(exit=close, pnl=pnl, status="EXPIRED", close_date=df.index[-1].strftime("%Y-%m-%d"))
        if t["in_test"]:
            trades.append(dict(t))

    if eq_dates:
        eq_series = pd.Series(eq_values, index=pd.DatetimeIndex(eq_dates))
    else:
        eq_series = pd.Series(dtype=float)

    return trades, eq_series


# ─── Random baseline ───────────────────────────────────────────────────────────
def random_baseline(df: pd.DataFrame,
                    test_start_str: str,
                    n_trades: int,
                    mc_runs: int = MC_RUNS) -> Dict:
    """
    Monte Carlo random SELL baseline.
    For each run: randomly pick n_trades bars from the test-period eligible universe,
    simulate each trade (ATR*2.5 stop, 2.0 RR) independently, measure WR.
    Returns mean/std WR and mean net P&L over mc_runs runs.
    """
    if n_trades == 0:
        return dict(mean_wr=0.0, std_wr=0.0, mean_pnl=0.0, runs=0)

    test_start = pd.Timestamp(test_start_str)
    closes = df["Close"].values
    highs  = df["High"].values
    lows   = df["Low"].values
    n      = len(df)

    # Build eligible bar index: test period, not volatile, not too near end
    eligible: List[int] = []
    for i in range(WARMUP_BARS, n - 10):
        if df.index[i] < test_start:
            continue
        atr = _v(df, "_atr", i)
        c   = closes[i]
        if atr <= 0 or c <= 0:
            continue
        if (atr / c * 100) > ATR_VOLATILE_PCT:
            continue
        eligible.append(i)

    if len(eligible) == 0:
        return dict(mean_wr=0.0, std_wr=0.0, mean_pnl=0.0, runs=0)

    rng = random.Random(42)
    wrs: List[float] = []
    pnls: List[float] = []

    for _ in range(mc_runs):
        sample = rng.sample(eligible, min(n_trades, len(eligible)))
        wins = 0
        run_pnl = 0.0
        for idx in sample:
            c   = closes[idx]
            atr = _v(df, "_atr", idx)
            sl  = c + atr * ATR_STOP_MULT
            tp  = c - (sl - c) * MIN_RR
            risk = 100.0   # fixed $100 risk per random trade for comparability
            spread_cost = SPREAD * 2 * (risk / (atr * ATR_STOP_MULT * GOLD_CONTRACT) if atr > 0 else 0.01) * GOLD_CONTRACT

            outcome = "EXPIRED"
            for j in range(idx + 1, min(idx + 150, n)):
                if highs[j] >= sl:
                    outcome = "LOSS"; break
                if lows[j]  <= tp:
                    outcome = "WIN";  break

            if outcome == "WIN":
                wins    += 1
                run_pnl += risk * MIN_RR - spread_cost
            elif outcome == "LOSS":
                run_pnl -= risk + spread_cost
        wrs.append(wins / len(sample) if sample else 0.0)
        pnls.append(run_pnl)

    return dict(
        mean_wr  = float(np.mean(wrs)),
        std_wr   = float(np.std(wrs)),
        mean_pnl = float(np.mean(pnls)),
        runs     = mc_runs,
    )


# ─── Analytics ─────────────────────────────────────────────────────────────────
def daily_sharpe(eq: pd.Series) -> float:
    if eq is None or len(eq) < 5:
        return 0.0
    d = eq.resample("B").last().dropna().pct_change().dropna()
    if len(d) < 2 or d.std() == 0:
        return 0.0
    return float((d.mean() / d.std()) * np.sqrt(252))


def analyse(trades: List[dict], eq: pd.Series) -> dict:
    closed = [t for t in trades if t["status"] != "OPEN"]
    if not closed:
        return dict(trades=0, wr=0, pf=0, pnl=0, sharpe=0, maxdd=0)
    wins   = [t for t in closed if t["status"] == "WIN"]
    losses = [t for t in closed if t["status"] == "LOSS"]
    gw     = sum(t["pnl"] for t in wins)
    gl     = abs(sum(t["pnl"] for t in losses))
    pf     = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)
    wr     = len(wins) / len(closed) if closed else 0.0
    pnl    = sum(t["pnl"] for t in closed)

    # Max drawdown from the equity curve
    vals = np.array([ACCOUNT_SIZE] + [ACCOUNT_SIZE + sum(t["pnl"] for t in closed[:k+1])
                                       for k in range(len(closed))])
    peak = vals[0]
    maxdd = 0.0
    for v in vals:
        peak = max(peak, v)
        if peak > 0:
            maxdd = max(maxdd, (peak - v) / peak * 100)

    return dict(trades=len(closed), wins=len(wins), losses=len(losses),
                wr=wr, pf=pf, pnl=pnl, sharpe=daily_sharpe(eq), maxdd=maxdd)


# ─── Single period runner ──────────────────────────────────────────────────────
def run_period(period: dict, approach_name: str, signal_fn: Callable) -> dict:
    df = fetch_daily(period["start"], period["end"])
    df = add_indicators(df)
    trades, eq = simulate(df, signal_fn, period["start"])
    stats      = analyse(trades, eq)

    # Random baseline (same number of trades)
    rand = random_baseline(df, period["start"], max(stats["trades"], 1))

    return dict(
        period   = period["name"],
        approach = approach_name,
        **stats,
        rand_wr  = rand["mean_wr"],
        rand_std = rand["std_wr"],
        wr_vs_rand = stats["wr"] - rand["mean_wr"],
        trades_list = trades,
    )


# ─── Deploy gate ───────────────────────────────────────────────────────────────
def check_gate(results: List[dict], approach: str) -> Tuple[bool, List[str]]:
    """
    Returns (passes, reasons_list).
    Gate criteria (all must be met):
      1. Average PF >= 1.3 across all periods
      2. Beats random WR by >= 5pp in average
      3. PF > 1.0 in at least 2 of 3 periods
      4. Average Sharpe >= 1.0
      5. No single period worse than -$500
    """
    rows = [r for r in results if r["approach"] == approach]
    if not rows:
        return False, ["No results found"]

    avg_pf     = np.mean([r["pf"] for r in rows])
    avg_sharpe = np.mean([r["sharpe"] for r in rows])
    avg_wr_gap = np.mean([r["wr_vs_rand"] for r in rows])
    pf_above_1 = sum(1 for r in rows if r["pf"] > 1.0)
    worst_pnl  = min(r["pnl"] for r in rows)

    reasons: List[str] = []
    passes = True

    checks = [
        (avg_pf >= 1.3,      f"PF avg={avg_pf:.2f} (need>=1.3)"),
        (avg_wr_gap >= 0.05, f"WR vs random avg={avg_wr_gap:+.1%} (need>=+5pp)"),
        (pf_above_1 >= 2,    f"PF>1.0 in {pf_above_1}/3 periods (need 2)"),
        (avg_sharpe >= 1.0,  f"Sharpe avg={avg_sharpe:.2f} (need>=1.0)"),
        (worst_pnl >= -500,  f"Worst period P&L=${worst_pnl:+,.0f} (need>=-$500)"),
    ]
    for ok, msg in checks:
        status = "PASS" if ok else "FAIL"
        reasons.append(f"[{status}] {msg}")
        if not ok:
            passes = False

    return passes, reasons


# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("\n" + "="*70)
    print("  SELL RESEARCH — Approach A (Mirror) vs Approach B (Structure)")
    print("="*70)

    all_results: List[dict] = []

    for period in PERIODS:
        print(f"\n{'─'*60}")
        print(f"  Period: {period['name']}")
        print(f"{'─'*60}")

        for name, fn in [("A_Mirror", signal_a), ("B_Structure", signal_b)]:
            print(f"\n  Running Approach {name} ...")
            try:
                result = run_period(period, name, fn)
                all_results.append(result)
                s = result
                print(f"    Trades={s['trades']}  WR={s['wr']:.1%}  "
                      f"PF={s['pf']:.2f}  Sharpe={s['sharpe']:.2f}  "
                      f"P&L=${s['pnl']:+,.0f}  "
                      f"vs_random={s['wr_vs_rand']:+.1%}")
            except Exception as exc:
                print(f"    ERROR: {exc}")
                import traceback; traceback.print_exc()

    # ── Results Table ─────────────────────────────────────────────────────────
    print("\n" + "="*70)
    print("  RESULTS TABLE")
    print("="*70)
    rows = []
    for r in all_results:
        rows.append([
            r["approach"],
            r["period"][:22],
            r["trades"],
            f"{r['wr']:.0%}",
            f"{r['pf']:.2f}",
            f"{r['sharpe']:.2f}",
            f"${r['pnl']:+,.0f}",
            f"{r['maxdd']:.1f}%",
            f"{r['wr_vs_rand']:+.0%}",
        ])
    print(tabulate(rows,
                   headers=["Approach", "Period", "Trades", "WR", "PF",
                             "Sharpe", "P&L", "MaxDD", "WR vs Rand"],
                   tablefmt="simple"))

    # ── Random baseline reference ─────────────────────────────────────────────
    print("\n" + "="*70)
    print("  RANDOM SELL BASELINE (avg of 200 MC runs per period)")
    print("="*70)
    rand_rows = []
    for r in all_results:
        rand_rows.append([
            r["period"][:22],
            r["approach"],
            f"{r['rand_wr']:.1%}",
            f"±{r['rand_std']:.1%}",
        ])
    print(tabulate(rand_rows,
                   headers=["Period", "Approach", "Random WR", "Std"],
                   tablefmt="simple"))

    # ── Gate evaluation ───────────────────────────────────────────────────────
    print("\n" + "="*70)
    print("  DEPLOY GATE EVALUATION")
    print("="*70)
    gate_a, reasons_a = check_gate(all_results, "A_Mirror")
    gate_b, reasons_b = check_gate(all_results, "B_Structure")

    print("\n  Approach A (Mirror):"); [print(f"    {r}") for r in reasons_a]
    print("\n  Approach B (Structure):"); [print(f"    {r}") for r in reasons_b]

    # ── Verdict ───────────────────────────────────────────────────────────────
    print("\n" + "="*70)
    print("  VERDICT")
    print("="*70)

    avg_pf = lambda app: np.mean([r["pf"] for r in all_results if r["approach"] == app]) if any(r["approach"]==app for r in all_results) else 0

    if gate_a and gate_b:
        winner = "A_Mirror" if avg_pf("A_Mirror") >= avg_pf("B_Structure") else "B_Structure"
        verdict = f"DEPLOY APPROACH {winner} (both pass; higher avg PF wins)"
    elif gate_a:
        verdict = "DEPLOY APPROACH A_Mirror"
    elif gate_b:
        verdict = "DEPLOY APPROACH B_Structure"
    else:
        verdict = "STAY BUY-ONLY — neither approach meets the deploy gate"

    print(f"\n  >>> {verdict} <<<\n")

    # ── Save results ──────────────────────────────────────────────────────────
    output = {
        "generated": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "verdict": verdict,
        "gate_a": {"passes": gate_a, "checks": reasons_a},
        "gate_b": {"passes": gate_b, "checks": reasons_b},
        "results": [
            {k: v for k, v in r.items() if k != "trades_list"}
            for r in all_results
        ],
        "trade_samples": {
            f"{r['approach']}_{r['period']}": r.get("trades_list", [])[:20]
            for r in all_results
        },
    }
    with open("sell_research_results.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str)
    print("  Results saved to sell_research_results.json")
    print()

    return all_results, verdict


if __name__ == "__main__":
    main()
