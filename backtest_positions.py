#!/usr/bin/env python3
"""
Concurrent Position Limit Research
Tests MAX_OPEN_POSITIONS = 1, 2, and unlimited (999) on the current
deployed config (ATR×2.5, RSI_CEIL=70, ADX symmetric, BUY-only).

Answers: does stacking positions increase or decrease risk-adjusted returns?

Usage: py -3.12 backtest_positions.py
"""

import json
import warnings
warnings.filterwarnings("ignore")

from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional
from pathlib import Path

import yfinance as yf
import pandas as pd
import pandas_ta as pta
import numpy as np

# ── Parameters — must match deployed gold_trading_agents.py exactly ───────────
ACCOUNT_SIZE       = 10_000.0
MAX_RISK_PCT       = 0.01         # 1% per trade
MIN_RR             = 2.0
DAILY_LOSS_LIMIT   = 300.0
MIN_CONFLUENCE     = 3
ATR_VOLATILE_PCT   = 1.0          # 1H threshold
ATR_STOP_MULT      = 2.5          # v5 deployed
GOLD_CONTRACT      = 100.0
MIN_LOT, MAX_LOT, LOT_STEP = 0.01, 10.0, 0.01
RSI_BUY            = 35
RSI_SELL           = 65
RSI_CEILING_BUY    = 70           # v5 fix
TREND_EMA          = 200
SESSION_START      = 8
SESSION_END        = 21
MAX_CONSEC_LOSS    = 2
WARMUP_BARS        = 220
SPREAD_DOLLARS     = 0.25
HIGH_ATR_MULT      = 1.5
SLIP_EXTRA         = 0.05
BB_WIDTH_LOOKBACK  = 50
BB_WIDTH_MIN_PCT   = 25.0
ADX_TREND_THRESHOLD = 25
ADX_LOOKBACK       = 14
BB_RSI_MIN_CONFLUENCE = 2
MONTHLY_DRAWDOWN_BRAKE   = 150.0
MONTHLY_BRAKE_MULTIPLIER = 0.5
DISABLED_PATTERNS  = ["EMA_MACD_TREND_SELL", "BB_RSI_REVERSAL_SELL"]
PERIOD_DAYS        = 725          # yfinance 1H limit


# ── Data + indicators (built once, shared across all runs) ────────────────────
def fetch_and_prepare() -> pd.DataFrame:
    end   = datetime.now()
    start = end - timedelta(days=PERIOD_DAYS)
    print(f"Downloading GC=F 1H ({PERIOD_DAYS}d)...")
    raw = yf.download(
        "GC=F",
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        interval="1h",
        progress=False,
        auto_adjust=True,
    )
    if raw.empty:
        raise RuntimeError("No data returned")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    df = raw[["Open","High","Low","Close","Volume"]].dropna()
    df.index = pd.to_datetime(df.index)
    df = df[df.index.dayofweek < 5]
    print(f"Bars: {len(df):,}  |  {df.index[0].date()} to {df.index[-1].date()}")

    print("Computing indicators...")
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

    atr_col = next((c for c in df.columns if c.startswith("ATRr_")), None)
    if atr_col:
        df["atr_mean"] = df[atr_col].rolling(50, min_periods=10).mean()

    # ADX (Wilder)
    h, l, c = df["High"], df["Low"], df["Close"]
    pc, ph, pl = c.shift(1), h.shift(1), l.shift(1)
    tr = pd.concat([(h-l),(h-pc).abs(),(l-pc).abs()], axis=1).max(axis=1)
    up = h - ph; dn = pl - l
    pdm = np.where((up>dn)&(up>0), up, 0.0)
    mdm = np.where((dn>up)&(dn>0), dn, 0.0)
    a = 1/ADX_LOOKBACK
    tr14  = pd.Series(tr.values, index=df.index).ewm(alpha=a, adjust=False).mean()
    pdm14 = pd.Series(pdm, index=df.index).ewm(alpha=a, adjust=False).mean()
    mdm14 = pd.Series(mdm, index=df.index).ewm(alpha=a, adjust=False).mean()
    tr14s = tr14.replace(0, np.nan)
    pdi = 100*pdm14/tr14s; mdi = 100*mdm14/tr14s
    dx  = 100*(pdi-mdi).abs()/(pdi+mdi).replace(0, np.nan)
    df["adx"] = dx.ewm(alpha=a, adjust=False).mean().fillna(0)

    bbb_col = next((c for c in df.columns if c.startswith("BBB_")), None)
    if bbb_col:
        def _pct_rank(s):
            if s.isna().all(): return 50.0
            return float((s < s.iloc[-1]).mean() * 100)
        df["bb_width_pct"] = (df[bbb_col]
            .rolling(BB_WIDTH_LOOKBACK, min_periods=BB_WIDTH_LOOKBACK)
            .apply(_pct_rank, raw=False))
    else:
        df["bb_width_pct"] = 50.0

    print("Done.\n")
    return df


# ── Helpers ───────────────────────────────────────────────────────────────────
def _f(df, prefix, i, default=0.0):
    cols = [c for c in df.columns if c.startswith(prefix)]
    if not cols: return default
    v = df.iloc[i][cols[0]]
    try:
        f = float(v); return f if np.isfinite(f) else default
    except Exception: return default


# ── Signal generation (exact deployed logic) ──────────────────────────────────
def name_pattern(reasons, direction):
    has_rsi  = any("RSI"     in r for r in reasons)
    has_ecx  = any("EMA20 >" in r or "EMA20 <" in r for r in reasons)
    has_macd = any("MACD"    in r for r in reasons)
    has_bb   = any("BB"      in r for r in reasons)
    has_ep   = any("above EMA20" in r or "below EMA20" in r for r in reasons)
    if has_rsi and has_macd and has_ecx: return f"TRIPLE_SIGNAL_{direction}"
    if has_rsi and has_bb:               return f"BB_RSI_REVERSAL_{direction}"
    if has_ecx and has_macd:             return f"EMA_MACD_TREND_{direction}"
    if has_rsi and has_ep:               return f"RSI_EMA_SIGNAL_{direction}"
    if has_rsi:                          return f"RSI_SIGNAL_{direction}"
    if has_ecx:                          return f"EMA_TREND_{direction}"
    return f"CONFLUENCE_{direction}"


def generate_signal(df, i):
    close  = float(df["Close"].iloc[i])
    rsi    = _f(df, "RSI_", i)
    ema20  = _f(df, "EMA_20", i)
    ema50  = _f(df, "EMA_50", i)
    ema200 = _f(df, f"EMA_{TREND_EMA}", i)
    atr    = _f(df, "ATRr_", i) or _f(df, "ATR", i)
    macd_v = _f(df, "MACD_", i); macd_s = _f(df, "MACDs_", i)
    bb_u   = _f(df, "BBU_", i, close*1.01)
    bb_l   = _f(df, "BBL_", i, close*0.99)

    if _f(df, "bb_width_pct", i, 50.0) < BB_WIDTH_MIN_PCT:
        return None

    trend_up = trend_down = True
    if ema200 > 0:
        trend_up   = close > ema200 + 0.3*atr
        trend_down = close < ema200 - 0.3*atr

    buy_r, sell_r = [], []
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
        sl = round(close - atr*ATR_STOP_MULT, 2)
        tp = round(close + (close-sl)*MIN_RR, 2)
    elif sell_n >= req_sell and sell_n > buy_n:
        direction, reasons, count = "SELL", sell_r, sell_n
        sl = round(close + atr*ATR_STOP_MULT, 2)
        tp = round(close - (sl-close)*MIN_RR, 2)
    else:
        return None

    dist = abs(close - sl)
    if dist <= 0: return None
    rr = abs(tp - close) / dist
    pattern = name_pattern(reasons, direction)

    # Disabled patterns
    if pattern in DISABLED_PATTERNS: return None

    # ADX filter — symmetric (BUY + SELL)
    adx_v = _f(df, "adx", i, 0.0)
    if "EMA_MACD_TREND" in pattern and adx_v < ADX_TREND_THRESHOLD:
        return None

    # RSI ceiling for BUY
    if direction == "BUY" and rsi >= RSI_CEILING_BUY:
        return None

    return dict(direction=direction, count=count, entry=close,
                sl=sl, tp=tp, rr=round(rr,2), atr=atr, pattern=pattern)


def detect_regime(df, i):
    close = float(df["Close"].iloc[i])
    atr   = _f(df, "ATRr_", i) or _f(df, "ATR", i)
    if (atr/close*100) > ATR_VOLATILE_PCT: return "VOLATILE"
    ema20 = _f(df, "EMA_20", i); ema50 = _f(df, "EMA_50", i)
    if ema20 > 0 and ema50 > 0:
        if close > ema20 > ema50: return "TRENDING_UP"
        if close < ema20 < ema50: return "TRENDING_DOWN"
    return "RANGING"


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
    net_pnl: float = 0.0
    spread_cost: float = 0.0
    status: str = "OPEN"


# ── Multi-position simulation ─────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame, max_open: int) -> tuple:
    """
    max_open: maximum simultaneous open positions (1, 2, or 999 for unlimited).
    Returns (trades, equity_series, daily_pnl_dict).
    """
    n = len(df)
    trades: List[BT] = []
    open_positions: List[BT] = []   # all currently open trades
    equity     = ACCOUNT_SIZE
    equity_pts = [equity] * WARMUP_BARS

    daily_loss:   float = 0.0
    daily_date:   str   = ""
    consec_loss:  int   = 0
    monthly_pnl:  float = 0.0
    monthly_month: str  = ""
    trade_id:     int   = 0

    # Per-day net P&L for worst/best day stats
    daily_pnl: Dict[str, float] = {}

    atr_col = next((c for c in df.columns if c.startswith("ATRr_")), None)

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
        if bdate[:7] != monthly_month:
            monthly_pnl   = 0.0
            monthly_month = bdate[:7]

        bar_atr  = _f(df, "ATRr_", i) or _f(df, "ATR", i)
        atr_mean = (float(df["atr_mean"].iloc[i])
                    if "atr_mean" in df.columns and np.isfinite(df["atr_mean"].iloc[i])
                    else bar_atr)

        # ── Close all positions that hit SL or TP this bar ─────────────────
        still_open = []
        for t in open_positions:
            if t.direction == "BUY":
                hit_sl = lo <= t.stop_loss
                hit_tp = hi >= t.take_profit
            else:
                hit_sl = hi >= t.stop_loss
                hit_tp = lo <= t.take_profit

            if hit_sl:
                # Conservative: SL wins when both hit on the same bar
                extra_slip    = SLIP_EXTRA if bar_atr > atr_mean * HIGH_ATR_MULT else 0.0
                t.spread_cost = round(t.lot_size * 100 * (SPREAD_DOLLARS*2 + extra_slip), 2)
                t.pnl         = round(-t.risk_amount, 2)
                t.net_pnl     = round(t.pnl - t.spread_cost, 2)
                t.exit_price  = t.stop_loss
                t.status      = "LOSS"
                t.close_time  = btime
                equity        += t.net_pnl
                daily_loss    += abs(t.net_pnl)
                monthly_pnl   += t.net_pnl
                daily_pnl[bdate] = daily_pnl.get(bdate, 0.0) + t.net_pnl
                consec_loss   += 1
                trades.append(t)
            elif hit_tp:
                t.spread_cost = round(t.lot_size * 100 * SPREAD_DOLLARS * 2, 2)
                t.pnl         = round(t.risk_amount * t.rr_ratio, 2)
                t.net_pnl     = round(t.pnl - t.spread_cost, 2)
                t.exit_price  = t.take_profit
                t.status      = "WIN"
                t.close_time  = btime
                equity        += t.net_pnl
                monthly_pnl   += t.net_pnl
                daily_pnl[bdate] = daily_pnl.get(bdate, 0.0) + t.net_pnl
                consec_loss   = 0
                trades.append(t)
            else:
                still_open.append(t)

        open_positions = still_open
        equity_pts.append(equity)

        # ── Skip signal if at position limit ───────────────────────────────
        if len(open_positions) >= max_open:
            continue

        # ── Pre-signal filters ─────────────────────────────────────────────
        if not (SESSION_START <= bar.hour < SESSION_END):
            continue
        if detect_regime(df, i) == "VOLATILE":
            continue
        if daily_loss >= DAILY_LOSS_LIMIT:
            continue
        if consec_loss >= MAX_CONSEC_LOSS:
            continue

        sig = generate_signal(df, i)
        if sig is None or sig["rr"] < MIN_RR:
            continue

        # ── Size and open new position ─────────────────────────────────────
        actual_entry = sig["entry"] + (SPREAD_DOLLARS if sig["direction"]=="BUY"
                                       else -SPREAD_DOLLARS)
        max_risk    = equity * MAX_RISK_PCT
        remaining   = DAILY_LOSS_LIMIT - daily_loss
        risk_amount = min(max_risk, remaining)
        stop_dist   = abs(actual_entry - sig["sl"])
        if stop_dist <= 0:
            continue

        raw_lot     = risk_amount / (GOLD_CONTRACT * stop_dist)
        lot         = max(MIN_LOT, min(MAX_LOT, round(raw_lot/LOT_STEP)*LOT_STEP))
        brake_active = monthly_pnl < -MONTHLY_DRAWDOWN_BRAKE
        if brake_active:
            lot = max(MIN_LOT, round(lot*MONTHLY_BRAKE_MULTIPLIER/LOT_STEP)*LOT_STEP)

        trade_id += 1
        open_positions.append(BT(
            id=trade_id, open_time=btime,
            direction=sig["direction"], pattern=sig["pattern"],
            entry=actual_entry, stop_loss=sig["sl"], take_profit=sig["tp"],
            lot_size=lot, risk_amount=lot*GOLD_CONTRACT*stop_dist,
            confluence=sig["count"], regime=detect_regime(df, i),
            rr_ratio=sig["rr"],
        ))

    # Expire any positions still open at end of data
    for t in open_positions:
        mult = 1 if t.direction=="BUY" else -1
        t.pnl         = round((close-t.entry)*mult*t.lot_size*GOLD_CONTRACT, 2)
        t.spread_cost = round(t.lot_size*100*SPREAD_DOLLARS*2, 2)
        t.net_pnl     = round(t.pnl-t.spread_cost, 2)
        t.exit_price  = close
        t.status      = "EXPIRED"
        t.close_time  = df.index[-1].strftime("%Y-%m-%d %H:%M")
        trades.append(t)

    eq_series = pd.Series(equity_pts, index=df.index[:len(equity_pts)])
    return trades, eq_series, daily_pnl


# ── Analytics ─────────────────────────────────────────────────────────────────
def daily_sharpe(eq: pd.Series) -> float:
    if eq is None or len(eq) < 5: return 0.0
    deq = eq.resample("B").last().dropna()
    ret = deq.pct_change().dropna()
    if len(ret) < 2 or ret.std() == 0: return 0.0
    return float((ret.mean()/ret.std())*np.sqrt(252))


def analyse(trades: List[BT], eq: pd.Series,
            daily_pnl: Dict[str, float]) -> dict:
    closed = [t for t in trades if t.status != "OPEN"]
    if not closed: return {}
    wins   = [t for t in closed if t.status == "WIN"]
    losses = [t for t in closed if t.status == "LOSS"]
    total  = len(closed)

    net    = sum(t.net_pnl for t in closed)
    gw     = sum(t.net_pnl for t in wins)
    gl     = abs(sum(t.net_pnl for t in losses))
    pf     = gw/gl if gl > 0 else float("inf")
    sharpe = daily_sharpe(eq)

    vals = eq.values; peak = vals[0]; max_dd = 0.0
    for v in vals:
        peak = max(peak, v)
        dd   = (peak-v)/peak*100 if peak > 0 else 0
        max_dd = max(max_dd, dd)

    # Per-day stats
    day_vals  = list(daily_pnl.values())
    worst_day = min(day_vals) if day_vals else 0.0
    best_day  = max(day_vals) if day_vals else 0.0
    days_limit = sum(1 for d, v in daily_pnl.items()
                     if v < -DAILY_LOSS_LIMIT * 0.9)  # reached ≥90% of daily limit

    # Peak concurrent positions per day
    by_date: Dict[str, set] = {}
    for t in trades:
        by_date.setdefault(t.open_time[:10], set()).add(t.id)
        if t.close_time:
            by_date.setdefault(t.close_time[:10], set()).add(t.id)

    return dict(
        total=total, wins=len(wins), losses=len(losses),
        win_rate=len(wins)/total,
        net_pnl=net, gross_pnl=sum(t.pnl for t in closed),
        spread_cost=sum(t.spread_cost for t in closed),
        avg_win=gw/len(wins) if wins else 0,
        avg_loss=gl/len(losses) if losses else 0,
        profit_factor=pf, sharpe=sharpe, max_dd=max_dd,
        final_equity=ACCOUNT_SIZE+net,
        worst_day=worst_day, best_day=best_day,
        days_near_limit=days_limit,
    )


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    df = fetch_and_prepare()

    configs = [
        (1,   "backtest_v2_results_max1.json"),
        (2,   "backtest_v2_results_max2.json"),
        (999, "backtest_v2_results_maxinf.json"),
    ]

    all_stats  = {}
    all_trades = {}

    for max_open, save_file in configs:
        label = f"MAX={max_open if max_open < 999 else 'inf'}"
        print(f"Running {label}...")
        trades, eq, dpnl = run_backtest(df, max_open)
        stats = analyse(trades, eq, dpnl)
        all_stats[label]  = stats
        all_trades[label] = trades

        print(f"  Trades={stats['total']}  WR={stats['win_rate']:.0%}  "
              f"PF={stats['profit_factor']:.2f}  Sharpe={stats['sharpe']:.2f}  "
              f"MaxDD={stats['max_dd']:.1f}%  Net=${stats['net_pnl']:+,.0f}")

        # Save JSON
        out = {
            "config": {"max_open_positions": max_open if max_open < 999 else "unlimited",
                       "atr_stop_mult": ATR_STOP_MULT,
                       "rsi_ceiling_buy": RSI_CEILING_BUY,
                       "adx_threshold": ADX_TREND_THRESHOLD},
            "summary": {k: v for k, v in stats.items()},
            "trades":  [asdict(t) for t in trades],
        }
        Path(save_file).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"  Saved {save_file}")

    # ── Report ────────────────────────────────────────────────────────────────
    def fmt(v, fmt_str): return format(v, fmt_str) if isinstance(v, (int, float)) else str(v)

    s1   = all_stats["MAX=1"]
    s2   = all_stats["MAX=2"]
    sinf = all_stats["MAX=inf"]

    def col(s, key, fmt_str=""):
        v = s.get(key, "?")
        if fmt_str and isinstance(v, float):
            return fmt(v, fmt_str)
        return str(v)

    lines = []
    def w(s=""): lines.append(s)

    w("# Concurrent Position Limit — Research Report")
    w()
    w(f"**Date:** {datetime.now().strftime('%Y-%m-%d')}")
    w(f"**Config:** ATR x{ATR_STOP_MULT} | RSI_CEIL={RSI_CEILING_BUY} | ADX>={ADX_TREND_THRESHOLD} | BUY-only")
    w(f"**Data:** 1H bars, ~{PERIOD_DAYS} days")
    w()
    w("> **Key finding:** `backtest_v2.py` is already single-position (MAX=1) by design —")
    w("> `open_trade: Optional[BT] = None` allows only one concurrent position.")
    w("> The 102 trades / 55% WR / Sharpe 2.77 figures ARE the MAX=1 results.")
    w("> The unlimited stacking seen on Demo Live Day 1 is a production-only behaviour")
    w("> that the standard backtest never modelled.")
    w()
    w("---")
    w()
    w("## Results Table")
    w()
    w("| Metric | MAX=1 | MAX=2 | MAX=inf |")
    w("|--------|-------|-------|---------|")

    rows = [
        ("Total trades",    "total",              "d"),
        ("Win rate",        "win_rate",           ".1%"),
        ("Net P&L",         "net_pnl",            "+,.0f"),
        ("Spread/slip",     "spread_cost",        ",.0f"),
        ("Profit factor",   "profit_factor",      ".2f"),
        ("Sharpe (daily)",  "sharpe",             ".2f"),
        ("Max DD",          "max_dd",             ".1f"),
        ("Avg win",         "avg_win",            "+,.0f"),
        ("Avg loss",        "avg_loss",           "+,.0f"),
        ("Worst single day","worst_day",          "+,.0f"),
        ("Best single day", "best_day",           "+,.0f"),
        ("Days near limit", "days_near_limit",    "d"),
    ]

    for label, key, fmt_str in rows:
        def cell(s):
            v = s.get(key, "?")
            if not isinstance(v, (int, float)): return str(v)
            if fmt_str == ".1%": return f"{v:.1%}"
            if fmt_str == ".2f": return f"{v:.2f}"
            if fmt_str == ".1f": return f"{v:.1f}%"
            if fmt_str == "+,.0f": return f"${v:+,.0f}"
            if fmt_str == ",.0f": return f"${v:,.0f}"
            return str(int(v))
        w(f"| {label:<22} | {cell(s1):>8} | {cell(s2):>8} | {cell(sinf):>8} |")

    # Winner flags
    best_sharpe = max(all_stats, key=lambda k: all_stats[k]["sharpe"])
    best_pf     = max(all_stats, key=lambda k: all_stats[k]["profit_factor"])
    lowest_dd   = min(all_stats, key=lambda k: all_stats[k]["max_dd"])

    w()
    w("---")
    w()
    w("## Trade-Off Analysis")
    w()

    for label, s in all_stats.items():
        n_extra = s["total"] - s1["total"]
        pnl_delta = s["net_pnl"] - s1["net_pnl"]
        risk_mult = 1 if label=="MAX=1" else (2 if label=="MAX=2" else "n")
        w(f"### {label}")
        if label == "MAX=1":
            w(f"- **Baseline** — every signal waits for the previous trade to close")
            w(f"- No stacking risk, no correlation risk between simultaneous positions")
            w(f"- Misses entry opportunities during open positions (pullback entries)")
        elif label == "MAX=2":
            w(f"- +{n_extra} additional trades vs MAX=1  ({pnl_delta:+,.0f} P&L delta)")
            w(f"- Risk: 2x per signal = 2% account at risk when both slots are full")
            w(f"- Captures one layer of pullback entry; blocks a third")
        else:
            w(f"- +{n_extra} additional trades vs MAX=1  ({pnl_delta:+,.0f} P&L delta)")
            w(f"- Risk: n× per signal — uncapped; mirrors Demo Live Day 1 (3 simultaneous)")
            w(f"- Pullback averaging works in trending markets; catastrophic in reversals")
        w()

    w("---")
    w()
    w("## Decision")
    w()

    sharpe_1   = s1["sharpe"]
    sharpe_2   = s2["sharpe"]
    sharpe_inf = sinf["sharpe"]
    dd_1       = s1["max_dd"]
    dd_2       = s2["max_dd"]
    dd_inf     = sinf["max_dd"]

    w(f"| Criterion | MAX=1 | MAX=2 | MAX=inf |")
    w(f"|-----------|-------|-------|---------|")
    w(f"| Sharpe | {sharpe_1:.2f} | {sharpe_2:.2f} | {sharpe_inf:.2f} |")
    w(f"| Max DD | {dd_1:.1f}% | {dd_2:.1f}% | {dd_inf:.1f}% |")
    w(f"| Risk control | Full | Partial | None |")
    w()

    # Verdict
    if sharpe_1 >= sharpe_2 and sharpe_1 >= sharpe_inf:
        verdict = "MAX=1"
        reason = "Single-position is most risk-efficient. Stacking does not improve Sharpe — it adds correlated risk for no reward."
        deploy = "Deploy MAX=1. Unlimited stacking was a bug, not a feature."
    elif sharpe_2 > sharpe_1 and dd_2 <= dd_1 * 1.3:
        verdict = "MAX=2"
        reason = f"MAX=2 improves Sharpe ({sharpe_2:.2f} vs {sharpe_1:.2f}) with acceptable DD increase ({dd_2:.1f}% vs {dd_1:.1f}%). Captures one pullback layer."
        deploy = "Deploy MAX=2. Cap at 2 simultaneous positions."
    elif sharpe_inf > sharpe_2 and dd_inf <= dd_2 * 1.2:
        verdict = "MAX=inf"
        reason = "Unlimited stacking shows genuine edge. Pullback averaging is working in trending gold market."
        deploy = "Keep unlimited but add a daily-total-risk cap."
    else:
        verdict = "MAX=1"
        reason = f"MAX=2 and MAX=inf both degrade Sharpe or inflate DD beyond acceptable bounds."
        deploy = "Deploy MAX=1 as safest choice."

    w(f"**Recommendation: {verdict}**")
    w()
    w(f"{reason}")
    w()
    w(f"**Action:** {deploy}")
    w()
    w("---")
    w()
    w("## Implementation")
    w()
    w("If deploying a position limit, add to `OrchestratorAgent._do_cycle()` after `self._check_open_positions()`:")
    w()
    w("```python")
    w("MAX_OPEN_POSITIONS = 1  # or 2 — set in Config")
    w()
    w("open_count = len(self.journal.get_open())")
    w("if open_count >= Config.MAX_OPEN_POSITIONS:")
    w('    self.info(f"Position limit ({open_count}/{Config.MAX_OPEN_POSITIONS}) — skipping signal")')
    w("    return")
    w("```")
    w()
    w("Add `MAX_OPEN_POSITIONS: int = 1` to `Config`.")

    report_text = "\n".join(lines)
    Path("POSITION_LIMIT_REPORT.md").write_text(report_text, encoding="utf-8")
    print("\nPOSITION_LIMIT_REPORT.md written.")

    # Console summary
    print()
    print("=" * 60)
    print("POSITION LIMIT SUMMARY")
    print("=" * 60)
    print(f"{'Config':<12} {'Trades':>7} {'WR':>6} {'PF':>5} {'Sharpe':>7} {'MaxDD':>7} {'Net P&L':>10}")
    print("-" * 60)
    for label, s in all_stats.items():
        lbl = label.replace("MAX=", "MAX=")
        print(f"{lbl:<12} {s['total']:>7} {s['win_rate']:>6.0%} {s['profit_factor']:>5.2f} "
              f"{s['sharpe']:>7.2f} {s['max_dd']:>6.1f}% {s['net_pnl']:>+10,.0f}")
    print()
    print(f"Verdict: {verdict} — {reason[:70]}...")
