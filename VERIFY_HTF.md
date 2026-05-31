# Verification Prompt — HTF 4H Filter (v12 candidate)

Paste this into Claude Code in `D:\gold-trading` to verify the HTF filter on
your machine (backtest runs much faster than from Cowork sandbox).

---

## Paste this prompt

```
Read CLAUDE.md, TRADER_REVIEW.md "P1 #6", TRADE4_ANALYSIS.md, and
DRAWDOWN_DIAGNOSTIC.md.

Goal: Verify the v12 HTF (4H) bias filter just added to gold_trading_agents.py
and backtest_v2.py. Code is in place; this prompt runs the backtest gate.

Step 1 — Syntax check:
  py -3.12 -m py_compile gold_trading_agents.py logger.py backtest_v2.py
  Expected: silent (no output).

Step 2 — Baseline re-run (sanity check):
  py -3.12 backtest_v2.py --period 2y --be
  Expected: matches the v11.1 baseline numbers:
    101-110 trades, ~67% WR, Sharpe ~2.89, MaxDD ~4.7%, +$7,800 net
  This confirms our HTF code addition didn't break the baseline path.
  Save output to be_only_baseline.log.

Step 3 — HTF-only run (BE off, HTF on):
  py -3.12 backtest_v2.py --period 2y --htf
  This isolates HTF's impact vs the v9 baseline (no BE, no HTF).
  Save output to htf_only.log.

Step 4 — HTF + BE run (the deploy candidate):
  py -3.12 backtest_v2.py --period 2y --be --htf
  This is what production v12 will behave like.
  Save output to be_htf.log.

Step 5 — Apply deploy gate to the BE+HTF run (Step 4):
  Acceptance criteria (ALL must pass):
    [a] Sharpe ≥ 2.5
    [b] MaxDD ≤ 7%
    [c] Trade count drop ≤ 25% vs v11.1 baseline (110 trades)
        → minimum 82 trades
    [d] WR ≥ 60% (allowed to drop slightly from BE-only's 67% because HTF
        removes some borderline trades; if WR jumps too — verify nothing odd)
    [e] Walk-forward: val Sharpe ≥ 60% of train Sharpe (slightly stricter
        than the standard 50% threshold because HTF removes regime-transition
        edge cases that overfitting normally hides)

Step 6 — Critical reconstruction tests:
  Open backtest_v2_results_be_htf.json and check:
  6a. Trade #4 reconstruction: 2026-05-12 around 20:30 UTC
      Was there a BUY signal at that bar in the BE+HTF run? Expected: NO
      (4H bias should have been BEAR or NEUTRAL).
      → If a BUY trade exists at that time in the result: FAIL — HTF didn't help.
  6b. Q2 2026 drawdown: count BUY trades opened in 2026-04-01 to 2026-05-06.
      Compare to baseline (BE-only). HTF should remove most/all of the
      4 losing trades documented in DRAWDOWN_DIAGNOSTIC.md.

Step 7 — HTF activation timeline:
  From the HTF compute_htf_bias_series log line that prints BULL/BEAR/NEUTRAL
  distribution, report:
    - % of bars in 2024–2026 window that are BULL
    - % BEAR
    - % NEUTRAL
  Expected: BULL ≥ 50% (we're in bull market), BEAR < 15%, NEUTRAL < 35%.
  If BEAR > 30% in this window: HTF is too restrictive — investigate.

Step 8 — Verdict:
  Write HTF_VALIDATION.md report with:
    - 3 backtest table (BE-only / HTF-only / BE+HTF) side by side
    - Per-quarter breakdown (Q3 2025 best, Q2 2026 drawdown — both runs)
    - Trade #4 reconstruction result
    - Q2 2026 drawdown reconstruction (how many of the 4 losses blocked?)
    - HTF bias distribution
    - Deploy verdict: PASS or FAIL with reasoning

If ALL gate criteria pass:
  - Update CLAUDE.md with v12 section (use the template from v11/v11.1 entries)
  - Update logger.py _VERSION_TAG comment if not already done
  - Mark for live deployment
  - DO NOT yet deploy live — wait for user confirmation after they review
    HTF_VALIDATION.md

If ANY gate criterion fails:
  - Write FAILURE_HTF.md explaining which criterion failed and why
  - Revert no code (HTF defaults to disabled in backtest; in production it's
    gated by Config.HTF_BIAS_ENABLED which user can flip to False via .env)
  - Stop. Do not proceed to Data Hygiene change yet.

Out of scope:
  - Don't change HTF parameters (EMA len, slope lookback) without explicit ask
  - Don't combine with Data Hygiene yet (separate verification)
  - Don't deploy live — verification only
```

---

## What to expect

Step 2 should reproduce v11.1 numbers exactly (proves backward compat).
Steps 3 and 4 will tell us HTF's standalone impact.
Step 5 gate is the decision point.

**My prediction (be ready to update):**
- BUNDLE 1 candidate (BE+HTF): WR ~63–68%, Sharpe ~2.6–2.9, MaxDD ~4–5%, ~85–95 trades
- HTF blocks Trade #4 (high confidence — 4H EMA50 was declining at the time)
- HTF blocks 3 of 4 Q2 2026 losses (moderate confidence)
- Trade count drop ~10–20% vs BE-only baseline

If reality differs significantly → investigate before deploy. Especially watch for:
- WR dropping below 55% (means HTF is wrong-direction)
- Sharpe dropping below 2.5 (means we lost too much edge)
- BEAR bars > 30% (means filter too strict for this period)

---

## After HTF passes/fails

If PASS → tell me, I'll deliver Data Hygiene change (Bundle 1 part 2).
If FAIL → tell me what failed, I'll revise the HTF logic before continuing.
