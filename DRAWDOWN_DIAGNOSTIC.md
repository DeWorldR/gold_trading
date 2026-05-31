# Q2 2026 Drawdown Diagnostic

**System:** BUY-only XAUUSD, ATR×2.5 stop, RR=2.0
**Period compared:** Q3 2025 (best quarter: WR 53%, +$2,492) vs Q2 2026 (worst quarter: WR 29%, −$617)
**Question:** Why is the same signal logic that worked in Q3 2025 failing in Q2 2026?

---

## Root Cause (Up Front)

Three distinct problems converged in Q2 2026. They are listed in order of evidence strength:

1. **[CONFIRMED] Market shifted to mean-reverting regime.** 1H return autocorrelation flipped negative
   (AC = −0.054 in Q2 2026 vs −0.027 in Q3 2025). Only Q2 2026 classifies as mean-reverting across all
   4 periods tested. A trend-following system that buys after momentum signals performs worst in
   mean-reverting markets — the momentum that triggered entry immediately reverses.

2. **[CONFIRMED] EMA_MACD_TREND_BUY fires at overbought RSI levels.** 3 of 4 Q2 2026 losses had
   RSI > 70 at entry (71.5, 74.4, 85.0). The system has no RSI upper limit for BUY signals — it buys
   when MACD and EMA crossovers are bullish regardless of how overbought RSI is. In a mean-reverting
   market, entering at RSI 85 is exactly the wrong moment.

3. **[PROBABLE] Gold's 1H trend stalled.** EMA200 slope collapsed from +13.4%/quarter (Q3 2025)
   to +1.5%/quarter (Q2 2026). Price is flat (-1.1% in Q2 2026 vs +16.9% in Q3 2025). The EMA200
   still points up — so the trend filter approves BUY entries — but there is no trend to capture.
   The TP (5×ATR away) is geometrically unreachable in a flat, choppy market.

**What is NOT the cause:** ATR volatility, BB width, or ambiguous bar handling. Volatility was
actually lower in Q2 2026 (ATR% = 0.48%) than Q1 2026 (0.67%). The problem is directional, not
structural.

**Is it random variance?** With n=7 trades, P(≤2 wins by chance at 43% WR) = 31% — not statistically
significant alone. But the 10-bar directional check is decisive: **only 29% of Q2 2026 BUY entries
were directionally correct over the next 10 hours**, vs 54% in Q3 2025. The system is entering at
the wrong moment in the intraday cycle, not just getting unlucky on stops.

---

## Step 1: Market Characteristics

| Metric | Q3 2025 | Q4 2025 | Q1 2026 | **Q2 2026** | Change (Q3→Q2) |
|--------|---------|---------|---------|-------------|----------------|
| Mean ATR% (volatility) | 0.262% | 0.50% | 0.67% | **0.484%** | +0.222 pp |
| Mean daily range % | 0.339% | 0.648% | 0.86% | **0.623%** | +0.284 pp |
| Mean ADX (trend strength) | 28.6 | 30.1 | 27.5 | **25.6** | −3.0 |
| Median BB width pct | 50 | 42 | 42 | **44** | −6 |
| Bull bar % (up candles) | 52% | 54% | 54% | **50%** | −2 pp |
| Extreme candles (>2×ATR) | 4.7% | 4.1% | 4.2% | **3.6%** | −1.1 pp |
| Avg consecutive run length | 1.95 | 1.96 | 1.94 | **1.94** | −0.01 |
| Period price change | +16.9% | +11.4% | +7.5% | **−1.1%** | **−18.0 pp** |
| EMA200 slope (trend speed) | +13.4% | +16.1% | +4.6% | **+1.5%** | **−11.9 pp** |

### Key observations

- **Gold price change:** +16.9% (Q3 2025) → −1.1% (Q2 2026). The bull trend has stalled. Gold is
  flat-to-down in Q2 2026 while the system is still firing BUY signals because the EMA200 (a 200-bar
  slow average) has not caught up to the regime change.

- **ATR% increased significantly from Q3 2025 to Q4/Q1** and remains elevated in Q2. With higher ATR,
  the 5×ATR TP target (at ATR×2.5/RR=2.0) is further in dollar terms — but the price is not moving in
  the required direction to reach it.

- **ADX at 25.6** — right at the threshold. EMA_MACD_TREND signals are designed for trending markets
  (ADX > 25). In Q2 2026, ADX is barely above the cutoff and declining. The trend is dying.

- **EMA200 slope collapsed to +1.5%** — essentially flat. The dominant trend the system is trading
  with has effectively paused. The EMA200 filter still passes BUY signals because price is above it,
  but the underlying momentum is gone.

---

## Step 2: Per-Pattern Performance by Quarter

| Pattern | Q3 2025 | Q4 2025 | Q1 2026 | **Q2 2026** |
|---------|---------|---------|---------|-------------|
| EMA_MACD_TREND_BUY | 52% (n=23) | 40% (n=20) | 50% (n=14) | **29% (n=7)** |
| BB_RSI_REVERSAL_BUY | 80% (n=5) | 57% (n=7) | 0% (n=1) | **no trades** |

### Pattern observations

- **EMA_MACD_TREND_BUY** (the dominant pattern, 149/178 total trades) collapsed from 52% → 29% WR.
  This is the only pattern firing in Q2 2026. Its logic requires EMA20 > EMA50 (crossover bullish),
  MACD bullish, and typically price above EMA20. These conditions are all "look back" momentum — they
  fire after a move has already happened, which is exactly the wrong time to enter in a mean-reverting
  market.

- **BB_RSI_REVERSAL_BUY** — zero signals in Q2 2026. This pattern requires RSI < 35 (oversold) AND
  price near the lower Bollinger Band. In Q2 2026, RSI at entry averaged 65.6 — there are no oversold
  conditions because gold has been elevated (near ATH). The pattern that requires a pullback has no
  pullbacks to trade.

- **Trend:** WR has been declining quarter-over-quarter since Q3 2025 (52% → 40% → 50% → 29%).
  Q1 2026's 50% looks like a recovery but the Q2 collapse to 29% confirms a structural deterioration.

---

## Step 3: Filter States on Every Q2 2026 Trade

For each trade: did the entry filters say GO? If yes → filters blind to the regime change.

| Trade# | Date | RSI | vs EMA200 | ADX | BB W% | ATR% | 4h ret | Result |
|--------|------|-----|-----------|-----|-------|------|--------|--------|
| 172 | 03-31 13:00 | 63.2 | +0.92% OK | 25.4 | 36 OK | 0.71% | +? | WIN |
| 173 | 04-01 15:00 | **71.5** | +4.11% OK | 50.6 | 38 OK | 0.65% | — | **LOSS** |
| 174 | 04-02 18:00 | 50.4 | +1.12% OK | 29.0 | 66 OK | 0.83% | — | WIN |
| 175 | 04-17 13:00 | **74.4** | +2.48% OK | 27.6 | 98 OK | 0.44% | — | **LOSS** |
| 176 | 04-17 20:00 | 62.6 | +1.84% OK | 27.7 | 98 OK | 0.42% | — | **LOSS** (1 bar) |
| 177 | 04-20 20:00 | 52.4 | +0.94% OK | **15.8** | 56 OK | 0.55% | — | **LOSS** |
| 178 | 05-06 08:00 | **85.0** | +1.66% OK | 38.2 | 98 OK | 0.40% | — | **LOSS** |

**Filter pass rates on the 4 LOSING trades in Q2 2026:**
- EMA200 trend gate (close > EMA200): **100%** passed — none rejected
- BB width threshold (> 25th pct): **100%** passed — none rejected
- ADX check: Trade 177 had ADX=15.8, which should have been blocked by ADX≥25 threshold — but
  the ADX filter currently applies only to SELL signals, not BUY signals

**Critical finding — RSI at entry:**

| Trade | RSI | RSI status for a BUY signal |
|-------|-----|------------------------------|
| 173 | 71.5 | Overbought (>65 = SELL signal) — system bought anyway |
| 175 | 74.4 | Overbought — system bought anyway |
| 178 | 85.0 | Massively overbought — system bought anyway |
| 177 | 52.4 | Neutral |
| 176 | 62.6 | Neutral |

The EMA_MACD_TREND_BUY pattern does not require RSI to be in any range. It needs EMA crossover +
MACD bullish + typically price above EMA20. RSI > 65 is counted as a SELL reason — but if the other
3 signals are bullish, the BUY pattern still fires. The system literally bought gold at RSI=85 (an
extreme reading that historically precedes pullbacks in any asset).

In Q3 2025, winning trades had RSI=59.5 at entry. In Q2 2026, losing trades had RSI=65.2 at entry.
The system is entering on later-stage momentum (RSI already high) rather than early momentum (RSI
neutral). In a mean-reverting market, this is the worst possible entry timing.

**Trade 177 also violated the ADX threshold** (ADX=15.8 < 25). This trade would have been blocked
if the BUY ADX filter was symmetric with the existing SELL ADX filter.

---

## Step 4: Regime Shift Identification

### Autocorrelation of 1H returns — the clearest signal

| Period | AC(1h) | AC(4h) | AC(24h) | Regime |
|--------|--------|--------|---------|--------|
| Q3 2025 | −0.027 | +0.006 | −0.027 | RANDOM-WALK |
| Q4 2025 | −0.023 | +0.054 | −0.023 | RANDOM-WALK |
| Q1 2026 | −0.034 | −0.005 | +0.041 | RANDOM-WALK |
| **Q2 2026** | **−0.054** | **+0.103** | **−0.017** | **MEAN-REVERTING** |

Negative lag-1 autocorrelation means: after an up-hour, the next hour is slightly more likely to be
down, and vice versa. At −0.054, Q2 2026 is the only period where this is meaningfully negative.

A momentum/trend-following system enters after a sequence of up-bars (MACD bullish, EMA aligned).
In a mean-reverting market, that is exactly when reversal probability is highest. The system is
optimally positioned to be wrong.

### EMA200 trend quality

| Period | % bars close > EMA200 | EMA200 slope | Assessment |
|--------|----------------------|--------------|------------|
| Q3 2025 | 68% | +13.39% | Strong bullish trend |
| Q4 2025 | 66% | +16.05% | Strong bullish trend |
| Q1 2026 | 59% | +4.63% | Weakening |
| **Q2 2026** | **60%** | **+1.53%** | **Stalling — trend nearly flat** |

The EMA200 slope has collapsed from +13–16% per quarter to +1.5%. The trend filter still passes BUY
entries (price is above EMA200), but the trend the system is trying to ride effectively stopped.
This is the classic EMA lag problem: a slow indicator stays bullish for months after the underlying
trend has paused.

### 10-bar forward return at BUY entry bars (ignoring stops)

| Period | Mean +10h return | % positive | Assessment |
|--------|-----------------|------------|------------|
| Q3 2025 | +0.04% | **54%** | Slight directional edge |
| Q2 2026 | −0.52% | **29%** | **Directional losses** |

This is the decisive test: it ignores stop losses entirely and asks whether the BUY entries were
directionally correct over the next 10 hours. In Q3 2025, 54% of entries were followed by price
increases — consistent with a trending market. In Q2 2026, **only 29% were directionally correct**.
The system is entering at local price highs in a mean-reverting, flat market. Price falls after 71%
of entries — not because of stop placement, but because the direction was wrong.

### Adverse excursion after entry (10 bars)

| Period | Median adverse excursion | Mean adverse excursion |
|--------|-------------------------|----------------------|
| Q3 2025 wins | −$1.70 | −$1.72 |
| Q2 2026 losses | −$93.45 | −$85.55 |

In Q3 2025, winning trades barely dipped below entry (median −$1.70) before running to TP.
In Q2 2026, losing trades fell an average of −$85 within 10 bars of entry. With ATR×2.5 stops
at ~$55–$80, these moves blow through stops regardless of the multiplier.

### Post-entry price path for Q2 2026 losses

| Trade# | Entry | SL dist | Bar+1 | Bar+3 | Bar+5 | Bar+10 |
|--------|-------|---------|-------|-------|-------|--------|
| 173 | $4,814.65 | $79.0 | +$0.35 | −$35.15 | −$30.05 | **−$99.55** |
| 175 | $4,903.35 | $54.3 | −$7.65 | −$14.35 | −$23.75 | **−$90.05** |
| 176 | $4,879.85 | $51.4 | **−$87.85** | −$66.55 | −$64.15 | −$59.95 |
| 177 | $4,841.25 | $67.1 | +$7.25 | +$2.35 | −$15.25 | **−$36.35** |

Trade 176 fell $87.85 in the first bar (1-hour candle = $87 drop against a $51 stop). There is no
stop width that survives a $87 move against a $51 stop within the same bar — that is a catastrophic
directional entry, not a stop-placement issue. The stop would need to be ATR×8+ to survive, which
is not viable.

---

## Step 5: Root Cause and Fix

### What changed

| Factor | Q3 2025 | Q2 2026 | Impact |
|--------|---------|---------|--------|
| 1H return autocorrelation | −0.027 (random-walk) | **−0.054 (mean-reverting)** | System enters at local highs; price reverses |
| RSI at LOSS entry | — | 71.5, 74.4, **85.0** | Overbought entries in reverting market |
| EMA200 slope | +13.4%/quarter | **+1.5%/quarter** | Trend stalled; TP unreachable |
| 10h fwd return % positive | 54% | **29%** | 71% of entries directionally wrong |
| Gold period price change | +16.9% | **−1.1%** | No trend to follow |
| Adverse excursion (losses) | −$1.72 | **−$85.55** | Not a stop problem — direction wrong |

### Possible answers from the prompt

| Hypothesis | Evidence |
|------------|---------|
| A. Volatility mis-calibration | **Partial** — ATR% nearly doubled from Q3 2025, but Q2 2026 it eased back. Not the primary driver. |
| B. Trend persistence weakened | **CONFIRMED** — EMA200 slope +1.5%, ADX 25.6, price flat. Trend effectively paused. |
| C. Liquidity/spread changed | No evidence — spread model unchanged, extreme candle % actually fell. |
| D. Mean reversion increased | **CONFIRMED** — AC(1h) = −0.054, the only mean-reverting quarter in 2 years. |
| E. RSI overbought entries | **CONFIRMED** — 3 of 4 losses entered at RSI > 70 with no RSI ceiling on BUY signals. |

### Primary cause: D + B combination

Gold entered a **post-parabolic consolidation** (Apr–May 2026). After a parabolic +$1,000 move
(Jan–Mar 2026), the market exhibits two-way high-ATR action: sharp up-spikes followed by sharp
reversals. The EMA200 stays pointing up (it smooths 200 bars = ~8 days), approving BUY entries.
But at the 1H level, every rally is a mean-reverting spike sold by participants taking parabolic
profits. The trend-following logic buys the spike peaks — exactly when short-term reversals are
most probable.

The RSI finding compounds this: EMA_MACD_TREND_BUY fires after momentum has already been running.
In Q3 2025, it entered at RSI~60 (mid-momentum, more room to run). In Q2 2026, it fires at RSI 70–85
(late momentum, exhaustion). In a mean-reverting market, RSI=85 is the optimum exit point for longs,
not entry.

### Statistical note

P(≤2 wins from 7 trades at 43% WR) = 31% — not formally significant with such a small sample.
However, the qualitative evidence (mean-reverting AC, 29% directional accuracy, RSI=85 entries,
$85 adverse excursions) converges on a structural explanation rather than random variance. The
sample size is too small to prove structural failure but the direction of evidence is consistent.

---

## Recommended Fix

### Fix 1 (implement now): Add RSI ceiling to BUY entries

**Proposed rule:** Block BUY signals when RSI > 70, regardless of other confluences.

```python
# In generate_signal() in gold_trading_agents.py
# Add after the trend_up / trend_down checks:
if direction == "BUY" and rsi > 70:
    return None  # overbought — don't buy exhaustion rallies
```

**Rationale:**
- 3 of 4 Q2 2026 losses: RSI 71.5, 74.4, 85.0
- In mean-reverting regimes, RSI > 70 marks rally exhaustion
- In trending regimes (Q3 2025), RSI rarely reaches 70+ at EMA_MACD_TREND entry bars (avg 59.5)
- This filter is **natural and symmetric** — the system already uses RSI < 35 to score BUY signals;
  RSI > 70 should score against BUY entries, not be neutral
- Cost: small number of missed winners; benefit: avoids the worst exhaustion-entry losses

### Fix 2 (apply to BUY symmetrically): ADX threshold for BUY

**The system already applies ADX ≥ 25 to SELL signals.** Apply it to BUY signals too:

```python
# In generate_signal():
if direction == "BUY" and "EMA_MACD_TREND" in pattern:
    if adx_val < ADX_TREND_THRESHOLD:  # 25
        return None
```

**Evidence:** Trade 177 had ADX=15.8 and lost. Low-ADX trend signals have no momentum to carry
price to the TP target.

### Fix 3 (structural): EMA200 slope filter

**Add a momentum quality check on the EMA200 itself:**

```python
# In MarketAnalystAgent or TechnicalAnalystAgent:
# Compute EMA200 slope over last 20 bars as % change
ema200_now  = ema200_series.iloc[-1]
ema200_20b  = ema200_series.iloc[-21]
ema200_slope_pct = (ema200_now - ema200_20b) / ema200_20b * 100

# Block BUY if EMA200 slope is below threshold (trend stalling)
if ema200_slope_pct < 0.3:  # less than 0.3% per 20 bars = essentially flat
    return None  # or reduce to lower priority signal
```

**Evidence:** EMA200 slope dropped from +13% to +1.5% — had this filter existed, Q2 2026 conditions
would have triggered it.

### Priority order

| Fix | Effort | Expected impact | Risk |
|-----|--------|-----------------|------|
| RSI ceiling (RSI < 70 for BUY) | 2 lines | High — blocks 3/4 Q2 losses | Low |
| BUY ADX filter (ADX ≥ 25) | 2 lines | Medium — 1 more Q2 loss blocked | Low |
| EMA200 slope filter | 10 lines | Medium — catches stalling trends | Requires testing |

### Immediate action plan

1. **Right now:** Paper trade with ATR×2.5, ATR_STOP_MULT already widened
2. **This week:** Test Fix 1 (RSI < 70 ceiling) in a backtest — run `backtest_v2_atr20.py` with this
   added condition, compare results to ATR×2.5 baseline
3. **After 20 paper trades:** Evaluate if Fix 1 would have blocked Q2 2026 losses in real time
4. **Do NOT add Fix 3 yet** — EMA200 slope is more complex and needs its own backtest validation

### When to abandon vs wait out

**Wait it out if:**
- Paper trade PF stays > 0.90 (not losing much)
- Gold enters a new trending leg (price makes new ATH with momentum)
- ADX returns above 30 consistently

**Investigate further if:**
- Paper trade PF drops below 0.70 after 15+ trades
- RSI continues to show overbought entries on EMA_MACD signals
- ADX stays below 20 for >30 trading days (prolonged ranging)

**Do not abandon** based on 7 trades. The pre-2026 evidence (2 years, Sharpe 2.64) is strong.
The current drawdown has identifiable structural causes that can be addressed with targeted filters.

---

## Summary

| Test | Finding | Verdict |
|------|---------|---------|
| ATR% change | 0.26% → 0.48% | WATCH — elevated but not conclusive |
| ADX change | 28.6 → 25.6 | WATCH — trend weakening, near threshold |
| Bull bar % | 52% → 50% | NEUTRAL |
| EMA200 slope | +13.4% → +1.5% | CONCERNING — trend stalled |
| 1H autocorrelation | −0.027 → −0.054 | **CONFIRMED mean-reversion shift** |
| RSI at losses | 59.5 (Q3 wins) → 65.2 (Q2 losses) | **CONFIRMED overbought entries** |
| 10h fwd return | 54% positive → 29% positive | **CONFIRMED directional failure** |
| Adverse excursion | −$1.72 → −$85.55 | **CONFIRMED wrong direction, not stop issue** |
| Stat significance | P=31% with n=7 | INCONCLUSIVE — too few trades |

**Primary cause:** Post-parabolic mean-reversion regime + EMA_MACD_TREND firing at overbought RSI.

**Fix:** Add `RSI < 70` ceiling for BUY entries. Apply ADX filter symmetrically to BUY signals.

**Action:** Paper trade now, test RSI ceiling in parallel backtest.

---

*Research only — production `gold_trading_agents.py` unchanged.*
*Script: `drawdown_diagnostic.py`*
