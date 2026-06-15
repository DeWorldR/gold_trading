# Pre-Deployment Checklist

Run this BEFORE every restart of the bot in live mode. 10 minutes, hard pass/fail.

---

## 1. Code integrity

```cmd
py -3.12 -m py_compile gold_trading_agents.py logger.py backtest_v2.py
```
- [ ] Output is silent (no errors)
- [ ] No truncated files (each file ends with valid Python)

If fails: STOP. Restore from `git checkout v12 -- <file>` and retry.

## 2. Git state

```cmd
git status
git log --oneline -3
git tag --list "v*"
```
- [ ] Working tree clean (no uncommitted changes)
- [ ] HEAD is on a tagged commit (v12 or later)
- [ ] `.env` is NOT in `git status` (would mean it leaked)

If fails: commit or stash before restart.

## 3. Configuration audit

```cmd
type .env
```
Verify:
- [ ] `MT5_LOGIN` = your real demo account number
- [ ] `MT5_PASSWORD` set
- [ ] `MT5_SERVER` matches XM exactly (e.g., `XMGlobal-MT5 2`)
- [ ] `MT5_SYMBOL` = `GOLD#` (XM uses GOLD#, not XAUUSD)
- [ ] `PAPER_TRADE` = `false` for live; `true` for safe mode
- [ ] `TELEGRAM_BOT_TOKEN` set (or accept no notifications)
- [ ] `TELEGRAM_CHAT_ID` set
- [ ] `RISK_PER_TRADE_PCT` = your chosen value (current: `0.5`)
- [ ] `DAILY_LOSS_LIMIT` = your chosen value (current: `150`)
- [ ] `HTF_BIAS_ENABLED` = `true`
- [ ] `BEAR_REGIME_ENABLED` = `false` (failed gate)
- [ ] `PARTIAL_TP_ENABLED` = `false` (failed gate)

If anything off: edit `.env`, save, return to step 1.

## 4. Journal integrity

```cmd
type trade_journal.json | findstr /C:"\"status\": \"OPEN\""
```
- [ ] No output (no orphan OPEN trades)

If there are OPEN trades:
- Check MT5 terminal: are these positions still really open?
- If yes: leave them — bot will manage on restart
- If no: bot will sync at startup via `sync_all_open_positions`, marking them closed from MT5 deal history

## 5. Pre-flight backup

```cmd
backup_journal.bat
```
- [ ] Output shows new file created in `snapshots\`
- [ ] Output prints "Total trades", "Closed trades", "Realized P&L" summary

Why: if bot writes bad data to journal, you have a clean rollback point. The script
also doubles as a quick health check on the journal state.

## 6. MT5 terminal check

Manually open MT5 terminal:
- [ ] Logged in to correct demo account
- [ ] GOLD# symbol visible in Market Watch
- [ ] Can place a manual order on GOLD# (Right-click → New Order, then cancel) — confirms tradeable
- [ ] Account balance ≈ what journal says (Config.ACCOUNT_SIZE + sum of closed P&L)
- [ ] No existing positions you forgot about

## 7. Telegram test (optional)

If Telegram configured:
```cmd
py -3.12 -c "from gold_trading_agents import ReporterAgent; r=ReporterAgent(); r.send_telegram('Pre-deployment test from bot')"
```
- [ ] Message arrives within 10 seconds

If not: check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.

## 8. Disk space + permissions

```cmd
dir D:\gold-trading | findstr free
```
- [ ] At least 500 MB free (logs grow ~50 MB/month)
- [ ] Can write to `cycle_log.jsonl`, `daily_summary.md`, `sessions\`, `gold_trading.log`

If permission errors: run cmd as Administrator OR fix folder permissions.

## 9. Final go/no-go

All 8 sections passed?
- [ ] **GO** → Start bot:
  ```cmd
  py -3.12 gold_trading_agents.py
  ```

Any failed?
- **NO-GO** → Fix issue, return to step 1.

---

## What to watch in first 15 minutes after start

1. `gold_trading.log` shows "Gold Trading System — LIVE TRADE" (not PAPER TRADE)
2. "MT5 connected" with correct balance
3. "MT5 symbol verified: GOLD#" with correct contract size (100)
4. First cycle runs, logs market data (close, ATR, regime, HTF)
5. No "ERROR" lines
6. Telegram receives heartbeat "No signal | RANGING | $..."

If any of these missing/wrong: stop the bot (`q` + Enter) and investigate.

---

## After restart confirmation

Update `CLAUDE.md` "Trade Phase" section with the date of restart.
Continue with normal observation (per TRADE_PHASE_PLAN.md).
