#!/usr/bin/env python3
"""
Gold 3-Year Daily Backtest
Uses the same strategy parameters as the deployed system (v5 config):
  - ATR×2.5 stops, RSI ceiling 70, ADX BUY filter
  - EMA200 trend gate, BB width percentile filter
  - SELL patterns disabled (gold bull market)
  - Daily bars ("1d") — yfinance provides unlimited history at this resolution
"""

import warnings
warnings.filterwarnings("ignore")

import yfinance as yf
import pandas as pd
import pandas_ta as pta
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Dict, Optional

# ── Config (mirrors deployed gold_trading_agents.py v5) ───────────────────────
ACCOUNT_SIZE     = 10_000.0
MAX_RISK_PCT     = 0.01
MIN_RR           = 2.0
DAILY_LOSS_LIMIT = 300.0
MIN_CONFLUENCE   = 3
ATR_STOP_MULT    = 2.5       # v5 deployed value
GOLD_CONTRACT    = 100.0
MIN_LOT, MAX_LOT, LOT_STEP = 0.01, 10.0, 0.01

RSI_BUY          = 35
RSI_SELL         = 65
RSI_CEILING_BUY  = 70        # v5 fix — blocks overbought BUY entries
TREND_EMA        = 200
MAX_CONSEC_LOSS  = 2
WARMUP_BARS      = 220

# Daily bars: normal daily ATR% for gold is ~0.8–1.2%, >2.5% is genuinely volatile
ATR_VOLATILE_PCT = 2.5

# BB width percentile filter (same as live system)
BB_WIDTH_MIN_PCT = 25.0
BB_WIDTH_LOOKBACK = 50

# ADX filter
ADX_THRESHOLD = 25
ADX_LOOKBACK  = 14

# Spread/slippage for daily bars (slightly higher due to overnight gaps)
SPREAD_DOLLARS = 0.30
SLIP_EXTRA     = 0.10

# SELL patterns disabled — 2yr+ gold bull market makes SELL structurally losing
DISABLED_PATTERNS = ["EMA_MACD_TREND_SELL", "BB_RSI_REVERSAL_SELL"]

PERIOD_DAYS = 1095   # 3 calendar years


# ── Data ──────────────────────────────────────────────────────────────────────
def fetch_daily() -> pd.DataFrame:
    end   = datetime.now()
    start = end - timedelta(days=PERIOD_DAYS)
    print(f"Downloading GC=F daily data ({PERIOD_DAYS} days)...")
    df = yf.download(
        "GC=F",
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        interval="1d",
        progress=False,
        auto_adjust=True,
    )
    if df.empty:
        raise RuntimeError("No data returned from yfinance")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open","High","Low","Close","Volume"]].dropna()
    df.index = pd.to_datetime(df.index)
    df = df[df.index.dayofweek < 5]
    print(f"Bars: {len(df):,}  |  {df.index[0].date()} to {df.index[-1].date()}\n")
    return df


# ── Indicators ────────────────────────────────────────────────────────────────
def _f(df, prefix, i, default=0.0):
    cols = [c for c in df.columns if c.startswith(prefix)]
    if not cols:
        return default
    v = df.iloc[i][cols[0]]
    try:
        f = float(v)
        return f if np.isfinite(f) else default
    except Exception:
        return default


def _calc_adx(df, period=14):
    high, low, close = df["High"], df["Low"], df["Close"]
    pc, ph, pl = close.shift(1), high.shift(1), low.shift(1)
    tr = pd.concat([(high-low),(high-pc).abs(),(low-pc).abs()],axis=1).max(axis=1)
    up   = high - ph
    down = pl   - low
    pdm = np.where((up>down)&(up>0), up, 0.0)
    mdm = np.where((down>up)&(down>0), down, 0.0)
    alpha = 1.0/period
    tr14  = pd.Series(tr.values,  index=df.index).ewm(alpha=alpha,adjust=False).mean()
    pdm14 = pd.Series(pdm,        index=df.index).ewm(alpha=alpha,adjust=False).mean()
    mdm14 = pd.Series(mdm,        index=df.index).ewm(alpha=alpha,adjust=False).mean()
    safe  = tr14.replace(0, np.nan)
    pdi   = 100*pdm14/safe
    mdi   = 100*mdm14/safe
    dx    = 100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    return dx.ewm(alpha=alpha,adjust=False).mean().fillna(0.0)


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
    df["adx"] = _calc_adx(df, ADX_LOOKBACK)

    bbb_col = next((c for c in df.columns if c.startswith("BBB_")), None)
    if bbb_col:
        def _pct_rank(s):
            if s.isna().all(): return 50.0
            return float((s < s.iloc[-1]).mean() * 100)
        df["bb_width_pct"] = (
            df[bbb_col].rolling(BB_WIDTH_LOOKBACK, min_periods=BB_WIDTH_LOOKBACK)
            .apply(_pct_rank, raw=False)
        )
    else:
        df["bb_width_pct"] = 50.0
    return df


# ── Signal ────────────────────────────────────────────────────────────────────
def name_pattern(reasons, direction):
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


def generate_signal(df, i) -> Optional[dict]:
    close  = float(df["Close"].iloc[i])
    rsi    = _f(df, "RSI_", i)
    ema20  = _f(df, "EMA_20", i)
    ema50  = _f(df, "EMA_50", i)
    ema200 = _f(df, f"EMA_{TREND_EMA}", i)
    atr    = _f(df, "ATRr_", i) or _f(df, "ATR", i, 1.0)
    macd_v = _f(df, "MACD_", i)
    macd_s = _f(df, "MACDs_", i)
    bb_u   = _f(df, "BBU_", i, close*1.01)
    bb_l   = _f(df, "BBL_", i, close*0.99)

    # Volatile daily bar filter
    atr_pct = (atr / close * 100) if close > 0 else 0
    if atr_pct > ATR_VOLATILE_PCT:
        return None

    # BB width percentile filter
    bb_width_pct = _f(df, "bb_width_pct", i, 50.0)
    if bb_width_pct < BB_WIDTH_MIN_PCT:
        return None

    # EMA200 trend gate
    trend_up = trend_down = True
    if ema200 > 0:
        trend_up   = close > ema200 + 0.3 * atr
        trend_down = close < ema200 - 0.3 * atr

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
    if not trend_up:   buy_n = 0
    if not trend_down: sell_n = 0

    if buy_n < MIN_CONFLUENCE and sell_n < MIN_CONFLUENCE:
        return None

    if buy_n >= sell_n and buy_n >= MIN_CONFLUENCE:
        direction, reasons, count = "BUY", buy_r, buy_n
        sl = round(close - atr * ATR_STOP_MULT, 2)
        dist = close - sl
        tp = round(close + dist * MIN_RR, 2)
    elif sell_n > buy_n and sell_n >= MIN_CONFLUENCE:
        direction, reasons, count = "SELL", sell_r, sell_n
        sl = round(close + atr * ATR_STOP_MULT, 2)
        dist = sl - close
        tp = round(close - dist * MIN_RR, 2)
    else:
        return None

    dist = abs(close - sl)
    if dist <= 0:
        return None

    pattern = name_pattern(reasons, direction)

    # RSI ceiling — blocks BUY when overbought (v5 fix)
    if direction == "BUY" and rsi >= RSI_CEILING_BUY:
        return None

    # ADX filter — EMA_MACD_TREND requires momentum
    adx_val = _f(df, "adx", i, 0.0)
    if "EMA_MACD_TREND" in pattern and adx_val < ADX_THRESHOLD:
        return None

    # Disabled patterns
    if pattern in DISABLED_PATTERNS:
        return None

    rr = abs(tp - close) / dist
    return dict(direction=direction, reasons=reasons, count=count,
                entry=close, sl=sl, tp=tp, rr=round(rr,2),
                atr=atr, pattern=pattern)


# ── Simulation ────────────────────────────────────────────────────────────────
@dataclass
class Trade:
    id: int
    open_date: str
    direction: str
    pattern: str
    entry: float
    sl: float
    tp: float
    lot: float
    risk: float
    confluence: int
    close_date: str = ""
    exit_price: float = 0.0
    pnl: float = 0.0
    net_pnl: float = 0.0
    spread_cost: float = 0.0
    status: str = "OPEN"


def run_backtest(df: pd.DataFrame) -> List[Trade]:
    n = len(df)
    trades: List[Trade] = []
    open_trade: Optional[Trade] = None
    equity = ACCOUNT_SIZE
    daily_loss = 0.0
    daily_date = ""
    consec_loss = 0
    tid = 0

    for i in range(WARMUP_BARS, n):
        bar   = df.index[i]
        bdate = bar.strftime("%Y-%m-%d")
        high  = float(df["High"].iloc[i])
        low   = float(df["Low"].iloc[i])
        close = float(df["Close"].iloc[i])

        # Reset daily counters
        if bdate != daily_date:
            daily_loss  = 0.0
            daily_date  = bdate
            consec_loss = 0

        # Close open trade
        if open_trade is not None:
            t = open_trade
            sl_hit = (t.direction=="BUY" and low <= t.sl) or (t.direction=="SELL" and high >= t.sl)
            tp_hit = (t.direction=="BUY" and high >= t.tp) or (t.direction=="SELL" and low <= t.tp)

            if sl_hit and tp_hit:
                exit_price = t.sl
                status = "LOSS"
            elif tp_hit:
                exit_price = t.tp
                status = "WIN"
            elif sl_hit:
                exit_price = t.sl
                status = "LOSS"
            else:
                exit_price = None

            if exit_price is not None:
                mult = 1 if t.direction=="BUY" else -1
                t.pnl         = round((exit_price - t.entry)*mult*t.lot*GOLD_CONTRACT, 2)
                t.spread_cost = round(t.lot*GOLD_CONTRACT*SPREAD_DOLLARS*2, 2)
                t.net_pnl     = round(t.pnl - t.spread_cost, 2)
                t.exit_price  = exit_price
                t.status      = status
                t.close_date  = bdate
                equity       += t.net_pnl
                daily_loss   += -t.net_pnl if t.net_pnl < 0 else 0
                consec_loss   = consec_loss+1 if status=="LOSS" else 0
                trades.append(t)
                open_trade    = None

        if open_trade is not None:
            continue

        # Guards
        if daily_loss >= DAILY_LOSS_LIMIT:
            continue
        if consec_loss >= MAX_CONSEC_LOSS:
            continue

        sig = generate_signal(df, i)
        if sig is None:
            continue

        max_risk    = equity * MAX_RISK_PCT
        remaining   = DAILY_LOSS_LIMIT - daily_loss
        risk_amount = min(max_risk, remaining)
        stop_dist   = abs(sig["entry"] - sig["sl"])
        if stop_dist <= 0:
            continue

        raw_lot = risk_amount / (GOLD_CONTRACT * stop_dist)
        lot     = max(MIN_LOT, min(MAX_LOT, round(raw_lot/LOT_STEP)*LOT_STEP))

        tid += 1
        open_trade = Trade(
            id=tid, open_date=bdate, direction=sig["direction"],
            pattern=sig["pattern"], entry=sig["entry"]+SPREAD_DOLLARS,
            sl=sig["sl"], tp=sig["tp"], lot=lot, risk=lot*GOLD_CONTRACT*stop_dist,
            confluence=sig["count"],
        )

    # Expire any still-open trade
    if open_trade is not None:
        t = open_trade
        mult = 1 if t.direction=="BUY" else -1
        t.pnl         = round((close-t.entry)*mult*t.lot*GOLD_CONTRACT, 2)
        t.spread_cost = round(t.lot*GOLD_CONTRACT*SPREAD_DOLLARS*2, 2)
        t.net_pnl     = round(t.pnl-t.spread_cost, 2)
        t.exit_price  = close
        t.status      = "EXPIRED"
        t.close_date  = df.index[-1].strftime("%Y-%m-%d")
        trades.append(open_trade)

    return trades


# ── Report ────────────────────────────────────────────────────────────────────
def report(trades: List[Trade]):
    closed = [t for t in trades if t.status != "OPEN"]
    if not closed:
        print("No closed trades.")
        return

    wins   = [t for t in closed if t.status=="WIN"]
    total  = len(closed)
    win_n  = len(wins)
    net    = sum(t.net_pnl for t in closed)
    gross  = sum(t.pnl for t in closed)
    spread = sum(t.spread_cost for t in closed)

    gw = sum(t.net_pnl for t in wins)
    gl = abs(sum(t.net_pnl for t in closed if t.status=="LOSS"))
    pf = gw/gl if gl > 0 else float("inf")

    # Max drawdown
    eq_curve = ACCOUNT_SIZE
    peak = ACCOUNT_SIZE
    max_dd = 0.0
    for t in sorted(closed, key=lambda x: x.close_date):
        eq_curve += t.net_pnl
        peak = max(peak, eq_curve)
        dd = (peak - eq_curve)/peak*100 if peak > 0 else 0
        max_dd = max(max_dd, dd)

    print("\n" + "="*60)
    print("  GOLD 3-YEAR DAILY BACKTEST")
    print(f"  Period : Last {PERIOD_DAYS} calendar days")
    print(f"  Config : ATR x{ATR_STOP_MULT} stops | RSI ceil={RSI_CEILING_BUY} | ADX>={ADX_THRESHOLD}")
    print(f"  SELL patterns disabled (gold bull market)")
    print("="*60)

    print(f"\n  PROFIT")
    print(f"    Gross P&L      : ${gross:+,.2f}")
    print(f"    Spread/slip    : -${spread:,.2f}")
    print(f"    Net P&L        : ${net:+,.2f}")
    print(f"    Final equity   : ${ACCOUNT_SIZE+net:,.2f}")
    print(f"    Profit factor  : {pf:.2f}")
    print(f"    Max drawdown   : {max_dd:.1f}%")

    print(f"\n  WIN RATE")
    print(f"    Trades         : {total}")
    print(f"    Wins / Losses  : {win_n} / {total-win_n}")
    print(f"    Win rate       : {win_n/total:.1%}")
    print(f"    Avg win        : ${gw/win_n:+,.2f}" if win_n else "    Avg win        : —")
    losses_ = [t for t in closed if t.status=="LOSS"]
    print(f"    Avg loss       : -${gl/len(losses_):,.2f}" if losses_ else "    Avg loss       : —")

    print(f"\n  WINNING MOVES (by pattern, net P&L)")
    print(f"  {'Pattern':<32} {'Trades':>6} {'Wins':>5} {'Win%':>6} {'Net P&L':>10} {'Avg/trade':>10}")
    print(f"  {'-'*32} {'-'*6} {'-'*5} {'-'*6} {'-'*10} {'-'*10}")

    pat: Dict[str, dict] = {}
    for t in closed:
        s = pat.setdefault(t.pattern, {"n":0,"w":0,"pnl":0.0})
        s["n"] += 1
        s["pnl"] += t.net_pnl
        if t.status=="WIN": s["w"] += 1

    for p, s in sorted(pat.items(), key=lambda x: x[1]["pnl"], reverse=True):
        n_, w_, pnl_ = s["n"], s["w"], s["pnl"]
        wr = f"{w_/n_:.0%}" if n_ else "-"
        avg = f"${pnl_/n_:+.2f}" if n_ else "-"
        flag = " *" if n_>=3 and w_/n_>=0.60 else ""
        print(f"  {p:<32} {n_:>6} {w_:>5} {wr:>6} {pnl_:>+10,.2f} {avg:>10}{flag}")

    print(f"\n  * = pattern with >=3 trades and >=60% win rate (profitable edge)")
    print("="*60 + "\n")


# ── Entry ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    df = fetch_daily()
    print("Computing indicators...")
    df = add_indicators(df)
    print("Running bar-by-bar simulation...\n")
    trades = run_backtest(df)
    report(trades)
