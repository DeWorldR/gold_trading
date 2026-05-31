#!/usr/bin/env python3
"""
EMA200 slope lookback sensitivity sweep (v8 validation)

Tests EMA_SLOPE_LOOKBACK=[0=disabled, 5, 10, 20, 40, 80] on the 2yr 1H backtest.
Also compares correction period (2026-04-15+) for each setting to test the
filter's value during the actual regime transition that motivated it.

Backtest uses 1H bars; production uses 15m bars.
Same numeric lookback = different wall-clock windows in each:
  lookback=10 → 10h in 1H backtest vs 2.5h in 15m production.
The sweep shows sensitivity across the 1H range; notes the 15m equivalent.

Usage: py -3.12 slope_sensitivity.py
"""
import warnings
warnings.filterwarnings("ignore")

import io
import json
import contextlib

import numpy as np
import pandas as pd
from tabulate import tabulate

import backtest_v2 as bt

# ── Config ────────────────────────────────────────────────────────────────────

LOOKBACKS       = [0, 5, 10, 20, 40, 80]
CORRECTION_DATE = "2026-04-15"   # start of current gold correction

# Time-frame mapping: lookback N bars on each timeframe
# lookback * 1h  = wall clock on 1H backtest
# lookback * 15m = wall clock on 15m production
def _label(lb: int) -> str:
    if lb == 0:
        return "DISABLED"
    h1  = lb                     # hours on 1H backtest
    m15 = lb * 15                # minutes on 15m production
    h15 = m15 // 60
    r15 = m15 % 60
    return f"{lb}b ({h1}h/1H | {h15}h{r15:02d}m/15m)"


# ── Helpers ───────────────────────────────────────────────────────────────────

def run_lookback(df_raw: pd.DataFrame, lookback: int):
    """Re-run indicators + backtest with a specific slope lookback (0=disabled)."""
    bt.EMA_SLOPE_LOOKBACK = lookback
    df = bt.add_indicators(df_raw.copy())
    # pandas diff(0) returns 0 everywhere → would block ALL BUY signals.
    # Override to +1.0 (always positive) to mean "filter disabled".
    if lookback == 0:
        df["ema200_slope"] = 1.0

    # Suppress verbose per-bar output from run_backtest; results come from return values.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        trades, eq, sk = bt.run_backtest(df)

    stats = bt.analyse(trades, eq)
    return trades, eq, sk, stats


def correction_trades(trades: list) -> list:
    """Closed trades (WIN/LOSS) opened on or after CORRECTION_DATE."""
    return [t for t in trades
            if t.status in ("WIN", "LOSS") and t.open_time >= CORRECTION_DATE]


def corr_stats(trades: list) -> dict:
    if not trades:
        return dict(n=0, wins=0, losses=0, pnl=0.0, wr=0.0)
    wins   = [t for t in trades if t.status == "WIN"]
    losses = [t for t in trades if t.status == "LOSS"]
    return dict(
        n=len(trades), wins=len(wins), losses=len(losses),
        pnl=round(sum(t.net_pnl for t in trades), 2),
        wr=round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
    )


# ── Main sweep ────────────────────────────────────────────────────────────────

def main():
    print("Downloading 2yr 1H GC=F data...")
    df_raw = bt.fetch_1h(days=725)
    print()

    results = []

    for lb in LOOKBACKS:
        lbl = _label(lb)
        print(f"Running lookback={lb} ({lbl})...", flush=True)
        trades, eq, sk, stats = run_lookback(df_raw, lb)
        ct = corr_stats(correction_trades(trades))

        r = dict(
            lb=lb, label=lbl,
            # 2yr full backtest
            trades  = stats.get("total", 0),
            wr      = round(stats.get("win_rate", 0) * 100, 1),
            sharpe  = round(stats.get("sharpe", 0), 2),
            pnl     = round(stats.get("total_pnl", 0), 2),
            maxdd   = round(stats.get("max_dd", 0), 1),
            pf      = round(stats.get("profit_factor", 0), 2),
            avg_win = round(stats.get("avg_win", 0), 2),
            avg_loss= round(stats.get("avg_loss", 0), 2),
            blocked = sk.get("ema200_slope", 0),   # bars slope was the block reason
            # Correction period (2026-04-15+)
            c_n      = ct["n"],
            c_wins   = ct["wins"],
            c_losses = ct["losses"],
            c_pnl    = ct["pnl"],
            c_wr     = ct["wr"],
        )
        results.append(r)
        print(f"  2yr  : {r['trades']} trades | WR {r['wr']}% | Sharpe {r['sharpe']} | "
              f"P&L ${r['pnl']:+,.2f} | MaxDD {r['maxdd']}% | slope_blocked={r['blocked']}")
        print(f"  Corr : {r['c_n']} trades | WR {r['c_wr']}% | P&L ${r['c_pnl']:+,.2f}\n")

    # ── Save raw results ──────────────────────────────────────────────────────
    with open("slope_sensitivity_results.json", "w") as f:
        json.dump(results, f, indent=2)

    # ── Summary table: 2yr ───────────────────────────────────────────────────
    baseline = next(r for r in results if r["lb"] == 0)

    print("\n" + "="*90)
    print("  2-YEAR BACKTEST: SLOPE LOOKBACK SENSITIVITY")
    print("="*90)
    hdr = ["Lookback","Trades","dTrades","WR%","Sharpe","dSharpe","Net P&L","dP&L","MaxDD","PF","Blk-bars"]
    rows = []
    for r in results:
        dt = ("---" if r["lb"] == 0
              else f"{r['trades'] - baseline['trades']:+d}")
        ds = ("---" if r["lb"] == 0
              else f"{r['sharpe'] - baseline['sharpe']:+.2f}")
        dp = ("---" if r["lb"] == 0
              else f"${r['pnl'] - baseline['pnl']:+,.0f}")
        rows.append([
            "NONE" if r["lb"] == 0 else f"{r['lb']}b",
            r["trades"], dt,
            f"{r['wr']}%",
            r["sharpe"], ds,
            f"${r['pnl']:+,.0f}", dp,
            f"{r['maxdd']}%", r["pf"],
            r["blocked"],
        ])
    print(tabulate(rows, headers=hdr, tablefmt="simple"))
    print(f"\n  Baseline (NONE) = slope filter disabled: {baseline['trades']} trades, "
          f"Sharpe {baseline['sharpe']}, P&L ${baseline['pnl']:+,.0f}")

    # ── Summary table: correction period ─────────────────────────────────────
    base_corr = next(r for r in results if r["lb"] == 0)

    print("\n" + "="*90)
    print(f"  CORRECTION PERIOD ({CORRECTION_DATE} to now) — regime transition test")
    print(f"  Gold: ${df_raw['Close'].iloc[-1]:.0f} now vs peak ~$5,041 pre-correction")
    print("="*90)
    hdr2 = ["Lookback","Trades","dTrades","Wins","Losses","WR%","Net P&L","dP&L","Interpretation"]
    rows2 = []
    for r in results:
        dt = "---" if r["lb"] == 0 else f"{r['c_n'] - base_corr['c_n']:+d}"
        dp = "---" if r["lb"] == 0 else f"${r['c_pnl'] - base_corr['c_pnl']:+,.0f}"
        interp = "baseline"
        if r["lb"] > 0 and base_corr["c_n"] > 0:
            blocked_n = base_corr["c_n"] - r["c_n"]
            saved     = r["c_pnl"] - base_corr["c_pnl"]
            if saved > 0:
                interp = f"saved ${saved:+,.0f} ({blocked_n} trades blocked)"
            elif saved < 0:
                interp = f"cost ${abs(saved):,.0f} (blocked {blocked_n} winners)"
            else:
                interp = "no change"
        rows2.append([
            "NONE" if r["lb"] == 0 else f"{r['lb']}b",
            r["c_n"], dt,
            r["c_wins"], r["c_losses"],
            f"{r['c_wr']}%",
            f"${r['c_pnl']:+,.0f}", dp,
            interp,
        ])
    print(tabulate(rows2, headers=hdr2, tablefmt="simple"))
    print()
    print("  Note: lookback=0 (NONE) is the system without any slope filter.")
    print("  Negative dP&L vs NONE = filter prevented losses. Positive = blocked winners.")
    print()

    # ── Decision guidance ─────────────────────────────────────────────────────
    print("="*90)
    print("  DECISION CRITERIA")
    print("="*90)
    print(f"  Sharpe threshold: >= 2.5 on 2yr (current v8 with lb=10: {next(r['sharpe'] for r in results if r['lb']==10):.2f})")
    print(f"  Trade drop:       <= 5% on bull-market (2yr) period")
    print(f"  Correction value: filter should reduce losses vs no-filter baseline\n")
    crit_hdr = ["Lookback","Sharpe>=2.5","Trade drop<=5%","Corr P&L > NONE","Overall"]
    crit_rows = []
    for r in results:
        if r["lb"] == 0:
            continue
        pct_drop = (r["trades"] - baseline["trades"]) / baseline["trades"] * 100
        sharpe_ok = "PASS" if r["sharpe"] >= 2.5 else "FAIL"
        drop_ok   = "PASS" if abs(pct_drop) <= 5.0 else f"FAIL ({pct_drop:+.1f}%)"
        corr_ok   = "PASS" if r["c_pnl"] >= base_corr["c_pnl"] else f"FAIL (${r['c_pnl']-base_corr['c_pnl']:+,.0f})"
        n_pass    = sum([sharpe_ok == "PASS", "PASS" in drop_ok, "PASS" in corr_ok])
        overall   = "PASS" if n_pass == 3 else f"PARTIAL ({n_pass}/3)" if n_pass > 0 else "FAIL"
        crit_rows.append([
            f"{r['lb']}b", sharpe_ok, drop_ok, corr_ok, overall
        ])
    print(tabulate(crit_rows, headers=crit_hdr, tablefmt="simple"))

    print(f"\nResults saved to slope_sensitivity_results.json")


if __name__ == "__main__":
    main()
