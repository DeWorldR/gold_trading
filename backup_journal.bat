@echo off
REM Backup trade_journal.json with date-stamped filename to snapshots/
REM Run before any restart, after milestones, or weekly.

cd /d D:\gold-trading

if not exist snapshots mkdir snapshots

REM Date format: YYYYMMDD
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set "dt=%%a"
set "DATESTAMP=%dt:~0,8%"
set "TIMESTAMP=%dt:~8,6%"

set "DEST=snapshots\trade_journal_%DATESTAMP%_%TIMESTAMP%.json"

if not exist trade_journal.json (
    echo ERROR: trade_journal.json not found in current directory
    exit /b 1
)

copy trade_journal.json "%DEST%" > nul
if errorlevel 1 (
    echo ERROR: copy failed
    exit /b 1
)

echo Journal backed up to: %DEST%

REM Print summary
py -3.12 -c "import json; j=json.load(open('trade_journal.json')); print(f'Total trades: {len(j)}'); opens=[t for t in j if t.get('status')=='OPEN']; print(f'Open positions: {len(opens)}'); closed=[t for t in j if t.get('status','').startswith('CLOSED')]; print(f'Closed trades: {len(closed)}'); pnl=sum(t.get('pnl',0) for t in closed); print(f'Realized P&L: ${pnl:+.2f}')"
