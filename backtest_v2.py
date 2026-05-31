#!/usr/bin/env python3
"""
Gold 1H Backtest v2 — 1 full year of GC=F data
Fixes identified from v1 post-mortem + web research:
  1. Switch 15m -> 1H: yfinance provides 730 days of 1H (vs 60 days of 15m)
  2. EMA200 trend filter: only trade WITH the dominant trend
  3. Session filter: 08:00-21:00 UTC (London open -> NY close)
  4. Raise R:R target: 1.5 -> 2.0  (break-even drops from 40% to 33.3%)
  5. Tighten RSI thresholds: 40/60 -> 35/65
  6. Consecutive-loss guard: pause trading for the day after 2 losses in a row
"""

import json
import warnings
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple
warnings.filterwarnings("ignore")

import yfinance as yf
import pandas as pd
import pandas_ta as pta
import numpy as np
from tabulate import tabulate

# ── Tunable parameters ────────────────────────────────────────────────────────
ACCOUNT_SIZE     = 10_000.0
MAX_RISK_PCT     = 0.01
MIN_RR           = 2.0       # v1=1.5 → break-even 40%; v2=2.0 → break-even 33%
DAILY_LOSS_LIMIT = 300.0
MIN_CONFLUENCE   = 3
ATR_VOLATILE_PCT = 1.0       # 1H bar ATR% threshold (1H has ~0.3-0.7% normal)
ATR_STOP_MULT    = 2.5       # v5 deployed value (was 1.5; updated 2026-05-12)
GOLD_CONTRACT    = 100.0
MIN_LOT, MAX_LOT, LOT_STEP = 0.01, 10.0, 0.01

RSI_BUY          = 35        # v1=40  (stricter oversold)
RSI_SELL         = 65        # v1=60  (stricter overbought)
TREND_EMA        = 200       # EMA200 on 1H ≈ 8-day trend proxy
SESSION_START    = 8         # UTC hour — London open
SESSION_END      = 21        # UTC hour — NY close
MAX_CONSEC_LOSS  = 2         # pause day after this many consecutive losses
WARMUP_BARS      = 220       # bars before signals start (EMA200 needs 200+)

SYMBOL      = "GC=F"
INTERVAL    = "1h"
RESULTS_V2  = "backtest_v2_results_v9_no_slope.json"

# Walk-forward split
TRAIN_RATIO = 0.70   # 70% train / 30% out-of-sample validation

# Spread / slippage model (realistic broker costs)
SPREAD_PIPS    = 2.5    # typical XAUUSD spread on XM
SPREAD_DOLLARS = 0.25   # 2.5 pips × $0.10/pip = $0.25 per side
HIGH_ATR_MULT  = 1.5    # bars with ATR > mean×1.5 get extra slippage
SLIP_EXTRA     = 0.05   # extra slippage dollars on high-ATR SL exits

# Bollinger Band width percentile filter
BB_WIDTH_LOOKBACK  = 50    # bars to compute percentile rank over
BB_WIDTH_MIN_PCT   = 25.0  # skip signals below 25th percentile (range-bound)

# ADX filter — suppress EMA_MACD_TREND_SELL when ADX < threshold (weak momentum)
ADX_TREND_THRESHOLD = 25   # minimum ADX to allow EMA_MACD_TREND_SELL
ADX_LOOKBACK        = 14   # Wilder smoothing period

# BB_RSI lower confluence (rarer but higher quality setup — 60% WR in backtest)
BB_RSI_MIN_CONFLUENCE = 2  # BB_RSI patterns need only 2/5 signals

# Monthly drawdown brake
MONTHLY_DRAWDOWN_BRAKE    = 150.0  # USD — activate brake when month P&L < -$150
MONTHLY_BRAKE_MULTIPLIER  = 0.5    # lot size multiplier when brake is active

# Disabled patterns — must match Config.DISABLED_PATTERNS in gold_trading_agents.py exactly.
# EMA_MACD_TREND_SELL: 101 trades, 27% WR, -$2,232 over 2yr → structurally broken in bull market.
# BB_RSI_REVERSAL_SELL: 16 trades, 25% WR, -$274.90 over 2yr → same structural problem.
DISABLED_PATTERNS = ["EMA_MACD_TREND_SELL", "BB_RSI_REVERSAL_SELL"]

# yfinance hard limit for 1H bars is 730 calendar days; use 725 to avoid boundary errors
_PERIOD_DAYS = {"1y": 365, "2y": 725, "max": 725}

# Breakeven move — enabled via --be CLI flag
BE_ENABLED     = False   # overridden in __main__ when --be is passed
BE_TRIGGER_R   = 1.0     # move SL to BE when floating profit >= +1R
BE_CUSHION_USD = 0.50    # new SL = entry + 0.50 (BUY) or entry - 0.50 (SELL)

# v12: HTF (4H) bias filter — controlled via --htf CLI flag (default off in backtest
# for A/B comparison vs baseline; production has HTF_BIAS_ENABLED=True by default).
HTF_ENABLED        = False   # overridden in __main__ when --htf is passed
HTF_INTERVAL       = "4h"
HTF_EMA_LEN        = 50
HTF_SLOPE_LOOKBACK = 5       # 5 × 4H = 20 hours
HTF_BIAS_SERIES: Optional[pd.Series] = None  # populated once per backtest run


# ── Data download (1H, configurable period) ───────────────────────────────────
def fetch_1h(days: int = 365) -> pd.DataFrame:
    print(f"Downloading GC=F 1H data ({days} days)...")
    end   = datetime.now()
    start = end - timedelta(days=days)
    df = yf.download(
        SYMBOL,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        interval=INTERVAL,
        progress=False,
        auto_adjust=True,
    )
    if df.empty:
        raise RuntimeError("No data returned")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open","High","Low","Close","Volume"]].dropna()
    df.index = pd.to_datetime(df.index)
    df = df[df.index.dayofweek < 5]   # weekdays only

    trading_days = df.index.normalize().nunique()
    print(f"Bars: {len(df):,}  |  {df.index[0].date()} to {df.index[-1].date()}")
    if trading_days < 300:
        print(f"WARNING: Only {trading_days} trading days of data. "
              f"Results may overfit to this period.")
    print()
    return df


# ── HTF (4H) bias series for the backtest window ──────────────────────────────
def compute_htf_bias_series(start_dt: datetime, end_dt: datetime) -> pd.Series:
    """
    Fetch 4H GC=F bars for the requested window and compute a BULL/BEAR/NEUTRAL
    bias label per 4H bar. Returns a Series indexed by 4H timestamps. Lookup at
    backtest time uses `.asof(bar_time)` so each 1H bar gets the most recent
    completed 4H bar's bias.

    Logic mirrors MarketAnalystAgent._get_htf_bias in production:
      - close > EMA50 AND slope(EMA50, 5 bars) > 0 → BULL
      - close < EMA50 AND slope(EMA50, 5 bars) < 0 → BEAR
      - else → NEUTRAL

    Network failure → returns an all-NEUTRAL series (safe default — same as
    production fallback).
    """
    try:
        # yfinance limits 4H data to the last 730 days; using Ticker.history(period=)
        # avoids the hard date-range rejection that yf.download triggers when
        # the backtest window + EMA warmup exceeds that cap.
        print(f"Fetching HTF[{HTF_INTERVAL}] bars for bias computation...")
        raw = yf.Ticker(SYMBOL).history(period="730d", interval=HTF_INTERVAL)
        if raw.empty:
            print("HTF fetch returned empty — all NEUTRAL")
            return pd.Series(dtype=str)
        df4 = raw[["Open","High","Low","Close","Volume"]].dropna()
        df4.index = pd.to_datetime(df4.index)

        ema50 = df4["Close"].ewm(span=HTF_EMA_LEN, adjust=False).mean()
        slope = ema50 - ema50.shift(HTF_SLOPE_LOOKBACK)

        bias = pd.Series("NEUTRAL", index=df4.index, dtype=str)
        bull_mask = (df4["Close"] > ema50) & (slope > 0)
        bear_mask = (df4["Close"] < ema50) & (slope < 0)
        bias[bull_mask] = "BULL"
        bias[bear_mask] = "BEAR"

        n_bull = int((bias == "BULL").sum())
        n_bear = int((bias == "BEAR").sum())
        n_neut = int((bias == "NEUTRAL").sum())
        total = len(bias)
        print(
            f"HTF bias distribution: BULL={n_bull} ({n_bull*100//total}%) "
            f"BEAR={n_bear} ({n_bear*100//total}%) "
            f"NEUTRAL={n_neut} ({n_neut*100//total}%)  N={total}"
        )
        return bias
    except Exception as exc:
        print(f"HTF compute failed: {exc} — all NEUTRAL fallback")
        return pd.Series(dtype=str)


# ── Indicators ────────────────────────────────────────────────────────────────
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.ta.rsi(length=14, append=True)
    df.ta.ema(length=20, append=True)
    df.ta.ema(length=50, append=True)
    df.ta.ema(length=TREND_EMA, append=True)
    df.ta.atr(length=14, append=True)
    macd = df.ta.macd(fast=12, slow=26, signal=9)
    if macd is not None and not macd.empty:
        df = pd.concat([df, macd], axis=1)
    bb = df.ta.bbands(length=20, std=2)
    if bb is not None and not bb.empty:
        df = pd.concat([df, bb], axis=1)

    # Rolling ATR mean (for spread/slippage model)
    atr_col = next((c for c in df.columns if c.startswith("ATRr_") or c.startswith("ATR")), None)
    if atr_col:
        df["atr_mean"] = df[atr_col].rolling(50, min_periods=10).mean()

    # ADX — inline Wilder, no pip dependency
    df["adx"] = _calc_adx_series(df, ADX_LOOKBACK)

    # Bollinger Band width percentile rank over last BB_WIDTH_LOOKBACK bars
    bbb_col = next((c for c in df.columns if c.startswith("BBB_")), None)
    if bbb_col:
        def _pct_rank(s: pd.Series) -> float:
            if s.isna().all():
                return 50.0
            cur = s.iloc[-1]
            return float((s < cur).mean() * 100)
        df["bb_width_pct"] = (
            df[bbb_col]
            .rolling(BB_WIDTH_LOOKBACK, min_periods=BB_WIDTH_LOOKBACK)
            .apply(_pct_rank, raw=False)
        )
    else:
        df["bb_width_pct"] = 50.0   # allow all signals if BBB column missing

    return df


def _calc_adx_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Inline Wilder ADX — no external dependency. Returns a Series aligned to df.index."""
    high  = df["High"]
    low   = df["Low"]
    close = df["Close"]
    pc = close.shift(1)
    ph = high.shift(1)
    pl = low.shift(1)

    tr = pd.concat([
        (high - low),
        (high - pc).abs(),
        (low  - pc).abs(),
    ], axis=1).max(axis=1)

    up   = high - ph
    down = pl   - low

    plus_dm  = np.where((up > down)   & (up > 0),   up,   0.0)
    minus_dm = np.where((down > up)   & (down > 0), down, 0.0)

    alpha = 1.0 / period
    tr14  = pd.Series(tr.values,    index=df.index).ewm(alpha=alpha, adjust=False).mean()
    pdm14 = pd.Series(plus_dm,      index=df.index).ewm(alpha=alpha, adjust=False).mean()
    mdm14 = pd.Series(minus_dm,     index=df.index).ewm(alpha=alpha, adjust=False).mean()

    tr14_safe = tr14.replace(0, np.nan)
    pdi = 100 * pdm14 / tr14_safe
    mdi = 100 * mdm14 / tr14_safe
    dx  = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adx = dx.ewm(alpha=alpha, adjust=False).mean()
    return adx.fillna(0.0)


def _f(df: pd.DataFrame, prefix: str, i: int, default: float = 0.0) -> float:
    cols = [c for c in df.columns if c.startswith(prefix)]
    if not cols:
        return default
    v = df.iloc[i][cols[0]]
    try:
        f = float(v)
        return f if np.isfinite(f) else default
    except Exception:
        return default


# ── Signal (identical logic, tighter RSI thresholds) ─────────────────────────
def generate_signal(df: pd.DataFrame, i: int) -> Optional[dict]:
    close  = float(df["Close"].iloc[i])
    rsi    = _f(df, "RSI_", i)
    ema20  = _f(df, "EMA_20", i)
    ema50  = _f(df, "EMA_50", i)
    ema200 = _f(df, f"EMA_{TREND_EMA}", i)
    atr    = _f(df, "ATRr_", i) or _f(df, "ATR", i)
    macd_v = _f(df, "MACD_", i)
    macd_s = _f(df, "MACDs_", i)
    bb_u   = _f(df, "BBU_", i, close * 1.01)
    bb_l   = _f(df, "BBL_", i, close * 0.99)

    # BB width percentile filter: skip choppy/range-bound bars
    bb_width_pct = _f(df, "bb_width_pct", i, 50.0)
    if bb_width_pct < BB_WIDTH_MIN_PCT:
        return None

    # ── EMA200 trend gate: zone within 0.3 ATR is neutral (no trade) ──────
    if ema200 > 0:
        trend_up   = close > ema200 + 0.3 * atr
        trend_down = close < ema200 - 0.3 * atr
    else:
        trend_up = trend_down = True   # no filter if indicator not ready

    buy_r:  List[str] = []
    sell_r: List[str] = []

    # 1. RSI — tighter thresholds (35/65 vs v1's 40/60)
    if rsi > 0:
        if rsi < RSI_BUY:   buy_r.append(f"RSI oversold ({rsi:.1f})")
        elif rsi > RSI_SELL: sell_r.append(f"RSI overbought ({rsi:.1f})")

    # 2. Price vs EMA20
    if ema20 > 0:
        if close > ema20: buy_r.append(f"Price above EMA20")
        else:             sell_r.append(f"Price below EMA20")

    # 3. EMA20 vs EMA50
    if ema20 > 0 and ema50 > 0:
        if ema20 > ema50: buy_r.append("EMA20 > EMA50 uptrend")
        else:             sell_r.append("EMA20 < EMA50 downtrend")

    # 4. MACD
    if macd_v != 0 or macd_s != 0:
        if macd_v > macd_s: buy_r.append("MACD bullish")
        else:               sell_r.append("MACD bearish")

    # 5. Bollinger proximity
    bb_range = bb_u - bb_l
    if bb_range > 0:
        bp = (close - bb_l) / bb_range
        if bp < 0.2:   buy_r.append(f"Near lower BB ({bp:.0%})")
        elif bp > 0.8: sell_r.append(f"Near upper BB ({bp:.0%})")

    buy_n, sell_n = len(buy_r), len(sell_r)

    # Apply trend filter: suppress counter-trend signals
    if not trend_up:   buy_n = 0
    if not trend_down: sell_n = 0

    # BB_RSI patterns get a lower confluence threshold (2 instead of 3)
    bb_rsi_buy  = any("RSI" in r for r in buy_r)  and any("BB" in r for r in buy_r)
    bb_rsi_sell = any("RSI" in r for r in sell_r) and any("BB" in r for r in sell_r)
    req_buy  = BB_RSI_MIN_CONFLUENCE if bb_rsi_buy  else MIN_CONFLUENCE
    req_sell = BB_RSI_MIN_CONFLUENCE if bb_rsi_sell else MIN_CONFLUENCE

    if buy_n >= req_buy and buy_n >= sell_n:
        direction, reasons, count = "BUY", buy_r, buy_n
        sl   = round(close - atr * ATR_STOP_MULT, 2)
        dist = close - sl
        tp   = round(close + dist * MIN_RR, 2)
    elif sell_n >= req_sell and sell_n > buy_n:
        direction, reasons, count = "SELL", sell_r, sell_n
        sl   = round(close + atr * ATR_STOP_MULT, 2)
        dist = sl - close
        tp   = round(close - dist * MIN_RR, 2)
    else:
        return None

    dist = abs(close - sl)
    if dist <= 0:
        return None
    rr = abs(tp - close) / dist

    pattern = name_pattern(reasons, direction)

    # Disabled-pattern gate — mirrors Config.DISABLED_PATTERNS in gold_trading_agents.py
    if pattern in DISABLED_PATTERNS:
        return None

    # ADX filter: EMA_MACD_TREND requires confirmed trend momentum (BUY + SELL symmetric)
    # Mirrors gold_trading_agents.py v5: "if EMA_MACD_TREND in pattern" (no direction check)
    adx_val = _f(df, "adx", i, 0.0)
    if "EMA_MACD_TREND" in pattern:
        if adx_val < ADX_TREND_THRESHOLD:
            return None

    # RSI ceiling for BUY — mirrors Config.RSI_CEILING_BUY = 70 in gold_trading_agents.py v5
    rsi_val = _f(df, "RSI_", i)
    if direction == "BUY" and rsi_val >= 70:
        return None

    # v12: HTF (4H) bias gate — block BUY when HTF != BULL, block SELL when HTF != BEAR.
    # Mirrors Config.HTF_BIAS_ENABLED in gold_trading_agents.py.
    if HTF_ENABLED and HTF_BIAS_SERIES is not None and not HTF_BIAS_SERIES.empty:
        bar_time = df.index[i]
        try:
            htf_bias = HTF_BIAS_SERIES.asof(bar_time)
        except Exception:
            htf_bias = "NEUTRAL"
        if pd.isna(htf_bias) or htf_bias is None:
            htf_bias = "NEUTRAL"
        if (direction == "BUY" and htf_bias != "BULL") or (
            direction == "SELL" and htf_bias != "BEAR"
        ):
            return None

    return dict(direction=direction, reasons=reasons, count=count,
                entry=close, sl=sl, tp=tp, rr=round(rr, 2),
                atr=atr, ema200=ema200, adx=adx_val, pattern=pattern)


def detect_regime(df: pd.DataFrame, i: int) -> str:
    close = float(df["Close"].iloc[i])
    atr   = _f(df, "ATRr_", i) or _f(df, "ATR", i)
    atr_pct = (atr / close * 100) if close > 0 else 0
    if atr_pct > ATR_VOLATILE_PCT:
        return "VOLATILE"
    ema20 = _f(df, "EMA_20", i)
    ema50 = _f(df, "EMA_50", i)
    if ema20 > 0 and ema50 > 0:
        if close > ema20 > ema50: return "TRENDING_UP"
        if close < ema20 < ema50: return "TRENDING_DOWN"
    return "RANGING"


def name_pattern(reasons: List[str], direction: str) -> str:
    has_rsi  = any("RSI" in r for r in reasons)
    has_ecx  = any("EMA20 >" in r or "EMA20 <" in r for r in reasons)
    has_macd = any("MACD" in r for r in reasons)
    has_bb   = any("BB" in r for r in reasons)
    has_ep   = any("above EMA20" in r or "below EMA20" in r for r in reasons)
    if has_rsi and has_macd and has_ecx:  return f"TRIPLE_SIGNAL_{direction}"
    if has_rsi and has_bb:                return f"BB_RSI_REVERSAL_{direction}"
    if has_ecx and has_macd:              return f"EMA_MACD_TREND_{direction}"
    if has_rsi and has_ep:                return f"RSI_EMA_SIGNAL_{direction}"
    if has_rsi:                           return f"RSI_SIGNAL_{direction}"
    if has_ecx:                           return f"EMA_TREND_{direction}"
    return f"CONFLUENCE_{direction}"


# ── Trade record ──────────────────────────────────────────────────────────────
@dataclass
class BT:
    id: int
    open_time: str
    direction: str
    pattern: str
    entry: float
    stop_loss: float
    take_profit: float
    lot_size: float
    risk_amount: float
    confluence: int
    regime: str
    rr_ratio: float
    close_time: str = ""
    exit_price: float = 0.0
    pnl: float = 0.0
    status: str = "OPEN"
    spread_cost: float = 0.0          # entry spread + exit spread in dollars
    net_pnl: float = 0.0              # pnl minus spread_cost
    monthly_brake_active: bool = False # lot was halved by monthly drawdown brake
    be_moved: bool = False            # True if SL was moved to breakeven


# ── Simulation ────────────────────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame):
    n          = len(df)
    trades:    List[BT] = []
    open_trade: Optional[BT] = None
    equity     = ACCOUNT_SIZE
    equity_pts: List[float] = [equity] * WARMUP_BARS

    daily_loss:  float = 0.0
    daily_date:  str   = ""
    consec_loss: int   = 0
    trade_id:    int   = 0

    # Counters for diagnostics
    sk = dict(volatile=0, session=0, daily=0, rr=0, trend=0, consec=0,
              bb_width=0, adx=0, ambiguous=0, be_moves=0)

    # Running ATR mean for spread/slippage calibration
    atr_col = next((c for c in df.columns if c.startswith("ATRr_") or c.startswith("ATR") and "mean" not in c), None)

    monthly_pnl:   float = 0.0
    monthly_month: str   = ""

    for i in range(WARMUP_BARS, n):
        bar   = df.index[i]
        bdate = bar.strftime("%Y-%m-%d")
        btime = bar.strftime("%Y-%m-%d %H:%M")

        # Localise timezone if aware
        try:
            bar_hour = bar.hour
        except Exception:
            bar_hour = 12

        hi    = float(df["High"].iloc[i])
        lo    = float(df["Low"].iloc[i])
        close = float(df["Close"].iloc[i])

        # Reset daily counters on new day
        if bdate != daily_date:
            daily_loss  = 0.0
            consec_loss = 0
            daily_date  = bdate

        # Reset monthly P&L tracker on new calendar month
        bmonth = bdate[:7]
        if bmonth != monthly_month:
            monthly_pnl   = 0.0
            monthly_month = bmonth

        # ── Check open position ────────────────────────────────────────────
        if open_trade is not None:
            t = open_trade

            # BE move: if price has travelled +1R in our favour, lock in near-BE stop.
            # stop_loss at this point is still the original SL (be_moved=False).
            if BE_ENABLED and not t.be_moved:
                orig_stop_dist = abs(t.entry - t.stop_loss)
                if orig_stop_dist > 0:
                    if t.direction == "BUY":
                        trigger = t.entry + orig_stop_dist * BE_TRIGGER_R
                        if hi >= trigger:
                            new_sl = round(t.entry + BE_CUSHION_USD, 2)
                            if new_sl > t.stop_loss:
                                t.stop_loss = new_sl
                                t.be_moved  = True
                                sk["be_moves"] += 1
                    else:  # SELL
                        trigger = t.entry - orig_stop_dist * BE_TRIGGER_R
                        if lo <= trigger:
                            new_sl = round(t.entry - BE_CUSHION_USD, 2)
                            if new_sl < t.stop_loss:
                                t.stop_loss = new_sl
                                t.be_moved  = True
                                sk["be_moves"] += 1

            hit_sl = hit_tp = False
            if t.direction == "BUY":
                hit_sl = lo <= t.stop_loss
                hit_tp = hi >= t.take_profit
            else:
                hit_sl = hi >= t.stop_loss
                hit_tp = lo <= t.take_profit

            bar_atr  = _f(df, "ATRr_", i) or _f(df, "ATR", i)
            atr_mean = float(df["atr_mean"].iloc[i]) if "atr_mean" in df.columns and np.isfinite(df["atr_mean"].iloc[i]) else bar_atr

            if hit_sl and hit_tp:
                sk["ambiguous"] += 1
                print(f"  WARNING: ambiguous bar {btime} trade#{t.id} — "
                      f"both SL ({t.stop_loss:.2f}) and TP ({t.take_profit:.2f}) touched. "
                      f"Assuming SL exit (conservative).")

            if hit_sl:          # SL wins if both hit same bar (conservative)
                t.exit_price = t.stop_loss
                # Actual P&L from exit price — works for both original SL and BE-moved SL.
                # For original SL: (stop_loss - entry) * mult * lot * contract = -risk_amount.
                # For BE SL:       (entry + cushion - entry) * mult * lot * contract > 0.
                mult = 1 if t.direction == "BUY" else -1
                gross_pnl = round((t.stop_loss - t.entry) * mult * t.lot_size * GOLD_CONTRACT, 2)
                extra_slip = SLIP_EXTRA if (bar_atr > atr_mean * HIGH_ATR_MULT) else 0.0
                t.spread_cost = round(t.lot_size * 100 * (SPREAD_DOLLARS * 2 + extra_slip), 2)
                t.pnl     = gross_pnl
                t.net_pnl = round(t.pnl - t.spread_cost, 2)
                t.status  = "WIN" if gross_pnl > 0 else "LOSS"
                t.close_time = btime
                equity      += t.net_pnl
                if t.net_pnl < 0:
                    daily_loss += abs(t.net_pnl)
                monthly_pnl += t.net_pnl
                if t.pnl < 0:
                    consec_loss += 1
                else:
                    consec_loss = 0
                trades.append(t)
                open_trade = None
            elif hit_tp:
                t.exit_price  = t.take_profit
                t.pnl         = round(t.risk_amount * t.rr_ratio, 2)
                t.spread_cost = round(t.lot_size * 100 * SPREAD_DOLLARS * 2, 2)
                t.net_pnl     = round(t.pnl - t.spread_cost, 2)
                t.status      = "WIN"
                t.close_time  = btime
                equity       += t.net_pnl
                monthly_pnl  += t.net_pnl
                consec_loss   = 0
                trades.append(t)
                open_trade = None

        equity_pts.append(equity)

        if open_trade is not None:
            continue

        # ── Pre-signal filters ─────────────────────────────────────────────

        # 1. Session filter (London open to NY close)
        if not (SESSION_START <= bar_hour < SESSION_END):
            sk["session"] += 1
            continue

        # 2. Regime / volatility
        regime = detect_regime(df, i)
        if regime == "VOLATILE":
            sk["volatile"] += 1
            continue

        # 3. Daily loss limit
        if daily_loss >= DAILY_LOSS_LIMIT:
            sk["daily"] += 1
            continue

        # 4. Consecutive loss guard
        if consec_loss >= MAX_CONSEC_LOSS:
            sk["consec"] += 1
            continue

        # 5. Signal (includes EMA200 trend gate + BB width + ADX filters)
        sig = generate_signal(df, i)
        if sig is None:
            bb_w    = _f(df, "bb_width_pct", i, 50.0)
            adx_now = _f(df, "adx", i, 0.0)
            if bb_w < BB_WIDTH_MIN_PCT:
                sk["bb_width"] += 1
            elif adx_now < ADX_TREND_THRESHOLD:
                sk["adx"] += 1
            else:
                sk["trend"] += 1
            continue

        if sig["rr"] < MIN_RR:
            sk["rr"] += 1
            continue

        # ── Open trade ─────────────────────────────────────────────────────
        # Apply spread to entry price (realistic fill)
        raw_entry = sig["entry"]
        if sig["direction"] == "BUY":
            actual_entry = raw_entry + SPREAD_DOLLARS
        else:
            actual_entry = raw_entry - SPREAD_DOLLARS

        max_risk    = equity * MAX_RISK_PCT
        remaining   = DAILY_LOSS_LIMIT - daily_loss
        risk_amount = min(max_risk, remaining)
        stop_dist   = abs(actual_entry - sig["sl"])
        if stop_dist <= 0:
            continue
        raw_lot  = risk_amount / (GOLD_CONTRACT * stop_dist)
        lot      = max(MIN_LOT, min(MAX_LOT, round(raw_lot / LOT_STEP) * LOT_STEP))

        # Monthly drawdown brake: halve lot when month is down > $150
        brake_active = monthly_pnl < -MONTHLY_DRAWDOWN_BRAKE
        if brake_active:
            lot = max(MIN_LOT, round(lot * MONTHLY_BRAKE_MULTIPLIER / LOT_STEP) * LOT_STEP)

        act_risk = lot * GOLD_CONTRACT * stop_dist

        trade_id += 1
        open_trade = BT(
            id=trade_id,
            open_time=btime,
            direction=sig["direction"],
            pattern=sig["pattern"],
            entry=actual_entry,
            stop_loss=sig["sl"],
            take_profit=sig["tp"],
            lot_size=lot,
            risk_amount=act_risk,
            confluence=sig["count"],
            regime=regime,
            rr_ratio=sig["rr"],
            monthly_brake_active=brake_active,
        )

    # Expire any open trade at end of data
    if open_trade is not None:
        t = open_trade
        mult = 1 if t.direction == "BUY" else -1
        t.pnl         = round((close - t.entry) * mult * t.lot_size * GOLD_CONTRACT, 2)
        t.spread_cost = round(t.lot_size * 100 * SPREAD_DOLLARS * 2, 2)
        t.net_pnl     = round(t.pnl - t.spread_cost, 2)
        t.exit_price  = close
        t.status      = "EXPIRED"
        t.close_time  = df.index[-1].strftime("%Y-%m-%d %H:%M")
        equity       += t.net_pnl
        trades.append(t)

    print("Filter breakdown:")
    for k, v in sk.items():
        if k in ("ambiguous", "be_moves"):
            continue
        print(f"  {k:12s}: {v:,} bars skipped")
    if sk["ambiguous"]:
        print(f"  {'ambiguous':12s}: {sk['ambiguous']:,} bars — both SL+TP hit, SL assumed")
    else:
        print(f"  {'ambiguous':12s}: 0 (no same-bar SL+TP conflicts)")
    if BE_ENABLED:
        print(f"  {'be_moves':12s}: {sk['be_moves']:,} SL relocations to breakeven")

    eq_series = pd.Series(equity_pts, index=df.index[:len(equity_pts)])
    return trades, eq_series, sk


# ── Daily-equity-curve Sharpe (more accurate than per-trade) ──────────────────
def daily_sharpe(eq: pd.Series) -> float:
    """Resample equity curve to business-day frequency, compute annualised Sharpe."""
    if eq is None or len(eq) < 5:
        return 0.0
    daily_eq  = eq.resample("B").last().dropna()
    daily_ret = daily_eq.pct_change().dropna()
    if len(daily_ret) < 2 or daily_ret.std() == 0:
        return 0.0
    return float((daily_ret.mean() / daily_ret.std()) * np.sqrt(252))


# ── Analytics ─────────────────────────────────────────────────────────────────
def analyse(trades: List[BT], eq: pd.Series) -> Dict:
    closed = [t for t in trades if t.status != "OPEN"]
    if not closed:
        return {}
    wins   = [t for t in closed if t.status == "WIN"]
    losses = [t for t in closed if t.status == "LOSS"]
    total  = len(closed)
    win_n  = len(wins)

    gross_pnls  = [t.pnl for t in closed]
    net_pnls    = [t.net_pnl for t in closed]
    total_gross = sum(gross_pnls)
    total_net   = sum(net_pnls)
    total_spread = sum(t.spread_cost for t in closed)

    gw  = sum(t.net_pnl for t in wins)
    gl  = abs(sum(t.net_pnl for t in losses))
    pf  = gw / gl if gl > 0 else float("inf")

    # Max drawdown (on net equity)
    vals = eq.values
    peak = vals[0]
    max_dd = 0.0
    for v in vals:
        peak = max(peak, v)
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)

    # Daily-equity-curve Sharpe (correct method)
    sharpe = daily_sharpe(eq)

    # Per-pattern (net P&L)
    pat: Dict[str, Dict] = {}
    for t in closed:
        s = pat.setdefault(t.pattern, {"total":0,"wins":0,"pnl":0.0})
        s["total"] += 1
        s["pnl"]   += t.net_pnl
        if t.status == "WIN":
            s["wins"] += 1

    # Monthly (net P&L)
    monthly: Dict[str, float] = {}
    for t in closed:
        m = t.close_time[:7]
        monthly[m] = monthly.get(m, 0.0) + t.net_pnl

    # Direction breakdown (net P&L)
    dir_stats: Dict[str, Dict] = {}
    for t in closed:
        s = dir_stats.setdefault(t.direction, {"total":0,"wins":0,"pnl":0.0})
        s["total"] += 1; s["pnl"] += t.net_pnl
        if t.status == "WIN": s["wins"] += 1

    return dict(
        total=total, wins=win_n, losses=len(losses), expired=len(closed)-win_n-len(losses),
        win_rate=win_n/total if total else 0,
        total_pnl=total_net,
        gross_pnl=total_gross,
        spread_cost=total_spread,
        avg_win=gw/win_n if win_n else 0,
        avg_loss=gl/len(losses) if losses else 0,
        profit_factor=pf,
        max_dd=max_dd,
        sharpe=sharpe,
        final_equity=ACCOUNT_SIZE + total_net,
        patterns=pat, monthly=monthly, dir_stats=dir_stats,
    )


# ── Pretty report ─────────────────────────────────────────────────────────────
def print_report(trades: List[BT], stats: Dict, data_range: Tuple[str,str]):
    BE = 1 / (1 + MIN_RR)
    print("\n" + "=" * 72)
    print("  GOLD 1H BACKTEST v2 RESULTS")
    print(f"  Data  : {data_range[0]}  to  {data_range[1]}")
    print(f"  R:R   : {MIN_RR}  |  Break-even win rate: {BE:.1%}")
    print(f"  Filters: EMA{TREND_EMA} trend | Session {SESSION_START}:00-{SESSION_END}:00 UTC | RSI {RSI_BUY}/{RSI_SELL}")
    print("=" * 72)

    v1 = dict(total=181, win_rate=0.398, total_pnl=-235.93, max_dd=18.9, sharpe=-0.99, pf=0.97)
    summary = [
        ["",                    "v1 (15m, 55d)",           "v2 (1H, 1yr)"],
        ["Total trades",        v1["total"],                stats["total"]],
        ["Win rate",           f"{v1['win_rate']:.1%}",    f"{stats['win_rate']:.1%}"],
        ["Gross P&L",          f"${v1['total_pnl']:+,.2f}", f"${stats.get('gross_pnl', stats['total_pnl']):+,.2f}"],
        ["Spread/slip cost",   "—",                         f"${stats.get('spread_cost', 0):,.2f}"],
        ["Net P&L",            "—",                         f"${stats['total_pnl']:+,.2f}"],
        ["Avg win",            "—",                         f"${stats['avg_win']:,.2f}"],
        ["Avg loss",           "—",                         f"${stats['avg_loss']:,.2f}"],
        ["Profit factor",      f"{v1['pf']:.2f}",           f"{stats['profit_factor']:.2f}"],
        ["Max drawdown",       f"{v1['max_dd']:.1f}%",      f"{stats['max_dd']:.1f}%"],
        ["Sharpe (daily eq.)", f"{v1['sharpe']:.2f}",       f"{stats['sharpe']:.2f}"],
        ["Final equity",       "—",                         f"${stats['final_equity']:,.2f}"],
        ["Ambiguous bars",     "n/a",                       str(stats.get("ambiguous_bars", 0))
                                                            + (" <- SL assumed" if stats.get("ambiguous_bars", 0) else " (none)")],
    ]
    print(tabulate(summary, tablefmt="simple", headers="firstrow"))

    # Direction
    print("\n" + "=" * 72)
    print("  DIRECTION BREAKDOWN")
    print("=" * 72)
    drows = []
    for d, s in stats.get("dir_stats", {}).items():
        t = s["total"]
        w = s["wins"]
        drows.append([d, t, w, f"{w/t:.0%}" if t else "-", f"${s['pnl']:+,.2f}"])
    print(tabulate(drows, headers=["Dir","Trades","Wins","Win%","P&L"], tablefmt="simple"))

    # Patterns
    print("\n" + "=" * 72)
    print("  PATTERN BREAKDOWN")
    print("=" * 72)
    pat_rows = []
    for p, s in sorted(stats["patterns"].items(), key=lambda x: x[1]["pnl"], reverse=True):
        t = s["total"]; w = s["wins"]
        wr = f"{w/t:.0%}" if t else "-"
        avg = f"${s['pnl']/t:+.2f}" if t else "-"
        skill = " *" if t >= 3 and w/t >= 0.70 else ""
        pat_rows.append([p[:32], t, w, wr, avg, f"${s['pnl']:+,.2f}", skill])
    print(tabulate(pat_rows,
                   headers=["Pattern","Trades","Wins","Win%","Avg P&L","Total P&L",""],
                   tablefmt="simple"))

    # Monthly
    print("\n" + "=" * 72)
    print("  MONTHLY P&L")
    print("=" * 72)
    mrows = []
    for m in sorted(stats["monthly"]):
        p = stats["monthly"][m]
        bar = "+" * min(int(abs(p)/15), 40) if p >= 0 else "-" * min(int(abs(p)/15), 40)
        mrows.append([m, f"${p:+,.2f}", bar])
    print(tabulate(mrows, headers=["Month","P&L",""], tablefmt="simple"))

    # Top/bottom trades
    srt = sorted(trades, key=lambda x: x.pnl, reverse=True)
    for label, lst in [("TOP 5 TRADES", srt[:5]), ("BOTTOM 5 TRADES", srt[-5:])]:
        print(f"\n{'='*72}\n  {label}\n{'='*72}")
        rows = [[t.id, t.open_time[:16], t.direction, t.pattern[:24],
                 f"${t.entry:.2f}", f"${t.exit_price:.2f}",
                 f"${t.pnl:+.2f}", t.status] for t in lst]
        print(tabulate(rows,
                       headers=["#","Open","Dir","Pattern","Entry","Exit","P&L","Status"],
                       tablefmt="simple"))
    print()


def save(trades: List[BT], stats: Dict):
    out = {
        "generated": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "config": dict(interval=INTERVAL, rr=MIN_RR, rsi_buy=RSI_BUY,
                       rsi_sell=RSI_SELL, trend_ema=TREND_EMA,
                       session=f"{SESSION_START}:00-{SESSION_END}:00 UTC",
                       atr_volatile_pct=ATR_VOLATILE_PCT),
        "summary": {k: v for k, v in stats.items()
                    if k not in ("patterns","monthly","dir_stats")},
        "patterns": stats.get("patterns", {}),
        "monthly":  stats.get("monthly", {}),
        "trades":   [asdict(t) for t in trades],
    }
    import pathlib
    pathlib.Path(RESULTS_V2).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Results saved to {RESULTS_V2}")


# ── Walk-forward split helper ─────────────────────────────────────────────────
def _wf_stats_row(label: str, trades: List[BT], eq: pd.Series) -> Dict:
    """Compute analyse() on a subset of trades with a matching equity slice."""
    if not trades:
        return {}
    # Rebuild a mini equity curve for just this subset
    eq_mini = pd.Series(
        [ACCOUNT_SIZE] + [ACCOUNT_SIZE + sum(t.net_pnl for t in trades[:k+1])
                          for k in range(len(trades))],
        index=[pd.Timestamp(trades[0].open_time)] + [pd.Timestamp(t.close_time) for t in trades],
    )
    return analyse(trades, eq_mini)


# ── Entry ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Gold 1H Backtest v2")
    parser.add_argument(
        "--period", choices=["1y", "2y", "max"], default="1y",
        help="Data period: 1y=365d, 2y=730d, max=730d (yfinance 1H limit)"
    )
    parser.add_argument(
        "--be", action="store_true",
        help="Enable breakeven move: move SL to entry+cushion when +1R floating profit reached"
    )
    parser.add_argument(
        "--htf", action="store_true",
        help="Enable 4H higher-timeframe bias filter (v12): block BUY unless HTF=BULL, SELL unless HTF=BEAR"
    )
    args = parser.parse_args()
    fetch_days = _PERIOD_DAYS[args.period]

    # Activate BE globally so run_backtest() can read the flag
    if args.be:
        BE_ENABLED = True
        RESULTS_V2 = "backtest_v2_results_be.json"

    # v12: activate HTF filter globally so generate_signal() can read the flag
    if args.htf:
        HTF_ENABLED = True
        # If --be also passed, name the combined file accordingly
        if args.be:
            RESULTS_V2 = "backtest_v2_results_be_htf.json"
        else:
            RESULTS_V2 = "backtest_v2_results_htf.json"

    print("=" * 72)
    print("  GOLD 1H BACKTEST v2  —  Improvements over v1:")
    print("  [1] 1H interval  -> full year of data (730 days available)")
    print("  [2] EMA200 trend filter -> no counter-trend trades")
    print("  [3] Session filter 08:00-21:00 UTC -> liquid hours only")
    print(f"  [4] R:R {MIN_RR} (was 1.5) -> break-even drops from 40% to 33%")
    print(f"  [5] RSI thresholds {RSI_BUY}/{RSI_SELL} (was 40/60) -> cleaner entries")
    print(f"  [6] Max {MAX_CONSEC_LOSS} consecutive losses/day -> drawdown protection")
    print(f"  [7] BB width percentile filter (< {BB_WIDTH_MIN_PCT}th pct blocked)")
    print(f"  [8] Spread/slippage model ({SPREAD_PIPS} pips / ${SPREAD_DOLLARS})")
    print(f"  [9] Daily-equity-curve Sharpe (replaces per-trade Sharpe)")
    print(f"  [10] Walk-forward split {int(TRAIN_RATIO*100)}/{int((1-TRAIN_RATIO)*100)} train/val")
    print(f"  [11] ADX({ADX_LOOKBACK}) filter: EMA_MACD_TREND_SELL needs ADX >= {ADX_TREND_THRESHOLD}")
    print(f"  [12] BB_RSI lower confluence threshold ({BB_RSI_MIN_CONFLUENCE}/5)")
    print(f"  [13] Monthly drawdown brake (halve lot when month P&L < -${MONTHLY_DRAWDOWN_BRAKE:.0f})")
    if BE_ENABLED:
        print(f"  [BE] Breakeven move at +{BE_TRIGGER_R}R -> SL to entry +/- ${BE_CUSHION_USD:.2f}")
    if HTF_ENABLED:
        print(f"  [HTF] 4H bias filter: BUY needs BULL, SELL needs BEAR (EMA{HTF_EMA_LEN}, {HTF_SLOPE_LOOKBACK}-bar slope)")
    print(f"  Period: {args.period} ({fetch_days} days)")
    print("=" * 72 + "\n")

    df = fetch_1h(fetch_days)
    if len(df) < WARMUP_BARS + 20:
        print(f"Not enough bars: {len(df)}")
        raise SystemExit(1)

    print("Computing indicators...")
    df = add_indicators(df)

    # v12: pre-compute HTF bias series (one fetch, reused per-bar via .asof)
    if HTF_ENABLED:
        HTF_BIAS_SERIES = compute_htf_bias_series(df.index[0], df.index[-1])

    print("Running bar-by-bar simulation...\n")
    trades, eq, diag = run_backtest(df)

    if not trades:
        print("No trades generated.")
        raise SystemExit(0)

    stats = analyse(trades, eq)
    stats["ambiguous_bars"] = diag.get("ambiguous", 0)
    r = (df.index[0].strftime("%Y-%m-%d"), df.index[-1].strftime("%Y-%m-%d"))
    print_report(trades, stats, r)

    # ── Walk-forward 70/30 split ──────────────────────────────────────────────
    closed = [t for t in trades if t.close_time]
    if closed:
        split_idx = int(len(closed) * TRAIN_RATIO)
        split_ts  = closed[split_idx].open_time if split_idx < len(closed) else closed[-1].open_time

        train_trades = [t for t in closed if t.open_time < split_ts]
        val_trades   = [t for t in closed if t.open_time >= split_ts]

        train_stats = _wf_stats_row("TRAIN", train_trades, eq)
        val_stats   = _wf_stats_row("VAL",   val_trades,   eq)

        print("\n" + "=" * 72)
        print(f"  WALK-FORWARD VALIDATION  ({int(TRAIN_RATIO*100)}% train / {int((1-TRAIN_RATIO)*100)}% out-of-sample)")
        print(f"  Split point: {split_ts}")
        print("=" * 72)
        wf_rows = [
            ["",              "TRAIN",                                     "VAL (OOS)"],
            ["Trades",        train_stats.get("total","—"),                val_stats.get("total","—")],
            ["Win rate",     f"{train_stats.get('win_rate',0):.1%}",      f"{val_stats.get('win_rate',0):.1%}"],
            ["Net P&L",      f"${train_stats.get('total_pnl',0):+,.2f}",  f"${val_stats.get('total_pnl',0):+,.2f}"],
            ["Profit factor",f"{train_stats.get('profit_factor',0):.2f}", f"{val_stats.get('profit_factor',0):.2f}"],
            ["Max DD",       f"{train_stats.get('max_dd',0):.1f}%",       f"{val_stats.get('max_dd',0):.1f}%"],
            ["Sharpe",       f"{train_stats.get('sharpe',0):.2f}",        f"{val_stats.get('sharpe',0):.2f}"],
        ]
        print(tabulate(wf_rows, tablefmt="simple", headers="firstrow"))

        train_sharpe = train_stats.get("sharpe", 0)
        val_sharpe   = val_stats.get("sharpe", 0)
        if train_sharpe > 0 and val_sharpe < train_sharpe * 0.50:
            print(f"\n*** WARNING: possible overfitting — val Sharpe ({val_sharpe:.2f}) "
                  f"< 50% of train Sharpe ({train_sharpe:.2f}) ***")
        elif val_sharpe >= train_sharpe * 0.50:
            print(f"\nWalk-forward OK — val Sharpe within acceptable range of train Sharpe.")
        print()

    save(trades, stats)
