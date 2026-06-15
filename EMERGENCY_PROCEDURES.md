# Emergency Procedures

When something goes wrong in live trading. Read **before** anything goes wrong.

---

## Decision tree — what to do when

| Situation | First action | Then |
|---|---|---|
| Bot crashed, no positions open | Check log, restart | Investigate root cause |
| Bot crashed, 1 position open | Check MT5 manually | Decide manage manually or restart bot |
| Bot running but no signals for 5+ days in BULL HTF | Check block reasons | Possibly investigate filter chain |
| Single losing trade (in normal range) | Nothing — that's variance | Log it, move on |
| 2 consecutive losses today | Nothing — guard fires automatically | Bot pauses for the day |
| 3 consecutive losses on real money | Stop bot immediately | Review trades individually |
| 5% account drawdown | Stop bot | Decide: pause, revert, or quit |
| Unauthorized trade (no signal in log) | Stop bot, close position manually | Critical bug investigation |
| MT5 disconnect 1+ hour | Check MT5 terminal | If position open: monitor manually |
| Network failure, can't reach yfinance/MT5 | Bot logs warning, will retry | If persistent: stop bot |

---

## Procedure 1: Stop the bot

### Graceful stop (preferred)

In the bot's terminal window:
```
q
[Enter]
```

This:
- Calls `GoldTradingSystem.stop()`
- Writes final daily summary
- Saves session snapshot to `sessions/`
- Disconnects MT5 cleanly
- Exits

### Hard stop (when terminal unresponsive)

```cmd
Ctrl+C
```

If that fails:
- Find the python process: Task Manager → Details → python.exe
- End task

**After hard stop:**
- `cycle_log.jsonl` may have a partial line at the end → harmless, next start ignores it
- Open positions in MT5 stay open → managed manually until next bot start
- `trade_journal.json` may have positions marked OPEN — bot will sync on next start

---

## Procedure 2: Close a position manually

When bot is stopped and you need to close a real MT5 position:

1. Open MT5 terminal
2. Toolbox → Trade tab
3. Find the position (ticket = `mt5_ticket` in journal)
4. Right-click → Close Position
5. Confirm in dialog

**Update trade_journal.json manually:**
```json
"status": "CLOSED_LOSS"   // or CLOSED_WIN
"exit_price": <actual exit price>
"exit_timestamp": "YYYY-MM-DD HH:MM:SS"
"pnl": <actual P&L>
```

Or just leave it — on next bot start, `sync_all_open_positions` will reconcile from MT5 deal history.

---

## Procedure 3: Revert to v11 or v11.1

If v12 is showing problems and you want to roll back:

```cmd
cd D:\gold-trading

REM Find available tags
git tag --list "v*"

REM Stop current bot first (see Procedure 1)

REM Revert to specific version
git checkout v11.1

REM Verify
git log --oneline -1

REM Restart with old code
py -3.12 -m py_compile gold_trading_agents.py logger.py
py -3.12 gold_trading_agents.py
```

**Note on env vars:** Older versions don't recognize newer env vars. They'll be silently ignored. The bot just uses old logic.

**To go back to v12 later:**
```cmd
git checkout v12
```

---

## Procedure 4: Diagnose "no signals" (BULL HTF, no trades for days)

If bot is running but no trades for 5+ trading days in BULL HTF:

1. **Check daily_summary.md** for block reason distribution. Common culprits:
   - `BB_WIDTH(X<25.0)` dominant → market too range-bound; wait
   - `RSI_CEIL(X>=70)` dominant → market overbought (likely top); wait
   - `ADX(X<25)` dominant → trend weak; wait
   - `CONFLUENCE(BUY=2/3,SELL=0/3)` dominant → close to firing; wait

2. **Check cycle_log.jsonl tail** for actual indicator values:
   ```cmd
   powershell "Get-Content cycle_log.jsonl -Tail 20"
   ```
   Look for:
   - `htf_bias=BULL` confirmed
   - `regime` field
   - `rsi`, `adx`, `bb_width_pct` values

3. **If indicators look fine but no signal:**
   - Possibly DXY is UP → soft confluence penalty reducing eff_buy by 1
   - Check `gold_trading.log` for "DXY UP: BUY confluence reduced"

4. **If everything looks broken (zero block reasons, no cycles):**
   - Bot may have stopped logging → check process is alive
   - Check disk space (logs may fail if disk full)

---

## Procedure 5: Drawdown crisis (5%+ account loss)

If account drops 5%+ from peak:

1. **Stop bot immediately** (Procedure 1)
2. **Close all open positions manually** (Procedure 2)
3. **Compute realized vs unrealized loss:**
   - Realized: sum of CLOSED_LOSS entries in journal
   - Floating: MT5 terminal shows current equity
4. **Don't restart yet.** Take 24 hours away from charts.
5. **After 24h, review:**
   - Were losses individually within normal variance (each ≤ 1% of account)?
   - Or was there one catastrophic event (gap, spread spike, news)?
   - Did multiple filters fail to fire?
6. **Decision:**
   - Normal variance accumulated → continue, possibly reduce risk %
   - Single event slipped through → bug investigation; possibly revert
   - Multiple filter failures → recalibration sprint; possibly long pause

**Document in `INCIDENT_LOG_<date>.md`.**

---

## Procedure 6: MT5 connectivity issues

If `gold_trading.log` shows "MT5 fetch failed" or "MT5 connection failed":

1. **Check MT5 terminal is running** — should be visible in taskbar
2. **Check MT5 terminal is logged in** — Tools → Options → Server tab
3. **Try manual reconnect:** File → Login to Trade Account
4. **Check XM server status:** XM website or trader forum
5. **If persistent (> 30 min):**
   - Stop bot
   - Close MT5 terminal
   - Reopen MT5 terminal
   - Wait for "Connected" status in bottom-right
   - Restart bot

**Bot behavior during MT5 outage:**
- One Telegram alert: "WARNING — data source fallback"
- Subsequent cycles use yfinance fallback
- Trades are NOT placed (live mode requires MT5)
- Existing positions remain in MT5 — their SL/TP still active server-side

---

## Procedure 7: Telegram bot not delivering

1. Check `gold_trading.log` for "Telegram HTTP" errors
2. Common causes:
   - Bot token expired or revoked → regenerate via @BotFather
   - Chat ID changed → re-get via @userinfobot
   - Rate limited → wait 1 minute
3. Update `.env` if credentials changed
4. Restart bot

Bot continues trading without Telegram. Just no notifications.

---

## Procedure 8: Trade journal corruption

If `trade_journal.json` is corrupted (invalid JSON, missing fields):

1. **Stop bot**
2. **Check backups:**
   ```cmd
   dir snapshots\trade_journal_*.json
   ```
3. **Validate latest backup:**
   ```cmd
   py -3.12 -c "import json; print(len(json.load(open('snapshots\\trade_journal_phase_start_20260614.json'))))"
   ```
4. **Restore from backup:**
   ```cmd
   copy snapshots\trade_journal_phase_start_20260614.json trade_journal.json
   ```
5. **Run MT5 reconciliation:**
   - Restart bot — on startup, `sync_all_open_positions` will rebuild state from MT5 history
6. **Lose any trades between backup and corruption?** Yes, but rebuild manually if you remember them.

---

## Procedure 9: Power outage / system crash

Unplanned shutdown while bot was running:

1. **Boot back up. Open MT5 terminal first.**
2. **Check MT5 positions tab** — any open positions?
3. **Open trade_journal.json** — any OPEN entries?
4. **Cross-reference:**
   - MT5 open + journal OPEN → match: bot will resume normally
   - MT5 closed + journal OPEN → SL/TP triggered while offline: bot's `sync_all_open_positions` reconciles on next start
   - MT5 open + journal CLOSED (rare) → manual investigation: maybe close MT5 position or update journal
5. **Run PRE_DEPLOYMENT_CHECKLIST.md**
6. **Restart bot**

The v6 `sync_all_open_positions` is designed exactly for this scenario.

---

## Procedure 10: "I want to quit / take a break"

Take a break. The system was designed for this.

1. **Stop bot gracefully** (Procedure 1)
2. **Close all open positions** (Procedure 2) — or leave them with SL/TP active in MT5
3. **No harm in pausing 1 week or 1 month**
4. **When you come back:**
   - Run PRE_DEPLOYMENT_CHECKLIST.md
   - Restart bot
   - Continue from the milestone you were at

The system will be there. Markets will be there. Your discipline matters more than continuous uptime.

---

## What NOT to do in emergency

- **Don't** flip kill switches without reading the failure reports first
- **Don't** "fix" the bug at 2 AM — sleep first
- **Don't** override SL/TP in MT5 to "give it room"
- **Don't** add to a losing position
- **Don't** trade manually on the same account while bot is running (race conditions)
- **Don't** assume backtest behavior in extreme conditions (news, gaps, halts)
- **Don't** silence Telegram alerts to feel less stressed — the alerts are how you survive

---

## When to call for help

Ask Cowork Claude (or human trader friend) when:
- A specific failure mode you can't diagnose from logs
- A decision point you're emotionally biased about
- A code investigation requiring fresh eyes

Don't ask Cowork Claude when:
- You're emotional and want to be told what to do
- You want validation for a bad decision
- It's been less than 24 hours since the incident
