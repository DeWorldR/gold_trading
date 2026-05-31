# Forward Test — Gold Trading System

**Test Period**: 2026-05-11 → 2026-06-21 (6 weeks)
**Mode**: PAPER_TRADE=true
**Goal**: Validate that backtest expectations hold in live market conditions

---

## Baseline Expectations (from 2yr backtest)

| Metric | Expected Range |
|--------|---------------|
| Trades per week | 2-4 |
| Win rate | 35-45% |
| Avg win | +$50 to +$70 |
| Avg loss | -$20 to -$35 |
| Net P&L per trade | +$10 to +$20 |
| Spread per trade | $0.20 to $0.30 |

## Hard Failure Conditions (stop and investigate)

- [ ] System crash > 1 time per week
- [ ] Trade count < 50% or > 200% of backtest expectation
- [ ] Win rate < 25% over 20+ trades
- [ ] Daily loss > $300 (system should auto-halt)
- [ ] Max drawdown > 18%
- [ ] Telegram missing > 5% of cycles
- [ ] MT5 disconnect > 30 min in trading hours

---

## Daily Log

### Week 1: 2026-05-11 → 2026-05-15

| Date       | Day | Cycles Run | Signals | Trades Opened | Trades Closed | W/L | P&L | Spread Avg | Issues / Notes |
|------------|-----|------------|---------|---------------|---------------|-----|-----|------------|----------------|
| 2026-05-11 | Mon |   $0.32    |         |               |               |     |     |            |                |
| 2026-05-12 | Tue |            |         |               |               |     |     |            |                |
| 2026-05-13 | Wed |            |         |               |               |     |     |            |                |
| 2026-05-14 | Thu |            |         |               |               |     |     |            |                |
| 2026-05-15 | Fri |            |         |               |               |     |     |            |                |

**Week 1 Summary:**
- Total trades:
- Win rate:
- Net P&L:
- System uptime:
- Issues encountered:

### Week 2: 2026-05-18 → 2026-05-22

[same format]

### Week 3-6: [continue pattern]

---

## Spread Tracking (Critical)

Backtest assumed: $0.25 per side (2.5 pips)

| Date | Time UTC | Symbol | Bid | Ask | Spread | Backtest Diff |
|------|----------|--------|-----|-----|--------|---------------|
|      |          | XAUUSD |     |     |        |               |

**Sampling plan**: Note spread at:
- 08:00 UTC (London open)
- 13:00 UTC (NY open / overlap)
- 17:00 UTC (US session)
- 20:00 UTC (NY close approach)
- During any news event

---

## Weekly Review (every Friday)

### Week N Review

**Quantitative comparison:**
| Metric | Backtest expectation | Actual | Within range? |
|--------|---------------------|--------|---------------|
| Trades | | | |
| Win rate | | | |
| Net P&L | | | |
| Avg spread | | | |

**Qualitative observations:**
- What surprised me this week?
- Any signals that "looked wrong" but system took anyway?
- Any signals system missed that I would have taken manually?
- Telegram alerts: timely? clear? complete?
- MT5 connection: stable?

**Action items for next week:**
- [ ]

---

## Final Decision Checkpoint (end of Week 6)

### Pass criteria (all must be true to proceed to live)

- [ ] System ran ≥ 28 days without unhandled crashes
- [ ] Total trades within 50%-200% of expectation (8-32 trades)
- [ ] Win rate ≥ 30% over the test period
- [ ] No regime alerts triggered (or properly handled if triggered)
- [ ] Avg spread ≤ $0.40 (1.6x backtest assumption)
- [ ] Max single drawdown ≤ 18%
- [ ] Telegram delivered ≥ 95% of expected alerts
- [ ] MT5 disconnects < 1 hour total

### If all pass → proceed to live with reduced size

```
Week 1-4 live:  Lot size × 0.25
Week 5-8 live:  Lot size × 0.50
Week 9+ live:   Full lot size IF metrics hold
```

### If any fail → diagnose

| Failure | Likely cause | Action |
|---------|-------------|--------|
| Too few trades | Filters too strict in live data | Review filter parameters |
| Too many trades | Filters not triggering correctly | Check filter logic |
| Low win rate | Signal logic broken in live | Compare live signals vs backtest signals manually |
| High spread | Broker spread different | Recalculate breakeven, may need broker change |
| Crashes | Bug in code | Fix and restart forward test from week 1 |

---

## Comparison to Backtest (final)

To be filled at end of forward test:

| Metric | 2yr Backtest | 6-week Forward | Variance |
|--------|-------------|----------------|----------|
| Trades/week | 2.9 | | |
| Win rate | 39.9% | | |
| Avg win | | | |
| Avg loss | | | |
| Net P&L/trade | $15.92 | | |
| Profit factor | 1.24 | | |
| Spread/trade | $0.25 | | |

**Forward test verdict:**
- [ ] PASS — proceed to live with reduced size
- [ ] CONDITIONAL — extend test 2 more weeks
- [ ] FAIL — return to backtest/development phase

**Notes:**