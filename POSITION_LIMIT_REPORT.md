# Concurrent Position Limit — Research Report

**Date:** 2026-05-12
**Config:** ATR x2.5 | RSI_CEIL=70 | ADX>=25 | BUY-only
**Data:** 1H bars, ~725 days

> **Key finding:** `backtest_v2.py` is already single-position (MAX=1) by design —
> `open_trade: Optional[BT] = None` allows only one concurrent position.
> The 102 trades / 55% WR / Sharpe 2.77 figures ARE the MAX=1 results.
> The unlimited stacking seen on Demo Live Day 1 is a production-only behaviour
> that the standard backtest never modelled.

---

## Results Table

| Metric | MAX=1 | MAX=2 | MAX=inf |
|--------|-------|-------|---------|
| Total trades           |      103 |      173 |      448 |
| Win rate               |    54.4% |    52.6% |    43.5% |
| Net P&L                |  $+8,131 | $+12,782 |  $+8,362 |
| Spread/slip            |     $239 |     $409 |     $930 |
| Profit factor          |     2.33 |     2.13 |     1.26 |
| Sharpe (daily)         |     2.77 |     2.41 |     0.93 |
| Max DD                 |     5.7% |     8.7% |    20.9% |
| Avg win                |    $+251 |    $+260 |    $+206 |
| Avg loss               |    $+131 |    $+139 |    $+128 |
| Worst single day       |    $-280 |    $-481 |  $-2,125 |
| Best single day        |    $+390 |    $+998 |  $+3,678 |
| Days near limit        |        1 |       10 |       34 |

---

## Trade-Off Analysis

### MAX=1
- **Baseline** — every signal waits for the previous trade to close
- No stacking risk, no correlation risk between simultaneous positions
- Misses entry opportunities during open positions (pullback entries)

### MAX=2
- +70 additional trades vs MAX=1  (+4,651 P&L delta)
- Risk: 2x per signal = 2% account at risk when both slots are full
- Captures one layer of pullback entry; blocks a third

### MAX=inf
- +345 additional trades vs MAX=1  (+230 P&L delta)
- Risk: n× per signal — uncapped; mirrors Demo Live Day 1 (3 simultaneous)
- Pullback averaging works in trending markets; catastrophic in reversals

---

## Decision

| Criterion | MAX=1 | MAX=2 | MAX=inf |
|-----------|-------|-------|---------|
| Sharpe | 2.77 | 2.41 | 0.93 |
| Max DD | 5.7% | 8.7% | 20.9% |
| Risk control | Full | Partial | None |

**Recommendation: MAX=1**

Single-position is most risk-efficient. Stacking does not improve Sharpe — it adds correlated risk for no reward.

**Action:** Deploy MAX=1. Unlimited stacking was a bug, not a feature.

---

## Implementation

If deploying a position limit, add to `OrchestratorAgent._do_cycle()` after `self._check_open_positions()`:

```python
MAX_OPEN_POSITIONS = 1  # or 2 — set in Config

open_count = len(self.journal.get_open())
if open_count >= Config.MAX_OPEN_POSITIONS:
    self.info(f"Position limit ({open_count}/{Config.MAX_OPEN_POSITIONS}) — skipping signal")
    return
```

Add `MAX_OPEN_POSITIONS: int = 1` to `Config`.