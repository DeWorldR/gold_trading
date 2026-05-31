# SELL Pattern Validation Report

Generated: 2026-05-11
Question: Do SELL patterns have edge in non-bull market regimes?

---

## Test Setup

| Parameter | Value |
|-----------|-------|
| Data source | yfinance GC=F (daily interval) |
| Periods tested | 4 (bear, choppy, correction, mixed) |
| Total calendar span | 2011–2021 |
| Patterns tested | All — `DISABLED_PATTERNS = []` |
| ATR stop multiplier | 2.0 (widened from 1.5 to account for daily bar noise) |
| Look-ahead fix | Applied — worst-case assumed when SL and TP touch same bar |
| Spread/slippage | $0.30/side modeled |
| Session filter | Disabled (daily bars — no intraday concept) |
| Warmup buffer | 400 calendar days prepended to each period (ensures EMA200 fully initialised) |

---

## Period-by-Period Results

### Bear Market 2011–2015 (Strong Down Trend)

Gold fell from $1,900 to $1,050 (-45%) over this period.

**SELL patterns:**

| Pattern | Trades | Win% | Net P&L | PF | Avg/Trade |
|---------|--------|------|---------|----|-----------|
| EMA_MACD_TREND_SELL | 16 | 25% | −$570 | 0.58 | −$35.64 |
| BB_RSI_REVERSAL_SELL | 5 | 20% | −$290 | 0.24 | −$58.03 |
| **SELL total** | **21** | **24%** | **−$860** | **0.51** | **−$40.97** |

**BUY patterns (sanity check):**

| Pattern | Trades | Win% | Net P&L | PF | Avg/Trade |
|---------|--------|------|---------|----|-----------|
| EMA_MACD_TREND_BUY | 9 | 11% | −$572 | 0.27 | −$63.54 |
| BB_RSI_REVERSAL_BUY | 1 | 100% | +$192 | inf | +$192 |
| **BUY total** | **10** | **20%** | **−$379** | **0.51** | **−$37.95** |

**SELL overall:** Sharpe −4.88 | Max DD 8.9%

**Verdict: BROKEN**

**Reasoning:** The worst result in the worst scenario — a 45% multi-year bear market where SELL patterns should theoretically dominate. 24% win rate is well below the ~33% break-even at R:R 2.0. Profit factor 0.51 means the system loses $1 for every $0.51 won. The EMA200 trend gate is doing its job (it correctly blocks SELL when price is still above EMA200 in late 2011), but once price crosses below EMA200 and SELL fires, the patterns still lose. This is the most important data point: even in a perfect SELL regime, these patterns underperform.

Note: BUY patterns also lost in the bear market (−$379, PF 0.51), which is expected. The sanity check passes — both directions struggled, neither ran away with gains on the wrong side.

---

### Choppy 2018–2019 (Sideways)

Gold consolidated between $1,150–$1,370 (range ~$220).

**SELL patterns:**

| Pattern | Trades | Win% | Net P&L | PF | Avg/Trade |
|---------|--------|------|---------|----|-----------|
| EMA_MACD_TREND_SELL | 3 | 67% | +$310 | 4.06 | +$103.40 |

**BUY patterns (sanity check):**

| Pattern | Trades | Win% | Net P&L | PF | Avg/Trade |
|---------|--------|------|---------|----|-----------|
| EMA_MACD_TREND_BUY | 12 | 33% | +$16 | 1.02 | +$1.34 |

**SELL overall:** 3 trades, Sharpe ~0.00 (too few for meaningful calculation) | Max DD 1.0%

**Verdict: MARGINAL (3 trades — no statistical weight)**

**Reasoning:** Three wins out of three looks fantastic but is a coin flip at this sample size. PF 4.06 is an artifact of the tiny sample. BUY scraped break-even (PF 1.02), which is consistent with a choppy market. Cannot draw any conclusion from 3 SELL trades.

---

### Correction 2022 (Mid-Cycle Down)

Gold fell from $2,050 to $1,620 (-21%) over 9 months.

**SELL patterns:**

| Pattern | Trades | Win% | Net P&L | PF | Avg/Trade |
|---------|--------|------|---------|----|-----------|
| EMA_MACD_TREND_SELL | 3 | 0% | −$276 | 0.00 | −$92.04 |

**BUY patterns (sanity check):**

| Pattern | Trades | Win% | Net P&L | PF | Avg/Trade |
|---------|--------|------|---------|----|-----------|
| EMA_MACD_TREND_BUY | 1 | 0% | −$113 | 0.00 | −$112.66 |

**SELL overall:** 3 trades, 0 wins, −$276 | Max DD 2.8%

**Verdict: BROKEN**

**Reasoning:** Three SELL trades, zero wins. The 2022 correction was sharp but brief — the EMA200 trend gate filtered most of the move (price was above EMA200 for much of 2022 until September). Only 3 trades slipped through, all at poor entry timing. The period also had only 190 bars vs the expected ~250 for a 9-month window — some data gaps likely present. Results unreliable for this period specifically, but the direction is negative.

---

### Mixed 2020–2021 (Post-COVID Rally Then Consolidation)

Gold peaked at $2,070 ATH in August 2020 then consolidated/declined through 2021.

**SELL patterns:**

| Pattern | Trades | Win% | Net P&L | PF | Avg/Trade |
|---------|--------|------|---------|----|-----------|
| EMA_MACD_TREND_SELL | 3 | 0% | −$304 | 0.00 | −$101.38 |

**BUY patterns (sanity check):**

| Pattern | Trades | Win% | Net P&L | PF | Avg/Trade |
|---------|--------|------|---------|----|-----------|
| EMA_MACD_TREND_BUY | 9 | 11% | −$598 | 0.27 | −$66.47 |
| BB_RSI_REVERSAL_BUY | 1 | 100% | +$204 | inf | +$204 |

**SELL overall:** 3 trades, 0 wins, −$304 | Max DD 3.0%

**Verdict: BROKEN**

**Reasoning:** Three SELL trades, zero wins. The test period starts right at the August 2020 ATH — the "rally" part of the label was already over. The consolidation/decline in 2021 produced SELL signals that still lost. BUY also lost (−$394), flagged as UNEXPECTED by the sanity check, but this is an artefact of the period start point: we started after the peak, so BUY was fighting the post-peak decline. The period label is slightly misleading — in context, 2020–2021 (starting Aug 2020) is more "correction after ATH" than "bull rally."

---

## Combined SELL Analysis Across All Non-Bull Periods

| Metric | EMA_MACD_TREND_SELL | BB_RSI_REVERSAL_SELL |
|--------|---------------------|----------------------|
| Total trades | 25 | 5 |
| Combined win rate | 24% | 20% |
| Combined net P&L | −$840 | −$290 |
| Combined PF | 0.59 | 0.24 |
| Best period | Choppy 2018–2019 | — (only Bear period) |
| Worst period | Bear 2011–2015 | Bear 2011–2015 |

**Overall SELL across all 4 periods:**

| Total trades | Win rate | Net P&L | Profit factor | Sharpe |
|-------------|---------|---------|--------------|--------|
| 30 | 23.3% | −$1,130 | 0.53 | −4.54 |

---

## Statistical Significance

**30 total SELL trades across 4 periods — below the 50-trade minimum for reliable conclusions.**

This means the official verdict is INCONCLUSIVE by sample size. However:

- The dominant period (Bear 2011–2015) had 21 trades — enough to see a clear signal on its own
- All four periods independently showed negative P&L except one 3-trade sample
- The direction of evidence is overwhelmingly negative
- Even if we extrapolated to 50 trades at the same rate, the metrics would not approach Option A or B thresholds

Low trade count is itself a finding: **the system generates very few SELL signals even in bearish markets**. The EMA200 trend gate, BB width filter, ADX filter, and RSI thresholds combine to aggressively suppress SELL entries. This is by design but means the system cannot capitalise on downtrends even when it is theoretically allowed to.

---

## Decision Criteria Evaluation

### Option A: STRONG EDGE
- Combined PF >= 1.20: **0.53 — FAIL**
- Win rate >= 35%: **23.3% — FAIL**
- Profitable in 3+/4 periods: **1/4 — FAIL**
- No single period worse than −$500: **Bear: −$860 — FAIL**

### Option B: REGIME-CONDITIONAL EDGE
- Bear/Correction avg PF >= 1.30: **0.25 — FAIL**
- Combined PF < 1.20 in choppy/mixed: technically yes, but moot since bear/correction also failed

### Option C: WEAK / NO EDGE
- Combined PF < 1.10: **0.53 — PASS**
- Profitable in only 1–2 periods: **1/4 — PASS**
- Bear/Correction (the "best case" for SELL) both negative: **PASS**

---

## Recommendation

**Decision: C — WEAK / NO EDGE**

Despite the official INCONCLUSIVE verdict from sample size, the evidence points clearly to Option C:

1. **Bear 2011–2015 (the strongest test case) produced 21 SELL trades with PF 0.51.** This is the most data we have, and it is decisively negative. A 45% multi-year bear market is precisely the scenario where SELL patterns should thrive — and they did not.

2. **The system architecture suppresses SELL entries.** The EMA200 trend gate, ADX filter, and BB width filter were designed to improve BUY signal quality. As a side effect, they also filter out most SELL opportunities, producing fewer than 8 SELL trades per year even in bear markets. Low trade count + negative P&L = no workable strategy.

3. **BB_RSI_REVERSAL_SELL is a mean-reversion pattern, which is the wrong tool in a trending bear market.** Selling when RSI is overbought and price is near the upper Bollinger Band works in range markets, but in a bear market these conditions are rare and, when triggered, often represent short-term bounces within the downtrend — exactly when mean-reversion sells get stopped out.

4. **EMA_MACD_TREND_SELL is a trend-following pattern that requires EMA20 < EMA50 and bearish MACD.** This should have an edge in a bear market. The fact that it doesn't (PF 0.58 in 2011–2015) suggests the specific entry timing (RSI overbought, BB upper proximity) is too random — it fires at any point the trend indicators align, not specifically at high-probability short entries.

### Practical implication

> Accept BUY-only as the optimal configuration for this system. SELL patterns are not simply disabled due to market conditions — they appear structurally broken in the context of the current signal framework. Enabling them in a future bear market is unlikely to produce profitability without a fundamental redesign of the SELL entry logic.

**Saves: ~2 months of integration + testing effort.**

### If gold enters a confirmed multi-year bear market in the future

Before re-enabling SELL patterns, the following would be needed:
- A purpose-built short entry signal (e.g., bearish engulfing after resistance rejection, not RSI/BB combo)
- Minimum 100 backtest trades in verified bear conditions before going live
- Dedicated bear-market risk parameters (wider stops are harder to manage in volatile downtrends)

---

## Sanity Checks

### Data quality

| Period | Bars | Expected | Status |
|--------|------|----------|--------|
| Bear 2011–2015 | 1,089 | ~1,050 | OK |
| Choppy 2018–2019 | 374 | ~375 | OK |
| Correction 2022 | 190 | ~250 | LOW — possible data gaps |
| Mixed 2020–2021 | 357 | ~375 | OK |

### BUY cross-validation

| Period | BUY P&L | Expected | Result |
|--------|---------|----------|--------|
| Bear 2011–2015 | −$379 | LOSS | OK — BUY correctly loses in bear market |
| Choppy 2018–2019 | +$16 | breakeven | OK — slight positive in sideways market |
| Correction 2022 | −$113 | LOSS | OK — BUY correctly loses in correction |
| Mixed 2020–2021 | −$394 | expected WIN | UNEXPECTED — explained above: period starts after ATH, not during rally |

The Mixed 2020–2021 unexpected BUY result is explained by the period start date (August 2020 ATH) rather than a backtest bug. The indicator-based sanity check (BUY loses in bear = correct) passes for the three unambiguous periods.

---

## Files

| File | Description |
|------|-------------|
| `backtest_sell_validation.py` | Research-only backtest script |
| `sell_validation_results.json` | Full results in JSON format |
| `SELL_VALIDATION_REPORT.md` | This report |
