#!/usr/bin/env python3
"""
Gold Trading Multi-Agent System for XM Broker (MT5)
Agents: market_analyst, technical_analyst, orchestrator, risk_manager, reporter, learning_agent
Symbol: XAUUSD (GC=F via yfinance) | Interval: 15m | Scheduler: Mon-Fri every 15 min UTC
"""

import os
import sys
import json
import warnings
# pandas_ta sets a deprecated pandas option (copy_on_write) on import — suppress it
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"pandas_ta.*")
warnings.filterwarnings("ignore", message=r".*copy_on_write.*", category=DeprecationWarning)
import uuid
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field, asdict
from pathlib import Path

import yfinance as yf
import pandas as pd
import pandas_ta as ta  # noqa: F401  registers the .ta accessor on DataFrame
import numpy as np
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import requests
import pytz
from tabulate import tabulate

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

import logger as _obs_logger

load_dotenv()

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("gold_trading.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("GoldTrading")


# ── Configuration ─────────────────────────────────────────────────────────────
class Config:
    # Account
    ACCOUNT_SIZE: float = 10_000.0
    MAX_RISK_PCT: float = float(os.getenv("RISK_PER_TRADE_PCT", "1.0")) / 100  # env in %, e.g. "0.5" → 0.005
    MIN_RR: float = 2.0               # backtest: 2.0 breaks even at 33% win rate (1.5 needed 40%)
    DAILY_LOSS_LIMIT: float = float(os.getenv("DAILY_LOSS_LIMIT", "300"))  # USD

    # Signal quality gate
    MIN_CONFLUENCE: int = 3           # at least 3 of 5 signals must align

    # RSI entry thresholds (tightened from 40/60 — backtest showed 35/65 is cleaner)
    RSI_BUY: float = 35.0
    RSI_SELL: float = 65.0

    # Session filter: London open to NY close (08:00-21:00 UTC)
    SESSION_START_UTC: int = 8
    SESSION_END_UTC: int = 21

    # EMA200 trend gate (backtest: removed counter-trend trades that were systematically losing)
    TREND_EMA: int = 200
    TREND_NEUTRAL_ATR: float = 0.3    # zone within 0.3×ATR of EMA200 = no trade

    # Consecutive loss guard: stop for the day after N losses in a row
    MAX_CONSEC_LOSS: int = 2

    # Market data
    SYMBOL: str = "GC=F"             # yfinance Gold futures ticker
    MT5_SYMBOL: str = os.getenv("MT5_SYMBOL", "XAUUSD")  # XM uses "GOLD#"; default for other brokers
    INTERVAL: str = "15m"
    DATA_PERIOD: str = "5d"

    # Execution
    PAPER_TRADE: bool = os.getenv("PAPER_TRADE", "true").lower() == "true"

    # MT5 / XM broker
    MT5_LOGIN: int = int(os.getenv("MT5_LOGIN", "0") or "0")
    MT5_PASSWORD: str = os.getenv("MT5_PASSWORD", "")
    MT5_SERVER: str = os.getenv("MT5_SERVER", "XMGlobal-MT5 2")

    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Persistence
    JOURNAL_FILE: str = "trade_journal.json"
    SKILLS_DIR: Path = Path("skills")

    # XAUUSD contract: 1 lot = 100 troy oz → $1 move = $100 P&L per lot
    GOLD_CONTRACT_SIZE: float = 100.0
    MIN_LOT: float = 0.01
    MAX_LOT: float = 10.0
    LOT_STEP: float = 0.01

    # Regime thresholds
    # Gold 15m bars typically show ATR% of 0.05-0.25%.
    # 0.35% is ~2-3x normal, flagging genuine intraday volatility spikes.
    ATR_VOLATILE_PCT: float = 0.35   # ATR/price > 0.35% → VOLATILE
    ATR_STOP_MULT: float = 2.5       # stop = entry ± ATR × 2.5 (2yr backtest: WR 55%, PF 2.33, MaxDD 5.7%)

    # News filter (ForexFactory calendar — High-impact USD events)
    NEWS_CALENDAR_URL: str = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    NEWS_PRE_BLOCK_MIN: int = 30     # block trading this many minutes BEFORE event
    NEWS_POST_BLOCK_MIN: int = 15    # block trading this many minutes AFTER event
    NEWS_KEYWORDS: List[str] = [
        "Non-Farm", "NFP", "Federal Funds", "FOMC",
        "Consumer Price", "CPI", "Producer Price", "PPI", "PCE", "Core PCE",
    ]
    NEWS_CACHE_SECONDS: int = 3600   # re-fetch calendar at most once per hour

    # Bollinger Band width filter (range-detection)
    BB_WIDTH_LOOKBACK: int = 50      # bars to compute percentile rank over
    BB_WIDTH_MIN_PCT: float = 25.0   # skip signals when BBW < 25th pct (choppy)

    # DXY macro filter
    DXY_SYMBOL: str = "DX-Y.NYB"    # USD Index futures via yfinance

    # ADX filter — EMA_MACD_TREND requires confirmed trend momentum (applied to both BUY and SELL)
    # Backtest: weak-ADX BUY entries in Q2 2026 mean-reverting regime cost -$617; SELL filter pre-existing
    ADX_TREND_THRESHOLD: int = 25    # minimum ADX to allow EMA_MACD_TREND signals
    ADX_LOOKBACK: int = 14           # Wilder smoothing period (standard)

    # RSI ceiling for BUY entries — overbought RSI at entry = no momentum room left
    # Fix_validation: blocked 38% of all losses (RSI>70 at entry) in Q2 2026 drawdown
    RSI_CEILING_BUY: int = 70        # block BUY when RSI >= this value

    # BB_RSI lower confluence — rarer setup with superior quality (60% win rate)
    BB_RSI_MIN_CONFLUENCE: int = 2   # BB_RSI patterns need only 2/5 signals aligned

    # Monthly drawdown brake — halve lot size when month is down > $150
    MONTHLY_DRAWDOWN_BRAKE: float = 150.0   # USD threshold to activate brake
    MONTHLY_BRAKE_MULTIPLIER: float = 0.5   # lot size multiplier when brake is active

    # Disabled patterns — add pattern names here to suppress without deleting detection code.
    # EMA_MACD_TREND_SELL: 101 trades, 27% WR, -$2,232 over 2yr — broken in gold bull market.
    # BB_RSI_REVERSAL_SELL: 16 trades, 25% WR, -$274.90 over 2yr — same structural problem.
    DISABLED_PATTERNS: List[str] = ["EMA_MACD_TREND_SELL", "BB_RSI_REVERSAL_SELL"]

    # Concurrent position limit — backtest: MAX=1 → Sharpe 2.77, MAX=inf → Sharpe 0.93
    MAX_OPEN_POSITIONS: int = int(os.getenv("MAX_OPEN_POSITIONS", "1"))

    # v11: spread gate — XAUUSD typical 0.25-0.40; block if > 0.60 (news/illiquid windows)
    # Spread of $1+ on a $20 stop = 5%+ drag per trade — kills R:R economics.
    MAX_SPREAD_USD: float = float(os.getenv("MAX_SPREAD_USD", "0.60"))

    # v11: Friday afternoon cutoff — block NEW entries after this UTC hour on Fridays.
    # Open positions are still managed (SL/TP checks). Reduces weekend gap exposure.
    FRIDAY_CUTOFF_HOUR_UTC: int = int(os.getenv("FRIDAY_CUTOFF_HOUR_UTC", "17"))

    # v11: Breakeven move — when floating profit reaches +1R, move SL to entry + cushion.
    # Converts "winners that became losers" into near-zero-cost exits.
    BE_TRIGGER_R: float = float(os.getenv("BE_TRIGGER_R", "1.0"))     # move BE at this R multiple
    BE_CUSHION_USD: float = float(os.getenv("BE_CUSHION_USD", "0.50"))  # new SL = entry ± this

    # v12: Higher-timeframe (4H) trend bias filter — closes the EMA200-slope blind spot
    # that v8 tried (and failed) to address on the 15m bar. 4H EMA50 = 200 hours of
    # context; matches what the 15m EMA200 should capture but doesn't due to bar-count lag.
    # Block 15m BUY when 4H bias != BULL; block SELL when != BEAR.
    HTF_BIAS_ENABLED: bool = os.getenv("HTF_BIAS_ENABLED", "true").lower() == "true"
    HTF_INTERVAL: str = "4h"
    HTF_EMA_LEN: int = 50
    HTF_SLOPE_LOOKBACK: int = 5   # 5 bars on 4H = 20 hours
    HTF_CACHE_SECONDS: int = 1800  # re-fetch HTF data at most every 30 min (it's 4H bars)

    # v12: data hygiene — fetch buffer for MT5 bars (was 300; bumped to 500 for safer
    # indicator warmup margin — EMA200 needs 200, BB-width percentile needs 50 history,
    # ADX needs 30+; 500 gives 2x margin on every indicator).
    MT5_FETCH_N_BARS: int = int(os.getenv("MT5_FETCH_N_BARS", "500"))

    # v12: cross-source divergence guard — in live mode, if MT5 vs yfinance close
    # prices differ by more than DATA_DIVERGENCE_USD, log a warning. Catches scenarios
    # where MT5 feed lags or differs from the data the backtest was calibrated on.
    # Set to 0 to disable the check entirely.
    DATA_DIVERGENCE_USD: float = float(os.getenv("DATA_DIVERGENCE_USD", "5.0"))


# ── Data Classes ──────────────────────────────────────────────────────────────
@dataclass
class MarketState:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    regime: str           # TRENDING_UP | TRENDING_DOWN | RANGING | VOLATILE
    atr: float
    df: Any = field(default=None, repr=False)
    dxy_trend: str = "NEUTRAL"  # UP | DOWN | NEUTRAL — DXY macro confluence
    htf_bias: str = "NEUTRAL"   # BULL | BEAR | NEUTRAL — 4H EMA50 trend bias (v12)
    data_source: str = "unknown"  # mt5 | yfinance — which feed produced these bars (v12)


@dataclass
class TechnicalSignal:
    direction: str        # BUY | SELL | NONE
    pattern: str
    confluence_count: int
    entry: float
    stop_loss: float
    take_profit: float
    confidence: float
    reasons: List[str] = field(default_factory=list)
    indicators: Dict[str, float] = field(default_factory=dict)
    bb_width_pct: float = 50.0   # BBW percentile rank vs last 50 bars (0-100)
    adx_value: float = 0.0       # ADX(14) at signal time — informational
    block_reasons: List[str] = field(default_factory=list)
    buy_confluence_str: str = "0/3"
    sell_confluence_str: str = "0/3"


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    lot_size: float = 0.0
    risk_amount: float = 0.0
    rr_ratio: float = 0.0
    monthly_brake_active: bool = False


@dataclass
class TradeRecord:
    id: str
    timestamp: str
    direction: str
    pattern: str
    entry: float
    stop_loss: float
    take_profit: float
    lot_size: float
    risk_amount: float
    confluence_count: int
    regime: str
    paper: bool
    status: str           # OPEN | CLOSED_WIN | CLOSED_LOSS | CLOSED_BE | CANCELLED
    exit_price: float = 0.0
    exit_timestamp: str = ""
    pnl: float = 0.0
    mt5_ticket: int = 0
    rr_ratio: float = 0.0
    blocked_by_news: bool = False      # True if cycle was blocked by news filter
    monthly_brake_active: bool = False # True if lot size was halved by monthly brake
    be_moved: bool = False             # True after SL moved to breakeven


# ── Agent Base ────────────────────────────────────────────────────────────────
class Agent:
    def __init__(self, name: str):
        self.name = name
        self.log = logging.getLogger(f"Agent.{name}")

    def info(self, msg: str):
        self.log.info(msg)

    def warn(self, msg: str):
        self.log.warning(msg)

    def error(self, msg: str):
        self.log.error(msg)


# ── News Filter Agent ─────────────────────────────────────────────────────────
class NewsFilterAgent(Agent):
    """
    Fetches ForexFactory high-impact USD event calendar (cached 1h).
    Blocks signals ±30min before / ±15min after NFP, FOMC, CPI, PPI, PCE.
    Calendar is in Eastern Time; converted to UTC via pytz.
    """

    _EASTERN = pytz.timezone("US/Eastern")

    def __init__(self):
        super().__init__("news_filter")
        self._cache_data: Optional[List[dict]] = None
        self._cache_ts: float = 0.0

    def is_blackout(self) -> Tuple[bool, str]:
        """Return (blocked, reason_string). Called once per cycle."""
        events = self._get_events()
        if not events:
            return False, ""

        now_utc = datetime.now(timezone.utc).replace(tzinfo=pytz.utc)
        pre  = timedelta(minutes=Config.NEWS_PRE_BLOCK_MIN)
        post = timedelta(minutes=Config.NEWS_POST_BLOCK_MIN)

        for ev in events:
            if ev.get("impact", "").upper() != "HIGH":
                continue
            if ev.get("country", "").upper() != "USD":
                continue
            title = ev.get("title", "")
            if not any(kw.lower() in title.lower() for kw in Config.NEWS_KEYWORDS):
                continue

            ev_utc = self._parse_event_time(ev)
            if ev_utc is None:
                continue

            if (ev_utc - pre) <= now_utc <= (ev_utc + post):
                delta = int((now_utc - ev_utc).total_seconds() / 60)
                label = f"{delta:+d}min" if delta != 0 else "AT event"
                reason = f"News blackout: {title} ({label})"
                self.warn(reason)
                return True, reason

        return False, ""

    def _get_events(self) -> List[dict]:
        import time
        now = time.time()
        if self._cache_data is not None and (now - self._cache_ts) < Config.NEWS_CACHE_SECONDS:
            return self._cache_data
        try:
            r = requests.get(Config.NEWS_CALENDAR_URL, timeout=10)
            if r.status_code == 200:
                self._cache_data = r.json()
                self._cache_ts = now
                self.info(f"Calendar fetched: {len(self._cache_data)} events")
            else:
                self.warn(f"Calendar HTTP {r.status_code} — skipping news filter")
                self._cache_data = []
        except Exception as exc:
            self.warn(f"Calendar fetch failed: {exc} — skipping news filter")
            self._cache_data = []
        return self._cache_data or []

    def _parse_event_time(self, ev: dict) -> Optional[datetime]:
        """Parse ForexFactory date/time fields (Eastern) → UTC-aware datetime."""
        try:
            date_str = ev.get("date", "")   # e.g. "2026-05-09T08:30:00-04:00"
            if "T" in date_str:
                # ISO format — may already have offset
                dt = datetime.fromisoformat(date_str)
                if dt.tzinfo is None:
                    dt = self._EASTERN.localize(dt)
                return dt.astimezone(pytz.utc)
            # Fallback: combine date + time fields
            time_str = ev.get("time", "12:00am")
            dt_naive = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %I:%M%p")
            dt_et = self._EASTERN.localize(dt_naive)
            return dt_et.astimezone(pytz.utc)
        except Exception:
            return None


# ── Market Analyst Agent ──────────────────────────────────────────────────────
class MarketAnalystAgent(Agent):
    """
    Fetches OHLCV from yfinance (GC=F, 15m) and classifies market regime:
    VOLATILE (ATR% > 0.35%) | TRENDING_UP | TRENDING_DOWN | RANGING
    """

    def __init__(self, broker=None, reporter=None):
        super().__init__("market_analyst")
        self.broker = broker
        self.reporter = reporter  # v12: optional Telegram alerter for fallback warnings
        # v12: HTF cache — avoid re-fetching 4H data every 15min cycle (waste).
        self._htf_cache_bias: str = "NEUTRAL"
        self._htf_cache_ts: float = 0.0
        # v12: data hygiene — track which source produced the current cycle's bars
        self._last_data_source: str = "unknown"
        self._fallback_alerted: bool = False  # one Telegram alert per session

    def run(self) -> Optional[MarketState]:
        self.info(f"Fetching market data [{Config.INTERVAL}]")
        try:
            df = self.fetch_bars()
            if df is None or df.empty or len(df) < 60:
                self.warn("Insufficient data returned")
                return None

            atr_s = df.ta.atr(length=14)
            atr = float(atr_s.iloc[-1]) if atr_s is not None and not atr_s.empty else 0.0

            close = float(df["Close"].iloc[-1])
            atr_pct = (atr / close * 100) if close > 0 else 0.0
            regime = self._detect_regime(df, atr_pct)
            row = df.iloc[-1]

            dxy_trend = self._get_dxy_trend()
            htf_bias = self._get_htf_bias() if Config.HTF_BIAS_ENABLED else "NEUTRAL"
            state = MarketState(
                timestamp=datetime.now(timezone.utc),
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=close,
                volume=float(row["Volume"]),
                regime=regime,
                atr=atr,
                df=df,
                dxy_trend=dxy_trend,
                htf_bias=htf_bias,
                data_source=self._last_data_source,
            )
            self.info(
                f"close={close:.2f} ATR={atr:.2f} ({atr_pct:.2f}%) "
                f"regime={regime} DXY={dxy_trend} HTF={htf_bias} src={self._last_data_source}"
            )
            return state

        except Exception as exc:
            self.error(f"Market data fetch failed: {exc}")
            return None

    def fetch_bars(self) -> Optional[pd.DataFrame]:
        """Fetch OHLCV bars. MT5 in live mode (preferred); yfinance fallback.

        v12: records source in self._last_data_source so MarketState carries provenance.
        Sends one-time Telegram alert when live mode falls back to yfinance — that's
        a data integrity event the user must see.
        """
        if not Config.PAPER_TRADE:
            df = self._fetch_mt5_bars(n_bars=Config.MT5_FETCH_N_BARS)
            if df is not None and len(df) >= 200:
                self.log.info(f"Using MT5 data ({len(df)} bars)")
                self._last_data_source = "mt5"
                # v12: data divergence sanity check — non-blocking, log-only
                self._check_divergence(df)
                return df
            # MT5 unavailable in live mode — this is a real problem
            self.log.warning("MT5 fetch failed, falling back to yfinance")
            if self.reporter and not self._fallback_alerted:
                try:
                    self.reporter.send_telegram(
                        "<b>WARNING — data source fallback</b>\n"
                        "Live mode is now using yfinance GC=F instead of MT5 XAUUSD.\n"
                        "Indicator calibration may not match execution price."
                    )
                    self._fallback_alerted = True
                except Exception:
                    pass
        self.log.info(f"Fetching {Config.SYMBOL} [{Config.INTERVAL}]")
        raw = yf.Ticker(Config.SYMBOL).history(period=Config.DATA_PERIOD, interval=Config.INTERVAL)
        if raw.empty:
            self._last_data_source = "unknown"
            return None
        self._last_data_source = "yfinance"
        return raw[["Open", "High", "Low", "Close", "Volume"]].dropna()

    def _check_divergence(self, mt5_df: pd.DataFrame) -> None:
        """
        v12: cross-source price sanity check. Logs (does not block) when MT5
        XAUUSD close differs from yfinance GC=F close by more than DATA_DIVERGENCE_USD.

        This is a forward-test diagnostic: it tells you whether MT5 spot price is
        tracking the futures proxy the backtest was calibrated on. A persistent
        large divergence means the production system is running on a meaningfully
        different instrument than the one the strategy was tuned for.
        """
        if Config.DATA_DIVERGENCE_USD <= 0:
            return
        try:
            mt5_close = float(mt5_df["Close"].iloc[-1])
            yf_raw = yf.Ticker(Config.SYMBOL).history(period="2d", interval=Config.INTERVAL)
            if yf_raw is None or yf_raw.empty:
                return
            yf_close = float(yf_raw["Close"].iloc[-1])
            diff = abs(mt5_close - yf_close)
            if diff > Config.DATA_DIVERGENCE_USD:
                self.warn(
                    f"DATA DIVERGENCE: MT5 ${mt5_close:.2f} vs yfinance ${yf_close:.2f} "
                    f"(diff ${diff:.2f} > ${Config.DATA_DIVERGENCE_USD:.2f}). "
                    f"Indicator calibration may not transfer cleanly."
                )
        except Exception as exc:
            self.log.debug(f"Divergence check skipped: {exc}")

    def _fetch_mt5_bars(self, n_bars: int = 500) -> Optional[pd.DataFrame]:
        """Fetch bars from MT5 broker directly (live mode only)."""
        if not MT5_AVAILABLE:
            return None
        if not hasattr(self, "broker") or self.broker is None or not self.broker.connected:
            return None
        try:
            rates = mt5.copy_rates_from_pos(Config.MT5_SYMBOL, mt5.TIMEFRAME_M15, 0, n_bars)
            if rates is None or len(rates) == 0:
                self.log.warning(f"MT5 returned no bars for {Config.MT5_SYMBOL}")
                return None
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
            df = df.set_index("time").rename(columns={
                "open": "Open", "high": "High", "low": "Low",
                "close": "Close", "tick_volume": "Volume",
            })
            return df[["Open", "High", "Low", "Close", "Volume"]]
        except Exception as exc:
            self.log.error(f"MT5 bar fetch failed: {exc}")
            return None

    def _get_dxy_trend(self) -> str:
        """Fetch DXY (USD Index) and classify trend via EMA20/EMA50."""
        try:
            dxy_df = yf.Ticker(Config.DXY_SYMBOL).history(period="60d", interval="1d")
            if dxy_df.empty or len(dxy_df) < 55:
                return "NEUTRAL"
            close = dxy_df["Close"]
            ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
            ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
            last_close = float(close.iloc[-1])
            if last_close > ema20 > ema50:
                return "UP"
            if last_close < ema20 < ema50:
                return "DOWN"
            return "NEUTRAL"
        except Exception as exc:
            self.warn(f"DXY fetch failed: {exc}")
            return "NEUTRAL"

    def _get_htf_bias(self) -> str:
        """
        v12: 4H higher-timeframe trend bias.

        Returns BULL / BEAR / NEUTRAL based on:
          - close > EMA50 AND EMA50 slope > 0 over HTF_SLOPE_LOOKBACK bars → BULL
          - close < EMA50 AND EMA50 slope < 0 over HTF_SLOPE_LOOKBACK bars → BEAR
          - otherwise → NEUTRAL

        Closes the 15m EMA200 blind spot identified in TRADE4_ANALYSIS.md and the
        Q2 2026 drawdown root cause (DRAWDOWN_DIAGNOSTIC.md): 15m EMA200 sees only
        50 hours back; 4H EMA50 captures 200 hours — actual macro regime, not noise.

        Cached for HTF_CACHE_SECONDS (30 min) — 4H bars don't update faster anyway.
        Uses MT5 H4 bars in live mode, yfinance "4h" otherwise. Network failure
        returns NEUTRAL (safe default — does not block trades).
        """
        import time
        now = time.time()
        if (now - self._htf_cache_ts) < Config.HTF_CACHE_SECONDS:
            return self._htf_cache_bias

        try:
            df_htf = self._fetch_htf_bars()
            if df_htf is None or len(df_htf) < Config.HTF_EMA_LEN + Config.HTF_SLOPE_LOOKBACK + 5:
                self.warn("HTF: insufficient bars — returning NEUTRAL")
                self._htf_cache_bias = "NEUTRAL"
                self._htf_cache_ts = now
                return "NEUTRAL"

            close = df_htf["Close"]
            ema50_s = close.ewm(span=Config.HTF_EMA_LEN, adjust=False).mean()
            ema50_now = float(ema50_s.iloc[-1])
            ema50_prev = float(ema50_s.iloc[-1 - Config.HTF_SLOPE_LOOKBACK])
            slope = ema50_now - ema50_prev
            last_close = float(close.iloc[-1])

            if slope > 0 and last_close > ema50_now:
                bias = "BULL"
            elif slope < 0 and last_close < ema50_now:
                bias = "BEAR"
            else:
                bias = "NEUTRAL"

            self.info(
                f"HTF[{Config.HTF_INTERVAL}]: close={last_close:.2f} "
                f"EMA50={ema50_now:.2f} slope={slope:+.2f} -> {bias}"
            )
            self._htf_cache_bias = bias
            self._htf_cache_ts = now
            return bias
        except Exception as exc:
            self.warn(f"HTF fetch failed: {exc} — returning NEUTRAL")
            self._htf_cache_bias = "NEUTRAL"
            self._htf_cache_ts = now
            return "NEUTRAL"

    def _fetch_htf_bars(self) -> Optional[pd.DataFrame]:
        """Fetch HTF (4H) bars. MT5 in live mode, yfinance otherwise."""
        # MT5 live path
        if (
            not Config.PAPER_TRADE
            and MT5_AVAILABLE
            and self.broker is not None
            and self.broker.connected
        ):
            try:
                n_needed = Config.HTF_EMA_LEN + Config.HTF_SLOPE_LOOKBACK + 30
                rates = mt5.copy_rates_from_pos(
                    Config.MT5_SYMBOL, mt5.TIMEFRAME_H4, 0, n_needed
                )
                if rates is not None and len(rates) > 0:
                    df = pd.DataFrame(rates)
                    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
                    df = df.set_index("time").rename(columns={
                        "open": "Open", "high": "High", "low": "Low",
                        "close": "Close", "tick_volume": "Volume",
                    })
                    return df[["Open", "High", "Low", "Close", "Volume"]]
            except Exception as exc:
                self.warn(f"MT5 HTF fetch failed: {exc} — falling back to yfinance")

        # yfinance fallback (also used in paper mode)
        raw = yf.Ticker(Config.SYMBOL).history(period="60d", interval=Config.HTF_INTERVAL)
        if raw.empty:
            return None
        return raw[["Open", "High", "Low", "Close", "Volume"]].dropna()

    def _detect_regime(self, df: pd.DataFrame, atr_pct: float) -> str:
        if atr_pct > Config.ATR_VOLATILE_PCT:
            return "VOLATILE"

        ema20 = df.ta.ema(length=20)
        ema50 = df.ta.ema(length=50)
        c = df["Close"].iloc[-1]
        e20 = float(ema20.iloc[-1]) if ema20 is not None and not ema20.empty else 0.0
        e50 = float(ema50.iloc[-1]) if ema50 is not None and not ema50.empty else 0.0

        if e20 > 0 and e50 > 0:
            if c > e20 > e50:
                return "TRENDING_UP"
            if c < e20 < e50:
                return "TRENDING_DOWN"
        return "RANGING"


# ── Technical Analyst Agent ───────────────────────────────────────────────────
class TechnicalAnalystAgent(Agent):
    """
    Computes via pandas_ta: RSI(14), EMA(20/50), MACD(12,26,9), ATR(14), BBands(20,2).
    Scores up to 5 BUY or SELL confluences and names the pattern.
    Emits a signal only when MIN_CONFLUENCE (3) signals align for one direction.
    """

    def __init__(self):
        super().__init__("technical_analyst")
        self._cycle_count: int = 0

    def run(self, state: MarketState) -> TechnicalSignal:
        self._cycle_count += 1
        block_reasons: List[str] = []
        self.info("Computing indicators via pandas_ta")
        df = state.df.copy()

        # ── pandas_ta indicators ──
        rsi_s   = df.ta.rsi(length=14)
        ema20_s = df.ta.ema(length=20)
        ema50_s = df.ta.ema(length=50)
        atr_s   = df.ta.atr(length=14)

        macd_df = df.ta.macd(fast=12, slow=26, signal=9)
        bb_df   = df.ta.bbands(length=20, std=2)

        def last(s, default=0.0):
            if s is None or (hasattr(s, "empty") and s.empty):
                return default
            v = float(s.iloc[-1])
            return v if np.isfinite(v) else default

        rsi    = last(rsi_s)
        ema20  = last(ema20_s)
        ema50  = last(ema50_s)
        atr    = last(atr_s, state.atr)
        close  = state.close

        # MACD columns: MACD_12_26_9 and MACDs_12_26_9
        macd_val, macd_sig = 0.0, 0.0
        if macd_df is not None and not macd_df.empty:
            mc = [c for c in macd_df.columns if c.startswith("MACD_")]
            ms = [c for c in macd_df.columns if c.startswith("MACDs_")]
            if mc:
                macd_val = last(macd_df[mc[0]])
            if ms:
                macd_sig = last(macd_df[ms[0]])

        # Bollinger Bands columns: BBU_20_2.0 / BBL_20_2.0 / BBB_20_2.0 (bandwidth)
        bb_upper = close * 1.01
        bb_lower = close * 0.99
        bb_width_pct = 50.0   # default: allow signal (50th percentile)
        if bb_df is not None and not bb_df.empty:
            uc = [c for c in bb_df.columns if "BBU" in c]
            lc = [c for c in bb_df.columns if "BBL" in c]
            bc = [c for c in bb_df.columns if "BBB" in c]  # bandwidth column
            if uc:
                bb_upper = last(bb_df[uc[0]], close * 1.01)
            if lc:
                bb_lower = last(bb_df[lc[0]], close * 0.99)
            if bc:
                bw_series = bb_df[bc[0]].dropna()
                if len(bw_series) < Config.BB_WIDTH_LOOKBACK:
                    self.info(
                        f"BB width: only {len(bw_series)} bars available "
                        f"(need {Config.BB_WIDTH_LOOKBACK}) — skipping filter"
                    )
                else:
                    current_bw = float(bw_series.iloc[-1])
                    if current_bw > 0 and not np.isnan(current_bw):
                        window = bw_series.iloc[-Config.BB_WIDTH_LOOKBACK:]
                        bb_width_pct = float((window < current_bw).mean() * 100)
                    else:
                        self.info("BB width: current bandwidth is 0/NaN — skipping filter")

        # BB width filter: skip signals during narrow-range (choppy) markets
        if bb_width_pct < Config.BB_WIDTH_MIN_PCT:
            block_reasons.append(f"BB_WIDTH({bb_width_pct:.0f}<{Config.BB_WIDTH_MIN_PCT})")
            self.info(
                f"Blocked: {', '.join(block_reasons)} | "
                f"close={close:.2f} ATR={atr:.2f} BUY=- SELL=-"
            )
            return TechnicalSignal(
                "NONE", "NO_PATTERN", 0, close, 0.0, 0.0, 0.0, [],
                {"bb_width_pct": round(bb_width_pct, 1)},
                bb_width_pct=round(bb_width_pct, 1),
                block_reasons=list(block_reasons),
                buy_confluence_str="-",
                sell_confluence_str="-",
            )

        # ADX — inline Wilder, no extra dependency
        adx_val = self._calc_adx(df, Config.ADX_LOOKBACK)

        indicators = {
            "rsi":           round(rsi, 2),
            "ema20":         round(ema20, 2),
            "ema50":         round(ema50, 2),
            "macd":          round(macd_val, 4),
            "macd_signal":   round(macd_sig, 4),
            "bb_upper":      round(bb_upper, 2),
            "bb_lower":      round(bb_lower, 2),
            "atr":           round(atr, 2),
            "bb_width_pct":  round(bb_width_pct, 1),
            "adx":           round(adx_val, 2),
        }

        buy_reasons:  List[str] = []
        sell_reasons: List[str] = []

        # EMA200 trend gate — suppress counter-trend signals
        ema200_s = df.ta.ema(length=Config.TREND_EMA)
        ema200 = last(ema200_s)
        neutral_zone = atr * Config.TREND_NEUTRAL_ATR
        trend_up   = ema200 > 0 and close > ema200 + neutral_zone
        trend_down = ema200 > 0 and close < ema200 - neutral_zone
        indicators["ema200"] = round(ema200, 2)

        # 1. RSI(14) — tightened thresholds (backtest: 35/65 cleaner than 40/60)
        if rsi > 0:
            if rsi < Config.RSI_BUY:
                buy_reasons.append(f"RSI oversold ({rsi:.1f})")
            elif rsi > Config.RSI_SELL:
                sell_reasons.append(f"RSI overbought ({rsi:.1f})")

        # 2. Price vs EMA20
        if ema20 > 0:
            if close > ema20:
                buy_reasons.append(f"Price above EMA20 ({ema20:.2f})")
            else:
                sell_reasons.append(f"Price below EMA20 ({ema20:.2f})")

        # 3. EMA20 vs EMA50 trend alignment
        if ema20 > 0 and ema50 > 0:
            if ema20 > ema50:
                buy_reasons.append("EMA20 > EMA50 uptrend")
            else:
                sell_reasons.append("EMA20 < EMA50 downtrend")

        # 4. MACD vs signal line
        if macd_val != 0 or macd_sig != 0:
            if macd_val > macd_sig:
                buy_reasons.append("MACD bullish crossover")
            else:
                sell_reasons.append("MACD bearish crossover")

        # 5. Bollinger Band proximity
        bb_range = bb_upper - bb_lower
        if bb_range > 0:
            bb_pct = (close - bb_lower) / bb_range
            if bb_pct < 0.2:
                buy_reasons.append(f"Near lower BB ({bb_pct:.0%})")
            elif bb_pct > 0.8:
                sell_reasons.append(f"Near upper BB ({bb_pct:.0%})")

        buy_n, sell_n = len(buy_reasons), len(sell_reasons)

        # Apply EMA200 trend gate: zero out counter-trend count
        if not trend_up:   buy_n = 0
        if not trend_down: sell_n = 0

        # BB_RSI patterns get a lower confluence threshold (2 vs 3) because they are
        # rarer but statistically stronger (60% win rate in backtest).
        bb_rsi_buy  = (any("RSI" in r for r in buy_reasons)
                       and any("BB" in r for r in buy_reasons))
        bb_rsi_sell = (any("RSI" in r for r in sell_reasons)
                       and any("BB" in r for r in sell_reasons))
        req_buy  = Config.BB_RSI_MIN_CONFLUENCE if bb_rsi_buy  else Config.MIN_CONFLUENCE
        req_sell = Config.BB_RSI_MIN_CONFLUENCE if bb_rsi_sell else Config.MIN_CONFLUENCE

        # Hourly context dump (every 4th cycle ≈ 1h) — forward-test observability, file log only
        if self._cycle_count % 4 == 0:
            rsi_dir   = "->BUY"  if any("RSI"    in r for r in buy_reasons)  else ("->SELL"  if any("RSI"    in r for r in sell_reasons)  else "->neutral")
            pema_dir  = "->BUY"  if any("EMA20"  in r for r in buy_reasons)  else ("->SELL"  if any("EMA20"  in r for r in sell_reasons)  else "->neutral")
            cross_dir = "->BUY"  if any("EMA20 >" in r for r in buy_reasons) else ("->SELL"  if any("EMA20 <" in r for r in sell_reasons) else "->neutral")
            macd_dir  = "->BUY"  if any("MACD"   in r for r in buy_reasons)  else ("->SELL"  if any("MACD"   in r for r in sell_reasons)  else "->neutral")
            bb_dir    = "->BUY"  if any("BB"     in r for r in buy_reasons)  else ("->SELL"  if any("BB"     in r for r in sell_reasons)  else "->neutral")
            self.info(
                f"[HOURLY-DUMP c={self._cycle_count}] "
                f"close={close:.2f} ATR={atr:.2f} "
                f"RSI={rsi:.1f}{rsi_dir} PvEMA20{pema_dir} EMAcross{cross_dir} "
                f"MACD{macd_dir} BB{bb_dir} | "
                f"EMA20={ema20:.2f} EMA50={ema50:.2f} EMA200={ema200:.2f} "
                f"ADX={adx_val:.1f} BBW%={bb_width_pct:.0f} | "
                f"trend_up={trend_up} trend_down={trend_down} | "
                f"raw BUY={len(buy_reasons)} SELL={len(sell_reasons)} "
                f"eff BUY={buy_n}(need {req_buy}) SELL={sell_n}(need {req_sell})"
            )

        if buy_n >= req_buy and buy_n >= sell_n:
            direction, reasons, count = "BUY", buy_reasons, buy_n
            stop_loss   = round(close - atr * Config.ATR_STOP_MULT, 2)
            stop_dist   = close - stop_loss
            take_profit = round(close + stop_dist * Config.MIN_RR, 2)
        elif sell_n >= req_sell and sell_n > buy_n:
            direction, reasons, count = "SELL", sell_reasons, sell_n
            stop_loss   = round(close + atr * Config.ATR_STOP_MULT, 2)
            stop_dist   = stop_loss - close
            take_profit = round(close - stop_dist * Config.MIN_RR, 2)
        else:
            block_reasons.append(f"CONFLUENCE(BUY={buy_n}/{req_buy},SELL={sell_n}/{req_sell})")
            self.info(
                f"Blocked: {', '.join(block_reasons)} | "
                f"close={close:.2f} ATR={atr:.2f} "
                f"BUY={buy_n} SELL={sell_n}"
            )
            return TechnicalSignal(
                "NONE", "NO_PATTERN", 0, close, 0.0, 0.0, 0.0, [], indicators,
                bb_width_pct=round(bb_width_pct, 1), adx_value=round(adx_val, 2),
                block_reasons=list(block_reasons),
                buy_confluence_str=f"{buy_n}/{req_buy}",
                sell_confluence_str=f"{sell_n}/{req_sell}",
            )

        stop_dist  = abs(close - stop_loss)
        rr         = abs(take_profit - close) / stop_dist if stop_dist > 0 else 0.0
        pattern    = self._name_pattern(reasons, direction)
        confidence = round(count / 5, 2)

        # v12: HTF (4H) bias gate — block entries when macro regime opposes signal.
        # Closes the 15m EMA200 blind spot (Trade #4 root cause) and the Q2 2026
        # drawdown root cause: 15m sees too little context to detect regime shifts.
        # Net behavior: BUY allowed ONLY when 4H trend is BULL; SELL ONLY when BEAR.
        # NEUTRAL HTF means trend regime is unclear — both directions blocked.
        if Config.HTF_BIAS_ENABLED:
            htf = state.htf_bias
            if (direction == "BUY" and htf != "BULL") or (
                direction == "SELL" and htf != "BEAR"
            ):
                block_reasons.append(f"HTF_BIAS({htf})")
                self.info(
                    f"Blocked: {', '.join(block_reasons)} | "
                    f"close={close:.2f} ATR={atr:.2f} "
                    f"BUY={buy_n} SELL={sell_n} HTF={htf}"
                )
                return TechnicalSignal(
                    "NONE", "NO_PATTERN", 0, close, 0.0, 0.0, 0.0, [], indicators,
                    bb_width_pct=round(bb_width_pct, 1), adx_value=round(adx_val, 2),
                    block_reasons=list(block_reasons),
                    buy_confluence_str=f"{buy_n}/{req_buy}",
                    sell_confluence_str=f"{sell_n}/{req_sell}",
                )

        # Disabled-pattern gate — suppresses without removing detection logic.
        # Edit Config.DISABLED_PATTERNS to toggle patterns without code changes.
        if pattern in Config.DISABLED_PATTERNS:
            block_reasons.append(f"DISABLED({pattern})")
            self.info(
                f"Blocked: {', '.join(block_reasons)} | "
                f"close={close:.2f} ATR={atr:.2f} "
                f"BUY={buy_n} SELL={sell_n}"
            )
            return TechnicalSignal(
                "NONE", "NO_PATTERN", 0, close, 0.0, 0.0, 0.0, [], indicators,
                bb_width_pct=round(bb_width_pct, 1), adx_value=round(adx_val, 2),
                block_reasons=list(block_reasons),
                buy_confluence_str=f"{buy_n}/{req_buy}",
                sell_confluence_str=f"{sell_n}/{req_sell}",
            )

        # ADX filter: EMA_MACD_TREND requires confirmed trend momentum (ADX >= 25), both directions.
        # SELL: 60 trades at 28% WR = -$1,283 drag. BUY: Q2 2026 mean-reverting regime losses.
        if "EMA_MACD_TREND" in pattern:
            if adx_val < Config.ADX_TREND_THRESHOLD:
                block_reasons.append(f"ADX({adx_val:.0f}<{Config.ADX_TREND_THRESHOLD})")
                self.info(
                    f"Blocked: {', '.join(block_reasons)} | "
                    f"close={close:.2f} ATR={atr:.2f} "
                    f"BUY={buy_n} SELL={sell_n}"
                )
                return TechnicalSignal(
                    "NONE", "NO_PATTERN", 0, close, 0.0, 0.0, 0.0, [], indicators,
                    bb_width_pct=round(bb_width_pct, 1), adx_value=round(adx_val, 2),
                    block_reasons=list(block_reasons),
                    buy_confluence_str=f"{buy_n}/{req_buy}",
                    sell_confluence_str=f"{sell_n}/{req_sell}",
                )

        # RSI ceiling for BUY — overbought entries have no momentum room (Q2 2026: 3/4 losses RSI>70).
        if direction == "BUY" and rsi >= Config.RSI_CEILING_BUY:
            block_reasons.append(f"RSI_CEIL({rsi:.0f}>={Config.RSI_CEILING_BUY})")
            self.info(
                f"Blocked: {', '.join(block_reasons)} | "
                f"close={close:.2f} ATR={atr:.2f} "
                f"BUY={buy_n} SELL={sell_n}"
            )
            return TechnicalSignal(
                "NONE", "NO_PATTERN", 0, close, 0.0, 0.0, 0.0, [], indicators,
                bb_width_pct=round(bb_width_pct, 1), adx_value=round(adx_val, 2),
                block_reasons=list(block_reasons),
                buy_confluence_str=f"{buy_n}/{req_buy}",
                sell_confluence_str=f"{sell_n}/{req_sell}",
            )

        self.info(
            f"Signal={direction} pattern={pattern} confluence={count} "
            f"RR={rr:.2f} BBW%={bb_width_pct:.0f} ADX={adx_val:.1f}"
        )
        return TechnicalSignal(
            direction=direction,
            pattern=pattern,
            confluence_count=count,
            entry=close,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            reasons=reasons,
            indicators=indicators,
            bb_width_pct=round(bb_width_pct, 1),
            adx_value=round(adx_val, 2),
            block_reasons=[],
            buy_confluence_str=f"{buy_n}/{req_buy}",
            sell_confluence_str=f"{sell_n}/{req_sell}",
        )

    @staticmethod
    def _calc_adx(df: pd.DataFrame, period: int = 14) -> float:
        """
        Inline Wilder ADX(period). No external dependency.
        TR = max(H-L, |H-prevC|, |L-prevC|)
        +DM / -DM clipped to zero when the other is larger.
        Wilder smooth ≡ ewm(alpha=1/period, adjust=False).
        Returns the last ADX value, or 0.0 if not enough data.
        """
        if len(df) < period * 2 + 1:
            return 0.0
        high  = df["High"]
        low   = df["Low"]
        close = df["Close"]
        pc = close.shift(1)
        ph = high.shift(1)
        pl = low.shift(1)

        tr = pd.concat([
            (high - low),
            (high - pc).abs(),
            (low  - pc).abs(),
        ], axis=1).max(axis=1)

        up   = high - ph
        down = pl   - low

        plus_dm  = np.where((up > down)   & (up > 0),   up,   0.0)
        minus_dm = np.where((down > up)   & (down > 0), down, 0.0)

        alpha = 1.0 / period
        tr14  = pd.Series(tr.values,    index=df.index).ewm(alpha=alpha, adjust=False).mean()
        pdm14 = pd.Series(plus_dm,      index=df.index).ewm(alpha=alpha, adjust=False).mean()
        mdm14 = pd.Series(minus_dm,     index=df.index).ewm(alpha=alpha, adjust=False).mean()

        tr14_safe = tr14.replace(0, np.nan)
        pdi = 100 * pdm14 / tr14_safe
        mdi = 100 * mdm14 / tr14_safe

        denom = (pdi + mdi).replace(0, np.nan)
        dx    = 100 * (pdi - mdi).abs() / denom
        adx   = dx.ewm(alpha=alpha, adjust=False).mean()

        val = adx.iloc[-1]
        return float(val) if np.isfinite(val) else 0.0

    def _name_pattern(self, reasons: List[str], direction: str) -> str:
        has_rsi       = any("RSI" in r for r in reasons)
        has_ema_cross = any("EMA20 >" in r or "EMA20 <" in r for r in reasons)
        has_macd      = any("MACD" in r for r in reasons)
        has_bb        = any("BB" in r for r in reasons)
        has_ema_price = any("above EMA20" in r or "below EMA20" in r for r in reasons)

        if has_rsi and has_macd and has_ema_cross:
            return f"TRIPLE_SIGNAL_{direction}"
        if has_rsi and has_bb:
            return f"BB_RSI_REVERSAL_{direction}"
        if has_ema_cross and has_macd:
            return f"EMA_MACD_TREND_{direction}"
        if has_rsi and has_ema_price:
            return f"RSI_EMA_SIGNAL_{direction}"
        if has_rsi:
            return f"RSI_SIGNAL_{direction}"
        if has_ema_cross:
            return f"EMA_TREND_{direction}"
        return f"CONFLUENCE_{direction}"


# ── Risk Manager Agent ────────────────────────────────────────────────────────
class RiskManagerAgent(Agent):
    """
    Four sequential gates (any failure blocks the trade):
    1. Regime must not be VOLATILE
    2. confluence_count >= MIN_CONFLUENCE (3)
    3. Daily realized loss < $300
    4. R:R >= 1.5
    Position size: lot = risk_$ / (contract_size × stop_distance)
    """

    def __init__(self):
        super().__init__("risk_manager")
        self._daily_loss: float = 0.0
        self._daily_date: str = ""
        self._monthly_pnl: float = 0.0    # running net P&L for the current calendar month
        self._monthly_month: str = ""     # "YYYY-MM" of the current tracking window

    def run(
        self,
        signal: TechnicalSignal,
        state: MarketState,
        journal: List[TradeRecord],
        account_size: Optional[float] = None,
    ) -> RiskDecision:
        # v11: dynamic account sizing — defaults to Config.ACCOUNT_SIZE when caller
        # doesn't pass a live balance (paper mode, backtest, MT5 disconnected).
        if account_size is None or account_size <= 0:
            account_size = Config.ACCOUNT_SIZE
        now   = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        month = now.strftime("%Y-%m")

        # Refresh daily loss from journal on new day
        if self._daily_date != today:
            self._daily_loss = sum(
                abs(t.pnl)
                for t in journal
                if t.timestamp.startswith(today) and t.pnl < 0 and t.status != "OPEN"
            )
            self._daily_date = today

        # Refresh monthly P&L from journal on new calendar month
        if self._monthly_month != month:
            self._monthly_pnl = sum(
                t.pnl for t in journal
                if t.timestamp.startswith(month) and t.status != "OPEN"
            )
            self._monthly_month = month

        if state.regime == "VOLATILE":
            return self._block("Volatile regime — standing aside")

        # DXY soft-confluence: gold is inversely correlated with DXY.
        # DXY UP → headwind for BUY signals; DXY DOWN → headwind for SELL signals.
        effective_confluence = signal.confluence_count
        if state.dxy_trend == "UP" and signal.direction == "BUY":
            effective_confluence -= 1
            self.info(f"DXY UP: BUY confluence reduced {signal.confluence_count} -> {effective_confluence}")
        elif state.dxy_trend == "DOWN" and signal.direction == "SELL":
            effective_confluence -= 1
            self.info(f"DXY DOWN: SELL confluence reduced {signal.confluence_count} -> {effective_confluence}")

        # Pattern-specific confluence threshold: BB_RSI is a rarer, higher-quality setup
        required = (Config.BB_RSI_MIN_CONFLUENCE
                    if signal.pattern.startswith("BB_RSI")
                    else Config.MIN_CONFLUENCE)
        if effective_confluence < required:
            return self._block(
                f"Confluence too low ({effective_confluence} < {required} for {signal.pattern})"
            )

        if self._daily_loss >= Config.DAILY_LOSS_LIMIT:
            return self._block(
                f"Daily loss limit reached (${self._daily_loss:.2f} >= ${Config.DAILY_LOSS_LIMIT})"
            )

        stop_dist = abs(signal.entry - signal.stop_loss)
        tp_dist   = abs(signal.take_profit - signal.entry)
        if stop_dist <= 0:
            return self._block("Stop-loss distance is zero")

        rr = tp_dist / stop_dist
        if rr < Config.MIN_RR:
            return self._block(f"R:R too low ({rr:.2f} < {Config.MIN_RR})")

        max_risk    = account_size * Config.MAX_RISK_PCT
        remaining   = Config.DAILY_LOSS_LIMIT - self._daily_loss
        risk_amount = min(max_risk, remaining)

        raw_lot  = risk_amount / (Config.GOLD_CONTRACT_SIZE * stop_dist)
        lot_size = max(
            Config.MIN_LOT,
            min(Config.MAX_LOT, round(raw_lot / Config.LOT_STEP) * Config.LOT_STEP),
        )

        # Monthly drawdown brake: halve lot when month is down > $150
        brake_active = self._monthly_pnl < -Config.MONTHLY_DRAWDOWN_BRAKE
        if brake_active:
            lot_size = max(
                Config.MIN_LOT,
                round(lot_size * Config.MONTHLY_BRAKE_MULTIPLIER / Config.LOT_STEP) * Config.LOT_STEP,
            )
            self.warn(
                f"Monthly brake active (month P&L=${self._monthly_pnl:+.2f}): "
                f"lot halved to {lot_size:.2f}"
            )

        actual_risk = lot_size * Config.GOLD_CONTRACT_SIZE * stop_dist

        self.info(
            f"APPROVED lot={lot_size:.2f} risk=${actual_risk:.2f} RR={rr:.2f} "
            f"acct=${account_size:.2f} monthPnL=${self._monthly_pnl:+.2f} "
            f"brake={brake_active}"
        )
        return RiskDecision(
            approved=True,
            reason="All checks passed",
            lot_size=lot_size,
            risk_amount=actual_risk,
            rr_ratio=round(rr, 2),
            monthly_brake_active=brake_active,
        )

    def _block(self, reason: str) -> RiskDecision:
        self.warn(f"BLOCKED: {reason}")
        return RiskDecision(approved=False, reason=reason)

    def refresh_state_from_journal(self, journal: List[TradeRecord]) -> None:
        """Force-refresh internal accounting state from the journal.

        Called after startup MT5 sync so today's loss limit and monthly P&L brake
        are correct before the first trading cycle runs.
        """
        now   = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        month = now.strftime("%Y-%m")

        self._daily_loss = sum(
            abs(t.pnl)
            for t in journal
            if t.timestamp.startswith(today) and t.pnl < 0 and t.status != "OPEN"
        )
        self._daily_date = today

        self._monthly_pnl = sum(
            t.pnl for t in journal
            if t.timestamp.startswith(month) and t.status != "OPEN"
        )
        self._monthly_month = month

        self.info(
            f"Risk state refreshed — daily_loss=${self._daily_loss:.2f} "
            f"monthly_pnl=${self._monthly_pnl:+.2f}"
        )


# ── Reporter Agent ────────────────────────────────────────────────────────────
class ReporterAgent(Agent):
    """Sends Markdown-formatted Telegram messages for every signal and trade close."""

    def __init__(self):
        super().__init__("reporter")

    def send_signal(
        self,
        signal: TechnicalSignal,
        risk: RiskDecision,
        state: MarketState,
        paper: bool,
    ):
        mode    = "PAPER" if paper else "LIVE"
        verdict = "APPROVED" if risk.approved else "BLOCKED"
        lines = [
            f"<b>Gold Signal — {verdict}</b>",
            f"Mode: <code>{mode}</code> | {signal.direction} <code>{signal.pattern}</code>",
            f"Regime: {state.regime} | Confluence: {signal.confluence_count}/5",
            "",
            f"Entry:  ${signal.entry:,.2f}",
            f"SL:     ${signal.stop_loss:,.2f}",
            f"TP:     ${signal.take_profit:,.2f}",
        ]
        if risk.approved:
            lines += [
                f"Lot:    {risk.lot_size:.2f}",
                f"Risk:   ${risk.risk_amount:.2f}",
                f"R:R:    {risk.rr_ratio:.2f}",
            ]
        else:
            lines.append(f"Reason: <i>{risk.reason}</i>")

        lines += ["", "<b>Signals:</b>"] + [f"  • {r}" for r in signal.reasons]
        lines.append(f"\n<i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</i>")
        self._send("\n".join(lines))

    def send_closed(self, trade: TradeRecord):
        label = {
            "CLOSED_WIN": "PROFIT",
            "CLOSED_LOSS": "LOSS",
            "CLOSED_BE": "BREAKEVEN",
        }.get(trade.status, trade.status)
        self._send(
            f"<b>Trade Closed — {label}</b>\n"
            f"{trade.direction} <code>{trade.pattern}</code>\n"
            f"Entry ${trade.entry:,.2f} → Exit ${trade.exit_price:,.2f}\n"
            f"P&amp;L: <b>${trade.pnl:+.2f}</b>\n"
            f"<i>{trade.exit_timestamp}</i>"
        )

    def send_info(self, msg: str):
        self._send(f"<i>{msg}</i>")

    def send_telegram(self, text: str):
        """Public alias for _send — accepts raw HTML text."""
        self._send(text)

    def _send(self, text: str):
        token   = Config.TELEGRAM_BOT_TOKEN
        chat_id = Config.TELEGRAM_CHAT_ID
        if not token or not chat_id:
            self.warn("Telegram not configured — skipping notification")
            return
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            if r.status_code != 200:
                self.warn(f"Telegram HTTP {r.status_code}: {r.text[:200]}")
        except Exception as exc:
            self.error(f"Telegram send error: {exc}")


# ── Learning Agent ────────────────────────────────────────────────────────────
class LearningAgent(Agent):
    """
    After every cycle: computes per-pattern win rates from closed trades.
    Auto-generates skills/<pattern>.md when win rate >= 70% over >= 3 trades.
    """

    def __init__(self):
        super().__init__("learning_agent")
        Config.SKILLS_DIR.mkdir(exist_ok=True)

    def analyze(self, journal: List[TradeRecord]):
        closed = [t for t in journal if t.status != "OPEN"]
        if not closed:
            return

        stats: Dict[str, Dict] = {}
        for t in closed:
            s = stats.setdefault(t.pattern, {"total": 0, "wins": 0, "pnl": 0.0})
            s["total"] += 1
            s["pnl"]   += t.pnl
            if t.status == "CLOSED_WIN":
                s["wins"] += 1

        for pattern, s in stats.items():
            total    = s["total"]
            wins     = s["wins"]
            win_rate = wins / total if total else 0.0
            self.info(f"{pattern}: {total} trades, {win_rate:.0%} win rate")
            if total >= 3 and win_rate >= 0.70:
                self._write_skill(pattern, wins, total, win_rate, s["pnl"])

    def _write_skill(
        self, pattern: str, wins: int, total: int, win_rate: float, total_pnl: float
    ):
        avg_pnl   = total_pnl / total
        direction = "BUY" if pattern.endswith("_BUY") else "SELL"
        content = f"""# Skill: {pattern}

## Performance
| Metric | Value |
|--------|-------|
| Win Rate | {win_rate:.0%} ({wins}/{total} trades) |
| Avg P&L per trade | ${avg_pnl:+.2f} |
| Cumulative P&L | ${total_pnl:+.2f} |

## Entry Conditions
{self._entry_conditions(pattern, direction)}

## Risk Parameters
- Stop Loss: entry ± ATR(14) × {Config.ATR_STOP_MULT}
- Take Profit: stop distance × {Config.MIN_RR} (R:R ≥ {Config.MIN_RR})
- Max risk per trade: {Config.MAX_RISK_PCT:.0%} of account (${Config.ACCOUNT_SIZE * Config.MAX_RISK_PCT:.0f})
- Min confluence signals: {Config.MIN_CONFLUENCE}/5

## Filters
- Skip: VOLATILE regime (ATR% > {Config.ATR_VOLATILE_PCT}%)
- Daily loss limit: ${Config.DAILY_LOSS_LIMIT}

*Auto-generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by learning_agent*
"""
        skill_file = Config.SKILLS_DIR / f"{pattern.lower()}.md"
        skill_file.write_text(content, encoding="utf-8")
        self.info(f"Skill written: {skill_file}")

    def _entry_conditions(self, pattern: str, direction: str) -> str:
        if "TRIPLE_SIGNAL" in pattern:
            return (
                "- RSI(14) < 40 — oversold\n"
                "- MACD above signal line — bullish momentum\n"
                "- EMA20 > EMA50 — uptrend confirmation"
            ) if direction == "BUY" else (
                "- RSI(14) > 60 — overbought\n"
                "- MACD below signal line — bearish momentum\n"
                "- EMA20 < EMA50 — downtrend confirmation"
            )
        if "BB_RSI" in pattern:
            return (
                "- RSI(14) < 40 — oversold\n- Price in bottom 20% of Bollinger Band"
            ) if direction == "BUY" else (
                "- RSI(14) > 60 — overbought\n- Price in top 20% of Bollinger Band"
            )
        if "EMA_MACD" in pattern:
            return (
                "- EMA20 > EMA50 — confirmed uptrend\n- MACD bullish"
            ) if direction == "BUY" else (
                "- EMA20 < EMA50 — confirmed downtrend\n- MACD bearish"
            )
        return f"- At least {Config.MIN_CONFLUENCE} aligned {direction} signals"


# ── Trade Journal ─────────────────────────────────────────────────────────────
class TradeJournal:
    def __init__(self):
        self._file   = Path(Config.JOURNAL_FILE)
        self._lock   = threading.Lock()
        self._trades: List[TradeRecord] = []
        self._load()

    def _load(self):
        if not self._file.exists():
            return
        try:
            raw = json.loads(self._file.read_text(encoding="utf-8"))
            self._trades = [TradeRecord(**r) for r in raw]
            log.info(f"Journal: {len(self._trades)} trades loaded")
        except Exception as exc:
            log.error(f"Journal load error: {exc}")

    def _save(self):
        try:
            self._file.write_text(
                json.dumps([asdict(t) for t in self._trades], indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            log.error(f"Journal save error: {exc}")

    def add(self, trade: TradeRecord):
        with self._lock:
            self._trades.append(trade)
            self._save()

    def update(self, trade_id: str, **kwargs):
        with self._lock:
            for t in self._trades:
                if t.id == trade_id:
                    for k, v in kwargs.items():
                        setattr(t, k, v)
                    break
            self._save()

    def get_open(self) -> List[TradeRecord]:
        return [t for t in self._trades if t.status == "OPEN"]

    def get_all(self) -> List[TradeRecord]:
        return list(self._trades)

    def show(self):
        if not self._trades:
            print("No trades recorded yet.")
            return

        rows = []
        for t in sorted(self._trades, key=lambda x: x.timestamp, reverse=True):
            pnl_str = f"${t.pnl:+.2f}" if t.status != "OPEN" else "open"
            rows.append([
                t.id[:8],
                t.timestamp[:16],
                t.direction,
                t.pattern[:24],
                f"${t.entry:.2f}",
                f"${t.exit_price:.2f}" if t.exit_price else "—",
                pnl_str,
                t.status,
                "paper" if t.paper else "LIVE",
            ])

        print("\n" + "=" * 96)
        print("TRADE JOURNAL")
        print("=" * 96)
        print(tabulate(
            rows,
            headers=["ID", "Time", "Dir", "Pattern", "Entry", "Exit", "P&L", "Status", "Mode"],
            tablefmt="simple",
        ))

        stats: Dict[str, Dict] = {}
        for t in self._trades:
            if t.status == "OPEN":
                continue
            s = stats.setdefault(t.pattern, {"total": 0, "wins": 0, "pnl": 0.0})
            s["total"] += 1
            s["pnl"]   += t.pnl
            if t.status == "CLOSED_WIN":
                s["wins"] += 1

        if stats:
            pat_rows = []
            for pattern, s in sorted(stats.items()):
                total, wins = s["total"], s["wins"]
                wr    = f"{wins / total:.0%}" if total else "-"
                avg   = f"${s['pnl'] / total:+.2f}" if total else "-"
                skill = " ★" if total >= 3 and (wins / total) >= 0.70 else ""
                pat_rows.append([pattern[:30], total, wins, wr, avg, f"${s['pnl']:+.2f}", skill])

            print("\n" + "=" * 96)
            print("PATTERN WIN RATES  (★ = skill file auto-generated)")
            print("=" * 96)
            print(tabulate(
                pat_rows,
                headers=["Pattern", "Trades", "Wins", "Win%", "Avg P&L", "Total P&L", ""],
                tablefmt="simple",
            ))
        print()


# ── MT5 Broker ────────────────────────────────────────────────────────────────
class MT5Broker:
    def __init__(self):
        self.connected = False
        self.log = logging.getLogger("MT5Broker")
        self.symbol: str = Config.MT5_SYMBOL
        self.contract_size: float = Config.GOLD_CONTRACT_SIZE

    def connect(self) -> bool:
        if not MT5_AVAILABLE:
            self.log.warning("MetaTrader5 package not importable on this platform")
            return False
        ok = mt5.initialize(
            login=Config.MT5_LOGIN,
            password=Config.MT5_PASSWORD,
            server=Config.MT5_SERVER,
        )
        if not ok:
            self.log.error(f"MT5 init failed: {mt5.last_error()}")
            return False
        info = mt5.account_info()
        if info is None:
            self.log.error("Could not retrieve MT5 account info")
            mt5.shutdown()
            return False
        self.log.info(f"MT5 connected | login={info.login} | balance=${info.balance:.2f}")
        self.connected = True
        sym_info = mt5.symbol_info(self.symbol)
        if sym_info is None:
            self.log.error(f"Symbol {self.symbol} not found on this broker")
            mt5.shutdown()
            self.connected = False
            return False
        if sym_info.trade_mode != 4:
            self.log.error(f"Symbol {self.symbol} not tradeable (mode={sym_info.trade_mode})")
            mt5.shutdown()
            self.connected = False
            return False
        if sym_info.trade_contract_size != 100:
            self.log.warning(
                f"Non-standard contract size: {sym_info.trade_contract_size}. "
                "Position sizing may need adjustment."
            )
        mt5.symbol_select(self.symbol, True)
        self.contract_size = sym_info.trade_contract_size
        self.log.info(f"MT5 symbol verified: {self.symbol} ({self.contract_size} oz/lot)")
        return True

    def disconnect(self):
        if MT5_AVAILABLE and self.connected:
            mt5.shutdown()
            self.connected = False

    def get_balance(self) -> float:
        """Return live MT5 account balance (USD), or 0.0 if unavailable.

        Used by OrchestratorAgent to size positions against the real account
        rather than the static Config.ACCOUNT_SIZE constant.
        """
        if not MT5_AVAILABLE or not self.connected:
            return 0.0
        try:
            info = mt5.account_info()
            return float(info.balance) if info is not None else 0.0
        except Exception as exc:
            self.log.warning(f"get_balance failed: {exc}")
            return 0.0

    def get_current_spread(self) -> float:
        if not MT5_AVAILABLE or not self.connected:
            return 0.0
        tick = mt5.symbol_info_tick(self.symbol)
        return round(tick.ask - tick.bid, 5) if tick else 0.0

    def send_order(self, signal: TechnicalSignal, lot: float) -> int:
        if not self.connected:
            self.log.error("send_order called while disconnected from MT5")
            return 0
        sym      = self.symbol
        sym_info = mt5.symbol_info(sym)
        if sym_info is None:
            self.log.error(f"Symbol {sym} not found in MT5")
            return 0
        if not sym_info.visible:
            mt5.symbol_select(sym, True)
        tick = mt5.symbol_info_tick(sym)
        if tick is None:
            self.log.error("Could not get MT5 tick data")
            return 0
        order_type = mt5.ORDER_TYPE_BUY if signal.direction == "BUY" else mt5.ORDER_TYPE_SELL
        price      = tick.ask if signal.direction == "BUY" else tick.bid
        req = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       sym,
            "volume":       lot,
            "type":         order_type,
            "price":        price,
            "sl":           signal.stop_loss,
            "tp":           signal.take_profit,
            "deviation":    20,
            "magic":        202406,
            "comment":      f"GoldBot:{signal.pattern[:20]}",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(req)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            self.log.error(f"Order failed retcode={result.retcode} comment={result.comment}")
            return 0
        self.log.info(f"MT5 order placed ticket={result.order}")
        return result.order

    def check_position(self, ticket: int) -> Optional[Dict]:
        if not MT5_AVAILABLE or not self.connected:
            return None
        positions = mt5.positions_get(ticket=ticket)
        if positions:
            pos = positions[0]
            return {"open": True, "profit": pos.profit, "price": pos.price_current}
        deals = mt5.history_deals_get(ticket=ticket)
        if deals:
            for d in deals:
                if d.entry == mt5.DEAL_ENTRY_OUT:
                    return {"open": False, "profit": d.profit, "price": d.price}
        return None

    def sync_all_open_positions(self, journal: "TradeJournal") -> None:
        """Reconcile journal OPEN trades with MT5 actual state on startup/restart.

        Called once after MT5 connects. For each OPEN journal entry with a ticket,
        checks MT5 position history and closes the entry if MT5 says it's done.
        This fixes the case where the system was restarted after MT5 closed positions
        (SL/TP hit while Python was offline).
        """
        if not self.connected:
            return
        open_trades = journal.get_open()
        if not open_trades:
            return

        self.log.info(f"Syncing {len(open_trades)} open journal entries with MT5...")
        for trade in open_trades:
            if not trade.mt5_ticket:
                continue

            # Still open in MT5 — nothing to do
            positions = mt5.positions_get(ticket=trade.mt5_ticket)
            if positions and len(positions) > 0:
                self.log.info(
                    f"Trade {trade.id[:8]}: still OPEN in MT5 (ticket={trade.mt5_ticket})"
                )
                continue

            # Not in live positions — search position history for the closing deal
            deals = mt5.history_deals_get(position=trade.mt5_ticket)
            if not deals:
                self.log.warning(
                    f"Trade {trade.id[:8]}: no MT5 record for ticket={trade.mt5_ticket}"
                    " — leaving as OPEN"
                )
                continue

            for deal in deals:
                if deal.entry != mt5.DEAL_ENTRY_OUT:
                    continue
                exit_price = deal.price
                pnl        = round(deal.profit, 2)
                status     = ("CLOSED_WIN"  if pnl > 0
                              else "CLOSED_LOSS" if pnl < 0
                              else "CLOSED_BE")
                exit_ts    = (datetime.fromtimestamp(deal.time, tz=timezone.utc)
                              .strftime("%Y-%m-%d %H:%M:%S"))
                journal.update(
                    trade.id,
                    status=status,
                    exit_price=exit_price,
                    pnl=pnl,
                    exit_timestamp=exit_ts,
                )
                self.log.info(
                    f"Synced trade {trade.id[:8]}: {status} @ ${exit_price:.2f} "
                    f"P&L={pnl:+.2f} (MT5 closed {exit_ts})"
                )
                break

    def modify_sl(self, ticket: int, new_sl: float) -> bool:
        """Move the stop-loss of an open MT5 position via TRADE_ACTION_SLTP. Returns True on success."""
        if not MT5_AVAILABLE or not self.connected:
            self.log.warning(f"modify_sl({ticket}): MT5 not available or disconnected")
            return False
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            self.log.warning(f"modify_sl({ticket}): position not found in MT5")
            return False
        pos = positions[0]
        req = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "symbol":   pos.symbol,
            "position": ticket,
            "sl":       new_sl,
            "tp":       pos.tp,
        }
        result = mt5.order_send(req)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            self.log.error(
                f"modify_sl ticket={ticket} failed: retcode={result.retcode} "
                f"comment={result.comment}"
            )
            return False
        self.log.info(f"SL modified: ticket={ticket} new_sl={new_sl:.2f}")
        return True


# ── Orchestrator Agent ────────────────────────────────────────────────────────
class OrchestratorAgent(Agent):
    """
    Coordinates all agents in order each cycle:
    1. Check open paper positions against latest candle H/L → close at SL/TP
    2. market_analyst   → MarketState
    3. technical_analyst → TechnicalSignal
    4. risk_manager     → RiskDecision  (only if signal != NONE)
    5. Execute trade    (paper record or live MT5 order)
    6. reporter         → Telegram
    7. learning_agent   → win-rate stats + skill file generation
    """

    def __init__(self):
        super().__init__("orchestrator")
        self.broker            = MT5Broker()
        self.reporter          = ReporterAgent()
        # v12: pass reporter into market_analyst so it can send a Telegram alert
        # when live-mode falls back from MT5 to yfinance (data integrity event).
        self.market_analyst    = MarketAnalystAgent(broker=self.broker, reporter=self.reporter)
        self.technical_analyst = TechnicalAnalystAgent()
        self.risk_manager      = RiskManagerAgent()
        self.learning_agent    = LearningAgent()
        self.news_filter       = NewsFilterAgent()
        self.journal           = TradeJournal()
        self._cycle_lock       = threading.Lock()
        self._consec_loss: int = 0
        self._consec_date: str = ""
        self._session_stats: Dict[str, Any] = {
            "session_start": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "cycles_total":   0,
            "trades_opened":  0,
            "trades_closed":  0,
            "errors":         0,
            "mt5_disconnects": 0,
            "config": {
                "atr_stop_mult":         Config.ATR_STOP_MULT,
                "rsi_ceiling_buy":       Config.RSI_CEILING_BUY,
                "adx_threshold":         Config.ADX_TREND_THRESHOLD,
                "max_open_positions":    Config.MAX_OPEN_POSITIONS,
                "min_rr":                Config.MIN_RR,
                "risk_per_trade_pct":    round(Config.MAX_RISK_PCT * 100, 2),
                "daily_loss_limit":      Config.DAILY_LOSS_LIMIT,
                "disabled_patterns":     list(Config.DISABLED_PATTERNS),
                "mt5_symbol":            Config.MT5_SYMBOL,
                "max_spread_usd":        Config.MAX_SPREAD_USD,
                "friday_cutoff_hour":    Config.FRIDAY_CUTOFF_HOUR_UTC,
                "live_balance_sizing":   True,
                "be_trigger_r":          Config.BE_TRIGGER_R,
                "be_cushion_usd":        Config.BE_CUSHION_USD,
                "htf_bias_enabled":      Config.HTF_BIAS_ENABLED,
                "htf_interval":          Config.HTF_INTERVAL,
                "htf_ema_len":           Config.HTF_EMA_LEN,
                "htf_slope_lookback":    Config.HTF_SLOPE_LOOKBACK,
                "mt5_fetch_n_bars":      Config.MT5_FETCH_N_BARS,
                "data_divergence_usd":   Config.DATA_DIVERGENCE_USD,
            },
        }
        self._last_trade_event: Optional[Dict[str, Any]] = None
        self._last_market_state: Optional[MarketState] = None
        self._last_spread: float = 0.0

        if not Config.PAPER_TRADE:
            if not self.broker.connect():
                self.warn("MT5 connection failed — falling back to paper trade")
                Config.PAPER_TRADE = True
            elif self.broker.contract_size != Config.GOLD_CONTRACT_SIZE:
                self.warn(
                    f"Contract size adjusted: {Config.GOLD_CONTRACT_SIZE} -> "
                    f"{self.broker.contract_size} (symbol={self.broker.symbol})"
                )
                Config.GOLD_CONTRACT_SIZE = self.broker.contract_size

        if not Config.PAPER_TRADE and self.broker.connected:
            # Sync any positions closed in MT5 while Python was offline
            self.broker.sync_all_open_positions(self.journal)
            # Rebuild risk counters from the now-updated journal
            self.risk_manager.refresh_state_from_journal(self.journal.get_all())
            self._refresh_consec_loss()

    def run_cycle(self):
        if not self._cycle_lock.acquire(blocking=False):
            self.warn("Previous cycle still running — skipping")
            return
        try:
            self._do_cycle()
        finally:
            self._cycle_lock.release()

    def _do_cycle(self):
        self._session_stats["cycles_total"] += 1
        self._last_trade_event = None
        _cycle_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        state:       Optional[MarketState]    = None
        signal:      Optional[TechnicalSignal] = None
        skip_reason: Optional[str]            = None
        try:
            self.info("-" * 52)
            self.info("Cycle start")
            if not Config.PAPER_TRADE and self.broker.connected:
                self._last_spread = self.broker.get_current_spread()
                self.info(f"Live spread: ${self._last_spread:.2f} ({Config.MT5_SYMBOL})")

            self._check_open_positions()

            # Session filter: only trade London open → NY close (08:00-21:00 UTC)
            now_dt = datetime.now(timezone.utc)
            now_hour = now_dt.hour
            if not (Config.SESSION_START_UTC <= now_hour < Config.SESSION_END_UTC):
                self.info(f"Outside session ({now_hour:02d}:xx UTC) — skipping signal")
                skip_reason = "OUTSIDE_SESSION"
                return

            # v11: Friday afternoon cutoff — no NEW entries after 17:00 UTC Friday.
            # Open positions are still managed above by _check_open_positions().
            # Reduces weekend gap exposure (XAUUSD gaps ±$30-80 on geopol/policy news).
            if (
                now_dt.weekday() == 4
                and now_hour >= Config.FRIDAY_CUTOFF_HOUR_UTC
            ):
                self.info(
                    f"Friday cutoff ({now_hour:02d}:xx UTC >= "
                    f"{Config.FRIDAY_CUTOFF_HOUR_UTC:02d}:00) — no new entries"
                )
                skip_reason = "FRIDAY_CUTOFF"
                return

            # Consecutive-loss guard: reset on new day
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if self._consec_date != today:
                self._consec_loss = 0
                self._consec_date = today
            # Update from journal (in case previous cycles ran)
            recent = [t for t in self.journal.get_all()
                      if t.timestamp.startswith(today) and t.status != "OPEN"]
            if recent:
                streak = 0
                for t in reversed(recent):
                    if t.status == "CLOSED_LOSS":
                        streak += 1
                    else:
                        break
                self._consec_loss = streak

            if self._consec_loss >= Config.MAX_CONSEC_LOSS:
                self.warn(f"Consecutive loss guard triggered ({self._consec_loss}) — skipping today")
                skip_reason = "CONSEC_LOSS"
                return

            # News blackout gate: block ±30min before / ±15min after high-impact USD events
            blocked, news_reason = self.news_filter.is_blackout()
            if blocked:
                self.warn(f"Cycle blocked — {news_reason}")
                skip_reason = "NEWS_BLACKOUT"
                return

            state = self.market_analyst.run()
            if state is None:
                self.warn("No market state — aborting cycle")
                skip_reason = "NO_MARKET_DATA"
                return
            self._last_market_state = state

            signal = self.technical_analyst.run(state)

            if signal.direction == "NONE":
                self.info("No trade signal this cycle")
                self.reporter.send_info(
                    f"No signal | {state.regime} | ${state.close:,.2f} | "
                    f"{datetime.now(timezone.utc).strftime('%H:%M UTC')}"
                )
                return

            # v11: live MT5 balance for position sizing (fall back to Config.ACCOUNT_SIZE)
            live_balance: Optional[float] = None
            if not Config.PAPER_TRADE and self.broker.connected:
                bal = self.broker.get_balance()
                if bal > 0:
                    live_balance = bal
                    self.info(f"Live balance: ${bal:.2f}")
            risk = self.risk_manager.run(
                signal, state, self.journal.get_all(), account_size=live_balance
            )
            self.reporter.send_signal(signal, risk, state, Config.PAPER_TRADE)

            if risk.approved:
                # v11: spread gate — refuse execution when live spread is wider than
                # MAX_SPREAD_USD (news windows, illiquid sessions). Paper trades skip.
                if (
                    not Config.PAPER_TRADE
                    and self._last_spread > Config.MAX_SPREAD_USD
                ):
                    self.warn(
                        f"Trade BLOCKED: spread ${self._last_spread:.2f} > "
                        f"${Config.MAX_SPREAD_USD:.2f} (signal preserved for next cycle)"
                    )
                    self.reporter.send_telegram(
                        f"Signal BLOCKED -- spread ${self._last_spread:.2f} > "
                        f"${Config.MAX_SPREAD_USD:.2f}"
                    )
                else:
                    open_positions = self.journal.get_open()
                    if len(open_positions) >= Config.MAX_OPEN_POSITIONS:
                        self.info(
                            f"Trade BLOCKED: {len(open_positions)} position(s) already open "
                            f"(max={Config.MAX_OPEN_POSITIONS}). Signal preserved for next cycle."
                        )
                        self.reporter.send_telegram(
                            f"Signal BLOCKED -- {len(open_positions)} position(s) open "
                            f"(max={Config.MAX_OPEN_POSITIONS})"
                        )
                    else:
                        self._open_trade(signal, risk, state)

            self.learning_agent.analyze(self.journal.get_all())
            self.info("Cycle complete")
        finally:
            try:
                _obs_logger.log_cycle(self._build_cycle_entry(_cycle_ts, state, signal, skip_reason))
            except Exception:
                pass

    def _build_cycle_entry(
        self,
        ts: str,
        state: Optional[MarketState],
        signal: Optional[TechnicalSignal],
        skip_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        indicators = (signal.indicators if signal else {}) or {}
        all_trades = self.journal.get_all()
        closed_pnl = sum(t.pnl for t in all_trades if t.status != "OPEN")
        balance    = round(Config.ACCOUNT_SIZE + closed_pnl, 2)
        return {
            "ts":              ts,
            "cycle":           self._session_stats["cycles_total"],
            "skip_reason":     skip_reason,
            "close":           round(state.close, 2)    if state  else None,
            "high":            round(state.high,  2)    if state  else None,
            "low":             round(state.low,   2)    if state  else None,
            "atr":             round(state.atr,   2)    if state  else None,
            "regime":          state.regime             if state  else None,
            "dxy":             state.dxy_trend          if state  else None,
            "htf_bias":        state.htf_bias           if state  else None,
            "data_source":     state.data_source        if state  else None,
            "spread":          round(self._last_spread, 2),
            "rsi":             indicators.get("rsi"),
            "adx":             indicators.get("adx"),
            "bb_width_pct":    indicators.get("bb_width_pct"),
            "ema200":          indicators.get("ema200"),
            "buy_confluence":  signal.buy_confluence_str  if signal else None,
            "sell_confluence": signal.sell_confluence_str if signal else None,
            "block_reasons":   signal.block_reasons       if signal else [],
            "signal":          signal.direction if (signal and signal.direction != "NONE") else None,
            "trade_event":     self._last_trade_event,
            "open_positions":  len(self.journal.get_open()),
            "balance":         balance,
            "equity":          balance,
        }

    def _open_trade(self, signal: TechnicalSignal, risk: RiskDecision, state: MarketState):
        trade_id   = str(uuid.uuid4())
        mt5_ticket = 0

        if not Config.PAPER_TRADE:
            mt5_ticket = self.broker.send_order(signal, risk.lot_size)
            if mt5_ticket == 0:
                self.error("MT5 order rejected — trade not recorded")
                return

        trade = TradeRecord(
            id=trade_id,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            direction=signal.direction,
            pattern=signal.pattern,
            entry=signal.entry,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            lot_size=risk.lot_size,
            risk_amount=risk.risk_amount,
            confluence_count=signal.confluence_count,
            regime=state.regime,
            paper=Config.PAPER_TRADE,
            status="OPEN",
            mt5_ticket=mt5_ticket,
            rr_ratio=risk.rr_ratio,
            monthly_brake_active=risk.monthly_brake_active,
        )
        self.journal.add(trade)
        self._last_trade_event = {
            "action":    "OPEN",
            "id":        trade_id[:8],
            "direction": signal.direction,
            "pattern":   signal.pattern,
            "price":     signal.entry,
            "pnl":       None,
        }
        self._session_stats["trades_opened"] += 1
        mode_str = "paper" if Config.PAPER_TRADE else "LIVE"
        self.info(
            f"Trade opened ({mode_str}): {trade_id[:8]} {signal.direction} @ {signal.entry:.2f}"
        )

    def _check_open_positions(self):
        open_trades = self.journal.get_open()
        if not open_trades:
            return
        self.info(f"Checking {len(open_trades)} open position(s)")
        for trade in open_trades:
            if not Config.PAPER_TRADE and trade.mt5_ticket:
                self._check_mt5_position(trade)
            else:
                self._paper_simulate(trade)

    def _check_mt5_position(self, trade: TradeRecord):
        pos = self.broker.check_position(trade.mt5_ticket)
        if pos is None or pos["open"]:
            # Position still open — check BE trigger using current tick
            if not trade.be_moved and MT5_AVAILABLE and self.broker.connected:
                try:
                    tick = mt5.symbol_info_tick(Config.MT5_SYMBOL)
                    if tick:
                        stop_dist = abs(trade.entry - trade.stop_loss)
                        if stop_dist > 0:
                            if trade.direction == "BUY":
                                trigger = trade.entry + stop_dist * Config.BE_TRIGGER_R
                                cur = tick.ask  # ask = worst-case high for BUY
                                if cur >= trigger:
                                    new_sl = round(trade.entry + Config.BE_CUSHION_USD, 2)
                                    if new_sl > trade.stop_loss:
                                        if self.broker.modify_sl(trade.mt5_ticket, new_sl):
                                            self.journal.update(trade.id, stop_loss=new_sl, be_moved=True)
                                            trade.stop_loss = new_sl
                                            trade.be_moved = True
                                            self.info(f"Live BE move {trade.id[:8]}: SL -> {new_sl:.2f}")
                            else:  # SELL
                                trigger = trade.entry - stop_dist * Config.BE_TRIGGER_R
                                cur = tick.bid  # bid = worst-case low for SELL
                                if cur <= trigger:
                                    new_sl = round(trade.entry - Config.BE_CUSHION_USD, 2)
                                    if new_sl < trade.stop_loss:
                                        if self.broker.modify_sl(trade.mt5_ticket, new_sl):
                                            self.journal.update(trade.id, stop_loss=new_sl, be_moved=True)
                                            trade.stop_loss = new_sl
                                            trade.be_moved = True
                                            self.info(f"Live BE move {trade.id[:8]}: SL -> {new_sl:.2f}")
                except Exception as exc:
                    self.warn(f"Live BE check error for {trade.id[:8]}: {exc}")
            return
        pnl        = pos["profit"]
        exit_price = pos["price"]
        status     = "CLOSED_WIN" if pnl > 0 else ("CLOSED_LOSS" if pnl < 0 else "CLOSED_BE")
        self._close_trade(trade, exit_price, status, pnl)

    def _paper_simulate(self, trade: TradeRecord):
        """
        Simulate paper trade by checking ALL bars since trade open.
        BE logic tracked locally per simulation run — not persisted to journal
        while trade is open, which avoids incorrect SL application to pre-trigger bars
        on subsequent simulation passes.
        For ambiguous bars (both SL and TP touched), assume worst-case (SL first).
        """
        try:
            hist = yf.Ticker(Config.SYMBOL).history(period="5d", interval="15m")
            if hist.empty:
                return

            # Parse trade timestamp as UTC-aware
            trade_time = pd.to_datetime(trade.timestamp)
            if trade_time.tzinfo is None:
                trade_time = trade_time.tz_localize("UTC")

            # Filter bars AFTER trade was opened
            bars_after = hist[hist.index > trade_time]

            # Local BE state — re-derived each simulation pass so pre-trigger bars
            # are always checked against the original SL, not the moved SL.
            local_sl = trade.stop_loss
            orig_stop_dist = abs(trade.entry - trade.stop_loss)
            local_be_moved = False

            for bar_time, bar in bars_after.iterrows():
                hi = float(bar["High"])
                lo = float(bar["Low"])

                # BE move: if price has traveled +1R in our favour, lock in entry ± cushion
                if not local_be_moved and orig_stop_dist > 0:
                    if trade.direction == "BUY":
                        trigger = trade.entry + orig_stop_dist * Config.BE_TRIGGER_R
                        if hi >= trigger:
                            new_sl = round(trade.entry + Config.BE_CUSHION_USD, 2)
                            if new_sl > local_sl:
                                local_sl = new_sl
                                local_be_moved = True
                                self.info(
                                    f"Paper BE move {trade.id[:8]}: "
                                    f"SL {trade.stop_loss:.2f} -> {new_sl:.2f}"
                                )
                    else:  # SELL
                        trigger = trade.entry - orig_stop_dist * Config.BE_TRIGGER_R
                        if lo <= trigger:
                            new_sl = round(trade.entry - Config.BE_CUSHION_USD, 2)
                            if new_sl < local_sl:
                                local_sl = new_sl
                                local_be_moved = True
                                self.info(
                                    f"Paper BE move {trade.id[:8]}: "
                                    f"SL {trade.stop_loss:.2f} -> {new_sl:.2f}"
                                )

                if trade.direction == "BUY":
                    hit_sl = lo <= local_sl
                    hit_tp = hi >= trade.take_profit
                else:  # SELL
                    hit_sl = hi >= local_sl
                    hit_tp = lo <= trade.take_profit

                if hit_sl and hit_tp:
                    # AMBIGUOUS — assume SL (conservative); if BE moved, P&L is positive
                    self.log.warning(
                        f"Ambiguous bar at {bar_time} for trade {trade.id}: "
                        f"both SL and TP touched. Assuming SL exit (conservative)."
                    )
                    mult = 1 if trade.direction == "BUY" else -1
                    pnl = round(
                        (local_sl - trade.entry) * mult * trade.lot_size * Config.GOLD_CONTRACT_SIZE, 2
                    )
                    status = "CLOSED_WIN" if pnl > 0 else "CLOSED_LOSS"
                    self._close_trade(trade, local_sl, status, pnl)
                    return
                elif hit_sl:
                    mult = 1 if trade.direction == "BUY" else -1
                    pnl = round(
                        (local_sl - trade.entry) * mult * trade.lot_size * Config.GOLD_CONTRACT_SIZE, 2
                    )
                    status = "CLOSED_WIN" if pnl > 0 else "CLOSED_LOSS"
                    self._close_trade(trade, local_sl, status, pnl)
                    return
                elif hit_tp:
                    pnl = trade.risk_amount * trade.rr_ratio
                    self._close_trade(trade, trade.take_profit, "CLOSED_WIN", pnl)
                    return
            # No exit yet — trade remains open
        except Exception as exc:
            self.log.error(f"Paper simulation error for {trade.id}: {exc}")

    def _close_trade(self, trade: TradeRecord, exit_price: float, status: str, pnl: float):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        pnl = round(pnl, 2)
        self.journal.update(
            trade.id,
            status=status,
            exit_price=exit_price,
            exit_timestamp=now,
            pnl=pnl,
        )
        trade.status         = status
        trade.pnl            = pnl
        trade.exit_price     = exit_price
        trade.exit_timestamp = now
        # Keep risk manager state in sync so limits apply within the same day
        self.risk_manager._monthly_pnl += pnl
        if pnl < 0:
            self.risk_manager._daily_loss += abs(pnl)

        # Keep consecutive-loss counter current so the guard fires within a cycle
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._consec_date != today:
            self._consec_date = today
            self._consec_loss = 0
        if pnl < 0:
            self._consec_loss += 1
        else:
            self._consec_loss = 0

        self._last_trade_event = {
            "action":    "CLOSE",
            "id":        trade.id[:8],
            "direction": trade.direction,
            "pattern":   trade.pattern,
            "price":     exit_price,
            "pnl":       round(pnl, 2),
        }
        self._session_stats["trades_closed"] += 1
        self.reporter.send_closed(trade)
        self.info(f"Trade closed {trade.id[:8]} {status} P&L=${pnl:+.2f}")

    def _refresh_consec_loss(self) -> None:
        """Recompute _consec_loss from today's closed journal entries.

        Called after startup sync so the consecutive-loss guard reflects
        trades that were closed by MT5 while the system was offline.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._consec_date = today
        recent = [t for t in self.journal.get_all()
                  if t.timestamp.startswith(today) and t.status != "OPEN"]
        streak = 0
        for t in reversed(recent):
            if t.status == "CLOSED_LOSS":
                streak += 1
            else:
                break
        self._consec_loss = streak
        if streak > 0:
            self.warn(f"Consecutive loss streak loaded from journal: {streak}")

    def get_snapshot_state(self) -> Dict[str, Any]:
        all_trades  = self.journal.get_all()
        closed_pnl  = sum(t.pnl for t in all_trades if t.status != "OPEN")
        balance     = round(Config.ACCOUNT_SIZE + closed_pnl, 2)
        open_trades = self.journal.get_open()
        last_mkt: Dict[str, Any] = {}
        if self._last_market_state:
            ms = self._last_market_state
            last_mkt = {
                "close":  round(ms.close, 2),
                "atr":    round(ms.atr,   2),
                "regime": ms.regime,
            }
        return {
            "final_state": {
                "balance":        balance,
                "equity":         balance,
                "open_positions": len(open_trades),
                "daily_loss":     round(self.risk_manager._daily_loss,   2),
                "monthly_pnl":    round(self.risk_manager._monthly_pnl,  2),
            },
            "open_position_ids": [t.id[:8] for t in open_trades],
            "last_market_state": last_mkt,
        }

    def shutdown(self):
        self.broker.disconnect()


# ── Gold Trading System ───────────────────────────────────────────────────────
class GoldTradingSystem:
    def __init__(self):
        self.orchestrator = OrchestratorAgent()
        self.scheduler    = BackgroundScheduler(timezone="UTC")
        self._running     = False

    def start(self):
        self._running = True
        mode = "PAPER TRADE" if Config.PAPER_TRADE else "LIVE TRADE"
        log.info("=" * 56)
        log.info(f"Gold Trading System — {mode}")
        log.info(f"Account ${Config.ACCOUNT_SIZE:,.0f}  |  Max risk {Config.MAX_RISK_PCT:.0%}/trade")
        log.info(f"Daily limit ${Config.DAILY_LOSS_LIMIT}  |  Min R:R {Config.MIN_RR}")
        log.info(f"Max concurrent positions: {Config.MAX_OPEN_POSITIONS}")
        log.info("=" * 56)

        self.scheduler.add_job(
            self.orchestrator.run_cycle,
            CronTrigger(day_of_week="mon-fri", minute="0,15,30,45", timezone="UTC"),
            id="gold_cycle",
            name="Gold 15m Cycle",
            misfire_grace_time=60,
            replace_existing=True,
        )
        self.scheduler.add_job(
            _obs_logger.write_daily_summary,
            CronTrigger(hour=0, minute=0, timezone="UTC"),
            id="daily_summary",
            name="Daily Summary (00:00 UTC)",
            misfire_grace_time=300,
            replace_existing=True,
        )
        self.scheduler.start()
        log.info("Scheduler active — every 15 min Mon-Fri UTC")

        self.orchestrator.run_cycle()
        self._command_loop()

    def _command_loop(self):
        print("\n" + "=" * 56)
        print("Commands: [s] show journal  [r] run cycle  [q] quit")
        print("=" * 56 + "\n")
        while self._running:
            try:
                cmd = input("> ").strip().lower()
                if cmd == "s":
                    self.orchestrator.journal.show()
                elif cmd == "r":
                    print("Running cycle now…")
                    self.orchestrator.run_cycle()
                elif cmd == "q":
                    self.stop()
                elif cmd:
                    print(f"Unknown command: {cmd!r}  (s / r / q)")
            except (KeyboardInterrupt, EOFError):
                self.stop()
                break

    def stop(self):
        self._running = False
        try:
            _obs_logger.write_daily_summary()
        except Exception:
            pass
        try:
            _obs_logger.save_session_snapshot(
                stats=self.orchestrator._session_stats,
                state=self.orchestrator.get_snapshot_state(),
            )
        except Exception:
            pass
        self.scheduler.shutdown(wait=False)
        self.orchestrator.shutdown()
        log.info("System stopped")


# ── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    GoldTradingSystem().start()
