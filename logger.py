"""
3-layer observability logging for the Gold Trading Forward Test.
All writes are wrapped in try/except — a failure never blocks the trading cycle.

Layer 1: cycle_log.jsonl  — one JSON line per cycle (append-only)
Layer 2: daily_summary.md — Markdown aggregate per day (midnight + shutdown)
Layer 3: sessions/        — full JSON snapshot on graceful shutdown
"""

import json
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

CYCLE_LOG_FILE = Path("cycle_log.jsonl")
DAILY_SUMMARY_FILE = Path("daily_summary.md")
SESSIONS_DIR = Path("sessions")

_VERSION_TAG = "v12 | ATR×2.5 | RSI≤70 | ADX≥25 | MAX=1 | BE+1R | HTF-4H | spread≤0.60 | Fri-cut 17z"
_FORWARD_TEST_START = date(2026, 5, 21)


# ── Layer 1 ───────────────────────────────────────────────────────────────────

def log_cycle(entry: Dict[str, Any]) -> None:
    """Append one compact JSON line to cycle_log.jsonl. Never raises."""
    try:
        with CYCLE_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except OSError as exc:
        print(f"[logging] cycle_log write failed: {exc}", file=sys.stderr)
    except Exception as exc:
        print(f"[logging] cycle_log unexpected error: {exc}", file=sys.stderr)


# ── Layer 2 ───────────────────────────────────────────────────────────────────

def write_daily_summary(today_str: Optional[str] = None) -> bool:
    """
    Aggregate today's cycle entries and append a Markdown section to daily_summary.md.
    Returns True if a new section was written, False if today's entry already exists.
    Never raises.
    """
    try:
        if today_str is None:
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if DAILY_SUMMARY_FILE.exists():
            existing = DAILY_SUMMARY_FILE.read_text(encoding="utf-8")
            if f"— {today_str}" in existing:
                return False

        entries = _read_today_entries(today_str)
        section = _format_day_section(today_str, entries) if entries else _format_empty_day(today_str)

        with DAILY_SUMMARY_FILE.open("a", encoding="utf-8") as f:
            f.write(section)
        return True
    except Exception as exc:
        print(f"[logging] daily_summary write failed: {exc}", file=sys.stderr)
        return False


# ── Layer 3 ───────────────────────────────────────────────────────────────────

def save_session_snapshot(stats: Dict[str, Any], state: Dict[str, Any]) -> None:
    """Write sessions/session_YYYYMMDD_HHMM.json. Never raises."""
    try:
        SESSIONS_DIR.mkdir(exist_ok=True)
        start_str = stats.get(
            "session_start",
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        try:
            start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        except Exception:
            start_dt = datetime.now(timezone.utc)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)

        end_dt = datetime.now(timezone.utc)
        duration_min = max(
            0,
            int((end_dt - start_dt.astimezone(timezone.utc)).total_seconds() / 60),
        )

        fname = f"session_{start_dt.strftime('%Y%m%d_%H%M')}.json"
        snapshot = {
            "session_start": start_str,
            "session_end": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration_minutes": duration_min,
            "config": stats.get("config", {}),
            "stats": {
                "cycles_total":   stats.get("cycles_total",   0),
                "trades_opened":  stats.get("trades_opened",  0),
                "trades_closed":  stats.get("trades_closed",  0),
                "errors":         stats.get("errors",         0),
                "mt5_disconnects": stats.get("mt5_disconnects", 0),
            },
            "final_state":       state.get("final_state",       {}),
            "open_position_ids": state.get("open_position_ids", []),
            "last_market_state": state.get("last_market_state", {}),
        }
        fpath = SESSIONS_DIR / fname
        with fpath.open("w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)
        print(f"[INFO] Session snapshot saved: {fpath}")
    except Exception as exc:
        print(f"[logging] session snapshot failed: {exc}", file=sys.stderr)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _read_today_entries(today_str: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    if not CYCLE_LOG_FILE.exists():
        return entries
    try:
        with CYCLE_LOG_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if str(entry.get("ts", "")).startswith(today_str):
                        entries.append(entry)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return entries


def _format_day_section(today_str: str, entries: List[Dict[str, Any]]) -> str:
    dt = date.fromisoformat(today_str)
    day_name = dt.strftime("%A")
    day_num = (dt - _FORWARD_TEST_START).days + 1
    cycles = len(entries)

    trades_opened = sum(
        1 for e in entries
        if e.get("trade_event") and e["trade_event"].get("action") == "OPEN"
    )
    trades_closed = sum(
        1 for e in entries
        if e.get("trade_event") and e["trade_event"].get("action") == "CLOSE"
    )

    closes    = [e["close"] for e in entries if e.get("close") is not None]
    atrs      = [e["atr"]   for e in entries if e.get("atr")   is not None]
    all_highs = [e["high"]  for e in entries if e.get("high")  is not None]
    all_lows  = [e["low"]   for e in entries if e.get("low")   is not None]

    open_price  = closes[0]  if closes else 0.0
    close_price = closes[-1] if closes else 0.0
    range_high  = max(all_highs) if all_highs else (max(closes) if closes else 0.0)
    range_low   = min(all_lows)  if all_lows  else (min(closes) if closes else 0.0)
    atr_avg     = sum(atrs) / len(atrs) if atrs else 0.0

    regimes = [e["regime"] for e in entries if e.get("regime")]
    dxys    = [e["dxy"]    for e in entries if e.get("dxy")]
    top_regime, top_regime_n = Counter(regimes).most_common(1)[0] if regimes else ("UNKNOWN", 0)
    top_dxy,    top_dxy_n    = Counter(dxys).most_common(1)[0]    if dxys    else ("NEUTRAL", 0)

    all_block_reasons: List[str] = []
    no_block_cycles = 0
    for e in entries:
        br = e.get("block_reasons") or []
        sr = e.get("skip_reason")
        if e.get("signal") is not None:
            no_block_cycles += 1
        elif sr:
            all_block_reasons.append(sr)
        elif br:
            all_block_reasons.extend(br)
        else:
            all_block_reasons.append("NO_SIGNAL(unclassified)")
    top_blocks = Counter(all_block_reasons).most_common(5)

    spreads = [e["spread"] for e in entries if (e.get("spread") or 0) > 0]

    close_events = [
        e["trade_event"] for e in entries
        if e.get("trade_event") and e["trade_event"].get("action") == "CLOSE"
    ]
    daily_pnl = sum((ev.get("pnl") or 0) for ev in close_events)

    last_balance = next(
        (e["balance"] for e in reversed(entries) if e.get("balance") is not None), 0.0
    )
    last_equity = next(
        (e["equity"] for e in reversed(entries) if e.get("equity") is not None), 0.0
    )
    open_pnl    = round(last_equity - last_balance, 2) if (last_balance and last_equity) else 0.0
    monthly_pnl = round(last_balance - 10_000.0, 2)   if last_balance else 0.0

    errors = sum(1 for e in entries if e.get("error"))

    lines: List[str] = [
        f"## Day {day_num} — {today_str} ({day_name})",
        "",
        f"**Session config:** {_VERSION_TAG}",
        "",
        "**Activity:**",
        f"- Cycles run: {cycles}",
        f"- Trades opened: {trades_opened}",
        f"- Trades closed: {trades_closed}",
        "",
        "**Market context:**",
        f"- Open: ${open_price:,.2f} | Close: ${close_price:,.2f} | Range: ${range_low:,.0f}-${range_high:,.0f}",
        f"- ATR avg: ${atr_avg:.2f} | Regime: {top_regime} ({top_regime_n}/{cycles} cycles)",
        f"- DXY: {top_dxy} ({top_dxy_n}/{cycles})",
        "",
        "**Block reasons (top 5):**",
    ]
    for reason, count in top_blocks:
        pct = count * 100 // cycles if cycles else 0
        lines.append(f"- {reason}: {count} cycles ({pct}%)")
    if no_block_cycles:
        lines.append(f"- (no block - signal generated): {no_block_cycles} cycles")

    if spreads:
        lines += [
            "",
            "**Spread samples:**",
            (
                f"- Min: ${min(spreads):.2f} | Max: ${max(spreads):.2f} | "
                f"Avg: ${sum(spreads)/len(spreads):.2f} | N={len(spreads)}"
            ),
        ]

    lines += [
        "",
        "**P&L:**",
        f"- Daily realized: ${daily_pnl:+.2f}",
        f"- Floating (open positions): ${open_pnl:+.2f}",
        f"- Balance: ${last_balance:,.2f}",
        f"- Monthly cumulative: ${monthly_pnl:+.2f}",
        "",
        "**Issues:**",
        "- MT5 disconnects: 0",
        f"- Errors: {errors}",
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


def _format_empty_day(today_str: str) -> str:
    dt = date.fromisoformat(today_str)
    day_name = dt.strftime("%A")
    day_num = (dt - _FORWARD_TEST_START).days + 1
    return (
        f"## Day {day_num} — {today_str} ({day_name})\n\n"
        f"**Session config:** {_VERSION_TAG}\n\n"
        "**Activity:** No cycle data recorded.\n\n"
        "---\n\n"
    )
