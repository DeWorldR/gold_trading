#!/usr/bin/env python3
"""
Faithful backtest of gold_trading_agents.py.

Imports the actual Config, TechnicalAnalystAgent, and RiskManagerAgent
classes so the backtest runs the identical code that runs in production.

Data: GC=F 1H bars (yfinance max ~730 days) with a 400-bar sliding window
per cycle — mirrors the production "last N bars" window the agents receive.

DXY: historical DX-Y.NYB daily bars downloaded once; trend computed per bar
using the same EMA20/EMA50 crossover logic as MarketAnalystAgent.

News filter: skipped (cannot replay historical ForexFactory calendars).
"""

import sys
import logging
import warnings
warnings.filterwarnings("ignore")

# Silence all loggers from gold_trading_agents during import + simulation
logging.disable(logging.CRITICAL)
sys.path.insert(0, ".")
from gold_trading_agents import (       # noqa: E402
    Config,
    TechnicalAnalystAgent,
    RiskManagerAgent,
    MarketState,
    TradeRecord,
)
logging.disable(logging.NOTSET)

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import List, Optional, Dict
import uuid

import yfinance as yf
import pandas as pd
import numpy as np

# ── Backtest-only tunables ─────────────────────────────────────────────────────
PERIOD_DAYS   = 720           # yfinance 1H limit is 730d; use 720 to avoid boundary errors
INTERVAL      = "1h"          # production uses 15m; 1H is the longest available
WINDOW        = 400           # sliding bar window fed to agents (mirrors ~5d of 15m)
WARMUP        = 220           # bars before signals start (EMA200 needs 200+)
SPREAD_USD    = 0.25          # 2.5 pip XM spread in dollars per side
SESSION_START = Config.SESSION_START_UTC   # 8
SESSION_END   = Config.SESSION_END_UTC     # 21

# ── Data download ──────────────────────────────────────────────────────────────
def fetch_gold() -> pd.DataFrame:
    end   = datetime.now()
    start = end - timedelta(days=PERIOD_DAYS)
    print(f"Downloading GC=F {INTERVAL} ({PERIOD_DAYS} days)...")
    df = yf.download(
        "GC=F",
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        interval=INTERVAL,
        progress=False,
        auto_adjust=True,
    )
    if df.empty:
        raise RuntimeError("No data returned for GC=F")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open","High","Low","Close","Volume"]].dropna()
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[df.index.dayofweek < 5]
    print(f"Bars: {len(df):,}  |  {df.index[0].date()} to {df.index[-1].date()}\n")
    return df


def fetch_dxy_trend_series(gold_index: pd.DatetimeIndex) -> pd.Series:
    """
    Downloads DX-Y.NYB daily closes and computes EMA20/EMA50 crossover
    for every date in gold_index.  Returns a Series indexed by gold_index
    with values UP | DOWN | NEUTRAL.
    """
    print("Downloading DX-Y.NYB daily data for DXY macro filter...")
    dxy_raw = yf.download(
        Config.DXY_SYMBOL,
        period="3y",
        interval="1d",
        progress=False,
        auto_adjust=True,
    )
    if dxy_raw.empty:
        print("  DXY download failed — all bars will use NEUTRAL")
        return pd.Series("NEUTRAL", index=gold_index)

    if isinstance(dxy_raw.columns, pd.MultiIndex):
        dxy_raw.columns = dxy_raw.columns.get_level_values(0)
    dxy_close = dxy_raw["Close"].dropna()
    dxy_close.index = pd.to_datetime(dxy_close.index, utc=True)

    ema20 = dxy_close.ewm(span=20, adjust=False).mean()
    ema50 = dxy_close.ewm(span=50, adjust=False).mean()

    def _trend_at(dt: pd.Timestamp) -> str:
        mask = dxy_close.index <= dt
        if not mask.any():
            return "NEUTRAL"
        idx = dxy_close.index[mask][-1]
        c, e20, e50 = float(dxy_close[idx]), float(ema20[idx]), float(ema50[idx])
        if c > e20 > e50:
            return "UP"
        if c < e20 < e50:
            return "DOWN"
        return "NEUTRAL"

    trend = pd.Series([_trend_at(t) for t in gold_index], index=gold_index)
    print(f"  DXY trend: UP={( trend=='UP').sum()}  DOWN={( trend=='DOWN').sum()}  NEUTRAL={( trend=='NEUTRAL').sum()}\n")
    return trend


# ── Regime helper (mirrors MarketAnalystAgent._detect_regime) ──────────────────
def detect_regime(df_window: pd.DataFrame, atr: float) -> str:
    close = float(df_window["Close"].iloc[-1])
    atr_pct = (atr / close * 100) if close > 0 else 0.0
    if atr_pct > Config.ATR_VOLATILE_PCT:
        return "VOLATILE"
    ema20_s = df_window["Close"].ewm(span=20, adjust=False).mean()
    ema50_s = df_window["Close"].ewm(span=50, adjust=False).mean()
    e20 = float(ema20_s.iloc[-1])
    e50 = float(ema50_s.iloc[-1])
    if close > e20 > e50:
        return "TRENDING_UP"
    if close < e20 < e50:
        return "TRENDING_DOWN"
    return "RANGING"


# ── Trade record for simulation ────────────────────────────────────────────────
@dataclass
class SimTrade:
    id: str
    open_bar: int
    open_time: str
    direction: str
    pattern: str
    entry: float
    sl: float
    tp: float
    lot: float
    risk: float
    confluence: int
    regime: str
    brake: bool
    close_bar: int = -1
    close_time: str = ""
    exit_price: float = 0.0
    pnl: float = 0.0
    net_pnl: float = 0.0
    spread_cost: float = 0.0
    status: str = "OPEN"


# ── Main simulation ────────────────────────────────────────────────────────────
def run(df: pd.DataFrame, dxy_trend: pd.Series) -> List[SimTrade]:
    n = len(df)
    trades: List[SimTrade] = []
    open_trade: Optional[SimTrade] = None
    journal: List[TradeRecord] = []    # minimal journal fed to RiskManager

    # Instantiate the actual production agents
    tech  = TechnicalAnalystAgent()
    risk  = RiskManagerAgent()

    equity       = Config.ACCOUNT_SIZE
    daily_loss   = 0.0
    daily_date   = ""
    consec_loss  = 0

    for i in range(WARMUP, n):
        bar      = df.index[i]
        btime_str = bar.strftime("%Y-%m-%d %H:%M")
        high      = float(df["High"].iloc[i])
        low       = float(df["Low"].iloc[i])
        close     = float(df["Close"].iloc[i])

        # ── Reset daily counters on new day ──
        bdate = bar.strftime("%Y-%m-%d")
        if bdate != daily_date:
            daily_loss  = 0.0
            daily_date  = bdate
            consec_loss = 0

        # ── Close open position ──
        if open_trade is not None:
            t = open_trade
            sl_hit = (t.direction=="BUY" and low<=t.sl) or (t.direction=="SELL" and high>=t.sl)
            tp_hit = (t.direction=="BUY" and high>=t.tp) or (t.direction=="SELL" and low<=t.tp)

            if sl_hit and tp_hit:
                exit_px, status = t.sl, "CLOSED_LOSS"
            elif tp_hit:
                exit_px, status = t.tp, "CLOSED_WIN"
            elif sl_hit:
                exit_px, status = t.sl, "CLOSED_LOSS"
            else:
                exit_px, status = None, None

            if exit_px is not None:
                mult = 1 if t.direction=="BUY" else -1
                t.pnl         = round((exit_px-t.entry)*mult*t.lot*Config.GOLD_CONTRACT_SIZE, 2)
                t.spread_cost = round(t.lot*Config.GOLD_CONTRACT_SIZE*SPREAD_USD*2, 2)
                t.net_pnl     = round(t.pnl-t.spread_cost, 2)
                t.exit_price  = exit_px
                t.status      = status
                t.close_bar   = i
                t.close_time  = btime_str
                equity       += t.net_pnl
                if t.net_pnl < 0:
                    daily_loss += abs(t.net_pnl)
                consec_loss = consec_loss+1 if status=="CLOSED_LOSS" else 0
                trades.append(t)
                open_trade = None

                # Sync the RiskManager's internal daily loss counter
                risk._daily_loss = daily_loss
                risk._monthly_pnl += t.net_pnl

                # Add a minimal TradeRecord so RiskManager journal lookups work
                journal.append(TradeRecord(
                    id=t.id, timestamp=t.open_time, direction=t.direction,
                    pattern=t.pattern, entry=t.entry, stop_loss=t.sl,
                    take_profit=t.tp, lot_size=t.lot, risk_amount=t.risk,
                    confluence_count=t.confluence, regime=t.regime,
                    paper=True, status=status, exit_price=exit_px,
                    exit_timestamp=t.close_time, pnl=t.net_pnl,
                ))

        if open_trade is not None:
            continue   # one trade at a time

        # ── Session filter (mirrors OrchestratorAgent) ──
        hour_utc = bar.hour
        if not (SESSION_START <= hour_utc < SESSION_END):
            continue

        # ── Consecutive-loss guard ──
        if consec_loss >= Config.MAX_CONSEC_LOSS:
            continue

        # ── Build sliding-window MarketState (mirrors production data feed) ──
        w_start  = max(0, i - WINDOW + 1)
        df_win   = df.iloc[w_start:i+1]

        atr_s    = df_win.ta.atr(length=14)
        atr      = float(atr_s.iloc[-1]) if atr_s is not None and not atr_s.empty else 0.0
        regime   = detect_regime(df_win, atr)
        dxy      = str(dxy_trend.iloc[i])

        state = MarketState(
            timestamp=bar.to_pydatetime(),
            open=float(df["Open"].iloc[i]),
            high=high, low=low, close=close,
            volume=float(df["Volume"].iloc[i]),
            regime=regime, atr=atr, df=df_win,
            dxy_trend=dxy,
        )

        # ── TechnicalAnalystAgent (exact production class) ──
        logging.disable(logging.CRITICAL)
        signal = tech.run(state)
        logging.disable(logging.NOTSET)

        if signal.direction == "NONE":
            continue

        # ── RiskManagerAgent (exact production class) ──
        logging.disable(logging.CRITICAL)
        decision = risk.run(signal, state, journal)
        logging.disable(logging.NOTSET)

        if not decision.approved:
            continue

        # ── Open trade ──
        spread_entry = (signal.entry + SPREAD_USD
                        if signal.direction=="BUY"
                        else signal.entry - SPREAD_USD)

        open_trade = SimTrade(
            id=str(uuid.uuid4())[:8],
            open_bar=i, open_time=btime_str,
            direction=signal.direction, pattern=signal.pattern,
            entry=spread_entry, sl=signal.stop_loss, tp=signal.take_profit,
            lot=decision.lot_size, risk=decision.risk_amount,
            confluence=signal.confluence_count, regime=regime,
            brake=decision.monthly_brake_active,
        )

    # Expire any open trade at end of data
    if open_trade is not None:
        t = open_trade
        mult = 1 if t.direction=="BUY" else -1
        t.pnl         = round((close-t.entry)*mult*t.lot*Config.GOLD_CONTRACT_SIZE, 2)
        t.spread_cost = round(t.lot*Config.GOLD_CONTRACT_SIZE*SPREAD_USD*2, 2)
        t.net_pnl     = round(t.pnl-t.spread_cost, 2)
        t.exit_price  = close
        t.status      = "EXPIRED"
        t.close_time  = df.index[-1].strftime("%Y-%m-%d %H:%M")
        trades.append(t)

    return trades


# ── Report ─────────────────────────────────────────────────────────────────────
def report(trades: List[SimTrade], start_date: str, end_date: str):
    closed = [t for t in trades if t.status != "OPEN"]
    if not closed:
        print("No closed trades in this period.")
        return

    wins   = [t for t in closed if t.status=="CLOSED_WIN"]
    losses = [t for t in closed if t.status=="CLOSED_LOSS"]
    total  = len(closed)
    win_n  = len(wins)

    net    = sum(t.net_pnl for t in closed)
    gross  = sum(t.pnl for t in closed)
    spread = sum(t.spread_cost for t in closed)
    gw     = sum(t.net_pnl for t in wins)
    gl     = abs(sum(t.net_pnl for t in losses))
    pf     = gw/gl if gl > 0 else float("inf")

    # Max drawdown on cumulative net equity
    peak = Config.ACCOUNT_SIZE
    eq   = Config.ACCOUNT_SIZE
    max_dd = 0.0
    for t in sorted(closed, key=lambda x: x.close_time):
        eq   += t.net_pnl
        peak  = max(peak, eq)
        dd    = (peak-eq)/peak*100 if peak>0 else 0
        max_dd = max(max_dd, dd)

    print("\n" + "="*64)
    print("  GOLD AGENT BACKTEST  (exact production code)")
    print(f"  Period  : {start_date}  to  {end_date}")
    print(f"  Interval: {INTERVAL} bars (production uses 15m)")
    print(f"  Config  : ATR x{Config.ATR_STOP_MULT} | RSI {Config.RSI_BUY}/{Config.RSI_SELL} "
          f"| RSI_CEIL={Config.RSI_CEILING_BUY} | ADX>={Config.ADX_TREND_THRESHOLD}")
    print(f"  Disabled: {Config.DISABLED_PATTERNS}")
    print("="*64)

    print(f"\n  PROFIT")
    print(f"    Gross P&L    : ${gross:+,.2f}")
    print(f"    Spread/slip  : -${spread:,.2f}")
    print(f"    Net P&L      : ${net:+,.2f}   ({net/Config.ACCOUNT_SIZE*100:+.1f}%)")
    print(f"    Final equity : ${Config.ACCOUNT_SIZE+net:,.2f}")
    print(f"    Profit factor: {pf:.2f}")
    print(f"    Max drawdown : {max_dd:.1f}%")

    print(f"\n  WIN RATE")
    print(f"    Trades total : {total}  (wins {win_n} / losses {len(losses)})")
    print(f"    Win rate     : {win_n/total:.1%}")
    wr_be = 1/(1+Config.MIN_RR)
    print(f"    Break-even   : {wr_be:.1%}  (at R:R {Config.MIN_RR})")
    print(f"    Avg win      : ${gw/win_n:+,.2f}" if win_n else "    Avg win      : --")
    print(f"    Avg loss     : -${gl/len(losses):,.2f}" if losses else "    Avg loss     : --")

    # ── Winning moves ──
    pat: Dict[str, dict] = {}
    for t in closed:
        s = pat.setdefault(t.pattern, {"n":0,"w":0,"pnl":0.0})
        s["n"] += 1; s["pnl"] += t.net_pnl
        if t.status=="CLOSED_WIN": s["w"] += 1

    print(f"\n  WINNING MOVES  (patterns, by net P&L)")
    print(f"  {'Pattern':<32} {'N':>4} {'W':>4} {'WR':>6} {'Net P&L':>10} {'Avg':>8}  Edge")
    print(f"  {'-'*32} {'-'*4} {'-'*4} {'-'*6} {'-'*10} {'-'*8}  ----")
    for p, s in sorted(pat.items(), key=lambda x: x[1]["pnl"], reverse=True):
        n_, w_, pnl_ = s["n"], s["w"], s["pnl"]
        wr_ = w_/n_ if n_ else 0
        edge = "STRONG" if n_>=5 and wr_>=0.55 else ("OK" if n_>=3 and wr_>=0.45 else "WEAK")
        print(f"  {p:<32} {n_:>4} {w_:>4} {wr_:>6.0%} {pnl_:>+10,.2f} {pnl_/n_:>+8.2f}  {edge}")

    # ── Monthly P&L ──
    monthly: Dict[str, float] = {}
    for t in closed:
        m = t.close_time[:7]
        monthly[m] = monthly.get(m, 0.0) + t.net_pnl

    print(f"\n  MONTHLY P&L")
    for m in sorted(monthly):
        p = monthly[m]
        bar_ = ("+" if p>=0 else "-") * min(int(abs(p)/20), 30)
        print(f"    {m}  ${p:+8,.2f}  {bar_}")

    # ── Regime breakdown ──
    reg: Dict[str, dict] = {}
    for t in closed:
        s = reg.setdefault(t.regime, {"n":0,"w":0,"pnl":0.0})
        s["n"] += 1; s["pnl"] += t.net_pnl
        if t.status=="CLOSED_WIN": s["w"] += 1

    print(f"\n  REGIME BREAKDOWN")
    for r_, s in sorted(reg.items(), key=lambda x: x[1]["pnl"], reverse=True):
        n_, w_ = s["n"], s["w"]
        print(f"    {r_:<20} n={n_:>3}  win={w_/n_:.0%}  pnl=${s['pnl']:+,.2f}")

    # ── Monthly brake stats ──
    brake_trades = [t for t in closed if t.brake]
    if brake_trades:
        print(f"\n  MONTHLY BRAKE: fired on {len(brake_trades)} trades "
              f"(lot halved when month P&L < -${Config.MONTHLY_DRAWDOWN_BRAKE:.0f})")

    print("="*64 + "\n")


# ── Entry ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    df       = fetch_gold()
    dxy      = fetch_dxy_trend_series(df.index)

    print("Running agent simulation bar by bar...")
    print(f"  Window={WINDOW} bars per cycle | Warmup={WARMUP} bars\n")
    trades   = run(df, dxy)

    start_dt = df.index[WARMUP].strftime("%Y-%m-%d")
    end_dt   = df.index[-1].strftime("%Y-%m-%d")
    report(trades, start_dt, end_dt)
