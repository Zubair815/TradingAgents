"""Forex Multi-Timeframe OHLCV Market Data Engine.

Provides:
- First-class Forex candle and multi-timeframe data models:
  - ``ForexBar``: Single OHLCV bar with bullish/bearish, body, wick, and pip calculations.
  - ``MultiTimeframeData``: Multi-timeframe candle container (D1, H4, H1, M15, M5, etc.).
- Robust multi-timeframe fetching and aggregation:
  - Standard intervals: M1, M5, M15, M30, H1, D1, W1 via Yahoo Finance.
  - Higher-timeframe synthetic resampling: H4 generated deterministically from H1 bars.
- Strict point-in-time safeguarding:
  - Zero lookahead: strips all candles closing after the specified ``as_of`` timestamp.
- Defensive validation:
  - Validates OHLCV integrity (High >= Low, High >= max(Open, Close), Low <= min(Open, Close), Volume >= 0).
  - Handles timezone alignment (normalizing to UTC).
  - Rejects duplicates, incorrect ordering, and invalid OHLC geometry.
- Caching layer:
  - TTL-based local caching matching project standards to prevent vendor rate-limiting.
- Formatter utilities:
  - Pair-aware precision formatting (5 decimals for standard pairs, 3 for JPY pairs).
  - Multi-timeframe summary generation for LLM analyst prompts and reports.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import NoMarketDataError, VendorDataUnavailableError
from tradingagents.dataflows.forex_quality import DataInsufficientError, validate_candles
from tradingagents.dataflows.stockstats_utils import OHLCV_CACHE_TTL_SECONDS, yf_retry
from tradingagents.dataflows.utils import safe_ticker_component
from tradingagents.forex.domain import ForexPair, Timeframe, get_forex_pair
from tradingagents.forex.pips import price_to_pips
from tradingagents.forex.symbols import canonical_to_yahoo, strip_broker_suffix

logger = logging.getLogger(__name__)

# Standard required OHLCV columns
OHLCV_COLUMNS = ("Date", "Open", "High", "Low", "Close", "Volume")


# ---------------------------------------------------------------------------
# 1. ForexBar Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForexBar:
    """Individual OHLCV candle for a Forex instrument."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    spread_pips: float = 0.0
    close_time: datetime | None = None
    is_closed: bool = False
    data_source: str = "unknown"
    broker: str | None = None
    broker_symbol: str | None = None
    retrieved_at_utc: datetime | None = None

    @property
    def open_time(self) -> datetime:
        return self.timestamp

    @property
    def is_bullish(self) -> bool:
        """True if close >= open."""
        return self.close >= self.open

    @property
    def is_bearish(self) -> bool:
        """True if close < open."""
        return self.close < self.open

    @property
    def body(self) -> float:
        """Absolute candle body size in price terms."""
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        """Total candle range (High - Low) in price terms."""
        return self.high - self.low

    @property
    def upper_wick(self) -> float:
        """Upper shadow size (High - max(Open, Close))."""
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        """Lower shadow size (min(Open, Close) - Low)."""
        return min(self.open, self.close) - self.low

    def body_pips(self, pair: ForexPair | str) -> float:
        """Calculate candle body size in pips for the given pair."""
        return price_to_pips(self.body, pair)

    def range_pips(self, pair: ForexPair | str) -> float:
        """Calculate total candle range in pips for the given pair."""
        return price_to_pips(self.range, pair)


# ---------------------------------------------------------------------------
# 2. MultiTimeframeData Container
# ---------------------------------------------------------------------------


def resolve_timeframe(tf: Timeframe | str) -> Timeframe:
    """Resolve a Timeframe enum or timeframe string (including aliases like 1m, 5m, 1h, 4h)."""
    if isinstance(tf, Timeframe):
        return tf
    s = str(tf).strip().upper()
    _aliases = {
        "1M": Timeframe.M1,
        "5M": Timeframe.M5,
        "15M": Timeframe.M15,
        "30M": Timeframe.M30,
        "1H": Timeframe.H1,
        "60M": Timeframe.H1,
        "4H": Timeframe.H4,
        "240M": Timeframe.H4,
        "1D": Timeframe.D1,
        "D": Timeframe.D1,
        "DAILY": Timeframe.D1,
        "1W": Timeframe.W1,
        "W": Timeframe.W1,
        "WEEKLY": Timeframe.W1,
    }
    if s in _aliases:
        return _aliases[s]
    return Timeframe.from_string(s)


@dataclass
class MultiTimeframeData:

    """Container holding multi-timeframe OHLCV DataFrames for a Forex instrument."""

    symbol: str
    pair: ForexPair | None = None
    candles: dict[Timeframe, pd.DataFrame] = field(default_factory=dict)
    as_of: datetime | None = None

    @property
    def available_timeframes(self) -> list[Timeframe]:
        """List of timeframes currently loaded in this bundle."""
        return list(self.candles.keys())

    def get_timeframe(self, tf: Timeframe | str) -> pd.DataFrame:
        """Retrieve the DataFrame for the requested timeframe.

        Raises KeyError if the timeframe is not present in this bundle.
        """
        resolved_tf = resolve_timeframe(tf)
        if resolved_tf not in self.candles:
            available = ", ".join(t.value for t in self.candles)
            raise KeyError(f"Timeframe {resolved_tf.value} not found in bundle. Available: [{available}]")

        return self.candles[resolved_tf]


    def latest_bar(self, tf: Timeframe | str = Timeframe.H1) -> pd.Series | None:
        """Return the latest available OHLCV row for the given timeframe, or None if empty."""
        try:
            df = self.get_timeframe(tf)
            if df.empty:
                return None
            return df.iloc[-1]
        except KeyError:
            return None

    def latest_close(self, tf: Timeframe | str = Timeframe.H1) -> float | None:
        """Return the latest close price for the given timeframe, or None."""
        bar = self.latest_bar(tf)
        if bar is None:
            return None
        return float(bar["Close"])

    def to_summary_dict(self) -> dict[str, Any]:
        """Summarize multi-timeframe latest prices, ranges, and bar counts."""
        summary: dict[str, Any] = {
            "symbol": self.symbol,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "sources": {tf.value: dict(df.attrs) for tf, df in self.candles.items()},
            "timeframes": {},
        }
        for tf, df in self.candles.items():
            if df.empty:
                summary["timeframes"][tf.value] = {"bars": 0}
                continue
            last = df.iloc[-1]
            summary["timeframes"][tf.value] = {
                "bars": len(df),
                "latest_timestamp": str(last["Date"]),
                "open": float(last["Open"]),
                "high": float(last["High"]),
                "low": float(last["Low"]),
                "close": float(last["Close"]),
                "volume": float(last["Volume"]),
            }
        return summary


# ---------------------------------------------------------------------------
# 3. Candle Validation & Point-In-Time Filtering
# ---------------------------------------------------------------------------


def _ensure_utc_datetime(val: Any) -> datetime:
    """Normalize any date/datetime/string/Timestamp to a timezone-aware UTC datetime."""
    if isinstance(val, str):
        dt = pd.to_datetime(val)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc).to_pydatetime()
        return dt.tz_convert(timezone.utc).to_pydatetime()

    if isinstance(val, pd.Timestamp):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc).to_pydatetime()
        return val.tz_convert(timezone.utc).to_pydatetime()

    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)

    if isinstance(val, date):
        return datetime.combine(val, datetime.min.time(), tzinfo=timezone.utc)

    raise ValueError(f"Cannot convert {val!r} to UTC datetime.")


def validate_forex_candles(df, pair=None, **kwargs) -> pd.DataFrame:
    """Validate without silently repairing prices or removing bad rows."""
    return validate_candles(df, **kwargs)


def filter_candles_by_cutoff(df, as_of, timeframe=None) -> pd.DataFrame:
    """Select completed candles using close time, never the provider's open timestamp."""
    if df is None or df.empty:
        return df
    cutoff = _ensure_utc_datetime(as_of) if as_of is not None else datetime.now(timezone.utc)
    tf = resolve_timeframe(timeframe or df.attrs.get("timeframe")) if (timeframe or df.attrs.get("timeframe")) else None
    clean = df.copy()
    if tf is not None:
        clean["close_time"] = pd.to_datetime(clean["Date"], utc=True) + pd.Timedelta(seconds=tf.seconds)
    elif "close_time" not in clean:
        raise DataInsufficientError("timeframe or close_time is required to identify completed candles")
    close_times = pd.to_datetime(clean["close_time"], utc=True)
    clean = clean.loc[close_times <= cutoff].copy()
    clean["is_closed"] = True
    return clean.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 4. Resampling Engine (e.g. H1 -> H4)
# ---------------------------------------------------------------------------

_TIMEFRAME_PANDAS_RULES: dict[Timeframe, str] = {
    Timeframe.M1: "1min",
    Timeframe.M5: "5min",
    Timeframe.M15: "15min",
    Timeframe.M30: "30min",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1D",
    Timeframe.W1: "1W",
}


def resample_candles(
    df: pd.DataFrame,
    target_timeframe: Timeframe | str,
    source_timeframe: Timeframe | str = Timeframe.H1,
) -> pd.DataFrame:
    """Resample lower-timeframe OHLCV candles to a higher timeframe.

    Examples:
        - Resample H1 candles into H4 candles (aligned to 00:00, 04:00, 08:00, etc. UTC).
        - Resample H1 candles into D1 candles.
        - Resample M5 candles into M15 candles.

    Args:
        df: Source OHLCV DataFrame (must pass validate_forex_candles).
        target_timeframe: The target higher timeframe (e.g. Timeframe.H4).
        source_timeframe: The source lower timeframe (default Timeframe.H1).

    Returns:
        New DataFrame aggregated into target_timeframe bars.
    """
    target_tf = resolve_timeframe(target_timeframe)
    source_tf = resolve_timeframe(source_timeframe)


    if target_tf.seconds < source_tf.seconds:
        raise ValueError(
            f"Cannot resample from higher timeframe {source_tf.value} ({source_tf.seconds}s) "
            f"to lower timeframe {target_tf.value} ({target_tf.seconds}s)."
        )

    if target_tf == source_tf:
        return df.copy()

    valid_df = validate_forex_candles(df, timeframe=source_tf)

    rule = _TIMEFRAME_PANDAS_RULES.get(target_tf)
    if not rule:
        raise ValueError(f"No pandas resampling rule defined for {target_tf.value}")

    # Set Date as index for resampling
    work_df = valid_df.set_index("Date")

    agg_dict: dict[str, str] = {
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }
    # Preserve Spread or other extra numeric columns if present
    for extra in ("Spread", "spread", "spread_pips"):
        if extra in work_df.columns:
            agg_dict[extra] = "mean"

    # Resample with origin='start_day' so H4 blocks align at 00:00, 04:00, 08:00... UTC
    groups = work_df.resample(rule, origin="start_day", closed="left", label="left")
    resampled = groups.agg(agg_dict)
    # A partial H4/D1 bucket must never be advertised as a complete candle.
    expected = target_tf.seconds // source_tf.seconds
    complete = groups["Close"].count() == expected
    resampled = resampled.loc[complete]
    resampled.attrs.update(df.attrs)
    resampled.attrs["timeframe"] = target_tf.value

    # Drop intervals that had no candles (e.g. weekend gaps)
    resampled = resampled.dropna(subset=["Open", "Close"]).reset_index()

    return resampled


# ---------------------------------------------------------------------------
# 5. Intraday & Multi-Timeframe Fetcher
# ---------------------------------------------------------------------------


def _get_cache_path(safe_symbol: str, tf_label: str) -> str:
    """Return the absolute filesystem path for a cached candle file."""
    config = get_config()
    cache_dir = os.path.join(config.get("data_cache_dir", "cache"), "forex_ohlcv")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f"{safe_symbol}_{tf_label}_ohlcv.csv")


def _is_cache_fresh(file_path: str, ttl_seconds: int = OHLCV_CACHE_TTL_SECONDS) -> bool:
    """Check if cache file exists and is within TTL."""
    if not os.path.exists(file_path):
        return False
    try:
        mtime = os.path.getmtime(file_path)
        return (time.time() - mtime) <= ttl_seconds
    except OSError:
        return False


def _fetch_yahoo_candles(
    symbol: str | ForexPair,
    timeframe: Timeframe | str = Timeframe.H1,
    count: int | None = 100,
    as_of: datetime | date | str | pd.Timestamp | None = None,
    start_date: date | datetime | str | None = None,
    end_date: date | datetime | str | None = None,
    use_cache: bool = True,
) -> pd.DataFrame:
    """Fetch OHLCV candles for a Forex pair and timeframe.

    Supports:
    - Canonical symbols (``EURUSD``), broker symbols (``EURUSDm``), or ``ForexPair`` objects.
    - Standard timeframes (M1, M5, M15, M30, H1, D1, W1) and synthetic H4.
    - Zero-lookahead filtering via ``as_of``.
    - Local caching with rate-limit protection.

    Args:
        symbol: Canonical or broker symbol or ForexPair.
        timeframe: Candle duration (M1..W1).
        count: Maximum number of recent candles to return (default 100).
        as_of: Maximum cutoff timestamp (UTC). Candles closing after this are excluded.
        start_date: Optional explicit start date string (YYYY-MM-DD).
        end_date: Optional explicit end date string (YYYY-MM-DD).
        use_cache: Whether to use disk caching (default True).

    Returns:
        DataFrame with columns Date (UTC), Open, High, Low, Close, Volume.
    """
    raw_sym = symbol.symbol if isinstance(symbol, ForexPair) else str(symbol)
    canonical = strip_broker_suffix(raw_sym)
    pair = get_forex_pair(canonical)
    yahoo_symbol = canonical_to_yahoo(canonical)
    safe_symbol = safe_ticker_component(yahoo_symbol)

    tf = resolve_timeframe(timeframe)

    # H4 is not a native Yahoo interval; fetch H1 and resample
    if tf == Timeframe.H4:
        # Request enough H1 bars to produce the requested H4 count
        h1_count = (count * 4 + 48) if count is not None else None
        h1_df = _fetch_yahoo_candles(
            symbol=symbol,
            timeframe=Timeframe.H1,
            count=h1_count,
            as_of=as_of,
            start_date=start_date,
            end_date=end_date,
            use_cache=use_cache,
        )
        h4_df = resample_candles(h1_df, target_timeframe=Timeframe.H4, source_timeframe=Timeframe.H1)
        filtered = filter_candles_by_cutoff(h4_df, as_of, tf)
        if count is not None:
            return filtered.tail(count).reset_index(drop=True)
        return filtered.reset_index(drop=True)

    request_key = hashlib.sha256(json.dumps([str(as_of), str(start_date), str(end_date), count]).encode()).hexdigest()[:16]
    cache_file = _get_cache_path(safe_symbol, tf.value + "_" + request_key)
    retrieved_at = datetime.now(timezone.utc).isoformat()
    data: pd.DataFrame | None = None

    if use_cache and _is_cache_fresh(cache_file):
        try:
            cached = pd.read_csv(cache_file, encoding="utf-8")
            retrieved_at = datetime.fromtimestamp(os.path.getmtime(cache_file), timezone.utc).isoformat()
            if not cached.empty and "Close" in cached.columns:
                data = validate_forex_candles(cached, pair)
        except Exception as exc:
            logger.debug(f"Cache read failed for {cache_file}: {exc}")
            data = None

    if data is None:
        import yfinance as yf

        interval_str = tf.yfinance_interval

        # Default periods if explicit dates are not passed
        if start_date is None and as_of is not None:
            cutoff = _ensure_utc_datetime(as_of)
            start_date = (cutoff - timedelta(seconds=tf.seconds * (count or 100) * 3, days=7)).date()
            end_date = (cutoff + timedelta(days=1)).date()
        if start_date is not None:
            dl_kwargs = {"start": str(start_date)}
            if end_date is not None:
                dl_kwargs["end"] = str(end_date)
        elif tf == Timeframe.M1:
            dl_kwargs = {"period": "7d"}
        elif tf in (Timeframe.M5, Timeframe.M15, Timeframe.M30):
            dl_kwargs = {"period": "60d"}
        elif tf == Timeframe.H1:
            dl_kwargs = {"period": "730d"}
        else:
            dl_kwargs = {"period": "5y"}

        try:
            downloaded = yf_retry(
                lambda: yf.download(
                    yahoo_symbol,
                    interval=interval_str,
                    progress=False,
                    auto_adjust=False,
                    ignore_tz=False,
                    **dl_kwargs,
                )
            )
        except Exception as exc:
            raise VendorDataUnavailableError(f"Failed to fetch Forex data for {canonical} ({yahoo_symbol}): {exc}") from exc

        if downloaded is None or downloaded.empty:
            raise NoMarketDataError(canonical, yahoo_symbol, f"no candles for timeframe {tf.value}")

        # Flatten multi-index columns if present (yfinance 0.2.x+ downloads often have multi-level headers)
        if isinstance(downloaded.columns, pd.MultiIndex):
            downloaded.columns = [c[0] for c in downloaded.columns]

        # Reset index to turn Datetime/Date into a column
        raw_df = downloaded.reset_index()
        data = validate_forex_candles(raw_df, pair)

        if use_cache and not data.empty:
            try:
                data.to_csv(cache_file, index=False, encoding="utf-8")
            except Exception as exc:
                logger.debug(f"Failed to write cache {cache_file}: {exc}")

    # Enforce point-in-time cutoff
    data.attrs.update(source="Yahoo", symbol=yahoo_symbol, canonical_symbol=canonical,
                      broker=None, timeframe=tf.value, retrieved_at_utc=retrieved_at,
                      price_basis="provider_ohlc", execution_market=False)
    filtered = filter_candles_by_cutoff(data, as_of, tf)
    if start_date is not None:
        filtered = filtered.loc[filtered["Date"] >= _ensure_utc_datetime(start_date)]
    if end_date is not None:
        filtered = filtered.loc[filtered["Date"] < _ensure_utc_datetime(end_date)]

    if count is not None:
        return filtered.tail(count).reset_index(drop=True)
    return filtered.reset_index(drop=True)


def fetch_forex_candles(symbol, timeframe=Timeframe.H1, count=100, as_of=None,
                        start_date=None, end_date=None, use_cache=True, *, source=None,
                        allow_fallback=None, observer=None) -> pd.DataFrame:
    """Use MT5 by default; Yahoo is an explicit, labeled analytical fallback."""
    config = get_config()
    provider = source or config.get("forex_market_source", "mt5")
    fallback = config.get("forex_allow_yahoo_fallback", False) if allow_fallback is None else allow_fallback
    tf = resolve_timeframe(timeframe)
    if count is not None and count < 1:
        raise ValueError("count must be positive")
    cutoff = _ensure_utc_datetime(as_of) if as_of is not None else datetime.now(timezone.utc)
    fallback_reason = None
    if provider not in ("mt5", "yahoo"):
        raise ValueError("forex_market_source must be mt5 or yahoo")
    raw_symbol = symbol.symbol if isinstance(symbol, ForexPair) else str(symbol)
    if provider == "mt5":
        from tradingagents.mt5.errors import MT5Error
        try:
            if observer is None:
                from tradingagents.mt5.observer import MT5Observer
                observer = MT5Observer()
            if start_date is not None:
                bars = observer.get_candles_range(raw_symbol, tf, _ensure_utc_datetime(start_date),
                                                 min(cutoff, _ensure_utc_datetime(end_date)) if end_date else cutoff)
            else:
                bars = observer.get_candles(raw_symbol, tf, count or 100, cutoff)
            data = bars_to_frame(bars, tf)
        except DataInsufficientError:
            raise
        except MT5Error as exc:
            if not fallback:
                raise DataInsufficientError("MT5 candles unavailable; no fallback enabled") from exc
            fallback_reason = type(exc).__name__
            data = _fetch_yahoo_candles(symbol, tf, count, as_of, start_date, end_date, use_cache)
    else:
        data = _fetch_yahoo_candles(symbol, tf, count, as_of, start_date, end_date, use_cache)
    data = filter_candles_by_cutoff(data, cutoff, tf)
    if count is not None:
        data = data.tail(count)
    # Closed-market gaps are allowed; missing bars while the market is open are errors.
    age = None if end_date is not None else config.get("forex_candle_max_age_seconds", tf.seconds * 2)
    data = validate_candles(data, tf, as_of=cutoff, max_age_seconds=age,
                            max_spread_pips=config.get("forex_max_spread_pips", 5.0))
    data.attrs.update(last_bar_closed=True, as_of_utc=cutoff.isoformat(),
                      fallback=fallback_reason is not None, fallback_reason=fallback_reason)
    return data


def bars_to_frame(bars, timeframe):
    if not bars:
        raise DataInsufficientError("no completed MT5 candles")
    frame = pd.DataFrame([{"Date": b.timestamp, "Open": b.open, "High": b.high,
                           "Low": b.low, "Close": b.close, "Volume": b.volume,
                           "spread_pips": b.spread_pips, "close_time": b.close_time,
                           "is_closed": b.is_closed} for b in bars])
    tf = resolve_timeframe(timeframe)
    frame.attrs["alignment_offset_seconds"] = bars[0].timestamp.timestamp() % tf.seconds if tf == Timeframe.H4 else 0
    frame.attrs.update(source="MT5", broker=bars[0].broker, symbol=bars[0].broker_symbol,
                       canonical_symbol=strip_broker_suffix(bars[0].broker_symbol or ""),
                       retrieved_at_utc=bars[0].retrieved_at_utc.isoformat(),
                       timeframe=resolve_timeframe(timeframe).value, execution_market=True,
                       price_basis="broker_ohlc")
    return frame


def fetch_multi_timeframe_data(
    symbol: str | ForexPair,
    timeframes: tuple[Timeframe | str, ...] = (
        Timeframe.D1,
        Timeframe.H4,
        Timeframe.H1,
        Timeframe.M15,
    ),
    as_of: datetime | date | str | pd.Timestamp | None = None,
    count: int = 100,
    use_cache: bool = True,
) -> MultiTimeframeData:
    """Fetch and bundle multiple timeframes for a Forex pair.

    Args:
        symbol: Canonical or broker symbol or ForexPair.
        timeframes: Sequence of timeframes to fetch (default D1, H4, H1, M15).
        as_of: Point-in-time cutoff (UTC).
        count: Number of bars per timeframe.
        use_cache: Whether to use local caching.

    Returns:
        MultiTimeframeData bundle containing candles for all requested timeframes.
    """
    raw_sym = symbol.symbol if isinstance(symbol, ForexPair) else str(symbol)
    canonical = strip_broker_suffix(raw_sym)
    pair = get_forex_pair(canonical)

    as_of_utc = _ensure_utc_datetime(as_of) if as_of is not None else datetime.now(timezone.utc)

    candles_dict: dict[Timeframe, pd.DataFrame] = {}

    for tf_item in timeframes:
        tf = resolve_timeframe(tf_item)
        try:

            df = fetch_forex_candles(
                symbol=canonical,
                timeframe=tf,
                count=count,
                as_of=as_of_utc,
                use_cache=use_cache,
            )
            candles_dict[tf] = df
        except Exception as exc:
            raise DataInsufficientError(f"required timeframe {tf.value} unavailable for {canonical}") from exc

    return MultiTimeframeData(
        symbol=canonical,
        pair=pair,
        candles=candles_dict,
        as_of=as_of_utc,
    )


# ---------------------------------------------------------------------------
# 6. Formatting Utilities for Agent Prompts & Markdown Reports
# ---------------------------------------------------------------------------


def format_forex_candles_csv(
    df: pd.DataFrame,
    pair: ForexPair | str | None = None,
    max_rows: int = 30,
) -> str:
    """Format OHLCV DataFrame into CSV text with instrument-accurate precision."""
    if df is None or df.empty:
        return "# No candle records available\n"

    resolved_pair: ForexPair | None = None
    if isinstance(pair, ForexPair):
        resolved_pair = pair
    elif isinstance(pair, str):
        resolved_pair = get_forex_pair(pair)

    digits = resolved_pair.digits if resolved_pair else 5
    price_fmt = f"{{:.{digits}f}}"

    display_df = df.tail(max_rows).copy()

    # Format numeric columns with exact digit count
    for col in ("Open", "High", "Low", "Close"):
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(lambda v: price_fmt.format(float(v)))

    if "Volume" in display_df.columns:
        display_df["Volume"] = display_df["Volume"].apply(lambda v: f"{float(v):.0f}")

    if "Date" in display_df.columns:
        display_df["Date"] = display_df["Date"].astype(str)

    header = f"# Forex OHLCV records: {len(display_df)} (total: {len(df)})\n"
    if resolved_pair:
        header += f"# Instrument: {resolved_pair.display_name} | Digits: {digits} | Pip: {resolved_pair.pip_size}\n"
    header += "\n"

    header += format_data_provenance(df) + "\n"
    return header + display_df.to_csv(index=False)


def format_multi_timeframe_summary(
    bundle: MultiTimeframeData,
    max_rows: int = 5,
) -> str:
    """Render a comprehensive multi-timeframe markdown summary for analyst agents."""
    pair = bundle.pair
    digits = pair.digits if pair else 5
    price_fmt = f"{{:.{digits}f}}"

    sym_label = pair.display_name if pair else bundle.symbol
    lines = [
        f"### Multi-Timeframe Market Data: {sym_label}",
        f"- Reference / Cutoff (UTC): {bundle.as_of.isoformat() if bundle.as_of else 'Live'}",
        "",
    ]

    for tf in (Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5):
        if tf not in bundle.candles:
            continue
        df = bundle.candles[tf]
        if df.empty:
            lines.append(f"#### Timeframe {tf.value}: No data")
            continue

        latest = df.iloc[-1]
        c_open = float(latest["Open"])
        c_high = float(latest["High"])
        c_low = float(latest["Low"])
        c_close = float(latest["Close"])

        direction = "BULLISH 🟢" if c_close >= c_open else "BEARISH 🔴"
        range_val = c_high - c_low
        range_pips_str = f"{price_to_pips(range_val, pair):.1f} pips" if pair else f"{range_val:.5f}"

        lines.extend([
            f"#### Timeframe {tf.value} (Bars: {len(df)})",
            f"- Latest Bar ({latest['Date']}): O={price_fmt.format(c_open)} H={price_fmt.format(c_high)} L={price_fmt.format(c_low)} C={price_fmt.format(c_close)} [{direction}]",
            f"- Current Bar Range: {range_pips_str}",
            "",
            "Recent Bars:",
            format_forex_candles_csv(df.tail(max_rows), pair=pair, max_rows=max_rows),
            "",
        ])

    return "\n".join(lines)


def format_data_provenance(df):
    """Keep feed identity visible in every derived analyst input."""
    keys = ("source", "broker", "symbol", "timeframe", "retrieved_at_utc", "execution_market", "fallback")
    return "Data provenance: " + "; ".join(f"{key}={df.attrs.get(key, 'unknown')}" for key in keys)
