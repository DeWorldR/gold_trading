# Verification Prompt — Data Hygiene (Bundle 1 part 2)

Paste this into Claude Code in `D:\gold-trading`. This change has NO backtest
gate (it's pure observability — backtest still runs on yfinance only). Verification
is structural: compile clean + smoke test + verify cycle_log has new fields.

---

## Paste this prompt

```
Read CLAUDE.md (the v12 "Data hygiene" continued section). Code is in place;
this prompt verifies the change doesn't break anything.

Step 1 — Syntax compile:
  py -3.12 -m py_compile gold_trading_agents.py logger.py backtest_v2.py
  Expected: silent.

Step 2 — Smoke test on paper mode:
  Briefly run the bot in paper mode (1 cycle) to confirm:
  py -3.12 -c "
  from gold_trading_agents import OrchestratorAgent
  import gold_trading_agents as gta
  gta.Config.PAPER_TRADE = True
  o = OrchestratorAgent()
  o.run_cycle()
  print('OK: one cycle completed')
  "
  Expected: completes without exception. May print 'No signal' or signal info.

Step 3 — Verify cycle_log.jsonl has data_source field:
  Open the most recent cycle_log entry written by Step 2.
  py -3.12 -c "
  import json
  with open('cycle_log.jsonl', 'r') as f:
      last = list(f)[-1]
  entry = json.loads(last)
  assert 'data_source' in entry, 'data_source field missing'
  print(f'data_source = {entry[\"data_source\"]}')
  print(f'htf_bias    = {entry.get(\"htf_bias\")}')
  print('OK: cycle_log schema updated')
  "
  Expected (paper mode):
    data_source = yfinance
    htf_bias    = BULL  (or BEAR/NEUTRAL — depends on market)

Step 4 — Verify backtest still runs and produces results identical to Step 4 of
HTF verification (BE+HTF):
  py -3.12 backtest_v2.py --period 2y --be --htf
  Compare to backtest_v2_results_be_htf.json from prior verification.
  Expected: byte-identical or trivial floating-point delta only. Net P&L should
  match within $1.

Step 5 — (Optional, live-mode dry run if MT5 is configured):
  If you can briefly connect to MT5, do a one-cycle live run:
  py -3.12 gold_trading_agents.py
  Then press Ctrl-C after the first cycle completes.
  Verify in gold_trading.log:
    - Line "Using MT5 data (500 bars)" appears (not 300)
    - Line "close=... regime=... HTF=... src=mt5" appears
    - If divergence > $5: warning "DATA DIVERGENCE: MT5 $X vs yfinance $Y"
  This is not a deploy gate, just a sanity check.

Step 6 — Verdict:
  Write VERIFY_DATA_HYGIENE_RESULT.md with:
    - Steps 1-3 pass/fail
    - Step 4 backtest delta (expect 0)
    - Step 5 result (if attempted)
    - Decision: ready to bundle with HTF for v12 deploy

If all steps pass:
  Bundle deploy v12 = HTF + Data Hygiene combined. Tag v12 in git, then
  forward test 2 weeks before next change.

Out of scope:
  - Don't run live for more than smoke test (let user start the actual live run)
  - Don't change Config defaults
  - Don't recalibrate thresholds based on MT5 XAUUSD (that's a separate sprint)
```

---

## What to expect

**Step 1-4 are mechanical:** must pass for the change to be safe to deploy.

**Step 5 is the interesting one** — if MT5 close diverges from yfinance close by
> $5 in live mode, you'll see the warning. That tells you the calibration gap
between backtest data (GC=F) and execution data (XAUUSD) is meaningful.

**If divergence is consistently > $5–$10:** the recommendation is to schedule a
recalibration sprint:
  1. Export MT5 H1 history (2 years) via MT5 terminal → CSV
  2. Modify backtest_v2.py to accept CSV input as alternative to yfinance
  3. Re-run all backtest gates on MT5 XAUUSD data
  4. Compare to GC=F results. If significantly different, recalibrate thresholds.

This is deferred — only do it if the live divergence warnings actually fire.

---

## Bundle 1 deployment

After both `VERIFY_HTF.md` and `VERIFY_DATA_HYGIENE.md` pass:

```
git add -A
git commit -m "v12: HTF 4H bias filter + data source provenance + divergence guard"
git tag v12
git push  # if you push to remote

# in your terminal in D:\gold-trading
py -3.12 gold_trading_agents.py
```

If problems appear within 1–2 weeks of forward test:
```
git checkout v11.1
py -3.12 gold_trading_agents.py
```

Roll back is one command. That's the discipline.

---

## After deploy → Bundle 2

When forward test of v12 looks clean (2 weeks minimum), tell me. I'll deliver
Bundle 2: SELL strategy (gated bear regime) + Partial TP. Same pattern: code
from me, verification prompts for your Claude Code, you decide deploy.
