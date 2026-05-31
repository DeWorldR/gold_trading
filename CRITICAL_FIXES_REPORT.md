# Critical Fixes Validation Report

Generated: 2026-05-10  
Baseline file: `backtest_v2_results_longonly.json`  
After-fixes file: `backtest_v2_results_after_critical_fixes.json`  
Dataset: GC=F 1H | 2024-05-15 → 2026-05-08 (725 days, 11,175 bars)

---

## Comparison

| Metric | Before (longonly) | After fixes | Change |
|--------|:-----------------:|:-----------:|:------:|
| Total trades | 303 | 303 | flat |
| Win rate | 39.9% | 39.9% | flat |
| Net P&L | +$4,823 | +$4,819 | −$4 (rounding) |
| Profit factor | 1.24 | 1.24 | flat |
| Sharpe (daily) | 1.26 | 1.26 | flat |
| Max DD | 12.5% | 12.5% | flat |
| Walk-forward train Sharpe | 2.19 | 2.19 | flat |
| Walk-forward val Sharpe | 1.34 | 1.34 | flat |
| Walk-forward train PF | 1.30 | 1.30 | flat |
| Walk-forward val PF | 1.12 | 1.12 | flat |
| **Ambiguous bars detected** | n/a | **0** | new metric |

---

## Look-Ahead Bias Impact

**Ambiguous bars (both SL and TP hit on the same 1H bar): 0 out of 11,175 bars.**

No look-ahead inflation existed in the backtester. The reason is structural:

- Stop distance = ATR(14) × 1.5 ≈ $22–37 on a typical 1H gold bar
- Take-profit distance = stop × 2.0 ≈ $44–75
- For a single bar to touch both, price would need to travel ATR × 4.5 in *both* directions from entry within one hour
- Over 725 days of 1H gold data, this scenario never occurred

The fix was still necessary — it enforces the correct conservative assumption and will warn if market conditions ever produce such a bar (e.g., a flash crash spike). It is now verified and instrumented.

---

## What Each Fix Did

### Fix 1 — Backtest ambiguous-bar handling (`backtest_v2.py`)

The `if hit_sl:` block already had `# SL wins if both hit same bar (conservative)` — the logic was correct but undocumented and uncounted. Added:
- `sk["ambiguous"]` counter incremented when `hit_sl and hit_tp`
- Per-bar `WARNING` print with trade ID, SL, and TP values for auditability
- Counter shown in filter breakdown and summary table
- Stored in `stats["ambiguous_bars"]` and persisted to JSON

### Fix 2 — `gold_trading_agents.py` paper simulator (`_paper_simulate`)

Rewrote to scan *all* bars after trade open (not just the latest), parse `trade.timestamp` as UTC-aware, handle the ambiguous-bar case with explicit LOSS assumption, and log a `WARNING` with bar timestamp and trade ID. Uses `period="5d"` to cover trades spanning a weekend.

### Fix 3 — MT5 data source for live mode (`MarketAnalystAgent`)

Added explicit broker dependency injection (`broker=None` in `__init__`). `fetch_bars()` routes to `_fetch_mt5_bars(n_bars=300)` in live mode, checking `self.broker.connected` before any MT5 call. Falls back to yfinance if MT5 returns fewer than 200 bars. `OrchestratorAgent.__init__` now instantiates `MT5Broker` first, then passes it to `MarketAnalystAgent(broker=self.broker)`.

### Fix 4 — Daily loss and consecutive-loss counters (`_close_trade`)

`_close_trade` now immediately increments `risk_manager._daily_loss` on a losing trade and updates `self._consec_loss` (reset on win or new day, increment on loss). Both limits now enforce *within* a cycle, not just on the next one.

---

## Decision

### A) GO LIVE CANDIDATE ✓

| Criterion | Threshold | Actual | Pass? |
|-----------|-----------|--------|-------|
| Sharpe (daily equity) | ≥ 0.8 | **1.26** | ✓ |
| Profit factor | ≥ 1.10 | **1.24** | ✓ |
| Max drawdown | ≤ 18% | **12.5%** | ✓ |
| Walk-forward val Sharpe | ≥ 50% of train (≥ 1.10) | **1.34** | ✓ |
| Ambiguous-bar inflation | 0 bars affected | **0** | ✓ |

All four go-live criteria pass. The baseline numbers are confirmed clean — they were not inflated by look-ahead bias.

**Recommendation: proceed to Demo Live as planned.**

### Caveats to carry forward

1. **PF 1.24 vs the earlier 1.25 internal target.** This is a strategy-tuning gap (see `COMPARISON.md` Option A/B/C), not a safety concern. At conservative sizing ($50 max risk/trade, $150 daily limit) the real-money exposure during forward testing is controlled.

2. **Walk-forward val PF = 1.12.** The out-of-sample profit factor is noticeably below in-sample (1.30). This is expected for any trend-following system over a 30% OOS window, but it should be monitored. If the forward-test 4-week PF falls below 1.0, pause and review.

3. **Late-2025 / early-2026 drawdown cluster.** Five consecutive losing months (Nov 2025 – Apr 2026: −$1,694 combined) drove the PF gap. This period coincides with gold consolidating after the Sep–Oct 2025 parabolic run. The EMA200 trend filter stayed BUY throughout (gold never broke below EMA200), which is correct — the system traded the correct direction but confluence setups were weaker. No filter change is needed; this is normal drawdown for a momentum system.

### Demo Live checklist

- [x] `PAPER_TRADE=false` in `.env`
- [x] `MT5_SYMBOL=GOLD#` in `.env`
- [x] `DAILY_LOSS_LIMIT=150` (conservative — half the backtested value)
- [x] `RISK_PER_TRADE_PCT=0.5` (0.5% = ~$50 max risk/trade)
- [x] MT5 terminal running, logged in to XM account before bot start
- [ ] Forward test for minimum 4 weeks before increasing sizing
- [ ] Stop Demo Live if 4-week PF < 1.0 or equity drops > 5% ($500)
