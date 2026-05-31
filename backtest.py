#!/usr/bin/env python3
"""
Gold 15m Backtest — up to 1 year of GC=F data
Identical signal + risk logic as gold_trading_agents.py
Bar-by-bar walk-forward: no look-ahead bias.

yfinance limitation: 15m data is only stored for ~60 days on their servers.
This script fetches in 55-day chunks going back 365 days and uses whatever
is actually available (typically the last 60 days).
"""

import json
import time
import warnings
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Dict, Optional, Tuple
warnings.filterwarnings("ignore")

import yfinance as yf
import pandas as pd
import pandas_ta as pta
import numpy as np
from tabulate import tabulate

# ── Constants (identical to Config in gold_trading_agents.py) ─────────────────
ACCOUNT_SIZE     = 10_000.0
MAX_RISK_PCT     = 0.01
MIN_RR           = 1.5
DAILY_LOSS_LIMIT = 300.0
MIN_CONFLUENCE   = 3
ATR_VOLATILE_PCT = 0.35
ATR_STOP_MULT    = 1.5
GOLD_CONTRACT    = 100.0
MIN_LOT          = 0.01
LOT_STEP         = 0.01
MAX_LOT          = 10.0
WARMUP_BARS      = 100    # bars before signals start (for indicator stability)
SYMBOL           = "GC=F"
INTERVAL         = "15m"
RESULTS_FILE     = "backtest_results.json"


# ── Trade record ──────────────────────────────────────────────────────────────
@dataclass
class BT:
    id: int
    open_bar: int
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
    close_bar: int = -1
    close_time: str = ""
    exit_price: float = 0.0
    pnl: float = 0.0
    status: str = "OPEN"   # WIN | LOSS | BE | EXPIRED


# ── Data download ─────────────────────────────────────────────────────────────
def fetch_data(days_back: int = 365) -> pd.DataFrame:
    print(f"Downloading GC=F 15m data (target: {days_back} days back)...")
    end   = datetime.now()
    start = end - timedelta(days=days_back)

    chunks: List[pd.DataFrame] = []
    chunk_end = end

    while chunk_end > start:
        chunk_start = max(chunk_end - timedelta(days=55), start)
        s = chunk_start.strftime("%Y-%m-%d")
        e = chunk_end.strftime("%Y-%m-%d")
        try:
            df = yf.download(SYMBOL, start=s, end=e, interval=INTERVAL,
                             progress=False, auto_adjust=True)
            if not df.empty:
                # yfinance sometimes returns MultiIndex columns
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df[["Open","High","Low","Close","Volume"]].dropna()
                chunks.append(df)
                print(f"  {s} to {e}: {len(df):,} bars")
            else:
                print(f"  {s} to {e}: no data (yfinance limit reached)")
                break           # older data not available; stop chunking
        except Exception as exc:
            print(f"  {s} to {e}: error - {exc}")
            break
        chunk_end = chunk_start
        time.sleep(0.4)

    if not chunks:
        raise RuntimeError("No data returned by yfinance.")

    data = pd.concat(chunks[::-1])
    data = data[~data.index.duplicated(keep="first")].sort_index()
    data.index = pd.to_datetime(data.index)
    # keep only market hours Mon-Fri (gold trades nearly 24h but filter weekends)
    data = data[data.index.dayofweek < 5]
    print(f"\nTotal bars: {len(data):,}  |  "
          f"{data.index[0].strftime('%Y-%m-%d')} to {data.index[-1].strftime('%Y-%m-%d')}\n")
    return data


# ── Indicators (computed once on full dataset — no look-ahead) ────────────────
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.ta.rsi(length=14, append=True)
    df.ta.ema(length=20, append=True)
    df.ta.ema(length=50, append=True)
    df.ta.atr(length=14, append=True)
    macd_df = df.ta.macd(fast=12, slow=26, signal=9)
    if macd_df is not None and not macd_df.empty:
        df = pd.concat([df, macd_df], axis=1)
    bb_df = df.ta.bbands(length=20, std=2)
    if bb_df is not None and not bb_df.empty:
        df = pd.concat([df, bb_df], axis=1)
    return df


def _col(df: pd.DataFrame, prefix: str, idx: int, default: float = 0.0) -> float:
    cols = [c for c in df.columns if c.startswith(prefix)]
    if not cols:
        return default
    v = df.iloc[idx][cols[0]]
    return float(v) if pd.notna(v) and np.isfinite(float(v)) else default


# ── Signal generation (identical to TechnicalAnalystAgent) ───────────────────
def generate_signal(df: pd.DataFrame, i: int) -> Optional[dict]:
    close  = float(df["Close"].iloc[i])
    rsi    = _col(df, "RSI_",  i)
    ema20  = _col(df, "EMA_20", i)
    ema50  = _col(df, "EMA_50", i)
    atr    = _col(df, "ATRr_", i) or _col(df, "ATR", i)
    macd_v = _col(df, "MACD_",  i)
    macd_s = _col(df, "MACDs_", i)
    bb_u   = _col(df, "BBU_",   i, close * 1.01)
    bb_l   = _col(df, "BBL_",   i, close * 0.99)

    buy_r: List[str]  = []
    sell_r: List[str] = []

    if rsi > 0:
        if rsi < 40:   buy_r.append(f"RSI oversold ({rsi:.1f})")
        elif rsi > 60: sell_r.append(f"RSI overbought ({rsi:.1f})")

    if ema20 > 0:
        if close > ema20: buy_r.append(f"Price above EMA20")
        else:             sell_r.append(f"Price below EMA20")

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

    if buy_n >= sell_n and buy_n >= MIN_CONFLUENCE:
        direction, reasons, count = "BUY", buy_r, buy_n
        sl   = round(close - atr * ATR_STOP_MULT, 2)
        dist = close - sl
        tp   = round(close + dist * MIN_RR, 2)
    elif sell_n > buy_n and sell_n >= MIN_CONFLUENCE:
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

    return dict(direction=direction, reasons=reasons, count=count,
                entry=close, sl=sl, tp=tp, rr=rr, atr=atr)


def detect_regime(df: pd.DataFrame, i: int) -> str:
    close = float(df["Close"].iloc[i])
    atr   = _col(df, "ATRr_", i) or _col(df, "ATR", i)
    atr_pct = (atr / close * 100) if close > 0 else 0.0
    if atr_pct > ATR_VOLATILE_PCT:
        return "VOLATILE"
    ema20 = _col(df, "EMA_20", i)
    ema50 = _col(df, "EMA_50", i)
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


# ── Position sizing (identical to RiskManagerAgent) ───────────────────────────
def size_position(risk_amt: float, stop_dist: float) -> Tuple[float, float]:
    raw     = risk_amt / (GOLD_CONTRACT * stop_dist)
    lot     = max(MIN_LOT, min(MAX_LOT, round(raw / LOT_STEP) * LOT_STEP))
    act_risk = lot * GOLD_CONTRACT * stop_dist
    return lot, act_risk


# ── Bar-by-bar simulation ─────────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame) -> Tuple[List[BT], pd.Series]:
    n          = len(df)
    trades:    List[BT] = []
    open_trade: Optional[BT] = None
    equity     = ACCOUNT_SIZE
    equity_curve: List[float] = [equity] * WARMUP_BARS

    daily_loss:  float = 0.0
    daily_date:  str   = ""
    trade_id:    int   = 0
    skipped_volatile  = 0
    skipped_daily     = 0
    skipped_rr        = 0

    for i in range(WARMUP_BARS, n):
        bar_date = df.index[i].strftime("%Y-%m-%d")
        bar_time = df.index[i].strftime("%Y-%m-%d %H:%M")
        hi   = float(df["High"].iloc[i])
        lo   = float(df["Low"].iloc[i])
        close = float(df["Close"].iloc[i])

        # Reset daily loss tracker on new day
        if bar_date != daily_date:
            daily_loss = 0.0
            daily_date = bar_date

        # ── 1. Check open trade SL/TP ──────────────────────────────────────
        if open_trade is not None:
            t = open_trade
            hit_sl = hit_tp = False

            if t.direction == "BUY":
                hit_sl = lo <= t.stop_loss
                hit_tp = hi >= t.take_profit
            else:
                hit_sl = hi >= t.stop_loss
                hit_tp = lo <= t.take_profit

            # Conservative: if both hit in same bar, assume SL first
            if hit_sl:
                t.exit_price = t.stop_loss
                t.pnl        = round(-t.risk_amount, 2)
                t.status     = "LOSS"
                t.close_bar  = i
                t.close_time = bar_time
                equity       += t.pnl
                daily_loss   += abs(t.pnl)
                trades.append(t)
                open_trade = None
            elif hit_tp:
                t.exit_price = t.take_profit
                t.pnl        = round(t.risk_amount * t.rr_ratio, 2)
                t.status     = "WIN"
                t.close_bar  = i
                t.close_time = bar_time
                equity       += t.pnl
                trades.append(t)
                open_trade = None

        equity_curve.append(equity)

        # ── 2. Only one trade at a time ────────────────────────────────────
        if open_trade is not None:
            continue

        # ── 3. Risk checks before signal ──────────────────────────────────
        regime = detect_regime(df, i)

        if regime == "VOLATILE":
            skipped_volatile += 1
            continue

        if daily_loss >= DAILY_LOSS_LIMIT:
            skipped_daily += 1
            continue

        # ── 4. Signal ─────────────────────────────────────────────────────
        sig = generate_signal(df, i)
        if sig is None:
            continue

        if sig["rr"] < MIN_RR:
            skipped_rr += 1
            continue

        # ── 5. Position sizing ─────────────────────────────────────────────
        max_risk    = equity * MAX_RISK_PCT
        remaining   = DAILY_LOSS_LIMIT - daily_loss
        risk_amount = min(max_risk, remaining)

        stop_dist   = abs(sig["entry"] - sig["sl"])
        lot, act_risk = size_position(risk_amount, stop_dist)

        # ── 6. Open trade ──────────────────────────────────────────────────
        trade_id += 1
        open_trade = BT(
            id=trade_id,
            open_bar=i,
            open_time=bar_time,
            direction=sig["direction"],
            pattern=name_pattern(sig["reasons"], sig["direction"]),
            entry=sig["entry"],
            stop_loss=sig["sl"],
            take_profit=sig["tp"],
            lot_size=lot,
            risk_amount=act_risk,
            confluence=sig["count"],
            regime=regime,
            rr_ratio=round(sig["rr"], 2),
        )

    # Close any trade still open at end of data
    if open_trade is not None:
        t = open_trade
        t.exit_price = close
        t.pnl        = round(
            (close - t.entry) * t.lot_size * GOLD_CONTRACT
            if t.direction == "BUY"
            else (t.entry - close) * t.lot_size * GOLD_CONTRACT, 2
        )
        t.status     = "EXPIRED"
        t.close_time = df.index[-1].strftime("%Y-%m-%d %H:%M")
        t.close_bar  = n - 1
        equity       += t.pnl
        trades.append(t)

    print(f"Skipped bars — VOLATILE:{skipped_volatile:,}  daily_limit:{skipped_daily:,}  low_RR:{skipped_rr:,}")
    return trades, pd.Series(equity_curve, index=df.index[:len(equity_curve)])


# ── Analytics ─────────────────────────────────────────────────────────────────
def analyse(trades: List[BT], equity_curve: pd.Series) -> Dict:
    closed = [t for t in trades if t.status != "OPEN"]
    if not closed:
        return {}

    wins   = [t for t in closed if t.status == "WIN"]
    losses = [t for t in closed if t.status == "LOSS"]
    total  = len(closed)
    win_n  = len(wins)

    pnls      = [t.pnl for t in closed]
    total_pnl = sum(pnls)
    gross_win  = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    pf         = gross_win / gross_loss if gross_loss > 0 else float("inf")

    # Max drawdown
    eq = equity_curve.values
    peak = eq[0]
    max_dd = 0.0
    for v in eq:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Sharpe (annualised on per-trade returns, assuming 252 trading days × 26 cycles/day)
    ret_arr = np.array(pnls) / ACCOUNT_SIZE
    sharpe  = 0.0
    if len(ret_arr) > 1 and ret_arr.std() > 0:
        periods_per_year = 252 * 26   # 15m bars per trading year
        sharpe = (ret_arr.mean() / ret_arr.std()) * np.sqrt(periods_per_year)

    # Per-pattern stats
    pat: Dict[str, Dict] = {}
    for t in closed:
        s = pat.setdefault(t.pattern, {"total":0,"wins":0,"pnl":0.0})
        s["total"] += 1
        s["pnl"]   += t.pnl
        if t.status == "WIN":
            s["wins"] += 1

    # Monthly P&L
    monthly: Dict[str, float] = {}
    for t in closed:
        month = t.close_time[:7]
        monthly[month] = monthly.get(month, 0.0) + t.pnl

    return dict(
        total=total, wins=win_n, losses=len(losses),
        win_rate=win_n/total if total else 0,
        total_pnl=total_pnl,
        avg_win=gross_win/win_n if win_n else 0,
        avg_loss=gross_loss/len(losses) if losses else 0,
        profit_factor=pf,
        max_drawdown_pct=max_dd,
        sharpe=sharpe,
        patterns=pat,
        monthly=monthly,
    )


# ── Report ────────────────────────────────────────────────────────────────────
def print_report(trades: List[BT], stats: Dict, data_range: Tuple[str, str]):
    SEP = "=" * 70
    print(f"\n{SEP}")
    print("  GOLD 15m BACKTEST RESULTS")
    print(f"  Data: {data_range[0]}  to  {data_range[1]}")
    print(SEP)

    summary = [
        ["Total trades",     stats["total"]],
        ["Wins",            f"{stats['wins']} ({stats['win_rate']:.1%})"],
        ["Losses",           stats["losses"]],
        ["Total P&L",       f"${stats['total_pnl']:+,.2f}"],
        ["Avg win",         f"${stats['avg_win']:,.2f}"],
        ["Avg loss",        f"${stats['avg_loss']:,.2f}"],
        ["Profit factor",   f"{stats['profit_factor']:.2f}"],
        ["Max drawdown",    f"{stats['max_drawdown_pct']:.1f}%"],
        ["Sharpe ratio",    f"{stats['sharpe']:.2f}"],
        ["Final equity",    f"${ACCOUNT_SIZE + stats['total_pnl']:,.2f}"],
    ]
    print(tabulate(summary, tablefmt="simple"))

    # Pattern breakdown
    print(f"\n{SEP}")
    print("  PATTERN BREAKDOWN")
    print(SEP)
    pat_rows = []
    for pat, s in sorted(stats["patterns"].items(),
                         key=lambda x: x[1]["pnl"], reverse=True):
        t = s["total"]
        w = s["wins"]
        wr = f"{w/t:.0%}" if t else "-"
        avg = f"${s['pnl']/t:+.2f}" if t else "-"
        flag = " ★" if t >= 3 and w/t >= 0.70 else ""
        pat_rows.append([pat[:30], t, w, wr, avg, f"${s['pnl']:+,.2f}", flag])
    print(tabulate(pat_rows,
                   headers=["Pattern","Trades","Wins","Win%","Avg P&L","Total P&L",""],
                   tablefmt="simple"))

    # Monthly P&L
    print(f"\n{SEP}")
    print("  MONTHLY P&L")
    print(SEP)
    month_rows = []
    for month in sorted(stats["monthly"].keys()):
        pnl = stats["monthly"][month]
        bar = ("+" * min(int(pnl / 20), 30)) if pnl >= 0 else ("-" * min(int(abs(pnl) / 20), 30))
        month_rows.append([month, f"${pnl:+,.2f}", bar])
    print(tabulate(month_rows, headers=["Month","P&L",""], tablefmt="simple"))

    # Best / worst trades
    sorted_trades = sorted(trades, key=lambda x: x.pnl, reverse=True)
    print(f"\n{SEP}")
    print("  TOP 5 TRADES")
    print(SEP)
    top_rows = [[t.id, t.open_time[:16], t.direction, t.pattern[:24],
                 f"${t.entry:.2f}", f"${t.exit_price:.2f}",
                 f"${t.pnl:+.2f}", t.status]
                for t in sorted_trades[:5]]
    print(tabulate(top_rows,
                   headers=["#","Open","Dir","Pattern","Entry","Exit","P&L","Status"],
                   tablefmt="simple"))

    print(f"\n{SEP}")
    print("  BOTTOM 5 TRADES")
    print(SEP)
    bot_rows = [[t.id, t.open_time[:16], t.direction, t.pattern[:24],
                 f"${t.entry:.2f}", f"${t.exit_price:.2f}",
                 f"${t.pnl:+.2f}", t.status]
                for t in sorted_trades[-5:]]
    print(tabulate(bot_rows,
                   headers=["#","Open","Dir","Pattern","Entry","Exit","P&L","Status"],
                   tablefmt="simple"))
    print()


# ── Save results ──────────────────────────────────────────────────────────────
def save_results(trades: List[BT], stats: Dict):
    out = {
        "generated": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "summary": {k: v for k, v in stats.items()
                    if k not in ("patterns", "monthly")},
        "patterns": stats.get("patterns", {}),
        "monthly":  stats.get("monthly", {}),
        "trades":   [asdict(t) for t in trades],
    }
    Path(RESULTS_FILE).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Full results saved to {RESULTS_FILE}")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 70)
    print("  GOLD 15m BACKTEST — 1 Year Target")
    print("  Note: yfinance stores 15m data for ~60 days only.")
    print("        We will backtest on all bars actually available.")
    print("=" * 70 + "\n")

    # Download
    df = fetch_data(days_back=365)

    if len(df) < WARMUP_BARS + 10:
        print(f"Not enough bars ({len(df)}) to run backtest. Need > {WARMUP_BARS + 10}.")
        raise SystemExit(1)

    # Indicators
    print("Computing indicators...")
    df = add_indicators(df)

    # Simulate
    print("Running bar-by-bar simulation...\n")
    trades, equity_curve = run_backtest(df)

    if not trades:
        print("No trades were generated. Try lowering MIN_CONFLUENCE or check data.")
        raise SystemExit(0)

    # Analyse + report
    stats = analyse(trades, equity_curve)
    data_range = (df.index[0].strftime("%Y-%m-%d"), df.index[-1].strftime("%Y-%m-%d"))
    print_report(trades, stats, data_range)
    save_results(trades, stats)
