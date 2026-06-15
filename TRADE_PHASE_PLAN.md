# Trade Phase Plan — v12 (BUY-only + BE + HTF + Data Hygiene)

**Phase start:** 2026-06-14
**System version:** v12 (frozen)
**Account:** XM demo $10,000 (live MT5, demo money)
**Strategy:** Run v12 unchanged for 6 months. Collect data. Decide based on real performance.

---

## Build phase — CLOSED

Total iterations: v1 → v12 (12 versions over 5 weeks).
Last 3 attempted improvements (v8 slope, v13 bear-SELL, v13 partial TP) all failed deploy gate.
**Conclusion:** v12 is the local maximum at this complexity + timeframe + data.
**Decision:** No more entry/exit feature changes until real-money performance data exists.

---

## Trade phase — OPEN (6 months minimum)

### Goal

Accumulate 50-100 closed live trades. Use **real** performance to decide:
1. Switch demo → real money (Milestone 3)
2. Scale up (Milestone 4)
3. Or revert / revisit (red flags)

### Frozen elements (DO NOT CHANGE)

- `gold_trading_agents.py` — no edits
- `backtest_v2.py` — no edits
- `logger.py` — no edits
- Config defaults — no edits
- Kill switches:
  - `BEAR_REGIME_ENABLED=false` ✓
  - `PARTIAL_TP_ENABLED=false` ✓
  - `HTF_BIAS_ENABLED=true` ✓
- `.env` tunable parameters allowed: `RISK_PER_TRADE_PCT`, `DAILY_LOSS_LIMIT`, `MAX_OPEN_POSITIONS`, `MAX_SPREAD_USD`, `FRIDAY_CUTOFF_HOUR_UTC`, `MT5_FETCH_N_BARS`
- Everything else: untouchable

### Allowed exceptions (don't abuse)

- Bug fixes (crash, MT5 disconnect, log corruption) — fix the bug, don't add logic
- Documentation updates — encouraged
- Bundle B research (MT5 history recalibration) — research only, no production change
- Emergency stop / revert per EMERGENCY_PROCEDURES.md

---

## Milestones

### Milestone 0 — Phase start (today)

- [ ] Run PRE_DEPLOYMENT_CHECKLIST.md
- [ ] Backup trade_journal.json to `snapshots/trade_journal_phase_start_20260614.json`
- [ ] Restart bot: `py -3.12 gold_trading_agents.py`
- [ ] Verify first cycle logs cleanly
- [ ] Commit + git tag `v12-phase-start`

### Milestone 1 — HTF flips BULL (≈1-4 weeks)

**Trigger:** `daily_summary.md` shows `HTF=BULL` ≥ 50% of cycles for a day OR Telegram delivers first approved BUY signal

**Action:** None. Just confirm bot resumes trading.

**Red flag:** 5+ trading days after BULL flip without any trade = filter chain has issue. Investigate per EMERGENCY_PROCEDURES.md.

### Milestone 2 — 25 closed live trades (≈6-8 weeks after Milestone 1)

**Review checklist:**

| Metric | Backtest baseline | Acceptable | Concerning |
|---|---|---|---|
| Win rate | 67% | ≥ 50% | < 45% |
| Avg P&L per trade | +$95 | ≥ +$30 | < $0 |
| MaxDD (live) | 5.1% | ≤ 8% | > 10% |
| Operational errors | 0 | ≤ 2/week | > 5/week |

**Actions:**
- All OK → continue, no change
- 1 concerning → continue but document concern; review again at Milestone 3
- 2+ concerning → pause new trades, review individual trade attribution

**Document in `MILESTONE_2_REVIEW.md`** (template at bottom).

### Milestone 3 — 50 closed live trades (≈3 months from Milestone 1)

**Decision point: switch demo → real money?**

Pass criteria (ALL must pass):
- [ ] Live Sharpe (per-trade approximation) ≥ 1.5
- [ ] Win rate ≥ 50%
- [ ] Max DD ≤ 8% (50% margin vs backtest's 5.1%)
- [ ] At least 30 winners (sample large enough for confidence)
- [ ] Zero operational errors in last 25 cycles
- [ ] You personally trust the system (gut check)

If pass: start real money with SAME $10k size. Don't scale up.
If fail by 1-2: continue demo 25 more trades, re-evaluate at 75-trade mark.
If fail by 3+: investigate root cause. Possibly revert. Consider Bundle B recalibration.

**Document in `MILESTONE_3_DECISION.md`.**

### Milestone 4 — 100 closed live trades (≈6 months from Milestone 1)

**Decision point: scale up?**

Only if Milestone 3 = real money + still passing all criteria.

Scale options:
- Increase risk: 0.5% → 1.0% per trade (2× exposure)
- Increase account: deposit more capital
- Add 2nd broker (diversify operational risk)

Do NOT scale up if:
- Live Sharpe still < 1.5
- System hasn't traded through a full bull/bear cycle yet
- You feel any FOMO or rush

---

## Red flags — pause or revert immediately

These trigger emergency action regardless of milestone:

1. **3 consecutive losses on real money** → stop bot, review individual trades, decide pause/continue
2. **5% account drawdown** → stop, review entirety, possibly revert to v11 or v11.1
3. **MT5 connectivity loss > 1 hour during open position** → manual intervention required (see EMERGENCY_PROCEDURES.md)
4. **Bot crash mid-cycle** → restart with PRE_DEPLOYMENT_CHECKLIST.md, investigate cause
5. **Unauthorized live trade (no signal in log)** → critical bug, stop and investigate
6. **Data divergence warnings > 3/day for a week** → schedule Bundle B recalibration urgently
7. **You feel anxious checking the bot** → take a 1-week break, come back, decide

---

## Bundle B (optional, research-only) — MT5 history recalibration

Allowed in parallel with trade phase. Does not touch production code.

### Steps

1. **Export MT5 H1 history**
   - Open MT5 terminal → Tools → History Center (F2)
   - Select GOLD# H1 timeframe
   - Set date range: 2024-06-01 to today
   - Export → CSV

2. **Save CSV** to `D:\gold-trading\data\gold_mt5_h1_2024_2026.csv`

3. **Tell Claude (in a Cowork session):**
   "Bundle B step: write a CSV-input adapter for backtest_v2.py so I can run gate on MT5 XAUUSD data instead of yfinance GC=F."

4. **Run all backtest gates on MT5 data:**
   - v11.1 (BE only)
   - v12 (BE + HTF)
   - Compare metrics to existing yfinance results

5. **Write `MT5_CALIBRATION_REPORT.md`**
   - If metrics within ±10%: GC=F backtest is robust proxy for XAUUSD trading
   - If metrics differ > 20%: recalibration needed; may require new RSI/ATR thresholds
   - Document findings; do NOT change production until reviewed

This work is purely research. Does not block trade phase progress.

---

## What success looks like at month 6

- 80-120 closed live trades
- Live Sharpe approaching backtest (≥ 1.5)
- Live MaxDD ≤ 8%
- Real money for last 50+ trades
- Documented review reports at each milestone
- No code changes since phase start
- You trust the system

---

## What "trade phase done badly" looks like

- 200 closed trades in 6 months (over-trading via Config changes)
- 5+ code revisions during phase (build-mode bleeding back)
- 3+ kill switches flipped manually to "try something"
- Live metrics deteriorate vs backtest by 50%+
- You feel stressed and check bot every 30 minutes

If you notice any of this happening, stop immediately and re-read this file.

---

## Reflection prompts (read monthly)

1. Has the system done what I designed it to do?
2. Am I tempted to "improve" it for emotional reasons?
3. What does the data say (not what does my gut say)?
4. Have I tracked changes I made and why?
5. Would 30-year-experienced trader-me approve of what current me is doing?

---

## Milestone review template (use for M2, M3, M4)

```markdown
# Milestone N Review — YYYY-MM-DD

## Stats
- Total closed live trades: X
- Win rate: X%
- Net P&L: $X
- Best win: $X
- Worst loss: $X
- Max consecutive losses: X
- Max drawdown: X%

## Operational
- Errors logged: X
- MT5 disconnects: X
- Data divergence warnings: X
- Spread blocks: X
- Friday cutoffs hit: X

## Trade pattern analysis
- Patterns that fired: [list]
- HTF distribution: BULL=X% BEAR=X% NEUTRAL=X%
- Avg trade duration: X hours

## My judgment
- The system behaves: [as designed / better than expected / worse than expected]
- I trust it: [yes / partially / no]
- I want to change: [nothing / specific thing X]

## Decision
- Continue / Pause / Revert / Scale up
- Rationale: [why]
```

---

## Closing thought

You spent 5 weeks building. You spend 6 months trading.
The trading part is where edge gets confirmed or denied.
Both phases are necessary. This one is harder.

Boring trading systems compound.
