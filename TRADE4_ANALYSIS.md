# Trade #4 Root-Cause Analysis

**Trade:** BUY XAUUSD | MT5 ticket 312102150  
**Opened:** 2026-05-12 20:30 UTC @ $4713.13 (MT5) / $4722.20 (yfinance GC=F proxy)  
**Closed:** 2026-05-13 14:01 UTC @ $4693.30 — SL hit  
**Result:** -$59.49 LOSS  
**Config:** v6 (ATR×2.5 stop, RSI ceiling 70, ADX symmetric, MAX=1)

---

## Indicator Values at Entry Bar (2026-05-12 20:30 UTC)

*Computed from 60-day yfinance GC=F 15m dataset (4181 bars at entry point).*  
*Note: MT5 entry price $4713.13 vs yfinance $4722.20 — different instruments (XAUUSD spot vs GC= futures). Relative indicator alignment is equivalent.*

| Indicator | Value | Threshold | Status |
|-----------|-------|-----------|--------|
| Close | $4722.20 (proxy) | — | — |
| ATR(14) | 8.40 ($) / 0.178% | VOLATILE > 0.35% | Normal |
| RSI(14) | **68.50** | BUY < 35 / ceiling < 70 | Near-overbought; 1.5pt below ceiling |
| EMA20 | 4704.17 | — | Close > EMA20 (+$18) |
| EMA50 | 4702.98 | — | EMA20 > EMA50 (barely: +$1.19) |
| EMA200 | **4710.89** | Close must be > EMA200 + 0.3×ATR | Close ABOVE by +$11.31 |
| EMA200 slope (last 20 bars = 5h) | **-2.93** | Not checked | FALLING |
| EMA200 slope (last 200 bars = 50h) | +0.23 | Not checked | Nearly flat |
| MACD | 7.68 | Must be > signal | Bullish: MACD > signal (3.51) |
| BB position | 86% | Top 20% = sell zone | In SELL zone |
| ADX(14) | **29.92** | >= 25 | Trending |
| BB width percentile | 98% | >= 25 | Wide (not choppy) |

---

## Why Each Filter Did NOT Block the Trade

The `EMA_MACD_TREND_BUY` pattern requires 3 of 5 confluences, passes EMA200 gate, passes ADX, and RSI < 70. All seven checks were evaluated and all passed:

### 1. REGIME — PASS
ATR% = 0.178%, well below the VOLATILE threshold of 0.35%. Market classified as TRENDING_UP.

### 2. EMA200 TREND GATE — PASS (the critical one)
Close ($4722) was above EMA200 ($4711) by +$11.31. The neutral zone is only ±0.3×ATR = ±$2.52.  
`trend_up = True` → BUY confluence counting was **not** zeroed out.

**The gap sounds comfortable, but consider the context:**
- Gold had fallen from $5,041 (May 2) to $4,713 — a 6.4% drop in 10 days
- EMA200 (on 15m bars) covers only 200 × 15min = 50 hours of history
- The EMA200 was already **falling** (slope = -$2.93 in the last 5 hours)
- Price was only 0.24% above EMA200 — barely clearing the filter

The EMA200 lagged behind the decline and had not yet crossed above price. That crossing was ~1–2 candles away.

### 3. BB WIDTH — PASS
BB width at 98th percentile — the market was not choppy; it was moving directionally.

### 4. CONFLUENCE — PASS (3/3 required, exactly 3 achieved)
Raw BUY signals scored before EMA200 gate:
1. Price above EMA20 (+$18)
2. EMA20 > EMA50 (+$1.19 — barely)
3. MACD > signal (7.68 > 3.51)

Raw SELL signals (would have scored 2/5 if EMA200 gate allowed them):
- RSI = 68.5 → **scored as overbought (RSI > 65 = SELL confluence)**
- BB position = 86% → **scored as near upper BB (SELL confluence)**

The EMA200 gate zeroed `sell_n` to 0 because `trend_down = False`. The 2 sell signals were silenced. The 3 buy signals survived at exactly the minimum threshold.

### 5. ADX — PASS
ADX = 29.92, above the 25 threshold. The short-term bounce created genuine momentum, which ADX confirmed. ADX does not distinguish bounce momentum from trend momentum.

### 6. RSI CEILING — PASS (barely)
RSI = **68.50**, ceiling = 70.  
The trade passed by **1.5 RSI points.** Any bar with RSI ≥ 70 would have been blocked.

### 7. DXY SOFT-CONFLUENCE — PASS
DXY trend was NEUTRAL at the time (EMA20/EMA50 crossover ambiguous), so no -1 penalty was applied. Effective confluence remained at 3 (minimum). If DXY had been UP, effective BUY would have been 2 → **trade would have been blocked.**

---

## What Actually Happened Technically

Price context in the 2 hours before entry:

| Time (UTC) | Close |
|---|---|
| 18:15 | 4692.50 |
| 18:30 | 4702.80 |
| 18:45 | 4703.00 |
| 19:00 | 4706.90 |
| 19:15 | 4710.80 |
| 19:30 | 4716.90 |
| 19:45 | 4723.20 |
| 20:00 | 4720.70 |
| 20:15 | 4720.70 |
| **20:30** | **4722.20 ENTRY** |

Gold bounced +$30 (+0.64%) in about 2 hours off a local low. During this bounce:
- EMA20 and EMA50 aligned bullish (EMA20 > EMA50 by $1.19)
- MACD turned positive on the momentum of the bounce
- Price crossed back above EMA200

The system interpreted this 2-hour bounce as a trend entry signal. In reality it was a **dead-cat bounce within a macro downtrend**. The next session resumed the decline and hit the stop-loss.

---

## Verdict: "Reasonable Entry" or "Catching a Falling Knife"?

**Catching a falling knife** — but in a way that is algorithmic and repeatable under current rules.

The entry was not a filter failure in the traditional sense (no filter was bypassed). Every rule was followed. But the system has a structural blind spot:

> **The EMA200 trend gate checks whether price is above EMA200, but it does not check whether EMA200 itself is rising.**

A falling EMA200 with price barely above it is not a bullish trend — it is a lagging indicator converging toward a bearish crossover. At entry, the EMA200 slope over the prior 5 hours was -$2.93. The EMA200 was falling toward price from below, not price rising away from it.

Contributing aggravating factors:
- EMA20 > EMA50 by only $1.19 (essentially flat — not a strong crossover)
- RSI = 68.5 (1.5 points from being blocked; the system simultaneously flagged this as an overbought SELL signal, but silenced it via the EMA200 gate)
- Price at 86th BB percentile (top of range — another silenced sell signal)
- The bounce was short (~2 hours) against a 10-day macro downtrend

---

## Recommendation

### If this is variance → continue
The system has a 54% win rate and Sharpe 2.77 over 2 years. Individual losses are expected. If EMA200 slope filtering is too aggressive, it may over-filter legitimate trend-continuation entries.

### If this is a system flaw → add EMA200 slope filter

The evidence suggests a real structural gap: the EMA200 gate passes entries where the trend is *ending*, not just entries against the trend. To close this gap:

**Option A: EMA200 slope filter (direct fix)**
Block BUY when EMA200 slope is negative over the last N bars. Example:
```python
ema200_slope = ema200_s.iloc[-1] - ema200_s.iloc[-21]   # last 20 bars = 5h
EMA200_SLOPE_MIN_BUY = 0.0                               # EMA200 must be rising
if direction == "BUY" and ema200_slope < EMA200_SLOPE_MIN_BUY:
    # block
```
- Would have blocked Trade #4 (slope = -2.93)
- Needs backtest: how many valid winners does it eliminate?

**Option B: Raise the trend gate margin**
Current: `close > EMA200 + 0.3×ATR` (neutral zone = $2.52 at entry)  
Proposed: `close > EMA200 + 1.5×ATR` (neutral zone = $12.60 at entry)  
- Trade #4: close was +$11.31 above EMA200 → would have been **blocked** (needed +$12.60)
- Less mechanistic than slope, but simpler to implement and backtest
- Risk: reduces trade count in genuine trending environments

**Option C: Daily-timeframe trend confirmation**
Require that on the **daily** timeframe, close > EMA50 daily. This would catch the macro downtrend that the 15m EMA200 missed. More complex to implement (separate daily data fetch) but addresses the root cause at the right timeframe.

**Recommendation priority:** Option A (EMA200 slope) is the most targeted fix. Run the 2-year backtest with `EMA200_SLOPE_MIN_BUY = 0` and compare Sharpe/trade-count before deploying. If it costs fewer than 10% of trades and doesn't reduce Sharpe, deploy it.

---

## Summary

Trade #4 passed every filter because:
1. The 2-hour intraday bounce created temporary bullish alignment on 15m indicators
2. Price was still above EMA200 (lagging indicator had not yet caught up with the decline)
3. RSI was 68.5 — 1.5 points below the block threshold
4. EMA200 itself was falling (slope = -$2.93/5h) — a signal the system does not check

The v6 filters are correctly designed for their stated purpose, but they do not protect against the early phase of a major downtrend, when price is still above a declining EMA200. The BUY-only system is at increased risk whenever gold is in a sustained decline and EMA200 has not yet crossed above price.

**Immediate operational note:** Given the current macro context (gold -10% over 13 days, EMA200 slope flat-to-negative), monitor whether the consecutive-loss guard is engaging. If the system continues to BUY into weakness, the 2-consecutive-loss-per-day guard will limit exposure, but it won't prevent the first two losses of each session.
