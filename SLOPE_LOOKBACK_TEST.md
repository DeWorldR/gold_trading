# EMA200 Slope Lookback Sensitivity Test

**Date:** 2026-05-19  
**Question:** Is EMA_SLOPE_LOOKBACK=10 (current v8 deployment) the right value?  
**Script:** `slope_sensitivity.py`  
**Results:** `slope_sensitivity_results.json`  
**Verdict:** **Remove the slope filter. Revert to lb=0 (disabled).**

---

## Setup

`slope_sensitivity.py` sweeps `EMA_SLOPE_LOOKBACK` = [0=disabled, 5, 10, 20, 40, 80]
on the same 2yr 1H download (same data for all runs — apples-to-apples).

Also filters each run's trade list to correction period (2026-04-15 to 2026-05-19)
to test the filter's value during the actual regime transition that motivated it.

**Time-frame note:** The backtest uses 1H bars; production uses 15m bars.
Same numeric lookback = different wall-clock windows:

| Lookback | 1H backtest | 15m production |
|---|---|---|
| 5 | 5h | 1h 15m |
| 10 (current) | 10h | 2h 30m |
| 20 | 20h | 5h |
| 40 | 40h | 10h |
| 80 | 80h | 20h |

Time-aligned comparison (same wall-clock window in both timeframes):
`Production lb=20 (5h) ↔ Backtest lb=5 (5h)`. So if we want to know what
`lb=20` in production would look like, the backtest proxy is `lb=5`.

---

## Data Freshness Note

The v8 validation report (2026-05-18) showed:
- Baseline (no-filter): Sharpe 2.77, 103 trades
- With lb=10: Sharpe 2.59, 101 trades

This sweep (2026-05-19) shows:
- Baseline (lb=0): Sharpe 2.0, 98 trades  
- With lb=10: Sharpe 1.94, 95 trades

**Cause:** yfinance GC=F is a continuously-adjusted futures series. Contract rolls
retroactively adjust historical prices. Between 2026-05-18 and 2026-05-19, the
underlying price series shifted enough to change absolute results by ~$2,700 P&L
and 0.6 Sharpe. The validation report compared lb=10 against a stale no-filter
baseline from a different data snapshot. **That comparison was not clean.**

This sweep is the first honest comparison: all six lookbacks use the same single
download, so relative differences are valid even if absolute numbers change tomorrow.

---

## 2-Year Backtest Results

```
Data: 2024-05-24 to 2026-05-19  |  11,156 bars (1H)

Lookback  Trades  dTrades  WR%    Sharpe  dSharpe  Net P&L   dP&L    MaxDD   PF    Blk-bars
--------  ------  -------  -----  ------  -------  --------  ------  -----  ----  --------
NONE          98  ---      49.0%   2.00   ---      $+4,884   ---     6.1%   1.83        0
5b            98   +0      49.0%   2.00   +0.00    $+4,884   $+0     6.1%   1.83      719
10b           95   -3      49.5%   1.94   -0.06    $+4,519   $-365   6.1%   1.80      720
20b           85  -13      51.8%   2.23   +0.23    $+5,418   $+533   5.9%   2.01      738
40b           81  -17      50.6%   2.01   +0.01    $+4,632   $-252   6.0%   1.89      722
80b           77  -21      51.9%   2.15   +0.15    $+4,928   $+44    5.7%   2.04      671
```

### Key observations

**lb=5 is completely inert.**
719 bars have negative 5-bar slope but not a single actual trade is blocked.
The 5h window (1.25h in 15m production) flips too frequently — every short-term
EMA200 wobble triggers the slope counter but none coincide with real entry signals.

**lb=10 (current deployment) is net-negative.**
Removes 3 trades and loses $365 P&L vs no-filter. Sharpe 1.94 vs 2.0. Small but
consistent degradation. The 10h window (2.5h in 15m production) is too noisy for
the 1H backtest data; the 3 blocked trades were mostly legitimate.

**lb=20 is the best performer.**
Sharpe +0.23, P&L +$533 vs no-filter. WR improves from 49.0% to 51.8%.
Removes 13 trades (bad ones — quality metrics all improve). This is a genuinely
net-positive filter in the 1H backtest data.

**lb=40 and lb=80 are diminishing returns.**
lb=40: Sharpe barely better than no-filter (+0.01), removes 17 trades for almost
no benefit. lb=80: modestly positive (+0.15 Sharpe, +$44) but removes 21% of trades.

### Time-frame aligned view

If production uses lb=20 (5h window), the 1H backtest proxy is lb=5 → zero effect.
If production uses lb=80 (20h window), the 1H proxy is lb=20 → best performer (+0.23 Sharpe).

The lb=20 benefit in the backtest is measuring a 20h slope window. To get that same
20h window in 15m production, you would need lb=80. The backtest does not validate
deploying lb=20 to a 15m system.

---

## Correction Period Results

```
2026-04-15 to 2026-05-19 (Gold corrected from ~$5,041 to ~$4,549, -10%)

Lookback  Trades  dTrades  Wins  Losses  WR%    Net P&L  dP&L    Interpretation
--------  ------  -------  ----  ------  -----  -------  ------  ---------------------------
NONE           2  ---         0       2  0.0%   $-331    ---     baseline (no filter)
5b             2   +0         0       2  0.0%   $-331    $+0     no change
10b            2   +0         0       2  0.0%   $-331    $+0     no change
20b            2   +0         0       2  0.0%   $-331    $+0     no change
40b            3   +1         0       3  0.0%   $-437    $-106   WORSE: allowed extra trade
80b            2   +0         0       2  0.0%   $-344    $-14    no meaningful change
```

### Critical finding: the slope filter does not protect during the actual correction

Every lookback from 5 to 20 bars allows the same 2 trades in the correction period
at identical P&L. The slope filter blocks zero correction-period entries.

**Why:** At the start of the April 2026 gold correction, the EMA200 (200-bar 1H moving
average) was still rising — it lags price by weeks. Price dropped below EMA20/EMA50
quickly, but EMA200 kept climbing. The 2 trades that fired in the correction period
passed the slope check because EMA200 slope was still positive at those bars.

The slope filter targets "price above a falling EMA200". But in the early-to-mid phase
of a correction, EMA200 doesn't fall yet. The original Trade #4 scenario (EMA200 slope
already negative) occurs only in the late stages of a correction, not at the start.

**lb=40 is actively harmful:** It allows an extra losing trade in the correction
(-$106 worse). Path dependency: blocking a trade in the bull market period leaves
the system "free" to take the next signal, which can fall in the correction.

---

## Decision Criteria

```
Criteria defined before the test:
  1. Sharpe >= 2.5 on 2yr (minimal bull-market damage)
  2. Trade count drop <= 5% in bull periods
  3. Blocks most regime-transition losses (Corr P&L >= no-filter)

Lookback  Sharpe>=2.5    Trade-drop<=5%   Corr>=NONE         Overall
--------  -------------  ---------------  -----------------  -----------
5b        FAIL           PASS             PASS               PARTIAL 2/3
10b       FAIL           PASS             PASS               PARTIAL 2/3
20b       FAIL           FAIL (-13.3%)    PASS               PARTIAL 1/3
40b       FAIL           FAIL (-17.3%)    FAIL (-$106)       FAIL
80b       FAIL           FAIL (-21.4%)    FAIL (-$14)        FAIL
```

**No lookback passes all 3 criteria.**

Note on the Sharpe criterion: the threshold >=2.5 was calibrated to the previous
data snapshot (validation report Sharpe 2.77). On today's data, even the no-filter
baseline is only 2.0. The Sharpe criterion cannot be met by any configuration —
not because the slope filter is bad, but because the baseline itself has declined
due to data refresh. The **relative** comparison is valid: lb=20 improves Sharpe
by +0.23, but the absolute threshold is not achievable.

---

## Verdict

**Per the pre-defined rule:** "If NO lookback satisfies all → recommend removing the
slope filter entirely (data says it hurts more than helps)."

### Primary recommendation: Remove the slope filter. Revert to lb=0.

Reasons:

1. **The current lb=10 is net-negative.** Hurts Sharpe (-0.06) and P&L (-$365) vs
   no-filter on fresh 2yr data. The validation report's lb=10 result was an artifact
   of comparing against a different data snapshot (stale baseline).

2. **The filter provides no correction-period protection.** Zero trades blocked in the
   actual April 2026 correction regardless of lookback. The slope filter fires too late
   — EMA200 is still rising when price first corrects.

3. **lb=5 has zero effect.** 719 slope-negative bars, 0 blocked trades. The current
   production window (2.5h at 15m ≈ lb=5 proxy) is effectively already disabled.

4. **Both criteria that were passed (lb=5, lb=10) are Sharpe FAILS.** The filter only
   passes the "no worse" bar — not the "actively helps" bar.

### Alternative: Change lb=10 to lb=20 (deploy as lb=80 in production)

If you want to keep some slope protection for future regime transitions, lb=20 in the
1H backtest is the only configuration that genuinely improves quality:
- Sharpe: 2.0 → 2.23 (+0.23)
- P&L: $4,884 → $5,418 (+$533)
- WR: 49.0% → 51.8% (+2.8pp)
- Removes 13 trades (bad ones)

**BUT:** This improvement is from a 20h slope window (1H backtest). To get the same
20h window in 15m production, deploy `EMA_SLOPE_LOOKBACK=80`. The current deployment
of lb=10 in production (2.5h window) is much shorter and is net-negative.

**Caveat:** The 1H backtest does not validate lb=20 production behavior. lb=5 in the
1H backtest (= lb=20 in production time) shows zero effect. The benefit of "lb=20 in
the backtest" is specifically from a 20h slope window, which requires lb=80 in production.

### If removing the filter feels like going backwards

The slope filter addressed a real conceptual issue (Trade #4: price above a falling
EMA200). That issue is valid. But:
- The fix fires too late in real corrections (EMA200 lags)  
- At lb=10 (production 2.5h), the window is too short — only noisily negative EMA200
  readings occur in 2.5h, and they don't correlate with actual entry signals
- The right solution may not be a slope check — it may be a faster-responding trend
  indicator (e.g., EMA50 slope, or requiring price > EMA20 and EMA20 slope rising)

---

## Summary Table

| Setting | 2yr Sharpe | 2yr P&L | Corr P&L | Verdict |
|---|---|---|---|---|
| lb=0 (NONE) | 2.00 | $+4,884 | $-331 | Baseline |
| **lb=10 (current)** | **1.94** | **$+4,519** | **$-331** | **Net-negative. Remove.** |
| lb=20 (best 1H proxy) | 2.23 | $+5,418 | $-331 | Best, but ≠ lb=20 in 15m production |
| lb=80 (= lb=20 time-aligned) | 2.15 | $+4,928 | $-344 | Modest gain, 21% fewer trades |

---

## Proposed Action

**Remove the EMA200 slope filter.**

In `gold_trading_agents.py`:
- Remove the `EMA_SLOPE_LOOKBACK` config constant
- Remove the slope computation block (lines ~563–571)
- Remove the `EMA200_SLOPE` gate (lines ~714–728)
- Remove `ema200_slope` from indicators dict and hourly dump

In `backtest_v2.py`:
- Remove `EMA_SLOPE_LOOKBACK` constant
- Remove `df["ema200_slope"]` column from `add_indicators()`
- Remove the `if direction == "BUY": slope ... if slope <= 0: return None` gate
- Remove `ema200_slope` from skip counter

CLAUDE.md: Add v9 entry documenting the revert and this report.

**Note on Trade #4:** The EMA200 slope blind spot is still a real issue but the
slope filter is the wrong fix (fires too late, too noisy at short lookbacks). A
future iteration could address it via EMA50 slope or a faster trend confirmation
mechanism after collecting more forward-test data.
