#!/usr/bin/env python3
"""
SELL Pattern Validation Backtest — Research Only
Tests whether SELL patterns show edge in non-bull market regimes.
DO NOT modify gold_trading_agents.py, backtest_v2.py, or backtest_v2_results_longonly.json.
"""

import json
import pathlib
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

# ── Config ────────────────────────────────────────────────────────────────────
ACCOUNT_SIZE     = 10_000.0
MAX_RISK_PCT     = 0.01
MIN_RR           = 2.0
DAILY_LOSS_LIMIT = 300.0
MIN_CONFLUENCE   = 3
ATR_VOLATILE_PCT = 3.0      # daily bars: normal ~0.5-1.5%; block extreme days (>3%)
ATR_STOP_MULT    = 2.0      # wider stops on daily bars (was 1.5 for 1H)
GOLD_CONTRACT    = 100.0
MIN_LOT, MAX_LOT, LOT_STEP = 0.01, 10.0, 0.01

RSI_BUY          = 35
RSI_SELL         = 65
TREND_EMA        = 200
MAX_CONSEC_LOSS  = 2
WARMUP_BARS      = 220      # EMA200 needs 200+ daily bars (~11 months)

DATA_INTERVAL    = "1d"
USE_SESSION_FILTER = False  # daily bars have no intraday session concept
SYMBOL           = "GC=F"
RESULTS_FILE     = "sell_validation_results.json"

# Spread / slippage model
SPREAD_DOLLARS = 0.30       # per side (slightly higher than 1H model)
HIGH_ATR_MULT  = 1.5
SLIP_EXTRA     = 0.05

# BB width percentile filter
BB_WIDTH_LOOKBACK = 50
BB_WIDTH_MIN_PCT  = 25.0

# ADX filter
ADX_TREND_THRESHOLD = 25
ADX_LOOKBACK        = 14

# BB_RSI lower confluence
BB_RSI_MIN_CONFLUENCE = 2

# Monthly drawdown brake
MONTHLY_DRAWDOWN_BRAKE   = 150.0
MONTHLY_BRAKE_MULTIPLIER = 0.5

# ALL patterns enabled — key difference from production
DISABLED_PATTERNS: List[str] = []

# Calendar days to fetch before test period start (ensures EMA200 is fully initialised)
# 400 calendar days ≈ 280 trading days >> WARMUP_BARS (220)
WARMUP_BUFFER_DAYS = 400

TEST_PERIODS = [
    {
        "name":   "Bear Market 2011-2015",
        "start":  "2011-09-01",
        "end":    "2015-12-31",
        "regime": "BEAR",
        "note":   "Gold fell from $1900 to $1050 (-45%)",
    },
    {
        "name":   "Choppy 2018-2019",
        "start":  "2018-01-01",
        "end":    "2019-06-30",
        "regime": "CHOPPY",
        "note":   "Sideways consolidation, range $1150-$1370",
    },
    {
        "name":   "Correction 2022",
        "start":  "2022-03-01",
        "end":    "2022-11-30",
        "regime": "CORRECTION",
        "note":   "Gold fell from $2050 to $1620 (-21%)",
    },
    {
        "name":   "Mixed 2020-2021",
        "start":  "2020-08-01",
        "end":    "2021-12-31",
        "regime": "MIXED",
        "note":   "Post-COVID rally then consolidation",
    },
]


# ── Data fetching ─────────────────────────────────────────────────────────────
def fetch_period_data(start_date: str, end_date: str, name: str) -> Optional[pd.DataFrame]:
    """Fetch daily GC=F data for a test period with warmup buffer prepended."""
    start_dt    = datetime.strptime(start_date, "%Y-%m-%d")
    warmup_from = start_dt - timedelta(days=WARMUP_BUFFER_DAYS)

    print(f"  Downloading {name} ...")
    print(f"  Warmup from : {warmup_from.strftime('%Y-%m-%d')}  |  Test end: {end_date}")

    try:
        df = yf.Ticker(SYMBOL).history(
            start=warmup_from.strftime("%Y-%m-%d"),
            end=end_date,
            interval=DATA_INTERVAL,
        )
    except Exception as exc:
        print(f"  WARNING: yfinance error for {name}: {exc}. Skipping.")
        return None

    if df is None or df.empty:
        print(f"  WARNING: No data returned for {name}. Skipping.")
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Normalise to standard OHLCV column names
    col_map = {}
    for c in df.columns:
        cl = c.lower()
        if cl == "open":   col_map[c] = "Open"
        elif cl == "high":   col_map[c] = "High"
        elif cl == "low":    col_map[c] = "Low"
        elif cl == "close":  col_map[c] = "Close"
        elif cl == "volume": col_map[c] = "Volume"
    df = df.rename(columns=col_map)
    needed = [c for c in ["Open","High","Low","Close","Volume"] if c in df.columns]
    df = df[needed].dropna()

    # Strip timezone for resample compatibility
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_convert("UTC").tz_localize(None)
    df.index = pd.to_datetime(df.index)
    df = df[df.index.dayofweek < 5]   # weekdays only

    trading_days = df.index.normalize().nunique()
    print(f"  Total bars (incl. warmup): {len(df):,}  ({trading_days} trading days)")
    print(f"  Range: {df.index[0].date()} to {df.index[-1].date()}")

    if len(df) < WARMUP_BARS + 20:
        print(f"  WARNING: Only {len(df)} bars — not enough for warmup ({WARMUP_BARS} required). Skipping.")
        return None

    return df


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

    # Rolling ATR mean (for slippage model)
    atr_col = next(
        (c for c in df.columns if c.startswith("ATRr_") or (c.startswith("ATR") and "mean" not in c)),
        None,
    )
    if atr_col:
        df["atr_mean"] = df[atr_col].rolling(50, min_periods=10).mean()

    # Inline Wilder ADX
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
        df["bb_width_pct"] = 50.0

    return df


def _calc_adx_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    pc = close.shift(1)
    ph = high.shift(1)
    pl = low.shift(1)
    tr = pd.concat([(high - low), (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    up   = high - ph
    down = pl - low
    plus_dm  = np.where((up > down)   & (up > 0),   up,   0.0)
    minus_dm = np.where((down > up)   & (down > 0), down, 0.0)
    alpha = 1.0 / period
    tr14  = pd.Series(tr.values,   index=df.index).ewm(alpha=alpha, adjust=False).mean()
    pdm14 = pd.Series(plus_dm,     index=df.index).ewm(alpha=alpha, adjust=False).mean()
    mdm14 = pd.Series(minus_dm,    index=df.index).ewm(alpha=alpha, adjust=False).mean()
    tr14s = tr14.replace(0, np.nan)
    pdi   = 100 * pdm14 / tr14s
    mdi   = 100 * mdm14 / tr14s
    dx    = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=alpha, adjust=False).mean().fillna(0.0)


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


# ── Signal generation ─────────────────────────────────────────────────────────
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

    # EMA200 trend gate — zone within 0.3 ATR is neutral
    if ema200 > 0:
        trend_up   = close > ema200 + 0.3 * atr
        trend_down = close < ema200 - 0.3 * atr
    else:
        trend_up = trend_down = True   # indicator not ready yet

    buy_r:  List[str] = []
    sell_r: List[str] = []

    if rsi > 0:
        if rsi < RSI_BUY:    buy_r.append(f"RSI oversold ({rsi:.1f})")
        elif rsi > RSI_SELL: sell_r.append(f"RSI overbought ({rsi:.1f})")

    if ema20 > 0:
        if close > ema20: buy_r.append("Price above EMA20")
        else:             sell_r.append("Price below EMA20")

    if ema20 > 0 and ema50 > 0:
        if ema20 > ema50: buy_r.append("EMA20 > EMA50 uptrend")
        else:             sell_r.append("EMA20 < EMA50 downtrend")

    if macd_v != 0 or macd_s != 0:
        if macd_v > macd_s: buy_r.append("MACD bullish")
        else:               sell_r.append("MACD bearish")

    bb_range = bb_u - bb_l
    if bb_range > 0:
        bp = (close - bb_l) / bb_range
        if bp < 0.2:   buy_r.append(f"Near lower BB ({bp:.0%})")
        elif bp > 0.8: sell_r.append(f"Near upper BB ({bp:.0%})")

    buy_n, sell_n = len(buy_r), len(sell_r)

    if not trend_up:   buy_n  = 0
    if not trend_down: sell_n = 0

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

    # ADX filter: EMA_MACD_TREND_SELL requires confirmed trend momentum
    adx_val = _f(df, "adx", i, 0.0)
    if direction == "SELL" and "EMA_MACD_TREND" in pattern:
        if adx_val < ADX_TREND_THRESHOLD:
            return None

    if pattern in DISABLED_PATTERNS:
        return None

    return dict(
        direction=direction, reasons=reasons, count=count,
        entry=close, sl=sl, tp=tp, rr=round(rr, 2),
        atr=atr, ema200=ema200, adx=adx_val, pattern=pattern,
    )


def detect_regime(df: pd.DataFrame, i: int) -> str:
    close   = float(df["Close"].iloc[i])
    atr     = _f(df, "ATRr_", i) or _f(df, "ATR", i)
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
    has_rsi  = any("RSI"      in r for r in reasons)
    has_ecx  = any("EMA20 >" in r or "EMA20 <" in r for r in reasons)
    has_macd = any("MACD"     in r for r in reasons)
    has_bb   = any("BB"       in r for r in reasons)
    has_ep   = any("above EMA20" in r or "below EMA20" in r for r in reasons)
    if has_rsi and has_macd and has_ecx: return f"TRIPLE_SIGNAL_{direction}"
    if has_rsi and has_bb:               return f"BB_RSI_REVERSAL_{direction}"
    if has_ecx and has_macd:             return f"EMA_MACD_TREND_{direction}"
    if has_rsi and has_ep:               return f"RSI_EMA_SIGNAL_{direction}"
    if has_rsi:                          return f"RSI_SIGNAL_{direction}"
    if has_ecx:                          return f"EMA_TREND_{direction}"
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
    spread_cost: float = 0.0
    net_pnl: float = 0.0
    monthly_brake_active: bool = False


# ── Simulation ────────────────────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame) -> Tuple[List[BT], pd.Series, Dict]:
    n           = len(df)
    trades:     List[BT] = []
    open_trade: Optional[BT] = None
    equity      = ACCOUNT_SIZE
    equity_pts: List[float] = [equity] * WARMUP_BARS

    daily_loss:  float = 0.0
    daily_date:  str   = ""
    consec_loss: int   = 0
    trade_id:    int   = 0

    sk = dict(volatile=0, session=0, daily=0, rr=0, trend=0, consec=0, bb_width=0, adx=0, ambiguous=0)

    monthly_pnl:   float = 0.0
    monthly_month: str   = ""

    close = ACCOUNT_SIZE  # fallback for expiry

    for i in range(WARMUP_BARS, n):
        bar   = df.index[i]
        bdate = bar.strftime("%Y-%m-%d")
        btime = bar.strftime("%Y-%m-%d %H:%M")

        hi    = float(df["High"].iloc[i])
        lo    = float(df["Low"].iloc[i])
        close = float(df["Close"].iloc[i])

        if bdate != daily_date:
            daily_loss  = 0.0
            consec_loss = 0
            daily_date  = bdate

        bmonth = bdate[:7]
        if bmonth != monthly_month:
            monthly_pnl   = 0.0
            monthly_month = bmonth

        # ── Check open position ────────────────────────────────────────────
        if open_trade is not None:
            t = open_trade
            if t.direction == "BUY":
                hit_sl = lo <= t.stop_loss
                hit_tp = hi >= t.take_profit
            else:
                hit_sl = hi >= t.stop_loss
                hit_tp = lo <= t.take_profit

            bar_atr  = _f(df, "ATRr_", i) or _f(df, "ATR", i)
            atr_mean_val = (
                float(df["atr_mean"].iloc[i])
                if "atr_mean" in df.columns and np.isfinite(df["atr_mean"].iloc[i])
                else bar_atr
            )

            if hit_sl and hit_tp:
                sk["ambiguous"] += 1
                # Conservative: assume LOSS when both SL and TP touch same bar
                print(f"  WARNING: ambiguous bar {btime} trade#{t.id} — "
                      f"both SL ({t.stop_loss:.2f}) and TP ({t.take_profit:.2f}) hit. Assuming LOSS.")

            if hit_sl:
                t.exit_price  = t.stop_loss
                t.pnl         = round(-t.risk_amount, 2)
                extra_slip    = SLIP_EXTRA if bar_atr > atr_mean_val * HIGH_ATR_MULT else 0.0
                t.spread_cost = round(t.lot_size * 100 * (SPREAD_DOLLARS * 2 + extra_slip), 2)
                t.net_pnl     = round(t.pnl - t.spread_cost, 2)
                t.status      = "LOSS"
                t.close_time  = btime
                equity       += t.net_pnl
                daily_loss   += abs(t.net_pnl)
                monthly_pnl  += t.net_pnl
                consec_loss  += 1
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
        # Session filter intentionally disabled for daily bars
        regime = detect_regime(df, i)
        if regime == "VOLATILE":
            sk["volatile"] += 1
            continue

        if daily_loss >= DAILY_LOSS_LIMIT:
            sk["daily"] += 1
            continue

        if consec_loss >= MAX_CONSEC_LOSS:
            sk["consec"] += 1
            continue

        sig = generate_signal(df, i)
        if sig is None:
            bb_w = _f(df, "bb_width_pct", i, 50.0)
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

        # Apply spread to entry
        if sig["direction"] == "BUY":
            actual_entry = sig["entry"] + SPREAD_DOLLARS
        else:
            actual_entry = sig["entry"] - SPREAD_DOLLARS

        max_risk    = equity * MAX_RISK_PCT
        remaining   = DAILY_LOSS_LIMIT - daily_loss
        risk_amount = min(max_risk, remaining)
        stop_dist   = abs(actual_entry - sig["sl"])
        if stop_dist <= 0:
            continue
        raw_lot = risk_amount / (GOLD_CONTRACT * stop_dist)
        lot     = max(MIN_LOT, min(MAX_LOT, round(raw_lot / LOT_STEP) * LOT_STEP))

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

    eq_series = pd.Series(equity_pts, index=df.index[: len(equity_pts)])
    return trades, eq_series, sk


# ── Analytics helpers ─────────────────────────────────────────────────────────
def daily_sharpe(eq: pd.Series) -> float:
    if eq is None or len(eq) < 5:
        return 0.0
    daily_eq  = eq.resample("B").last().dropna()
    daily_ret = daily_eq.pct_change().dropna()
    if len(daily_ret) < 2 or daily_ret.std() == 0:
        return 0.0
    return float((daily_ret.mean() / daily_ret.std()) * np.sqrt(252))


def max_drawdown_pct(eq: pd.Series) -> float:
    vals = eq.values
    peak = vals[0]
    max_dd = 0.0
    for v in vals:
        peak = max(peak, v)
        dd = (peak - v) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    return max_dd


def build_equity_curve(trades: List[BT]) -> pd.Series:
    """Equity curve from BT objects (WIN/LOSS only, starting at ACCOUNT_SIZE)."""
    closed = [t for t in trades if t.status in ("WIN", "LOSS")]
    if not closed:
        return pd.Series([ACCOUNT_SIZE], index=[pd.Timestamp.now()])
    equity = ACCOUNT_SIZE
    pts    = [equity]
    times  = [pd.Timestamp(closed[0].open_time)]
    for t in closed:
        equity += t.net_pnl
        pts.append(equity)
        times.append(pd.Timestamp(t.close_time))
    return pd.Series(pts, index=times)


def build_equity_curve_from_dicts(trade_dicts: List[Dict]) -> pd.Series:
    """Equity curve from serialised trade dicts (for combined-period analysis)."""
    closed = sorted(
        [t for t in trade_dicts if t["status"] in ("WIN", "LOSS")],
        key=lambda x: x["close_time"],
    )
    if not closed:
        return pd.Series([ACCOUNT_SIZE], index=[pd.Timestamp.now()])
    equity = ACCOUNT_SIZE
    pts    = [equity]
    times  = [pd.Timestamp(closed[0]["open_time"])]
    for t in closed:
        equity += t["net_pnl"]
        pts.append(equity)
        times.append(pd.Timestamp(t["close_time"]))
    return pd.Series(pts, index=times)


def direction_stats(trades: List[BT], direction: str) -> Dict:
    """Compute stats for one direction from BT objects."""
    closed = [t for t in trades if t.direction == direction and t.status in ("WIN", "LOSS")]
    if not closed:
        return {}
    wins   = [t for t in closed if t.status == "WIN"]
    losses = [t for t in closed if t.status == "LOSS"]
    total  = len(closed)

    gw = sum(t.net_pnl for t in wins)
    gl = abs(sum(t.net_pnl for t in losses))
    pf = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)

    eq     = build_equity_curve(closed)
    sharpe = daily_sharpe(eq)
    max_dd = max_drawdown_pct(eq)

    # Per-pattern breakdown
    patterns: Dict[str, Dict] = {}
    for t in closed:
        s = patterns.setdefault(t.pattern, {"total": 0, "wins": 0, "pnl": 0.0, "wins_pnl": 0.0, "loss_pnl": 0.0})
        s["total"] += 1
        s["pnl"]   += t.net_pnl
        if t.status == "WIN":
            s["wins"]     += 1
            s["wins_pnl"] += t.net_pnl
        else:
            s["loss_pnl"] += abs(t.net_pnl)

    for p, s in patterns.items():
        s["pf"]      = s["wins_pnl"] / s["loss_pnl"] if s["loss_pnl"] > 0 else (float("inf") if s["wins_pnl"] > 0 else 0.0)
        s["avg_pnl"] = s["pnl"] / s["total"] if s["total"] > 0 else 0.0
        s["win_rate"] = s["wins"] / s["total"] if s["total"] > 0 else 0.0

    return {
        "total":         total,
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      len(wins) / total,
        "net_pnl":       sum(t.net_pnl for t in closed),
        "profit_factor": pf,
        "sharpe":        sharpe,
        "max_dd":        max_dd,
        "patterns":      patterns,
    }


# ── Period analysis ───────────────────────────────────────────────────────────
def analyze_period(period: Dict, all_trades: List[BT]) -> Dict:
    """Filter trades to the test window and split by direction."""
    # Exclude warmup trades (before period start) and EXPIRED trades from stats
    period_trades = [
        t for t in all_trades
        if t.open_time >= period["start"] and t.status in ("WIN", "LOSS", "EXPIRED")
    ]
    sell_stats = direction_stats(period_trades, "SELL")
    buy_stats  = direction_stats(period_trades, "BUY")

    # Count bars in test window
    return {
        "period":       period,
        "total_trades": len([t for t in period_trades if t.status in ("WIN","LOSS")]),
        "sell":         sell_stats,
        "buy":          buy_stats,
        "all_trades":   [asdict(t) for t in period_trades],
    }


# ── Report printing ───────────────────────────────────────────────────────────
def _pf_str(pf: float) -> str:
    if pf == float("inf"):
        return "inf"
    return f"{pf:.2f}"


def print_period_report(result: Dict) -> None:
    period = result["period"]
    print(f"\n{'='*72}")
    print(f"  PERIOD: {period['name']} ({period['regime']})")
    print(f"  {period['note']}")
    print(f"  Dates: {period['start']} to {period['end']}")
    print(f"  Total closed trades in period: {result['total_trades']}")
    print(f"{'='*72}")

    for direction, label in [("sell", "SELL only"), ("buy", "BUY only (sanity check)")]:
        stats = result.get(direction, {})
        print(f"\nPATTERN BREAKDOWN — {label}:")
        if not stats or not stats.get("patterns"):
            print(f"  No {direction.upper()} trades in this period.")
            continue

        rows = []
        for p, s in sorted(stats["patterns"].items(), key=lambda x: x[1]["pnl"], reverse=True):
            t  = s["total"]
            wr = f"{s['win_rate']:.0%}" if t else "-"
            pf = _pf_str(s.get("pf", 0.0))
            avg = f"${s.get('avg_pnl', 0):+.2f}"
            rows.append([p[:34], t, wr, f"${s['pnl']:+.2f}", pf, avg])
        print(tabulate(rows, headers=["Pattern","Trades","Win%","Net P&L","PF","Avg/Trade"], tablefmt="simple"))

        print(f"\nOVERALL METRICS — {direction.upper()} only:")
        print(f"  Total trades  : {stats['total']}")
        print(f"  Win rate      : {stats['win_rate']:.1%}")
        print(f"  Net P&L       : ${stats['net_pnl']:+,.2f}")
        print(f"  Profit factor : {_pf_str(stats['profit_factor'])}")
        print(f"  Sharpe        : {stats['sharpe']:.2f}")
        print(f"  Max drawdown  : {stats['max_dd']:.1f}%")


def print_combined_report(results: List[Optional[Dict]]) -> None:
    valid = [r for r in results if r is not None]
    print("\n" + "=" * 72)
    print("  COMBINED SELL ANALYSIS ACROSS ALL NON-BULL PERIODS")
    print("=" * 72)

    # Per-period summary table
    print("\nSELL TRADES BY PERIOD:")
    period_rows = []
    for r in valid:
        s = r.get("sell", {})
        if not s:
            period_rows.append([r["period"]["name"][:30], 0, "-", "$0.00", "-", "-", "-"])
            continue
        period_rows.append([
            r["period"]["name"][:30],
            s.get("total", 0),
            f"{s.get('win_rate', 0):.0%}",
            f"${s.get('net_pnl', 0):+,.2f}",
            _pf_str(s.get("profit_factor", 0.0)),
            f"{s.get('sharpe', 0):.2f}",
            f"{s.get('max_dd', 0):.1f}%",
        ])
    print(tabulate(
        period_rows,
        headers=["Period","Trades","WR","Net P&L","PF","Sharpe","Max DD"],
        tablefmt="simple",
    ))

    # Collect all SELL trades from all periods
    all_sell: List[Dict] = []
    pat_by_period: Dict[str, Dict[str, float]] = {}   # {pattern: {period: pnl}}

    for r in valid:
        pname = r["period"]["name"]
        for td in r.get("all_trades", []):
            if td["direction"] == "SELL" and td["status"] in ("WIN", "LOSS"):
                all_sell.append(td)
                p = td["pattern"]
                pat_by_period.setdefault(p, {})[pname] = (
                    pat_by_period.get(p, {}).get(pname, 0.0) + td["net_pnl"]
                )

    total_sell = len(all_sell)
    if total_sell == 0:
        print("\n  No SELL trades found across any period.")
        return

    # Aggregate per pattern
    pat_combined: Dict[str, Dict] = {}
    for td in all_sell:
        p = td["pattern"]
        s = pat_combined.setdefault(p, {"total":0,"wins":0,"pnl":0.0,"wins_pnl":0.0,"loss_pnl":0.0})
        s["total"] += 1
        s["pnl"]   += td["net_pnl"]
        if td["status"] == "WIN":
            s["wins"]     += 1
            s["wins_pnl"] += td["net_pnl"]
        else:
            s["loss_pnl"] += abs(td["net_pnl"])

    print("\nCOMBINED SELL BY PATTERN (all 4 periods):")
    pat_rows = []
    for p in sorted(pat_combined.keys()):
        s = pat_combined[p]
        t = s["total"]
        wr = f"{s['wins']/t:.0%}" if t else "-"
        pf = s["wins_pnl"] / s["loss_pnl"] if s["loss_pnl"] > 0 else (float("inf") if s["wins_pnl"] > 0 else 0.0)
        ppnl = pat_by_period.get(p, {})
        bp = max(ppnl.items(), key=lambda x: x[1])[0][:20] if ppnl else "-"
        wp = min(ppnl.items(), key=lambda x: x[1])[0][:20] if ppnl else "-"
        pat_rows.append([p[:34], t, wr, f"${s['pnl']:+,.2f}", _pf_str(pf), bp, wp])
    print(tabulate(pat_rows,
                   headers=["Pattern","Trades","WR","Net P&L","PF","Best Period","Worst Period"],
                   tablefmt="simple"))

    # Overall combined SELL totals
    total_wins   = sum(1 for t in all_sell if t["status"] == "WIN")
    total_pnl    = sum(t["net_pnl"] for t in all_sell)
    wins_pnl_sum = sum(t["net_pnl"] for t in all_sell if t["status"] == "WIN")
    loss_pnl_sum = abs(sum(t["net_pnl"] for t in all_sell if t["status"] == "LOSS"))
    combined_pf  = wins_pnl_sum / loss_pnl_sum if loss_pnl_sum > 0 else float("inf")
    combined_wr  = total_wins / total_sell if total_sell > 0 else 0.0

    print(f"\nCOMBINED SELL TOTALS:")
    print(f"  Total SELL trades : {total_sell}")
    print(f"  Win rate          : {combined_wr:.1%}")
    print(f"  Net P&L           : ${total_pnl:+,.2f}")
    print(f"  Profit factor     : {_pf_str(combined_pf)}")
    if total_sell < 50:
        print(f"\n  *** STATISTICAL SIGNIFICANCE WARNING ***")
        print(f"  Only {total_sell} SELL trades across all periods.")
        print(f"  Minimum for reliable conclusions: 50 trades.")
        print(f"  Treat these results as indicative only, not conclusive.")


def print_decision_framework(results: List[Optional[Dict]]) -> None:
    valid = [r for r in results if r is not None]

    # Collect all SELL trades and per-period stats
    all_sell: List[Dict] = []
    for r in valid:
        for td in r.get("all_trades", []):
            if td["direction"] == "SELL" and td["status"] in ("WIN","LOSS"):
                all_sell.append(td)

    total_sell = len(all_sell)

    print("\n" + "=" * 72)
    print("  DECISION FRAMEWORK")
    print("=" * 72)

    if total_sell == 0:
        print("\n  No SELL trades generated across any period.")
        print("  This means the EMA200 trend filter blocked all SELL signals.")
        print("  The system never saw price far enough below EMA200 for SELL to trigger.")
        print("\n  VERDICT: INCONCLUSIVE — EMA200 filter too restrictive on daily bars.")
        print("  RECOMMENDED: Lower ATR_STOP_MULT or soften the EMA200 zone threshold")
        print("  and re-run, OR accept that the system architecture strongly resists SELL.")
        return

    # Metrics
    wins_pnl_sum = sum(t["net_pnl"] for t in all_sell if t["status"] == "WIN")
    loss_pnl_sum = abs(sum(t["net_pnl"] for t in all_sell if t["status"] == "LOSS"))
    combined_pf  = wins_pnl_sum / loss_pnl_sum if loss_pnl_sum > 0 else float("inf")
    combined_wr  = sum(1 for t in all_sell if t["status"] == "WIN") / total_sell

    profitable_periods = sum(1 for r in valid if r.get("sell", {}).get("net_pnl", 0) > 0)
    worst_period_pnl   = min((r.get("sell", {}).get("net_pnl", 0) for r in valid), default=0)

    # Bear + Correction PF average
    bc_pfs = [
        r["sell"]["profit_factor"]
        for r in valid
        if r["period"]["regime"] in ("BEAR","CORRECTION") and r.get("sell") and r["sell"].get("total", 0) > 0
    ]
    bear_correction_pf = sum(bc_pfs) / len(bc_pfs) if bc_pfs else 0.0

    # Worst period max DD
    max_dd_all_periods = max(
        (r.get("sell", {}).get("max_dd", 0) for r in valid), default=0
    )

    print(f"\n  Combined PF              : {_pf_str(combined_pf)}")
    print(f"  Combined WR              : {combined_wr:.1%}")
    print(f"  Profitable periods       : {profitable_periods}/4")
    print(f"  Worst period P&L         : ${worst_period_pnl:+,.2f}")
    print(f"  Bear/Correction avg PF   : {_pf_str(bear_correction_pf)}")
    print(f"  Worst period max DD      : {max_dd_all_periods:.1f}%")
    print(f"  Total SELL trades tested : {total_sell}")

    # Evaluate criteria
    stat_sig = total_sell >= 50

    option_a = (
        stat_sig
        and combined_pf  >= 1.20
        and combined_wr  >= 0.35
        and profitable_periods >= 3
        and worst_period_pnl   >= -500.0
    )
    option_b = (
        stat_sig
        and bear_correction_pf >= 1.30
        and (combined_pf < 1.20 or profitable_periods < 3)
    )

    print("\n  OPTION A (Strong Edge) criteria:")
    print(f"    PF >= 1.20        : {combined_pf:.2f}  {'PASS' if combined_pf >= 1.20 else 'FAIL'}")
    print(f"    WR >= 35%         : {combined_wr:.1%}  {'PASS' if combined_wr >= 0.35 else 'FAIL'}")
    print(f"    Profitable 3+/4   : {profitable_periods}/4  {'PASS' if profitable_periods >= 3 else 'FAIL'}")
    print(f"    Worst >= -$500    : ${worst_period_pnl:+,.0f}  {'PASS' if worst_period_pnl >= -500 else 'FAIL'}")

    print("\n  OPTION B (Regime-Conditional) criteria:")
    print(f"    Bear/Corr PF >= 1.30  : {_pf_str(bear_correction_pf)}  {'PASS' if bear_correction_pf >= 1.30 else 'FAIL'}")
    print(f"    Combined PF < 1.20    : {combined_pf:.2f}  {'PASS' if combined_pf < 1.20 else 'N/A'}")

    if not stat_sig:
        decision  = "INCONCLUSIVE"
        reasoning = (f"Only {total_sell} SELL trades — below 50 trade minimum for statistical significance. "
                     "Cannot make a reliable decision. Consider extending date ranges or relaxing filters.")
    elif option_a:
        decision  = "A — STRONG EDGE"
        reasoning = (f"PF {combined_pf:.2f} >= 1.20, WR {combined_wr:.1%} >= 35%, "
                     f"profitable in {profitable_periods}/4 periods, worst period ${worst_period_pnl:+.0f} >= -$500. "
                     "SELL patterns show reliable edge across non-bull regimes. "
                     "Next: integrate with regime-aware activation, paper-trade 2+ weeks.")
    elif option_b:
        decision  = "B — REGIME-CONDITIONAL EDGE"
        reasoning = (f"Bear/Correction avg PF {_pf_str(bear_correction_pf)} >= 1.30 "
                     f"but combined PF {combined_pf:.2f} < 1.20 across all 4 regimes. "
                     "SELL edge exists in strong downtrends but not in choppy/mixed markets. "
                     "Next: build RegimeMonitorAgent first, then activate SELL only in BEAR/CORRECTION.")
    else:
        decision  = "C — WEAK / NO EDGE"
        reasoning = (f"Combined PF {_pf_str(combined_pf)} below 1.10 threshold or "
                     f"excessive drawdown ({max_dd_all_periods:.1f}%). "
                     "SELL patterns do not show reliable edge even in non-bull markets. "
                     "Accept BUY-only as optimal for this system. "
                     "Revisit only if gold enters a confirmed multi-year bear market.")

    print(f"\n  VERDICT   : {decision}")
    print(f"  REASONING : {reasoning}")


def print_final_summary(results: List[Optional[Dict]]) -> None:
    valid = [r for r in results if r is not None]
    all_sell: List[Dict] = []
    for r in valid:
        for td in r.get("all_trades", []):
            if td["direction"] == "SELL" and td["status"] in ("WIN","LOSS"):
                all_sell.append(td)

    total_sell = len(all_sell)

    print("\n" + "=" * 72)
    print("  FINAL SUMMARY")
    print("=" * 72)

    if total_sell == 0:
        print("1. Total SELL trades tested  : 0")
        print("2. Combined SELL Sharpe      : N/A")
        print("3. Combined SELL profit factor: N/A")
        print("4. Decision                  : INCONCLUSIVE — no SELL trades generated")
        print("5. Recommended next action   : Review EMA200 zone threshold (0.3 * ATR may be too wide on daily bars)")
        return

    wins_pnl = sum(t["net_pnl"] for t in all_sell if t["status"] == "WIN")
    loss_pnl = abs(sum(t["net_pnl"] for t in all_sell if t["status"] == "LOSS"))
    combined_pf = wins_pnl / loss_pnl if loss_pnl > 0 else float("inf")
    combined_wr = sum(1 for t in all_sell if t["status"] == "WIN") / total_sell

    # Combined Sharpe across all SELL trades sorted by close_time
    try:
        eq_combined = build_equity_curve_from_dicts(all_sell)
        combined_sharpe = daily_sharpe(eq_combined)
    except Exception:
        combined_sharpe = float("nan")

    profitable_periods = sum(1 for r in valid if r.get("sell", {}).get("net_pnl", 0) > 0)
    worst_period_pnl   = min((r.get("sell", {}).get("net_pnl", 0) for r in valid), default=0)
    bc_pfs = [
        r["sell"]["profit_factor"]
        for r in valid
        if r["period"]["regime"] in ("BEAR","CORRECTION") and r.get("sell") and r["sell"].get("total", 0) > 0
    ]
    bear_pf = sum(bc_pfs) / len(bc_pfs) if bc_pfs else 0.0

    stat_sig = total_sell >= 50
    if not stat_sig:
        decision = "INCONCLUSIVE (< 50 trades)"
        action   = "Cannot draw statistical conclusions — extend date ranges or review signal thresholds"
    elif (combined_pf >= 1.20 and combined_wr >= 0.35
          and profitable_periods >= 3 and worst_period_pnl >= -500):
        decision = "A — STRONG EDGE"
        action   = "Integrate SELL patterns with regime-aware activation; paper-trade 2 weeks minimum"
    elif bear_pf >= 1.30 and combined_pf < 1.20:
        decision = "B — REGIME-CONDITIONAL EDGE"
        action   = "Build RegimeMonitorAgent first; enable SELL only in confirmed BEAR/CORRECTION regime"
    else:
        decision = "C — NO EDGE"
        action   = "Stay BUY-only; revisit when gold enters a confirmed multi-year bear market"

    sharpe_str = f"{combined_sharpe:.2f}" if not (isinstance(combined_sharpe, float) and np.isnan(combined_sharpe)) else "N/A"
    print(f"1. Total SELL trades tested  : {total_sell}")
    print(f"2. Combined SELL Sharpe      : {sharpe_str}")
    print(f"3. Combined SELL profit factor: {_pf_str(combined_pf)}")
    print(f"4. Decision                  : {decision}")
    print(f"5. Recommended next action   : {action}")


# ── Sanity-check printing ─────────────────────────────────────────────────────
def print_sanity_checks(results: List[Optional[Dict]], df_bars: Dict[str, int]) -> None:
    valid = [r for r in results if r is not None]
    print("\n" + "=" * 72)
    print("  SANITY CHECKS")
    print("=" * 72)

    print("\nData quality (bars in test window):")
    rows = []
    for r in valid:
        pname  = r["period"]["name"]
        bars   = df_bars.get(pname, "?")
        expect = "~1,050" if "Bear" in pname else "~375" if "Correction" in pname else "~375"
        status = "OK" if isinstance(bars, int) and bars >= 200 else "LOW"
        rows.append([pname[:30], bars, expect, status])
    print(tabulate(rows, headers=["Period","Bars","Expected","Status"], tablefmt="simple"))

    print("\nCross-validation (BUY in same periods — should make intuitive sense):")
    cv_rows = []
    for r in valid:
        regime = r["period"]["regime"]
        buy    = r.get("buy", {})
        if not buy:
            cv_rows.append([r["period"]["name"][:30], 0, "-", "$0", "?"])
            continue
        expected_dir = "LOSS" if regime == "BEAR" else "WIN" if regime == "MIXED" else "?"
        actual_dir   = "WIN" if buy.get("net_pnl", 0) > 0 else "LOSS"
        match        = "OK" if expected_dir == "?" or actual_dir == expected_dir else "UNEXPECTED"
        cv_rows.append([
            r["period"]["name"][:30],
            buy.get("total", 0),
            f"{buy.get('win_rate', 0):.0%}",
            f"${buy.get('net_pnl', 0):+,.0f}",
            match,
        ])
    print(tabulate(cv_rows, headers=["Period","BUY Trades","BUY WR","BUY P&L","Expected?"], tablefmt="simple"))
    print("  (Bear period: BUY should lose. Mixed 2020-2021: BUY should win.)")
    print("  If BUY shows opposite of expected, the backtest likely has a bug.")


# ── Results saving ────────────────────────────────────────────────────────────
def save_results(results: List[Optional[Dict]]) -> None:
    valid = [r for r in results if r is not None]
    all_sell: List[Dict] = []
    for r in valid:
        for td in r.get("all_trades", []):
            if td["direction"] == "SELL" and td["status"] in ("WIN","LOSS"):
                all_sell.append(td)

    wins_pnl = sum(t["net_pnl"] for t in all_sell if t["status"] == "WIN")
    loss_pnl = abs(sum(t["net_pnl"] for t in all_sell if t["status"] == "LOSS"))

    out: Dict = {
        "generated": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "research_question": "Do SELL patterns have edge in non-bull market regimes?",
        "config": {
            "interval":         DATA_INTERVAL,
            "rr":               MIN_RR,
            "rsi_buy":          RSI_BUY,
            "rsi_sell":         RSI_SELL,
            "trend_ema":        TREND_EMA,
            "atr_stop_mult":    ATR_STOP_MULT,
            "atr_volatile_pct": ATR_VOLATILE_PCT,
            "disabled_patterns": DISABLED_PATTERNS,
            "session_filter":   USE_SESSION_FILTER,
            "spread_dollars":   SPREAD_DOLLARS,
        },
        "combined_sell": {
            "total_trades":   len(all_sell),
            "wins":           sum(1 for t in all_sell if t["status"] == "WIN"),
            "win_rate":       sum(1 for t in all_sell if t["status"] == "WIN") / len(all_sell) if all_sell else 0,
            "net_pnl":        sum(t["net_pnl"] for t in all_sell),
            "profit_factor":  wins_pnl / loss_pnl if loss_pnl > 0 else None,
        },
        "periods": [],
    }

    for r in valid:
        def _clean(d: Dict) -> Dict:
            return {k: v for k, v in d.items() if k != "patterns"}

        period_out = {
            "name":    r["period"]["name"],
            "regime":  r["period"]["regime"],
            "note":    r["period"]["note"],
            "start":   r["period"]["start"],
            "end":     r["period"]["end"],
            "total_trades": r["total_trades"],
            "sell":         _clean(r.get("sell", {})),
            "sell_patterns": r.get("sell", {}).get("patterns", {}),
            "buy":          _clean(r.get("buy", {})),
            "buy_patterns":  r.get("buy", {}).get("patterns", {}),
            "trades":       r.get("all_trades", []),
        }
        out["periods"].append(period_out)

    pathlib.Path(RESULTS_FILE).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nResults saved to {RESULTS_FILE}")


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 72)
    print("  SELL PATTERN VALIDATION BACKTEST — Research Only")
    print("  Question: Do SELL patterns have edge in non-bull market regimes?")
    print(f"  Interval     : {DATA_INTERVAL} (daily bars)")
    print(f"  ATR stop mult: {ATR_STOP_MULT}  (wider than 1H baseline of 1.5)")
    print(f"  Spread       : ${SPREAD_DOLLARS}/side")
    print(f"  Session filter: {USE_SESSION_FILTER} (daily bars — no intraday concept)")
    print(f"  DISABLED_PATTERNS: {DISABLED_PATTERNS or 'none (all patterns enabled)'}")
    print(f"  Periods tested: {len(TEST_PERIODS)}")
    print("=" * 72 + "\n")

    all_results:  List[Optional[Dict]] = []
    bars_per_period: Dict[str, int] = {}

    for period in TEST_PERIODS:
        print(f"\n{'='*72}")
        print(f"  FETCHING: {period['name']} ({period['regime']})")
        print(f"  {period['note']}")
        print(f"{'='*72}")

        df = fetch_period_data(period["start"], period["end"], period["name"])
        if df is None:
            print(f"  Skipping {period['name']} — data unavailable.")
            all_results.append(None)
            continue

        print("  Computing indicators ...")
        df = add_indicators(df)

        print("  Running bar-by-bar simulation ...")
        trades, eq, diag = run_backtest(df)

        # Count bars in the actual test window (excluding warmup)
        test_bars = df[df.index >= pd.Timestamp(period["start"])].shape[0]
        bars_per_period[period["name"]] = test_bars

        ambiguous = diag.get("ambiguous", 0)
        total_sim  = len([t for t in trades if t.status in ("WIN","LOSS")])
        print(f"  Simulation complete: {total_sim} closed trades total (incl. warmup window)")
        if ambiguous:
            print(f"  Ambiguous bars (SL+TP same bar): {ambiguous} — assumed LOSS (conservative)")

        result = analyze_period(period, trades)
        all_results.append(result)

        print_period_report(result)

    # Cross-period aggregation
    print_combined_report(all_results)
    print_decision_framework(all_results)
    print_sanity_checks(all_results, bars_per_period)
    print_final_summary(all_results)
    save_results(all_results)
