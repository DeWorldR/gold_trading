#!/usr/bin/env python3
"""
Gold 1H Backtest — Stop Width Research
Identical to backtest_v2.py except:
  - ATR_STOP_MULT defaults to 2.0 (was 1.5 in production)
  - --atr CLI arg lets you test 1.5 / 2.0 / 2.5 without editing the file
  - Results saved to backtest_v2_results_atr<X>.json
  - Extra diagnostic: % of losses stopped within 3 bars

Usage:
  py -3.12 backtest_v2_atr20.py --period 2y --atr 2.0
  py -3.12 backtest_v2_atr20.py --period 2y --atr 2.5
"""

import json, warnings
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
MIN_RR           = 2.0
DAILY_LOSS_LIMIT = 300.0
MIN_CONFLUENCE   = 3
ATR_VOLATILE_PCT = 1.0
ATR_STOP_MULT    = 2.0       # CHANGED from 1.5 — override via --atr
GOLD_CONTRACT    = 100.0
MIN_LOT, MAX_LOT, LOT_STEP = 0.01, 10.0, 0.01

RSI_BUY          = 35
RSI_SELL         = 65
TREND_EMA        = 200
SESSION_START    = 8
SESSION_END      = 21
MAX_CONSEC_LOSS  = 2
WARMUP_BARS      = 220

SYMBOL   = "GC=F"
INTERVAL = "1h"

TRAIN_RATIO = 0.70

SPREAD_PIPS    = 2.5
SPREAD_DOLLARS = 0.25
HIGH_ATR_MULT  = 1.5
SLIP_EXTRA     = 0.05

BB_WIDTH_LOOKBACK = 50
BB_WIDTH_MIN_PCT  = 25.0

ADX_TREND_THRESHOLD = 25
ADX_LOOKBACK        = 14

BB_RSI_MIN_CONFLUENCE = 2

MONTHLY_DRAWDOWN_BRAKE   = 150.0
MONTHLY_BRAKE_MULTIPLIER = 0.5

DISABLED_PATTERNS = ["EMA_MACD_TREND_SELL", "BB_RSI_REVERSAL_SELL"]

_PERIOD_DAYS = {"1y": 365, "2y": 725, "max": 725}


# ── Data download ─────────────────────────────────────────────────────────────
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
    df = df[df.index.dayofweek < 5]
    trading_days = df.index.normalize().nunique()
    print(f"Bars: {len(df):,}  |  {df.index[0].date()} to {df.index[-1].date()}")
    if trading_days < 300:
        print(f"WARNING: Only {trading_days} trading days.")
    print()
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

    atr_col = next((c for c in df.columns if c.startswith("ATRr_") or c.startswith("ATR")), None)
    if atr_col:
        df["atr_mean"] = df[atr_col].rolling(50, min_periods=10).mean()

    df["adx"] = _calc_adx_series(df, ADX_LOOKBACK)

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
    high  = df["High"]; low = df["Low"]; close = df["Close"]
    pc = close.shift(1); ph = high.shift(1); pl = low.shift(1)
    tr = pd.concat([(high - low), (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    up   = high - ph
    down = pl   - low
    plus_dm  = np.where((up > down)   & (up > 0),   up,   0.0)
    minus_dm = np.where((down > up)   & (down > 0), down, 0.0)
    alpha = 1.0 / period
    tr14  = pd.Series(tr.values,   index=df.index).ewm(alpha=alpha, adjust=False).mean()
    pdm14 = pd.Series(plus_dm,     index=df.index).ewm(alpha=alpha, adjust=False).mean()
    mdm14 = pd.Series(minus_dm,    index=df.index).ewm(alpha=alpha, adjust=False).mean()
    tr14_safe = tr14.replace(0, np.nan)
    pdi = 100 * pdm14 / tr14_safe
    mdi = 100 * mdm14 / tr14_safe
    dx  = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=alpha, adjust=False).mean().fillna(0.0)


def _f(df: pd.DataFrame, prefix: str, i: int, default: float = 0.0) -> float:
    cols = [c for c in df.columns if c.startswith(prefix)]
    if not cols: return default
    v = df.iloc[i][cols[0]]
    try:
        f = float(v)
        return f if np.isfinite(f) else default
    except Exception:
        return default


# ── Signal ────────────────────────────────────────────────────────────────────
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

    bb_width_pct = _f(df, "bb_width_pct", i, 50.0)
    if bb_width_pct < BB_WIDTH_MIN_PCT:
        return None

    if ema200 > 0:
        trend_up   = close > ema200 + 0.3 * atr
        trend_down = close < ema200 - 0.3 * atr
    else:
        trend_up = trend_down = True

    buy_r: List[str] = []; sell_r: List[str] = []

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

    adx_val = _f(df, "adx", i, 0.0)
    if direction == "SELL" and "EMA_MACD_TREND" in pattern:
        if adx_val < ADX_TREND_THRESHOLD:
            return None
    if pattern in DISABLED_PATTERNS:
        return None

    return dict(direction=direction, reasons=reasons, count=count,
                entry=close, sl=sl, tp=tp, rr=round(rr, 2),
                atr=atr, ema200=ema200, adx=adx_val, pattern=pattern)


def detect_regime(df: pd.DataFrame, i: int) -> str:
    close   = float(df["Close"].iloc[i])
    atr     = _f(df, "ATRr_", i) or _f(df, "ATR", i)
    atr_pct = (atr / close * 100) if close > 0 else 0
    if atr_pct > ATR_VOLATILE_PCT: return "VOLATILE"
    ema20 = _f(df, "EMA_20", i); ema50 = _f(df, "EMA_50", i)
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
    spread_cost: float = 0.0
    net_pnl: float = 0.0
    monthly_brake_active: bool = False
    bars_to_close: int = 0        # NEW: bars from open to SL/TP hit


# ── Simulation ────────────────────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame):
    n = len(df)
    trades: List[BT] = []
    open_trade: Optional[BT] = None
    equity = ACCOUNT_SIZE
    equity_pts: List[float] = [equity] * WARMUP_BARS

    daily_loss = 0.0; daily_date = ""; consec_loss = 0; trade_id = 0
    sk = dict(volatile=0, session=0, daily=0, rr=0, trend=0, consec=0,
              bb_width=0, adx=0, ambiguous=0)
    monthly_pnl = 0.0; monthly_month = ""
    open_bar_idx = 0   # bar index when trade was opened

    for i in range(WARMUP_BARS, n):
        bar   = df.index[i]
        bdate = bar.strftime("%Y-%m-%d")
        btime = bar.strftime("%Y-%m-%d %H:%M")
        try:
            bar_hour = bar.hour
        except Exception:
            bar_hour = 12

        hi    = float(df["High"].iloc[i])
        lo    = float(df["Low"].iloc[i])
        close = float(df["Close"].iloc[i])

        if bdate != daily_date:
            daily_loss = 0.0; consec_loss = 0; daily_date = bdate

        bmonth = bdate[:7]
        if bmonth != monthly_month:
            monthly_pnl = 0.0; monthly_month = bmonth

        # ── Check open position ────────────────────────────────────────────
        if open_trade is not None:
            t = open_trade
            hit_sl = hit_tp = False
            if t.direction == "BUY":
                hit_sl = lo <= t.stop_loss
                hit_tp = hi >= t.take_profit
            else:
                hit_sl = hi >= t.stop_loss
                hit_tp = lo <= t.take_profit

            bar_atr  = _f(df, "ATRr_", i) or _f(df, "ATR", i)
            atr_mean = float(df["atr_mean"].iloc[i]) if ("atr_mean" in df.columns
                         and np.isfinite(df["atr_mean"].iloc[i])) else bar_atr

            if hit_sl and hit_tp:
                sk["ambiguous"] += 1

            if hit_sl:
                t.exit_price  = t.stop_loss
                t.pnl         = round(-t.risk_amount, 2)
                extra_slip    = SLIP_EXTRA if (bar_atr > atr_mean * HIGH_ATR_MULT) else 0.0
                t.spread_cost = round(t.lot_size * 100 * (SPREAD_DOLLARS * 2 + extra_slip), 2)
                t.net_pnl     = round(t.pnl - t.spread_cost, 2)
                t.status      = "LOSS"
                t.close_time  = btime
                t.bars_to_close = i - open_bar_idx
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
                t.bars_to_close = i - open_bar_idx
                equity       += t.net_pnl
                monthly_pnl  += t.net_pnl
                consec_loss   = 0
                trades.append(t)
                open_trade = None

        equity_pts.append(equity)
        if open_trade is not None:
            continue

        # ── Pre-signal filters ─────────────────────────────────────────────
        if not (SESSION_START <= bar_hour < SESSION_END):
            sk["session"] += 1; continue

        regime = detect_regime(df, i)
        if regime == "VOLATILE":
            sk["volatile"] += 1; continue

        if daily_loss >= DAILY_LOSS_LIMIT:
            sk["daily"] += 1; continue

        if consec_loss >= MAX_CONSEC_LOSS:
            sk["consec"] += 1; continue

        sig = generate_signal(df, i)
        if sig is None:
            bb_w    = _f(df, "bb_width_pct", i, 50.0)
            adx_now = _f(df, "adx", i, 0.0)
            if bb_w < BB_WIDTH_MIN_PCT:      sk["bb_width"] += 1
            elif adx_now < ADX_TREND_THRESHOLD: sk["adx"] += 1
            else:                             sk["trend"] += 1
            continue

        if sig["rr"] < MIN_RR:
            sk["rr"] += 1; continue

        # ── Open trade ─────────────────────────────────────────────────────
        raw_entry = sig["entry"]
        actual_entry = raw_entry + SPREAD_DOLLARS if sig["direction"] == "BUY" else raw_entry - SPREAD_DOLLARS

        max_risk    = equity * MAX_RISK_PCT
        remaining   = DAILY_LOSS_LIMIT - daily_loss
        risk_amount = min(max_risk, remaining)
        stop_dist   = abs(actual_entry - sig["sl"])
        if stop_dist <= 0:
            continue
        raw_lot  = risk_amount / (GOLD_CONTRACT * stop_dist)
        lot      = max(MIN_LOT, min(MAX_LOT, round(raw_lot / LOT_STEP) * LOT_STEP))

        brake_active = monthly_pnl < -MONTHLY_DRAWDOWN_BRAKE
        if brake_active:
            lot = max(MIN_LOT, round(lot * MONTHLY_BRAKE_MULTIPLIER / LOT_STEP) * LOT_STEP)

        act_risk = lot * GOLD_CONTRACT * stop_dist
        trade_id += 1
        open_bar_idx = i
        open_trade = BT(
            id=trade_id, open_time=btime, direction=sig["direction"],
            pattern=sig["pattern"], entry=actual_entry,
            stop_loss=sig["sl"], take_profit=sig["tp"],
            lot_size=lot, risk_amount=act_risk,
            confluence=sig["count"], regime=regime,
            rr_ratio=sig["rr"], monthly_brake_active=brake_active,
        )

    if open_trade is not None:
        t = open_trade
        mult = 1 if t.direction == "BUY" else -1
        t.pnl         = round((close - t.entry) * mult * t.lot_size * GOLD_CONTRACT, 2)
        t.spread_cost = round(t.lot_size * 100 * SPREAD_DOLLARS * 2, 2)
        t.net_pnl     = round(t.pnl - t.spread_cost, 2)
        t.exit_price  = close
        t.status      = "EXPIRED"
        t.close_time  = df.index[-1].strftime("%Y-%m-%d %H:%M")
        t.bars_to_close = n - 1 - open_bar_idx
        equity       += t.net_pnl
        trades.append(t)

    print("Filter breakdown:")
    for k, v in sk.items():
        if k == "ambiguous": continue
        print(f"  {k:12s}: {v:,} bars skipped")
    if sk["ambiguous"]:
        print(f"  {'ambiguous':12s}: {sk['ambiguous']:,} — SL+TP same bar, LOSS assumed")
    else:
        print(f"  {'ambiguous':12s}: 0 (none)")

    eq_series = pd.Series(equity_pts, index=df.index[:len(equity_pts)])
    return trades, eq_series, sk


# ── Daily Sharpe ──────────────────────────────────────────────────────────────
def daily_sharpe(eq: pd.Series) -> float:
    if eq is None or len(eq) < 5: return 0.0
    daily_eq  = eq.resample("B").last().dropna()
    daily_ret = daily_eq.pct_change().dropna()
    if len(daily_ret) < 2 or daily_ret.std() == 0: return 0.0
    return float((daily_ret.mean() / daily_ret.std()) * np.sqrt(252))


# ── Analytics ─────────────────────────────────────────────────────────────────
def analyse(trades: List[BT], eq: pd.Series) -> Dict:
    closed = [t for t in trades if t.status != "OPEN"]
    if not closed: return {}
    wins   = [t for t in closed if t.status == "WIN"]
    losses = [t for t in closed if t.status == "LOSS"]
    total  = len(closed); win_n = len(wins)

    net_pnls     = [t.net_pnl for t in closed]
    gross_pnls   = [t.pnl for t in closed]
    total_net    = sum(net_pnls)
    total_gross  = sum(gross_pnls)
    total_spread = sum(t.spread_cost for t in closed)

    gw = sum(t.net_pnl for t in wins)
    gl = abs(sum(t.net_pnl for t in losses))
    pf = gw / gl if gl > 0 else float("inf")

    vals = eq.values; peak = vals[0]; max_dd = 0.0
    for v in vals:
        peak = max(peak, v)
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)

    sharpe = daily_sharpe(eq)

    # Bars-to-close diagnostics (new)
    loss_bars = [t.bars_to_close for t in losses if t.bars_to_close > 0]
    stopped_1bar  = sum(1 for b in loss_bars if b <= 1)
    stopped_3bar  = sum(1 for b in loss_bars if b <= 3)
    median_loss_bars = float(np.median(loss_bars)) if loss_bars else 0

    pat: Dict[str, Dict] = {}
    for t in closed:
        s = pat.setdefault(t.pattern, {"total":0,"wins":0,"pnl":0.0})
        s["total"] += 1; s["pnl"] += t.net_pnl
        if t.status == "WIN": s["wins"] += 1

    monthly: Dict[str, float] = {}
    for t in closed:
        m = t.close_time[:7]; monthly[m] = monthly.get(m, 0.0) + t.net_pnl

    dir_stats: Dict[str, Dict] = {}
    for t in closed:
        s = dir_stats.setdefault(t.direction, {"total":0,"wins":0,"pnl":0.0})
        s["total"] += 1; s["pnl"] += t.net_pnl
        if t.status == "WIN": s["wins"] += 1

    return dict(
        total=total, wins=win_n, losses=len(losses),
        expired=len(closed)-win_n-len(losses),
        win_rate=win_n/total if total else 0,
        total_pnl=total_net, gross_pnl=total_gross, spread_cost=total_spread,
        avg_win=gw/win_n if win_n else 0,
        avg_loss=gl/len(losses) if losses else 0,
        profit_factor=pf, max_dd=max_dd, sharpe=sharpe,
        final_equity=ACCOUNT_SIZE + total_net,
        stopped_1bar=stopped_1bar, stopped_3bar=stopped_3bar,
        pct_stopped_3bar=stopped_3bar/len(losses) if losses else 0,
        median_loss_bars=median_loss_bars,
        patterns=pat, monthly=monthly, dir_stats=dir_stats,
    )


# ── Pretty report ─────────────────────────────────────────────────────────────
def print_report(trades: List[BT], stats: Dict, data_range: Tuple[str,str]):
    BE = 1 / (1 + MIN_RR)
    print("\n" + "=" * 72)
    print(f"  GOLD 1H BACKTEST — ATR_STOP_MULT={ATR_STOP_MULT}")
    print(f"  Data  : {data_range[0]}  to  {data_range[1]}")
    print(f"  R:R   : {MIN_RR}  |  Break-even: {BE:.1%}  |  Stop: ATR x {ATR_STOP_MULT}")
    print("=" * 72)

    rows = [
        ["Total trades",    stats["total"]],
        ["Win rate",        f"{stats['win_rate']:.1%}"],
        ["Gross P&L",       f"${stats.get('gross_pnl',0):+,.2f}"],
        ["Spread cost",     f"${stats.get('spread_cost',0):,.2f}"],
        ["Net P&L",         f"${stats['total_pnl']:+,.2f}"],
        ["Avg win",         f"${stats['avg_win']:,.2f}"],
        ["Avg loss",        f"${stats['avg_loss']:,.2f}"],
        ["Profit factor",   f"{stats['profit_factor']:.2f}"],
        ["Max drawdown",    f"{stats['max_dd']:.1f}%"],
        ["Sharpe (daily)",  f"{stats['sharpe']:.2f}"],
        ["Final equity",    f"${stats['final_equity']:,.2f}"],
        ["Stopped <=1 bar", f"{stats['stopped_1bar']} ({stats['stopped_1bar']/stats['losses']:.0%} of losses)" if stats['losses'] else "-"],
        ["Stopped <=3 bar", f"{stats['stopped_3bar']} ({stats['pct_stopped_3bar']:.0%} of losses)" if stats['losses'] else "-"],
        ["Median loss bars",f"{stats['median_loss_bars']:.1f}"],
    ]
    print(tabulate(rows, tablefmt="simple"))

    print("\n" + "=" * 72)
    print("  PATTERN BREAKDOWN")
    print("=" * 72)
    pat_rows = []
    for p, s in sorted(stats["patterns"].items(), key=lambda x: x[1]["pnl"], reverse=True):
        t = s["total"]; w = s["wins"]
        pat_rows.append([p[:32], t, w, f"{w/t:.0%}" if t else "-",
                         f"${s['pnl']/t:+.2f}" if t else "-", f"${s['pnl']:+,.2f}"])
    print(tabulate(pat_rows,
                   headers=["Pattern","Trades","Wins","Win%","Avg","Total P&L"],
                   tablefmt="simple"))

    print("\n" + "=" * 72)
    print("  MONTHLY P&L")
    print("=" * 72)
    for m in sorted(stats["monthly"]):
        p = stats["monthly"][m]
        bar = "+" * min(int(abs(p)/15), 40) if p >= 0 else "-" * min(int(abs(p)/15), 40)
        print(f"  {m}  ${p:+8,.2f}  {bar}")
    print()


def save(trades: List[BT], stats: Dict, filename: str):
    out = {
        "generated":    datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "atr_stop_mult": ATR_STOP_MULT,
        "config":       dict(interval=INTERVAL, rr=MIN_RR, rsi_buy=RSI_BUY,
                             rsi_sell=RSI_SELL, trend_ema=TREND_EMA,
                             atr_stop_mult=ATR_STOP_MULT,
                             session=f"{SESSION_START}:00-{SESSION_END}:00 UTC"),
        "summary":      {k: v for k, v in stats.items()
                         if k not in ("patterns","monthly","dir_stats")},
        "patterns":     stats.get("patterns", {}),
        "monthly":      stats.get("monthly", {}),
        "trades":       [asdict(t) for t in trades],
    }
    import pathlib
    pathlib.Path(filename).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Results saved to {filename}")


def _wf_stats_row(label: str, trades: List[BT], eq: pd.Series) -> Dict:
    if not trades: return {}
    eq_mini = pd.Series(
        [ACCOUNT_SIZE] + [ACCOUNT_SIZE + sum(t.net_pnl for t in trades[:k+1])
                          for k in range(len(trades))],
        index=[pd.Timestamp(trades[0].open_time)] +
              [pd.Timestamp(t.close_time) for t in trades],
    )
    return analyse(trades, eq_mini)


# ── Entry ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Gold 1H Backtest — Stop Width Research")
    parser.add_argument("--period", choices=["1y","2y","max"], default="2y")
    parser.add_argument("--atr",    type=float, default=2.0,
                        help="ATR stop multiplier (default 2.0, was 1.5 in production)")
    args = parser.parse_args()

    # Override the module-level constant so generate_signal() picks it up
    ATR_STOP_MULT = args.atr
    results_file  = f"backtest_v2_results_atr{str(args.atr).replace('.','')}.json"
    fetch_days    = _PERIOD_DAYS[args.period]

    print("=" * 72)
    print(f"  GOLD 1H BACKTEST — Stop Width Research")
    print(f"  ATR_STOP_MULT = {ATR_STOP_MULT}  (production = 1.5)")
    print(f"  TP distance   = ATR x {ATR_STOP_MULT * MIN_RR:.1f}  (vs ATR x {1.5 * MIN_RR:.1f} in production)")
    print(f"  Break-even WR = {1/(1+MIN_RR):.1%}")
    print(f"  Period: {args.period} ({fetch_days} days)")
    print(f"  Output: {results_file}")
    print("=" * 72 + "\n")

    df = fetch_1h(fetch_days)
    if len(df) < WARMUP_BARS + 20:
        print(f"Not enough bars: {len(df)}")
        raise SystemExit(1)

    print("Computing indicators...")
    df = add_indicators(df)

    print("Running bar-by-bar simulation...\n")
    trades, eq, diag = run_backtest(df)

    if not trades:
        print("No trades generated.")
        raise SystemExit(0)

    stats = analyse(trades, eq)
    stats["ambiguous_bars"] = diag.get("ambiguous", 0)
    r = (df.index[0].strftime("%Y-%m-%d"), df.index[-1].strftime("%Y-%m-%d"))
    print_report(trades, stats, r)

    # Walk-forward 70/30
    closed = [t for t in trades if t.close_time]
    val_sharpe = 0.0
    if closed:
        split_idx = int(len(closed) * TRAIN_RATIO)
        split_ts  = closed[split_idx].open_time if split_idx < len(closed) else closed[-1].open_time
        train_trades = [t for t in closed if t.open_time < split_ts]
        val_trades   = [t for t in closed if t.open_time >= split_ts]
        train_stats  = _wf_stats_row("TRAIN", train_trades, eq)
        val_stats_wf = _wf_stats_row("VAL",   val_trades,   eq)

        print("\n" + "=" * 72)
        print(f"  WALK-FORWARD ({int(TRAIN_RATIO*100)}/{int((1-TRAIN_RATIO)*100)}) — split: {split_ts}")
        print("=" * 72)
        wf_rows = [
            ["",              "TRAIN",                                        "VAL (OOS)"],
            ["Trades",        train_stats.get("total","—"),                   val_stats_wf.get("total","—")],
            ["Win rate",     f"{train_stats.get('win_rate',0):.1%}",         f"{val_stats_wf.get('win_rate',0):.1%}"],
            ["Net P&L",      f"${train_stats.get('total_pnl',0):+,.2f}",     f"${val_stats_wf.get('total_pnl',0):+,.2f}"],
            ["Profit factor",f"{train_stats.get('profit_factor',0):.2f}",    f"{val_stats_wf.get('profit_factor',0):.2f}"],
            ["Max DD",       f"{train_stats.get('max_dd',0):.1f}%",          f"{val_stats_wf.get('max_dd',0):.1f}%"],
            ["Sharpe",       f"{train_stats.get('sharpe',0):.2f}",           f"{val_stats_wf.get('sharpe',0):.2f}"],
        ]
        print(tabulate(wf_rows, tablefmt="simple", headers="firstrow"))
        val_sharpe   = val_stats_wf.get("sharpe", 0)
        train_sharpe = train_stats.get("sharpe", 0)
        if train_sharpe > 0 and val_sharpe < train_sharpe * 0.50:
            print(f"\n  *** OVERFITTING WARNING — val Sharpe {val_sharpe:.2f} < 50% of train {train_sharpe:.2f}")
        else:
            print(f"\n  Walk-forward OK")

        stats["val_sharpe"]   = val_sharpe
        stats["train_sharpe"] = train_sharpe
        print()

    save(trades, stats, results_file)
