# Trader Review — Gold Bot (XAUUSD)

**Reviewer perspective:** 30 ปีในตลาด FX/Gold (discretionary + systematic)
**Reviewed version:** v10.1 (2026-05-22)
**Forward-test data ใช้:** trade_journal.json (5 trades), daily_summary.md (Day 1–9), cycle_log.jsonl ล่าสุด

---

## TL;DR — สรุปสำหรับคนรีบ

ระบบ engineer ดี (walk-forward, observability 3 layers, news filter, position limit) แต่ **ยังคิดเหมือน backtester มากกว่า trader** มี 3 เรื่องใหญ่ที่นัก trade มืออาชีพจะตั้งคำถามทันทีเมื่อเห็นโค้ดนี้:

1. **ไม่มี trade management หลังเปิด position** — ไม่มี breakeven move, ไม่มี trailing stop, ไม่มี partial TP. กำไรลอย +1R แล้วโดน reverse กลับมาชน SL เต็มจำนวน. Trade #4 (−$60) เป็นตัวอย่าง.
2. **มอง timeframe เดียว (15m) ในตลาดที่ context อยู่บน 4H/Daily** — EMA200 บน 15m เห็นแค่ 50 ชั่วโมงย้อนหลัง. ตอนทองร่วง $5,041→$4,533 (May 2026) bot ยังเข้า BUY เพราะ 15m EMA200 ยังตามไม่ทัน. ทีมเคยลอง EMA200 slope filter แล้ว revert — แต่นั่นไม่ใช่การแก้ปัญหา root cause, มันคือการ patch อาการ.
3. **Data source mismatch: Backtest/signal ใช้ GC=F (futures), trade จริงใช้ XAUUSD spot** — เห็นที่ Trade #4: yfinance entry $4722.20 vs MT5 entry $4713.13 (ห่าง $9). คนละ instrument, คนละ profile (futures contango, gaps, settlement). Backtest Sharpe 2.68 จริงๆ ไม่ apply กับการเทรดบน XM.

ที่เหลือเป็นเรื่อง refinement — ผมจัดอันดับให้ด้านล่าง.

---

## P0 — ต้องทำก่อน (Risk-critical, ทำได้ใน 1–2 วัน)

### 1. Move SL → Breakeven หลัง price ถึง +1R

ตอนนี้ทุก trade เป็น set-and-forget: เข้า → SL ที่ −1R → TP ที่ +2R. ถ้าราคาขึ้นไป +1R แล้วกลับ ทำ −1R เต็ม. นี่คือ amateur mistake ที่ใหญ่ที่สุดในระบบ.

**แก้:** เพิ่มใน `_check_open_positions()`:
```python
# หลัง price >= entry + 1×stop_distance (BUY)
if current_high >= trade.entry + (trade.entry - trade.stop_loss):
    new_sl = trade.entry + 0.05  # +cushion เพื่อกันค่าธรรมเนียม
    if new_sl > trade.stop_loss:
        broker.modify_sl(trade.mt5_ticket, new_sl)
        journal.update(trade.id, stop_loss=new_sl)
```

**Expected impact:** เปลี่ยนค่าเฉลี่ย loss ของ trades ที่เคยไป +1R แล้วถอย จาก −1R เป็น ~0R. จากสถิติ backtest 53.9% WR ของระบบ ประมาณ 15–25% ของ losses เป็น "winners ที่กลายเป็น losers" — คำนวณคร่าวๆ ผลกระทบ +$1k–$2k/yr ที่ Sharpe สูงขึ้น.

### 2. Partial TP at +1R, ride remainder to +3R

แทน TP เดียวที่ +2R, แบ่งเป็น 2 layers:
- TP1 = +1R → close 50%, move SL → breakeven
- TP2 = +3R → close ที่เหลือ

นี่คือ standard ของ pro gold traders. Backtest จะแย่ลงตัวเลข (เพราะ TP1 จะ trigger บ่อย, น้ำหนัก winner เฉลี่ยลด) แต่ realtime Sharpe ดีขึ้นเพราะ drawdown ลด.

### 3. Spread gate (ตอนนี้ log อย่างเดียว, ไม่ใช้ block)

โค้ดมี `broker.get_current_spread()` แล้ว แต่ไม่เคยใช้ block trade. ช่วง news/Asia/rollover spread ของ XAUUSD พุ่งจาก $0.30 ขึ้นไปถึง $1.50–$3.00. เข้า trade ที่ ATR×2.5 stop = $20 แต่ spread = $3 → ขโมยกำไรไป 15% ทันที.

**แก้:** ใน `_do_cycle()` ก่อนเรียก `_open_trade`:
```python
MAX_SPREAD_USD = 0.60  # XAUUSD typical = 0.25–0.40
if self._last_spread > Config.MAX_SPREAD_USD:
    self.warn(f"Spread {self._last_spread:.2f} > {Config.MAX_SPREAD_USD} — skip")
    return
```

### 4. Friday afternoon cutoff (weekend gap risk)

ตอนนี้ session กว้าง 08:00–21:00 UTC ทุกวัน. Trade ที่เปิดวันศุกร์ 20:45 UTC จะถูก expose กับ gap weekend 65 ชั่วโมงโดยไม่มี SL active (ตลาดปิด). ทอง weekend gap ในข่าวใหญ่ (geopol, FOMC leak) เกิดได้ง่าย ±$30–80.

**แก้:** Friday ตัด session เร็วขึ้น (เช่น 17:00 UTC) หรือ block open trades after Friday 18:00 แต่ปล่อยให้ manage positions ที่มีอยู่.

### 5. Use live MT5 balance for sizing, not hardcoded $10,000

```python
ACCOUNT_SIZE: float = 10_000.0  # หลายเดือนแล้ว ยังใช้ตัวนี้ size
```

หลังกำไร $107.82 → sizing ใช้ $10k (under-leverage 1.08%). หลัง drawdown $1,000 → ยัง risk 1% ของ $10k (over-leverage 1.11%). ต้องอ่านจาก `mt5.account_info().balance` ทุก cycle.

---

## P1 — ปรับใน 1–2 อาทิตย์ (Edge improvement)

### 6. แก้ root cause Trade #4: Higher-timeframe trend filter

ทีมเคยลอง EMA200 slope filter (v8) แล้ว revert (v9) เพราะ statistically marginal. แต่ logic จริงคือ:

> EMA200 บน 15m = 50 ชั่วโมงย้อนหลัง. ตลาดทองมี cycle 1–3 วัน. EMA200 บน 15m **ไม่ได้** วัด macro trend, มันวัด short-term mean.

วิธีถูก: ใช้ 4H หรือ Daily แยก fetch:
```python
def _get_htf_bias(self) -> str:
    """4H EMA50 slope + price vs EMA50"""
    df = yf.Ticker("GC=F").history(period="60d", interval="4h")
    ema50 = df["Close"].ewm(span=50, adjust=False).mean()
    slope = float(ema50.iloc[-1]) - float(ema50.iloc[-5])  # 20h
    if slope > 0 and df["Close"].iloc[-1] > ema50.iloc[-1]:
        return "BULL"
    if slope < 0 and df["Close"].iloc[-1] < ema50.iloc[-1]:
        return "BEAR"
    return "NEUTRAL"
```

แล้ว block BUY เมื่อ HTF != "BULL". Trade #4: 4H EMA50 ตอน 2026-05-12 declining ชัดเจน → trade ถูก block.

นี่ตอบโจทย์ "Known Limitations" ใน CLAUDE.md ด้วย (regime-transition entries).

### 7. Session-of-day refinement

08:00–21:00 UTC คือกรอบกว้าง. Gold มี 4 sessions ที่ behavior ต่างกัน:

| Session UTC | Behavior | ใช้ pattern อะไร |
|---|---|---|
| 08:00–10:00 (London open) | Breakout, directional | EMA_MACD_TREND OK |
| 10:00–13:00 (London midday) | Range, choppy | Skip หรือ BB_RSI only |
| 13:00–17:00 (LDN-NY overlap) | Best directional moves | Best window — ขยาย confluence |
| 17:00–21:00 (NY PM) | Mean reversion, ขึ้นข่าว | BB_RSI > EMA_MACD_TREND |

จาก journal: 4 จาก 5 trades เปิด 14:00–15:00 UTC (sweet spot) — ตรงกับทฤษฎี. แต่ trade #4 (−$60) เปิด 20:30 UTC = NY late. การจำกัด EMA_MACD_TREND ให้เฉพาะ 13:00–17:00 UTC จะช่วย.

### 8. Real-time DXY/US10Y correlation (intraday, ไม่ใช่ daily)

ตอนนี้ DXY trend ใช้ daily EMA20/EMA50 → lag 1–2 วัน. Gold-DXY correlation จริงตอบสนองภายในชั่วโมง.

**ทำ:** fetch DXY 15m พร้อมกับ GC=F. ถ้า DXY 1h return > +0.3% และ BUY signal → block. ถ้า US10Y yield พุ่ง > +5bps/h → block BUY (yields ขึ้นเร็ว = bearish gold momentum).

### 9. ATR-aware lot size minimum guard

ตอนนี้ในตลาด ultra-low ATR (< 0.1% เช่น Asian session) stop เล็กมาก, ทำให้ lot ใหญ่ผิดปกติเพื่อ risk $100. แต่ slippage/spread $0.30 บน stop $5 = 6% drag.

**แก้:** ถ้า `stop_distance < $4`, skip trade (ไม่ใช่เพราะ stop เล็ก แต่เพราะ slippage:risk ratio แย่).

### 10. Add SELL capability for confirmed bear regime ONLY

SELL research บอกชัดว่า mirror SELL ไม่ work, structural SELL (B) มีข้อมูลน้อยเกินไป. แต่ระบบ BUY-only คือครึ่งหนึ่งของอาวุธ. คำแนะนำของผม:

**SELL only when ALL of these:**
- Daily close ต่ำกว่า Daily EMA200 มากกว่า 5 วัน
- 4H EMA50 slope ลงต่อเนื่อง 20 bars
- 15m signal คือ rejection ของ resistance (BB upper + RSI > 65 + close < open)
- ตัด confluence รวมเป็น 4/5 (เข้มกว่า BUY)

อย่าทำเป็น mirror — ทำเป็น **separate strategy** ที่ activate ตอน daily regime เป็น bear เท่านั้น. ในตลาด bull (97% ของ 2024–2026) จะไม่ fire เลย, ไม่ทำลาย Sharpe ปัจจุบัน. ในตลาด bear จะเก็บกำไรที่ระบบปัจจุบันพลาด.

---

## P2 — ปรับใน 1 เดือน (Operational hygiene)

### 11. ใช้ MT5 bars สำหรับ signal computation (live mode), ไม่ใช่ yfinance

`_fetch_mt5_bars()` มีอยู่แล้ว, แต่ regime classification, BB width percentile, ADX, indicator history ทั้งหมดยังคำนวณบน yfinance data ก่อน. นี่ไม่สอดคล้องกับ price ที่ใช้ execute.

แก้: ใน `MarketAnalystAgent.fetch_bars()` ตอน live mode ให้ใช้ MT5 เป็นหลัก, yfinance เฉพาะ DXY/macro.

### 12. ลด Telegram noise

ตอนนี้ส่ง "No signal" heartbeat ทุก 15 นาที = 52 ครั้ง/วัน. มันไม่ใช่ alert, มันคือ noise. ส่งเฉพาะ:
- signal (any direction)
- trade open/close
- daily summary (1 ครั้ง/วัน)
- error (MT5 disconnect, fetch fail)

### 13. Backtest news filter effect

โค้ด block ±30/15min รอบ USD news, แต่ backtest_v2 ไม่ replay news data → ไม่รู้ว่า filter นี้ช่วยจริงไหม. อาจจะ block winners เยอะ. ต้อง backtest with/without news filter.

### 14. Duplicate confluence check (Risk Manager)

`TechnicalAnalystAgent` ตรวจ `buy_n >= req_buy` แล้ว, `RiskManagerAgent` ตรวจ `effective_confluence < required` อีกที. DXY soft-confluence ลด −1 ถูกแล้ว แต่ logic ซ้ำซ้อน. รวมไว้ที่เดียว.

### 15. Monthly hard-stop, ไม่ใช่แค่ brake

ปัจจุบัน: เดือนติดลบ $150 → ลด lot ครึ่ง. แต่ไม่มี hard stop. Pro rule: monthly drawdown ≥ 5% ของ account = หยุดทั้งเดือน, review ก่อน resume.

```python
MONTHLY_HARD_STOP_PCT: float = 0.05  # 5% drawdown = stop month
if monthly_pnl < -ACCOUNT_SIZE * MONTHLY_HARD_STOP_PCT:
    return self._block("Monthly hard stop — review required")
```

---

## เรื่องที่ "ดีแล้ว" ไม่ต้องแตะ

- **R:R = 2.0**: ถูก. ทอง 15m ไม่ค่อยเดินไกลกว่านี้แบบ clean.
- **MAX_OPEN_POSITIONS = 1**: ถูก. ข้อมูล backtest ชัด.
- **News filter ±30/15 min**: ถูก concept (ต้อง verify ด้วย backtest).
- **MT5 sync on startup**: ดีมาก. หลายระบบลืม.
- **3-layer observability (v10)**: ดีเยี่ยม. มีน้อยระบบที่ทำได้.
- **Walk-forward 70/30 + daily-equity Sharpe**: standard professional. หาคนทำได้ยาก.
- **Disabled patterns flag**: เก็บ detection logic, ปิด pattern โดยไม่ลบโค้ด — สง่างาม.

---

## ข้อสังเกตจากการดู Forward Test ปัจจุบัน

- Trade journal: 5 trades ทั้งหมด, 2W/2L/1 OPEN, P&L net +$107.82 (live).
- Win rate 50% (2/4 closed) บน sample เล็กเกินสรุป.
- Trade #4 −$60 ตรงกับ failure mode ที่วิเคราะห์ใน TRADE4_ANALYSIS.md.
- Day 9 (2026-05-29): มี signal BUY แต่ position MAX=1 เต็มแล้ว (ปกติ, working as designed).
- cycle_log แสดง block reasons ครบ → observability ใช้งานได้จริง.
- **ข้อสังเกตสำคัญ:** Trade #5 (OPEN, 2026-05-29 14:34) เปิดตอน RSI 71.48 บน RSI ceiling 70 (เป็น signal ก่อนหน้านี้ที่ยังถือ). Confluence 3/3 ทุก bar สมัยนั้น. ระบบทำตรงตามที่ออกแบบ.

---

## ลำดับ implement ที่แนะนำ

```
Sprint 1 (วันนี้–สุดสัปดาห์):
  - P0.5  Live balance sizing (1 hour)
  - P0.3  Spread gate (30 min)
  - P0.4  Friday cutoff (30 min)
  - P0.1  Breakeven move at +1R (2–3 hours, ต้อง backtest)

Sprint 2 (สัปดาห์หน้า):
  - P0.2  Partial TP1/TP2 (4 hours + backtest 2yr)
  - P1.6  Higher-TF trend filter (4H EMA50) — backtest required

Sprint 3 (เดือนหน้า):
  - P1.7  Session-of-day refinement
  - P1.8  Intraday DXY/yield filter
  - P1.10 SELL strategy for confirmed bear regime (separate, gated)

Maintenance:
  - P2.11–15 cleanup
```

**Backtest gate:** ทุก P0/P1 change ต้องผ่าน 2-year backtest ก่อน deploy. Sharpe ≥ 2.5, MaxDD ≤ 7%, trade count drop ≤ 20%. ถ้าไม่ผ่าน — revert.

---

## ข้อคิดสุดท้าย

ระบบนี้ขาดอย่างเดียวที่นัก trade 30 ปีเห็นชัดเจน: **มันไม่ได้คิดว่า "หลังเข้าแล้วทำอะไรต่อ"**. มันคิดว่า "หลังเข้าแล้วรอ"... รอ SL หรือ TP. ตลาดทองไม่ให้คุณรอเฉยๆ — ตลาดทองให้กำไรลอยแล้วเอาคืน. การมี trade management (BE, trailing, partial) คือสิ่งที่แยก hobbyist กับ professional.

ส่วน HTF context, spread guard, live sizing — เป็นสุขลักษณะพื้นฐานที่นักพัฒนาดี (ซึ่งคุณเป็น) มักลืม เพราะมัน "ไม่ใช่ trading logic" แต่จริงๆ มันคือ trading logic ส่วนที่ผู้เริ่มต้นเห็นไม่ชัด.

ทุกอย่างที่เหลือคือการ refine ของระบบที่ดีแล้ว.
