# SELL Development Report

**Generated:** 2026-05-18  
**Status:** Phase 3 complete — awaiting user confirmation before any production changes  
**Script:** `sell_research.py` (research only, production unchanged)

---

## Section 1: Results Table

| Approach | Period | Trades | WR | PF | Sharpe | MaxDD | Net P&L | vs Random WR |
|---|---|---|---|---|---|---|---|---|
| A Mirror | Bear 2011-2015 | 13 | 23% | 0.60 | -0.40 | 5.2% | -$320 | **-27pp** |
| A Mirror | Correction 2022 | 3 | 0% | 0.00 | -2.01 | 2.9% | -$293 | **-56pp** |
| A Mirror | Current 2026 | **0** | — | — | — | 0% | $0 | — |
| B Structure | Bear 2011-2015 | 6 | 50% | 1.61 | 0.28 | 2.1% | **+$215** | -0pp |
| B Structure | Correction 2022 | **1** | 0% | 0.00 | 0.00 | 0.2% | -$19 | -52pp |
| B Structure | Current 2026 | **0** | — | — | — | 0% | $0 | — |

### Random Sell Baseline (200 Monte Carlo runs per period)

| Period | Random WR | Std | Interpretation |
|---|---|---|---|
| Bear 2011-2015 | 49.8% | ±14.3% | Random short wins ~50% of the time in confirmed bear |
| Correction 2022 | 55.7% | ±29.3% | Even higher — sharp, brief corrections favour random entry |
| Current 2026 | 0.0% | 0.0% | No eligible bars for the random baseline either |

---

## Section 2: Deploy Gate Evaluation

**Gate criteria (all must pass, averaged across 3 periods):**

### Approach A — Mirror of BUY Logic

| Check | Result | Status |
|---|---|---|
| Avg PF >= 1.3 | 0.20 | **FAIL** |
| WR beats random by >= 5pp | -27.5pp | **FAIL** |
| PF > 1.0 in 2+ of 3 periods | 0/3 | **FAIL** |
| Avg Sharpe >= 1.0 | -0.80 | **FAIL** |
| No period worse than -$500 | Worst = -$320 | PASS |

**Approach A: 4/5 criteria fail.**

### Approach B — Bearish Structure

| Check | Result | Status |
|---|---|---|
| Avg PF >= 1.3 | 0.54 | **FAIL** |
| WR beats random by >= 5pp | -17.4pp | **FAIL** |
| PF > 1.0 in 2+ of 3 periods | 1/3 | **FAIL** |
| Avg Sharpe >= 1.0 | 0.09 | **FAIL** |
| No period worse than -$500 | Worst = -$19 | PASS |

**Approach B: 4/5 criteria fail.**

---

## Section 3: Verdict

> **STAY BUY-ONLY — neither approach meets the deploy gate.**

Both approaches fail decisively. The evidence below explains why.

---

## Section 4: Forensic Analysis — What Went Wrong and Why

### Finding 1: Both approaches fire zero signals in the current 2026 downtrend

Gold fell -10% from $5,041 (May 2) to $4,533 (May 15). This is the live out-of-sample test, and both approaches produced zero SELL entries in 23 trading days. This is not a backtest artifact — it reflects a real structural constraint.

**Root cause:** Both approaches require `close < EMA200` as the macro gate. As of April–May 2026, gold's daily EMA200 is approximately $3,500–4,000 (reflecting the 10-month average during a massive bull run from $2,400 in mid-2024 to $5,041 in May 2026). Current price at ~$4,533 is still 15–30% above the daily EMA200. The macro gate correctly withholds SELL permission.

The EMA200 slope filter (new in this research, designed to fix the Trade #4 BUY problem) compounds this: even if price were to dip below EMA200, the EMA200 itself would still be rising strongly from the prior bull run, failing the `slope < 0` requirement for several more months.

**Implication:** In the current market, neither approach can fire. Any gold correction short of a full-scale bear market lasting 6–12+ months is invisible to these approaches. This is actually the correct behaviour — it prevents premature shorting into what may be a temporary correction within a larger uptrend. But it also confirms these approaches are useless for the near term.

### Finding 2: In the best-case scenario (Bear 2011-2015), both approaches underperform random

The Bear 2011-2015 period is the ideal SELL environment: gold fell 45% over 4 years. A random SELL during this period achieves ~50% WR, which at R:R=2.0 translates to positive expected value.

| System | Trades | WR | PF | vs Random |
|---|---|---|---|---|
| Random (MC avg) | n/a | **49.8%** | ~2.0 | baseline |
| Approach A | 13 | 23% | 0.60 | **-27pp** |
| Approach B | 6 | 50% | 1.61 | -0pp (at par) |

Approach A achieves only 23% WR — less than half the random baseline. Approach B matches random at 50% WR but with only 6 trades over 4+ years (1.5 trades/year), offering no statistical advantage.

**Why does Approach A lose even in a bear market?**

The Bear 2011-2015 result reveals the same structural problem identified in the SELL_VALIDATION_REPORT.md for the old patterns. The indicator mirror logic fires signals that cluster around the wrong points in the correction cycle:

1. **RSI > 65 + price near upper BB**: These fire after short bounces within the bear market, when price has temporarily recovered and indicators are overbought on the bounce. These are exactly the wrong moments to initiate a new short — they're entering at the top of a dead-cat bounce, into a direction that may continue higher before resuming the decline.

2. **EMA20 < EMA50 + MACD bearish**: In a slow, grinding bear market, these signals fire throughout. But R:R=2.0 requires the TP to be 2× the stop distance away. In a choppy bear market with many brief rallies, the wider TP gets hit less reliably than the tighter stop.

3. **EMA200 slope < 0**: This filter correctly prevents premature entries in the early bear market phase. But once the slope flips negative (typically 6–12 months into the bear), the approach fires during the later "exhaustion" phase of the decline when mean-reversion is more likely.

**Why does Approach B only match random?**

Approach B (6 trades, 50% WR, +$215) gets its one bear-market win by detecting genuine structural breakdowns. The lower-high + support-break-retest pattern fires at higher-quality setups. But:
- Only 6 trades in 4+ years = statistically meaningless (50% WR on 6 trades has 95% CI of 12%–88%)
- The random baseline is 50% in this period, so even if the 50% WR is real, there's no edge beyond random
- The Correction 2022 result (1 trade, 0% WR) shows no robustness outside the specific 2011-2015 bear

### Finding 3: Low trade count is a system property, not a data artifact

In a 4-year, 45%-decline bear market (Bear 2011-2015), Approach A generated only 13 SELL trades. That's 3.25 per year. Approach B generated only 6 trades — 1.5 per year. Compare to the BUY side: the deployed v6 system generates ~50 BUY trades per year on 1H data (103 trades over 2 years).

Low trade count means:
- Statistical unreliability (50% WR on 6 trades is meaningless)
- Long periods with zero activity while a trade opportunity exists
- Daily loss limits and consecutive-loss guards are triggered by very few bad trades

This is the same finding as the SELL_VALIDATION_REPORT.md (Option C verdict): "the system generates very few SELL signals even in bearish markets." The EMA200 gate, ADX filter, and slope requirement suppress entries aggressively — too aggressively for a viable SELL system.

### Finding 4: The EMA200 slope filter — right idea, wrong application

The EMA200 slope requirement was introduced to fix the Trade #4 class of errors (buying a short-term bounce while the broader trend has already turned). For the SELL side, it was mirrored: only short when EMA200 is actively falling.

This filter is correct in principle, but it creates an asymmetric timing problem:
- For BUY: the bull trend typically lasts years, so EMA200 is rising for extended periods. The filter permits many entries.
- For SELL: a bear trend takes 6–12 months to turn EMA200 negative. By that point, the most profitable part of the decline has already occurred (the initial breakdown from the highs).

The filter correctly blocks premature shorts (good), but also blocks the best shorts (bad). There is no parameter value that resolves this tension — it's inherent to using a slow lagging indicator as a trend gate.

---

## Section 5: What Would Need to Be True for a SELL System to Work

For context, the SELL_VALIDATION_REPORT.md (May 2026) concluded: "SELL patterns are not simply disabled due to market conditions — they appear structurally broken in the context of the current signal framework. Enabling them in a future bear market is unlikely to produce profitability without a fundamental redesign of the SELL entry logic."

This research validates that conclusion with two newly-designed approaches. The conclusion still holds.

For a SELL approach to pass the deploy gate, it would need:
1. **A trigger that fires early in a correction**, not after the bear market is established. Options: price relative to recent N-day high, RSI divergence at new high, or multi-timeframe confirmation (daily close below weekly EMA20).
2. **A lighter macro filter** than EMA200 daily. A 50-day or 100-day moving average would respond faster to trend changes. However, this risks false positives in corrective phases of bull markets.
3. **Many more signals** — at least 50+ trades in a single bear period to establish statistical significance. With 6–13 trades, no conclusion is possible.
4. **A fundamentally different stop/TP geometry** for bear markets. Bear market trends tend to be faster and sharper than bull market trends. ATR×2.5 / RR=2.0, calibrated for 1H BUY setups, may be poorly suited to daily SELL setups in a grinding correction.
5. **A dedicated bear-market regime detector** that identifies "correction within bull" vs "start of bear" vs "ongoing bear." These have different characteristics and require different parameters.

None of these changes are trivial. They would each require independent backtesting and validation before combination.

---

## Section 6: Is the Current Market a Valid Test?

The user noted: "gold has fallen from $5,041 (May 2) to ~$4,533 (May 15), -10% in two weeks, daily/weekly technicals on 'Strong Sell'". This was described as an ideal out-of-sample test.

**Result: Both approaches generated zero signals in this period.** The conclusion has two valid interpretations:

1. **Protective filter working correctly:** The EMA200 gate is preventing us from shorting into what may be a temporary correction in a large bull market (gold is still 15–30% above its 200-day moving average). Without confirmed structural deterioration, the approaches correctly abstain. This would have prevented shorting into the $4,533 low, which may bounce.

2. **Approaches too conservative:** A 10% decline in 2 weeks with daily/weekly "Strong Sell" signals is exactly when a SELL system should fire. The EMA200 gate's 6–12 month lag makes the approaches unresponsive to medium-term corrections. By the time the gate opens, the move will be over.

Both interpretations are valid. The truth is that the approaches are calibrated for confirmed multi-year bear markets, which makes them too slow for any shorter correction.

---

## Section 7: Decision

### Verdict: STAY BUY-ONLY

Neither approach meets any of the quantitative criteria. The dominant result is structural: the EMA200-based SELL gate works as designed but prevents the approaches from generating enough valid signals in any time horizon short of a multi-year bear market.

The BUY-only system (v6 deployed: 103 trades, WR 54%, Sharpe 2.77, MaxDD 5.7%) is the optimal configuration. Adding a SELL layer with the approaches tested here would provide no P&L benefit and would introduce noise.

**Do not proceed to Phase 4 (integration).**

### Conditions for revisiting SELL capability

Revisit only if ALL of the following are true:
1. Gold establishes a confirmed bear market (daily close below EMA200 sustained for 20+ consecutive bars)
2. A new SELL approach concept is developed that doesn't rely on the same EMA200 gate as the BUY system
3. The new approach generates >= 50 backtest trades in the 2011-2015 bear period before any live testing

These conditions cannot be met in the current correction. The earliest a SELL system could be ready under these criteria is several months into a sustained downturn.

---

## Appendix: Why the Old SELL Patterns Failed and Why New Ones Also Fail

The SELL_VALIDATION_REPORT.md (2026-05-11) showed the old patterns had PF=0.51 in the 2011-2015 bear market. This research shows:

| System | Bear 2011-2015 PF | Bear 2011-2015 WR |
|---|---|---|
| Old EMA_MACD_TREND_SELL | 0.58 | 25% |
| Old BB_RSI_REVERSAL_SELL | 0.24 | 20% |
| New Approach A (Mirror + slope) | 0.60 | 23% |
| New Approach B (Structure) | 1.61 | 50% |
| Random SELL baseline | ~2.0 | ~50% |

The old and new indicator-based approaches perform similarly (PF 0.24–0.60). Approach B slightly improves on them but still only matches random. The core finding is consistent across both research cycles: **indicator-cross SELL signals in the production framework do not generate edge above random in any tested bear market.**

The production framework was designed and optimised for BUY entries in a gold bull market. Its filters, thresholds, and signal logic reflect that optimisation. Adapting them to SELL by mirroring produces inferior results because the entry conditions are fundamentally different: the ideal SELL setup occurs at momentum exhaustion and structural breakdown, not at indicator crossovers that lag price by several bars/days.

---

## Files Generated

| File | Description |
|---|---|
| `sell_research.py` | Research script — both approaches + simulation + MC baseline |
| `sell_research_results.json` | Full machine-readable results |
| `SELL_DEVELOPMENT_REPORT.md` | This report |
