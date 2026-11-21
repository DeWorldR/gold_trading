# Gold Trading Multi-Agent System

บอทเทรดทองคำ (XAUUSD) อัตโนมัติบน MetaTrader 5 ออกแบบเป็น agent หลายตัวที่ทำงานร่วมกัน
ค่าเริ่มต้นเป็น **paper trade** (จำลอง ไม่ใช้เงินจริง)

**Tech:** Python 3.12 · pandas · pandas-ta · MetaTrader5 API · APScheduler · Telegram Bot API · yfinance

## สถาปัตยกรรม
| Agent | หน้าที่ |
|---|---|
| Orchestrator | ควบคุมรอบการทำงานทุก 15 นาที (จ.–ศ.) |
| Market Analyst | ดูภาวะตลาดและแนวโน้มใน timeframe ใหญ่ |
| Technical Analyst | หาสัญญาณจาก EMA, MACD, RSI, Bollinger Bands, ADX, ATR |
| News Filter | หยุดเทรดช่วงข่าวสำคัญ |
| Risk Manager | คำนวณขนาด lot, stop loss และวงเงินขาดทุนต่อวัน |
| Reporter | แจ้งผลผ่าน Telegram |
| Learning Agent | สรุปผลจาก trade journal |

## การทดสอบ
- backtest แบบ walk-forward ด้วยข้อมูล 1H ย้อนหลัง 2 ปี พร้อมทดสอบความทนทาน (robustness), ความไวของพารามิเตอร์ และวิเคราะห์ drawdown
- ผล backtest ของค่าที่ใช้จริง: win rate 55% · Profit Factor 2.33 · Max Drawdown 5.7% (102 เทรด)
- ทุกการเปลี่ยนแปลงต้องผ่านเกณฑ์ก่อนนำไปใช้ รายงานผลแต่ละรอบอยู่ในไฟล์ `*_REPORT.md` และ `*_VALIDATION.md`

> ผลข้างต้นเป็นผล backtest ไม่ใช่ผลเทรดจริง และไม่ใช่คำแนะนำการลงทุน

## รัน
```bash
py -3.12 -m pip install -r requirements.txt
copy .env.example .env        # ใส่ข้อมูลบัญชี MT5 และ Telegram
py -3.12 gold_trading_agents.py
```
ก่อนเปิดระบบทุกครั้งให้ทำตาม `PRE_DEPLOYMENT_CHECKLIST.md`
