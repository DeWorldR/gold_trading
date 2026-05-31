# Changes — v3 Enhancement (2026-05-09)

Six improvements applied to `gold_trading_agents.py` and `backtest_v2.py`.

---

## gold_trading_agents.py

### 1. NewsFilterAgent (new class)
- Fetches `https://nfs.faireconomy.media/ff_calendar_thisweek.json` (cached 1h)
- Blocks ±30min before / ±15min after High-impact USD events: NFP, FOMC, CPI, PPI, PCE
- Calendar is in Eastern Time; converted to UTC via `pytz`
- `is_blackout() → Tuple[bool, str]`
- Wired into `OrchestratorAgent._do_cycle()` after session filter, before market data fetch
- New field: `TradeRecord.blocked_by_news: bool = False`

### 2. DXY macro filter
- `MarketAnalystAgent._get_dxy_trend()` fetches `DX-Y.NYB` daily closes (60d)
- EMA20 / EMA50 crossover → `"UP"` | `"DOWN"` | `"NEUTRAL"`
- Result stored in `MarketState.dxy_trend`
- `RiskManagerAgent.run()` applies soft-confluence penalty:
  - DXY UP + BUY signal → `effective_confluence -= 1`
  - DXY DOWN + SELL signal → `effective_confluence -= 1`
  - Trade is blocked if adjusted confluence falls below `MIN_CONFLUENCE` (3)

### 3. Bollinger Band width percentile filter
- Uses `BBB_*` (bandwidth) column from `pandas_ta bbands()`
- Computes percentile rank of current BBW vs last 50 bars
- Blocks signal when `bb_width_pct < BB_WIDTH_MIN_PCT` (25) — range-bound market
- New field: `TechnicalSignal.bb_width_pct: float = 50.0`
- New config: `BB_WIDTH_LOOKBACK = 50`, `BB_WIDTH_MIN_PCT = 25.0`

### 4. New Config parameters added
```
NEWS_CALENDAR_URL    = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEWS_PRE_BLOCK_MIN   = 30
NEWS_POST_BLOCK_MIN  = 15
NEWS_KEYWORDS        = [NFP, FOMC, CPI, PPI, PCE variants]
NEWS_CACHE_SECONDS   = 3600
BB_WIDTH_LOOKBACK    = 50
BB_WIDTH_MIN_PCT     = 25.0
DXY_SYMBOL           = "DX-Y.NYB"
```

---

## backtest_v2.py

### 5. Daily-equity-curve Sharpe (replaces per-trade Sharpe)
- New function `daily_sharpe(eq: pd.Series) -> float`
- `daily_eq = eq.resample("B").last().dropna()`
- `daily_ret = daily_eq.pct_change().dropna()`
- `sharpe = (daily_ret.mean() / daily_ret.std()) * sqrt(252)`
- Previous per-trade Sharpe was overstated (used wrong annualisation factor)

### 6. BB width percentile filter in backtest
- `add_indicators()` computes `bb_width_pct` column using rolling apply
- `generate_signal()` returns `None` when `bb_width_pct < BB_WIDTH_MIN_PCT`
- Skipped bars counted separately in filter breakdown as `bb_width`

### 7. Spread / slippage model
- `SPREAD_PIPS = 2.5`, `SPREAD_DOLLARS = 0.25`
- BUY entries: `actual_entry = close + 0.25`
- SELL entries: `actual_entry = close - 0.25`
- SL exits on high-ATR bars (`bar_atr > atr_mean × 1.5`): +$0.05 extra slippage
- `BT` dataclass gains `spread_cost` and `net_pnl` fields
- All equity tracking, analytics, and Sharpe use `net_pnl`
- Report shows gross P&L, spread/slip cost, and net P&L separately

### 8. Walk-forward 70/30 split
- `TRAIN_RATIO = 0.70`
- Closed trades split by `open_time` at the 70th percentile trade
- `analyse()` run independently on train and validation subsets
- Printed comparison table: trades / win rate / net P&L / profit factor / max DD / Sharpe
- Warning printed if `val_sharpe < train_sharpe × 0.50`

---

## To re-validate

```bash
# Re-run backtest with all improvements
py -3.12 backtest_v2.py

# Check walk-forward table for overfitting warning
# Check spread-adjusted P&L vs gross P&L (expected ~$30-70 drag over 231 trades)
# Sharpe will be lower than 3.28 (per-trade method was overstated)

# Run live system (paper trade)
py -3.12 gold_trading_agents.py
```

---

## Expected impact

| Change | Expected effect |
|--------|----------------|
| BB width filter | Fewer trades; removes range-bound false signals |
| DXY soft confluence | Slight reduction in BUY trade count during USD strength |
| News filter | Removes ~5-15 trades/year around major USD events |
| Spread/slippage | ~$30-70 net P&L reduction over the year (minor) |
| Daily Sharpe | Sharpe will print lower — it was inflated before |
| Walk-forward | Validates that performance holds out-of-sample |
