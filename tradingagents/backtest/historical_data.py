"""Fail-closed historical market data boundary. No demonstration fallback."""

from datetime import datetime, timedelta, timezone

import pandas as pd

from tradingagents.dataflows.forex_data import ForexBar, fetch_forex_candles
from tradingagents.dataflows.forex_quality import (
    DataInsufficientError,
    utc_timestamp,
    validate_candles,
)
from tradingagents.forex.domain import Timeframe, normalize_forex_pair


class HistoricalDataUnavailable(DataInsufficientError):
    code = "HISTORICAL_DATA_UNAVAILABLE"


def period_boundary(value):
    if value is None:
        raise HistoricalDataUnavailable("historical start and end are required")
    if len(str(value)) == 10:
        value = str(value) + "T00:00:00+00:00"
    return utc_timestamp(value)


def candle_frame(candles, provenance):
    frame = pd.DataFrame([{
        "Date": b.timestamp, "Open": b.open, "High": b.high, "Low": b.low,
        "Close": b.close, "Volume": b.volume, "spread_pips": b.spread_pips,
    } for b in candles])
    frame.attrs.update(provenance)
    return frame


def validate_historical_input(candles, provenance, pair, timeframe, start=None, end=None):
    """Require provider provenance and reject invalid data rather than repairing it."""
    meta = dict(provenance or {})
    tf = Timeframe.from_string(timeframe)
    if meta.get("source") not in ("MT5", "Yahoo") or meta.get("synthetic", False):
        raise HistoricalDataUnavailable("real MT5 or Yahoo source provenance is required")
    required = ("canonical_symbol", "symbol", "timeframe", "retrieved_at_utc", "execution_market")
    if any(key not in meta or meta[key] is None for key in required):
        raise HistoricalDataUnavailable("incomplete market data provenance")
    if meta["canonical_symbol"] != normalize_forex_pair(pair) or meta["timeframe"] != tf.value:
        raise HistoricalDataUnavailable("pair or timeframe provenance mismatch")
    if not isinstance(meta["execution_market"], bool) or (meta["source"] == "Yahoo" and meta["execution_market"]):
        raise HistoricalDataUnavailable("Yahoo is not broker execution history")
    now = datetime.now(timezone.utc)
    if utc_timestamp(meta["retrieved_at_utc"]) > now:
        raise HistoricalDataUnavailable("future retrieval timestamp")
    if len(candles) < 2:
        raise HistoricalDataUnavailable("at least two completed candles are required")
    cutoff = period_boundary(end) if end else utc_timestamp(candles[-1].close_time)
    lower = period_boundary(start) if start else utc_timestamp(candles[0].timestamp)
    if cutoff > now or lower >= cutoff:
        raise HistoricalDataUnavailable("invalid or future historical period")
    for bar in candles:
        if bar.data_source != meta["source"] or not bar.is_closed:
            raise HistoricalDataUnavailable("unverified, synthetic, or incomplete candle")
        expected_close = utc_timestamp(bar.timestamp) + timedelta(seconds=tf.seconds)
        if bar.close_time is None or utc_timestamp(bar.close_time) != expected_close:
            raise HistoricalDataUnavailable("invalid candle close time / timeframe")
        if bar.timestamp < lower or expected_close > cutoff:
            raise HistoricalDataUnavailable("candle outside requested historical cutoff")
    frame = validate_candles(candle_frame(candles, meta), tf, as_of=cutoff)
    # Permit only closed-market gaps at the requested boundaries.
    from tradingagents.forex.sessions import is_market_open
    for left, right in ((lower, candles[0].timestamp), (candles[-1].close_time, cutoff)):
        if left >= right:
            continue
        if any(is_market_open(t.to_pydatetime()) for t in pd.date_range(left, right, freq=f"{tf.seconds}s", inclusive="left")):
            raise HistoricalDataUnavailable("requested historical range is not covered")
    meta.update(requested_start=lower.isoformat(), requested_end=cutoff.isoformat(),
                actual_start=candles[0].timestamp.isoformat(), actual_end=candles[-1].close_time.isoformat(),
                data_quality="checked_order_uniqueness_geometry_timeframe_coverage_closed_bars",
                candle_count=len(frame), synthetic=False)
    return meta


def load_historical_candles(pair, timeframe, start, end):
    """Fetch the configured real provider, with automatic fallback disabled."""
    begin, cutoff = period_boundary(start), period_boundary(end)
    if begin >= cutoff or cutoff > datetime.now(timezone.utc):
        raise HistoricalDataUnavailable("invalid or future historical period")
    try:
        frame = fetch_forex_candles(pair, timeframe, count=None, as_of=cutoff,
                                    start_date=begin, end_date=cutoff, allow_fallback=False)
        meta = dict(frame.attrs)
        bars = [ForexBar(
            timestamp=utc_timestamp(row.Date), open=float(row.Open), high=float(row.High),
            low=float(row.Low), close=float(row.Close), volume=float(row.Volume),
            spread_pips=float(getattr(row, "spread_pips", 0)),
            close_time=utc_timestamp(row.close_time), is_closed=bool(row.is_closed),
            data_source=meta.get("source", "unknown"), broker=meta.get("broker"),
            broker_symbol=meta.get("symbol"), retrieved_at_utc=utc_timestamp(meta["retrieved_at_utc"]),
        ) for row in frame.itertuples()]
        meta = validate_historical_input(bars, meta, pair, timeframe, begin, cutoff)
        return bars, meta
    except HistoricalDataUnavailable:
        raise
    except Exception as exc:
        # Provider exception strings may include authenticated URLs.
        raise HistoricalDataUnavailable("provider did not return valid historical coverage") from exc
