# Data Hygiene Verification Result — v12

**Date:** 2026-05-31  
**Branch:** v12 (HTF + Data Hygiene combined)  
**Verifier:** automated smoke test

---

## Step 1 — Syntax compile

```
py -3.12 -m py_compile gold_trading_agents.py logger.py backtest_v2.py
```

**Result: PASS** — silent (no errors or warnings).

---

## Step 2 — Paper-mode smoke test (1 cycle)

```
OrchestratorAgent() → run_cycle()
```

**Result: PASS**

Key log lines observed:
```
HTF[4h]: close=4593.00 EMA50=4535.99 slope=+9.73 → BULL
close=4593.00 ATR=9.84 (0.21%) regime=TRENDING_UP DXY=NEUTRAL HTF=BULL src=yfinance
Blocked: BB_WIDTH(20<25.0) | close=4593.00 ATR=9.84 BUY=- SELL=-
OK: one cycle completed
```

- `src=yfinance` present in market state log line — data source tracking working.
- HTF bias computed and logged as `BULL`.
- BB_WIDTH filter blocked signal normally (market condition, not a fault).
- Pre-existing Windows `cp1252` console encoding warnings for `→` (U+2192) are a known
  issue (v4 CLAUDE.md entry), not a regression from this change.

---

## Step 3 — cycle_log.jsonl schema check

```python
assert 'data_source' in entry
print(f'data_source = {entry["data_source"]}')
print(f'htf_bias    = {entry.get("htf_bias")}')
```

**Result: PASS**

```
data_source = yfinance
htf_bias    = BULL
OK: cycle_log schema updated
```

Both new fields present in JSONL output.

---

## Step 4 — 2-year backtest delta (--be --htf)

Compared output to the saved `backtest_v2_results_be_htf.json` from prior HTF validation.

| Metric | HTF_VALIDATION.md baseline | This run | Delta |
|--------|---------------------------|----------|-------|
| Total trades | 106 | 106 | 0 |
| Win rate | 67.0% | 67.0% | 0 |
| Net P&L | $+7,507 | $+7,506.93 | $−0.07 |
| Profit factor | 2.63 | 2.63 | 0 |
| Max drawdown | 5.1% | 5.1% | 0 |
| Sharpe | 2.83 | 2.83 | 0 |
| Val Sharpe (OOS) | 5.90 | 5.90 | 0 |
| Train Sharpe | 7.13 | 7.13 | 0 |

**Result: PASS** — $0.07 delta is floating-point noise (< $1 threshold). Backtest is
deterministic and unaffected by the data hygiene additions.

---

## Step 5 — Live MT5 dry run (optional)

MT5 installed and `mt5.initialize()` returns `True` — terminal is running and reachable.

Live one-cycle run was **not executed** per the out-of-scope boundary ("let user start the
actual live run"). The user should:

```bash
py -3.12 gold_trading_agents.py
# Press Ctrl-C after first cycle completes and check gold_trading.log for:
#   "Using MT5 data (500 bars)"
#   "close=... regime=... HTF=... src=mt5"
#   DATA DIVERGENCE warning if |MT5 − yfinance| > $5
```

---

## Decision

**All automated steps PASS.**

| Step | Result |
|------|--------|
| 1 — Syntax compile | PASS |
| 2 — Paper smoke test | PASS |
| 3 — cycle_log schema | PASS |
| 4 — Backtest delta | PASS ($0.07) |
| 5 — Live MT5 | SKIPPED (terminal ready; user to start) |

**v12 = HTF + Data Hygiene is ready to deploy as a combined tag.**

Recommended next action:
1. User starts live run: `py -3.12 gold_trading_agents.py`
2. Confirm `src=mt5` appears in the first full-cycle log line.
3. Forward test for 2 weeks before any further signal-logic changes.
