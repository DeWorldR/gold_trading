# Claude Code Prompts — สำหรับ implement TRADER_REVIEW.md

ใช้คู่กับ `TRADER_REVIEW.md`. ทุก prompt ออกแบบให้ paste ลง Claude Code ใน folder `D:\gold-trading` ได้เลย.

---

## หลักการที่ใช้ทุก prompt

ระบบนี้มี convention ชัดเจนใน CLAUDE.md — Claude Code จะอ่านอัตโนมัติ. ดังนั้น prompt ไม่ต้องอธิบาย codebase ซ้ำ. แต่ต้องระบุ 5 อย่างเสมอ:

1. **Goal** — ทำอะไร, ทำไม (อ้าง TRADER_REVIEW.md section)
2. **Files to change** — ระบุไฟล์/function เป๊ะ ไม่ให้ค้นเอง
3. **Acceptance criteria** — backtest gate ตัวเลข
4. **Out of scope** — บอกชัดว่า "อย่าทำอะไร" (กัน scope creep)
5. **Deliverables** — code change + report file + CLAUDE.md update + version bump

**Backtest gate มาตรฐาน (ใช้ทุก change):**
- Sharpe ≥ 2.5 (daily-equity)
- MaxDD ≤ 7%
- Trade count drop ≤ 20% เทียบ v10.1 baseline (102 trades, Sharpe 2.68)
- Walk-forward 70/30: val Sharpe ≥ 50% ของ train Sharpe

ถ้าไม่ผ่าน → revert + เขียน report ว่าทำไมไม่ผ่าน, อย่า merge.

---

## SPRINT 1 — P0 fixes (deploy ภายในสุดสัปดาห์)

### Prompt 1.1 — Live MT5 balance for sizing

```
Read CLAUDE.md and TRADER_REVIEW.md section "P0 #5".

Goal: Replace hardcoded Config.ACCOUNT_SIZE = 10_000 with live MT5
account balance fetched per cycle. Falls back to 10_000 when PAPER_TRADE
or MT5 disconnected.

Files to change:
- gold_trading_agents.py:
  - Add MT5Broker.get_balance() -> float (returns mt5.account_info().balance,
    or 0.0 on failure)
  - Modify RiskManagerAgent.run() to accept optional `account_size` parameter
    instead of reading Config.ACCOUNT_SIZE directly. Default = Config.ACCOUNT_SIZE.
  - In OrchestratorAgent._do_cycle(), before risk_manager.run(), call
    broker.get_balance() when live, else fall back to Config.ACCOUNT_SIZE.
    Pass into risk_manager.run().
  - All daily_loss_limit / max_risk calculations now use the dynamic size.

Acceptance:
- Paper trade mode: behavior unchanged (uses 10_000).
- Live mode: lot_size scales with actual MT5 balance.
- Add unit-style test in a one-off script test_balance_sizing.py that mocks
  three balance values (8000, 10000, 12000) and verifies risk_amount and
  lot_size scale linearly.
- backtest_v2.py untouched (backtests always use 10_000).

Out of scope:
- Don't change DAILY_LOSS_LIMIT or MAX_RISK_PCT calibration.
- Don't add equity-based sizing (only balance, for now).

Deliverables:
- Code change with the test script
- Append section "v11 — <date>" to CLAUDE.md Dev Log with diff summary
- No new .md report needed (small change)
```

### Prompt 1.2 — Spread gate

```
Read CLAUDE.md and TRADER_REVIEW.md section "P0 #3".

Goal: Block trade execution when current MT5 spread > Config.MAX_SPREAD_USD.
The bot already calls broker.get_current_spread() and logs it but never
uses it as a gate.

Files to change:
- gold_trading_agents.py:
  - Add Config.MAX_SPREAD_USD: float = float(os.getenv("MAX_SPREAD_USD", "0.60"))
    with comment: "XAUUSD typical 0.25-0.40; block if > 0.60 (news/illiquid)"
  - In OrchestratorAgent._do_cycle(), after risk approval but BEFORE _open_trade,
    check self._last_spread. If > Config.MAX_SPREAD_USD: log block, send
    Telegram alert, increment skip_reason "SPREAD_TOO_WIDE", return.
  - Add "SPREAD_TOO_WIDE" to the list of skip_reason values logged.
- logger.py: no change (skip_reason field already exists).

Acceptance:
- Paper mode: spread = 0.0, gate never fires (no behavior change).
- Live mode with synthetic test: set _last_spread = 1.50 in code, run cycle,
  verify trade blocked and cycle log has skip_reason="SPREAD_TOO_WIDE".
- Verify spread = 0.55 still passes (under threshold).

Out of scope:
- Don't change spread cost modeling in backtest_v2.py.
- Don't fetch live tick spread on yfinance fallback.

Deliverables:
- Code change
- Append "v11 — <date>" or extend existing v11 entry in CLAUDE.md Dev Log
```

### Prompt 1.3 — Friday afternoon cutoff

```
Read CLAUDE.md and TRADER_REVIEW.md section "P0 #4".

Goal: Block NEW trade entries after Friday 17:00 UTC to avoid weekend gap
risk. Existing open positions are still managed (SL/TP checks continue) —
only NEW entries are blocked.

Files to change:
- gold_trading_agents.py:
  - Add Config.FRIDAY_CUTOFF_HOUR_UTC: int = int(os.getenv("FRIDAY_CUTOFF_HOUR_UTC", "17"))
  - In OrchestratorAgent._do_cycle(), after session filter check, add:
    - If weekday == 4 (Friday) and hour >= Config.FRIDAY_CUTOFF_HOUR_UTC:
      skip_reason = "FRIDAY_CUTOFF", log, return.
    - Place this check AFTER _check_open_positions() so existing trades
      still get managed.

Acceptance:
- Friday 16:45 UTC: cycle runs normally.
- Friday 17:00 UTC: skip_reason="FRIDAY_CUTOFF", no new trade.
- Friday 17:00 UTC with open position: _check_open_positions still runs (verify
  by reading the cycle log entry — open_positions field should reflect the live count).
- Monday–Thursday: gate never fires.

Out of scope:
- Don't reduce session window on other days.
- Don't force-close open positions at Friday cutoff (separate decision).

Deliverables:
- Code change
- 1-paragraph note in CLAUDE.md v11 entry
```

### Prompt 1.4 — Breakeven move at +1R (the big one)

```
Read CLAUDE.md, TRADER_REVIEW.md sections "TL;DR #1" and "P0 #1", and
TRADE4_ANALYSIS.md.

Goal: When floating P&L reaches +1R (price has traveled stop_distance in
favor of the trade), modify SL to entry + small cushion (BUY) or entry -
cushion (SELL). This converts "winners that became losers" into
breakeven exits.

Files to change:
- gold_trading_agents.py:
  - Add Config:
    - BE_TRIGGER_R: float = 1.0  # move to BE when price reaches +1R
    - BE_CUSHION_USD: float = 0.50  # entry + 0.50 to cover spread/commission
  - Add MT5Broker.modify_sl(ticket: int, new_sl: float) -> bool — calls
    mt5.order_send with TRADE_ACTION_SLTP. Return True on success.
  - Add TradeRecord field: be_moved: bool = False
  - In OrchestratorAgent._check_open_positions(), for each OPEN trade:
    - Compute current price from latest bar close (yfinance) or
      mt5.symbol_info_tick(symbol).bid (live)
    - stop_distance = abs(trade.entry - trade.stop_loss)
    - For BUY: trigger_price = trade.entry + stop_distance * Config.BE_TRIGGER_R
      If current >= trigger_price AND not trade.be_moved:
        new_sl = trade.entry + Config.BE_CUSHION_USD
        if new_sl > trade.stop_loss:
          live: broker.modify_sl(ticket, new_sl); update journal
          paper: journal.update(stop_loss=new_sl, be_moved=True)
    - Mirror logic for SELL (subtract).
  - Update _paper_simulate to respect the modified stop_loss (since it
    reads trade.stop_loss directly, this should already work after journal update).
- backtest_v2.py:
  - Add BE_TRIGGER_R = 1.0 and BE_CUSHION_USD = 0.50 constants
  - In the per-bar loop where open_trade is checked, before SL/TP check, add
    same logic: if intra-bar high reaches +1R, set stop = entry + cushion for
    the remainder of the trade.

Acceptance — CRITICAL:
- Run backtest_v2.py --period 2y WITH and WITHOUT this change.
- Required: net P&L delta vs v10.1 baseline (102 trades, Sharpe 2.68, +$7,608) within:
  - Sharpe ≥ 2.5 (relax slightly — BE move trades small wins for fewer big losses)
  - MaxDD ≤ 6% (must IMPROVE — that's the whole point)
  - WR should INCREASE by 3–10pp
  - Net P&L acceptable range: 80–120% of baseline ($6,000–$9,100)
- If MaxDD does not improve → revert + investigate.
- Trade #4 simulation: with this fix, what would the outcome have been?
  Document in BE_VALIDATION.md.

Out of scope:
- Do NOT add trailing stop in this prompt (separate, after BE proves out).
- Do NOT add partial TP in this prompt (next prompt).
- Backtest spread cost should INCLUDE 2× spread on BE-exited trades (entry + exit).

Deliverables:
- gold_trading_agents.py + backtest_v2.py changes
- BE_VALIDATION.md report with: backtest table (before/after), per-quarter
  breakdown, Trade #4 simulation, deploy verdict
- backtest_v2_results_be.json
- CLAUDE.md "v11 — Breakeven move" section
- If any criterion fails: do NOT modify production. Write a FAILURE report
  in BE_VALIDATION.md and stop.
```

---

## SPRINT 2 — P1 edge improvements

### Prompt 2.1 — Partial TP (TP1 50% at +1R, TP2 ride to +3R)

```
Read CLAUDE.md, TRADER_REVIEW.md "P0 #2", and BE_VALIDATION.md.

PREREQUISITE: Sprint 1.4 (breakeven move) must be deployed and verified
in production for at least 2 weeks. If not, STOP and ask user.

Goal: Split position into two halves at entry. When +1R reached, close 50%
at TP1 (price = entry + 1×stop_distance) and let remainder ride to TP2
(price = entry + 3×stop_distance). SL on remainder is already at BE from Sprint 1.4.

This trades absolute net P&L for Sharpe/MaxDD improvement (lower drawdown,
smoother equity curve).

Files to change:
- gold_trading_agents.py:
  - TradeRecord: add fields
    - tp1_price: float = 0.0
    - tp1_hit: bool = False
    - partial_closed_lot: float = 0.0
    - remainder_tp: float = 0.0  # +3R target for remainder
  - Modify _open_trade: compute tp1_price = entry + 1×stop_distance,
    remainder_tp = entry + 3×stop_distance. Store both.
  - In live execution, MT5 doesn't support partial TPs directly — emulate by:
    placing the full order with TP = tp1_price initially, then on tp1 hit,
    re-open the remainder lot with TP = remainder_tp. (Discuss alternative
    of placing TWO MT5 orders at entry: 50% lot with TP1, 50% lot with TP2 —
    cleaner, no race condition.)
  - Recommended: TWO MT5 orders at entry, both with same SL. Easier to manage.
    - Update _open_trade to call broker.send_order twice with lot/2 each.
    - Track both tickets in TradeRecord (mt5_ticket = ticket1, mt5_ticket2 = ticket2)
    - _check_mt5_position iterates both.
  - Update _paper_simulate accordingly.
- backtest_v2.py:
  - Apply same logic. On +1R touch, close 50% lot, move SL to BE on remainder,
    new TP = +3R.

Acceptance:
- Backtest 2y vs v11 (BE move) baseline:
  - Sharpe ≥ baseline × 0.95 (slight drop acceptable; BE+partial trades absolute P&L for stability)
  - MaxDD ≤ baseline × 0.85 (must improve 15%+ — the whole point)
  - WR INCREASES because TP1 fires often
  - Profit factor may DROP (smaller avg win) — OK if Sharpe holds
- Per-quarter: each quarter should show smoother equity (less variance in monthly P&L).
- Per-trade analysis: of the trades that had floating profit > +1R then reversed,
  how many now exit as TP1+BE vs full loss?

Out of scope:
- Don't add trailing stop on remainder yet.
- Don't change MIN_RR (TP1 is now the de-facto risk:reward).

Deliverables:
- Code changes (production + backtest)
- PARTIAL_TP_VALIDATION.md with table, per-quarter equity smoothness,
  side-by-side equity curve plots if possible
- backtest_v2_results_partial.json
- CLAUDE.md "v12 — Partial TP" section
```

### Prompt 2.2 — Higher-timeframe trend filter (4H EMA50)

```
Read CLAUDE.md, TRADER_REVIEW.md "P1 #6", TRADE4_ANALYSIS.md,
SLOPE_LOOKBACK_TEST.md, and the "Known Limitations — regime-transition"
section of CLAUDE.md.

Goal: Add a Higher Timeframe (4H) trend bias filter. Block 15m BUY entries
when 4H trend is not bullish. This addresses the root cause of Trade #4
(15m EMA200 lag during macro regime transition).

Why this might work where the 15m EMA200 slope filter (v8) failed:
- 4H EMA50 = 200 hours of context (matches what 15m EMA200 *should* capture
  but doesn't due to bar-count lag)
- Separate timeframe data fetch — no aliasing with 15m signals
- Captures macro regime, not intraday wobble

Files to change:
- gold_trading_agents.py:
  - Add MarketAnalystAgent._get_htf_bias() -> str
    Returns "BULL" / "BEAR" / "NEUTRAL"
    Logic:
      df_4h = yf.Ticker(SYMBOL).history(period="60d", interval="4h")
      (or MT5 copy_rates_from_pos with TIMEFRAME_H4 in live mode)
      ema50 = EMA(close, 50)
      slope = ema50[-1] - ema50[-5]  # 20h lookback
      if slope > 0 and close > ema50: BULL
      elif slope < 0 and close < ema50: BEAR
      else: NEUTRAL
    Cache result for the cycle (don't re-fetch within same _do_cycle).
  - Add MarketState.htf_bias: str = "NEUTRAL"
  - Add Config.HTF_BIAS_ENABLED: bool = True (kill switch for A/B test)
  - In TechnicalAnalystAgent.run(), AFTER signal direction decided but BEFORE
    pattern naming:
      if Config.HTF_BIAS_ENABLED:
        if signal.direction == "BUY" and state.htf_bias != "BULL":
          block_reasons.append(f"HTF_BIAS({state.htf_bias})")
          return NONE-signal
        if signal.direction == "SELL" and state.htf_bias != "BEAR":
          block_reasons.append(f"HTF_BIAS({state.htf_bias})")
          return NONE-signal
  - Add htf_bias to cycle_log entry (logger.py — extend _build_cycle_entry)
- backtest_v2.py:
  - Pre-compute 4H EMA50 trend bias from a parallel 4H download for the
    same 2y window. Index by date, lookup per 1H bar.
  - Apply gate identically.

Acceptance:
- Backtest 2y:
  - Sharpe ≥ 2.5
  - MaxDD ≤ 7%
  - Trade count drop ≤ 25% (this filter is more aggressive — accept higher drop)
  - WR should INCREASE 3–7pp (filter removes false-trend entries)
- Walk-forward 70/30: val Sharpe ≥ 60% of train Sharpe
- Trade #4 simulation: confirm 4H bias was "BEAR" or "NEUTRAL" at entry → blocked.
- Q2 2026 drawdown period: how many of the 4 losses are now blocked?

Out of scope:
- Don't change 15m EMA200 gate (keep both — defense in depth).
- Don't add daily-TF filter (too slow — once filter is enough).
- Don't remove RSI ceiling or ADX filter.

Deliverables:
- Code changes (production + backtest)
- HTF_FILTER_VALIDATION.md with:
  - 2y backtest table
  - Trade #4 reconstruction
  - Q2 2026 drawdown reconstruction
  - Per-quarter breakdown
  - Bias distribution (% of bars BULL vs BEAR vs NEUTRAL)
  - Deploy verdict
- backtest_v2_results_htf.json
- CLAUDE.md "v13 — HTF bias filter" section
- A/B test plan: deploy with Config.HTF_BIAS_ENABLED=True for 1 month,
  monitor block_reasons stats vs baseline.
```

### Prompt 2.3 — Session-of-day refinement

```
Read CLAUDE.md and TRADER_REVIEW.md "P1 #7".

Goal: Replace the flat 08:00-21:00 UTC window with session-aware pattern
restrictions:
- 08:00-10:00 UTC (London open): all patterns OK
- 10:00-13:00 UTC (London midday): only BB_RSI patterns
- 13:00-17:00 UTC (LDN-NY overlap): all patterns + boost confluence weight
- 17:00-21:00 UTC (NY PM): only BB_RSI patterns, no EMA_MACD_TREND

Files to change:
- gold_trading_agents.py:
  - Add Config dict:
    SESSION_PATTERN_RULES = {
      (8, 10):  {"allowed": ["TRIPLE_SIGNAL", "EMA_MACD_TREND", "BB_RSI", "RSI_EMA"]},
      (10, 13): {"allowed": ["BB_RSI"]},
      (13, 17): {"allowed": ["TRIPLE_SIGNAL", "EMA_MACD_TREND", "BB_RSI", "RSI_EMA"]},
      (17, 21): {"allowed": ["BB_RSI"]},
    }
  - In TechnicalAnalystAgent.run(), after pattern naming but before approve,
    check current hour UTC → find matching session window → if pattern
    family not in "allowed", block with reason "SESSION_PATTERN_MISMATCH".
- backtest_v2.py: apply same filter.

Acceptance:
- Backtest 2y:
  - Sharpe ≥ 2.5
  - MaxDD ≤ 7%
  - Trade count drop ≤ 30% (this is restrictive)
  - WR should INCREASE 2–5pp
- Hour-of-day P&L table: each session bucket shows positive expectancy.

Out of scope:
- Don't change SESSION_START_UTC / SESSION_END_UTC constants.
- Don't add Asia session (08-21 only).

Deliverables:
- Code + backtest changes
- SESSION_VALIDATION.md with hour-of-day P&L heatmap (text table OK)
- CLAUDE.md v14 entry
```

---

## SPRINT 3 — SELL strategy for confirmed bear regime

### Prompt 3.1 — Bear-regime SELL strategy (separate, gated)

```
Read CLAUDE.md, TRADER_REVIEW.md "P1 #10", SELL_DEVELOPMENT_REPORT.md,
SELL_VALIDATION_REPORT.md.

CONTEXT: Two prior SELL research cycles concluded "STAY BUY-ONLY" because
mirror SELL didn't work in BUY-friendly periods. This prompt is NOT a third
attempt at the same approach. The goal is a SEPARATE, REGIME-GATED SELL
sub-strategy that only fires in confirmed bear regimes.

Goal: Implement a SHORT strategy that activates ONLY when daily close is
below daily EMA200 for ≥ 5 consecutive days AND 4H EMA50 is sloping down
for ≥ 20 bars. In bull regimes (current 2024-2026 data is 97% bull) this
strategy fires zero trades and cannot harm existing Sharpe.

Entry logic (only when bear-regime active):
- 15m close rejects 4H resistance (price made lower-high in last 6h)
- RSI(14) > 55 (overbought relative to bear context)
- Close < EMA20 < EMA50
- ATR within normal range (not VOLATILE)
- BB position > 70%
- Required confluence: 4/5 (stricter than BUY)

Files to change:
- gold_trading_agents.py:
  - Add Config.BEAR_REGIME_ENABLED: bool = True
  - Add MarketState.bear_regime_active: bool = False
  - MarketAnalystAgent: add _check_bear_regime() returning bool
    (daily fetch — cache hourly to avoid yfinance hammering)
  - TechnicalAnalystAgent: add _detect_bear_setup(df, state) that runs ONLY
    when state.bear_regime_active. Returns a TechnicalSignal or NONE.
  - In TechnicalAnalystAgent.run(), if buy_n < 3 and sell_n < 3 (no BUY signal)
    and state.bear_regime_active: try _detect_bear_setup as a separate path.
- backtest_v2.py:
  - Replay 2y with new bear strategy. Confirm: zero trades in 2024-2026
    (no bear regime triggered).
  - Then backtest on 2011-2015 (bear period) data — full 4y, daily bars OK.
    Compare WR/PF/Sharpe vs random short baseline (Monte Carlo 200 runs).
- Add backtest_bear_strategy.py if not present.

Acceptance — BEAR PERIOD (2011-2015) backtest:
- WR ≥ random + 8pp (must beat random by clear margin)
- PF ≥ 1.4
- Sharpe ≥ 1.0
- ≥ 20 trades per year (or strategy is too sparse to deploy)
- Worst quarter ≤ -3% of account

Acceptance — BULL PERIOD (2024-2026) backtest:
- Trade count = 0 (regime gate works correctly)
- No change to existing BUY metrics

Out of scope:
- Don't reactivate DISABLED_PATTERNS BB_RSI_REVERSAL_SELL / EMA_MACD_TREND_SELL
  (those are mirror-SELL, this is structural-SELL — different code path)
- Don't change BUY logic at all.

Deliverables:
- New SHORT-strategy code path (separate from BUY signals)
- BEAR_STRATEGY_VALIDATION.md with:
  - 2011-2015 backtest table
  - 2024-2026 confirmation (zero trades)
  - Random baseline Monte Carlo
  - Regime activation timeline
  - Deploy verdict
- backtest_results_bear_2011_2015.json
- backtest_v2_results_with_bear.json (2y)
- CLAUDE.md v15 entry

If ANY 2011-2015 criterion fails: DO NOT merge. Bear strategy stays disabled.
This is research, not auto-deploy.
```

---

## Verification prompt template (reuse after every Sprint deploy)

```
Read CLAUDE.md.

Goal: Verify the latest production change (v<N>) is behavior-identical
to the backtest claim. This is a regression check, not new development.

Steps:
1. Re-run backtest_v2.py --period 2y on current HEAD.
2. Compare Sharpe / WR / trade_count / MaxDD against the values claimed
   in CLAUDE.md v<N> entry.
3. Tolerance: Sharpe ±0.05, WR ±1pp, trade_count exact, MaxDD ±0.3pp.
4. If any metric is outside tolerance: investigate divergence. Likely
   causes: yfinance retroactive data revisions (futures contract roll),
   pandas/pandas_ta version drift, lookahead bias introduced.
5. Re-run on a single fixed train/val split (70/30) and confirm both
   numbers still match SLOPE_LOOKBACK_TEST.md "data freshness" notes.

Deliverables:
- VERIFICATION_v<N>.md report with diff table and verdict.
- If divergence > tolerance: open issue in CLAUDE.md "Known Limitations",
  do not silently re-baseline.
```

---

## เคล็ดลับเขียน prompt ให้ Claude Code ทำตามเป๊ะ

1. **อ้าง CLAUDE.md เสมอใน prompt แรก** — Claude Code อ่านอัตโนมัติ, แต่การพูดถึงจะ pin ความสนใจให้ follow convention (versioning, dev log, file naming).

2. **บังคับ acceptance criteria เป็นตัวเลข ไม่ใช่คำอธิบาย** — "Sharpe ≥ 2.5" ไม่ใช่ "Sharpe ดี". Claude Code จะ verify ตามตัวเลขได้ตรง.

3. **เขียน "Out of scope" ทุกครั้ง** — ป้องกัน Claude Code ทำเกิน. ระบบนี้มีของให้แตะเยอะ มันจะอยากแก้ไปด้วย.

4. **แยก prompt ต่อ change, ไม่รวม** — แต่ละ change ต้องมี backtest ของตัวเอง. รวมกัน 2 เรื่อง → backtest แยกผลกระทบไม่ได้.

5. **บังคับเขียน .md report** — ระบบนี้มี report-driven dev cycle (ดู FINDINGS.md, FIX_VALIDATION_REPORT.md, etc.). ใช้ pattern เดิม.

6. **บอกว่า "ถ้าไม่ผ่าน gate ห้าม merge"** — ตรง. หลายครั้ง Claude Code จะ deploy ที่ "ใกล้เคียงพอ" ถ้าไม่ห้าม.

7. **PREREQUISITE clause** — ใช้ใน prompt 2.1 (partial TP ต้องรอ BE prove). ป้องกัน implement out of order.

8. **ระบุ "kill switch"** — เช่น `HTF_BIAS_ENABLED = True`. Pro change ต้องมี toggle ปิดได้ใน 5 วินาที, ไม่ต้อง redeploy.

---

## ลำดับ paste

```
Sprint 1 (วันนี้–อาทิตย์):
  Day 1: paste 1.1 → wait → 1.2 → wait → 1.3
  Day 2: paste 1.4 (BE move) → wait → review BE_VALIDATION.md ก่อน accept

Sprint 2 (สัปดาห์หน้า ขั้นต่ำ 2 อาทิตย์หลัง BE deploy):
  Day 8: paste 2.2 (HTF — ใหญ่ที่สุด)
  Day 10: review → paste 2.3 (session)
  Day 14: paste 2.1 (partial TP)

Sprint 3 (เดือนหน้า):
  Day 21+: paste 3.1 (bear strategy — research, ไม่ผ่าน gate ก็ปล่อย)

After every Sprint:
  paste verification prompt → ได้ VERIFICATION_v<N>.md
```

---

## ถ้าจะ paste prompt เดียวให้ครบ Sprint 1

```
Read CLAUDE.md and TRADER_REVIEW.md.

I'm implementing Sprint 1 from CLAUDE_CODE_PROMPTS.md. Execute prompts
1.1 (live balance), 1.2 (spread gate), 1.3 (Friday cutoff), 1.4
(breakeven move) IN ORDER. After each prompt:
- Run backtest_v2.py --period 2y
- Update the version log in CLAUDE.md
- Wait for me to type "next" before proceeding to the next prompt

Do NOT batch them. Do NOT skip the backtest between steps. If any
acceptance criterion fails on a step, STOP and write a failure report.

Start with prompt 1.1.
```

นี่คือ pattern ที่ทำให้คุณ control flow ได้ระดับ change-by-change.
