"""Strict validation shared by Forex market adapters and sourced feeds."""

from datetime import datetime, timedelta, timezone
from math import isfinite

import numpy as np
import pandas as pd

from tradingagents.dataflows.errors import VendorDataUnavailableError


class DataInsufficientError(VendorDataUnavailableError, ValueError):
    """Critical data cannot support a tradeable proposal."""

    def __init__(self, detail: str):
        super().__init__(f"DATA_INSUFFICIENT: {detail}")


def utc_timestamp(value) -> datetime:
    """Require an explicit timezone on sourced timestamps, then normalize to UTC."""
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp) or stamp.tzinfo is None:
            raise ValueError("timezone required")
        return stamp.tz_convert("UTC").to_pydatetime()
    except (ValueError, TypeError, OverflowError) as exc:
        raise DataInsufficientError("invalid timestamp or missing timezone") from exc


def validate_candles(df: pd.DataFrame, timeframe=None, *, as_of=None,
                     max_age_seconds=None, max_spread_pips=None) -> pd.DataFrame:
    """Reject corrupt rows; never silently repair prices, sort, or drop duplicates."""
    from tradingagents.forex.domain import Timeframe
    from tradingagents.forex.sessions import is_market_open
    if df is None or df.empty:
        raise DataInsufficientError("Cannot validate an empty candle dataset")
    result = df.copy()
    for alias in ("Datetime", "datetime", "index", "date", "Timestamp", "timestamp"):
        if "Date" not in result and alias in result:
            result = result.rename(columns={alias: "Date"})
    for column in ("Date", "Open", "High", "Low", "Close"):
        if column not in result:
            raise DataInsufficientError(f"Required OHLCV column {column!r} missing")
    if "Volume" not in result:
        result["Volume"] = 0.0
        result.attrs["volume_available"] = False
    result["Date"] = pd.to_datetime([utc_timestamp(t) for t in result["Date"]], utc=True)
    if result["Date"].duplicated().any():
        raise DataInsufficientError("duplicate candle timestamps")
    if not result["Date"].is_monotonic_increasing:
        raise DataInsufficientError("incorrect candle ordering")
    now = datetime.now(timezone.utc)
    if (result["Date"] > now).any():
        raise DataInsufficientError("future candle timestamps")
    for column in ("Open", "High", "Low", "Close", "Volume"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
        if not np.isfinite(result[column]).all():
            raise DataInsufficientError(f"invalid {column}")
    if (result[["Open", "High", "Low", "Close"]] <= 0).any().any():
        raise DataInsufficientError("zero or negative price")
    if (result["High"] < result["Low"]).any():
        raise DataInsufficientError("High < Low")
    if ((result["High"] < result[["Open", "Close"]].max(axis=1)).any()
            or (result["Low"] > result[["Open", "Close"]].min(axis=1)).any()):
        raise DataInsufficientError("invalid OHLC geometry")
    if (result["Volume"] < 0).any():
        raise DataInsufficientError("negative volume")
    if "spread_pips" in result:
        spread = pd.to_numeric(result["spread_pips"], errors="coerce")
        if not np.isfinite(spread).all() or (spread < 0).any():
            raise DataInsufficientError("invalid spread")
        if max_spread_pips is not None and (spread > max_spread_pips).any():
            raise DataInsufficientError("abnormal spread")
    if timeframe is not None:
        tf = Timeframe(timeframe)
        declared = result.attrs.get("timeframe")
        if declared and declared != tf.value:
            raise DataInsufficientError("timeframe mismatch")
        step = pd.Timedelta(seconds=tf.seconds)
        if tf.seconds < 86400 and any((stamp.timestamp() - result.attrs.get("alignment_offset_seconds", 0)) % tf.seconds for stamp in result["Date"]):
            raise DataInsufficientError("timeframe alignment mismatch")
        for previous, current in zip(result["Date"], result["Date"].iloc[1:], strict=False):
            if current - previous < step:
                raise DataInsufficientError("timeframe mismatch")
            if current - previous == step:
                continue
            missing = pd.date_range(previous + step, current, freq=step, inclusive="left")
            if any(is_market_open(stamp.to_pydatetime()) for stamp in missing):
                raise DataInsufficientError("missing candles / unexpected market gap")
        result["close_time"] = result["Date"] + step
        cutoff = utc_timestamp(as_of) if as_of is not None else now
        result["is_closed"] = result["close_time"] <= cutoff
        if max_age_seconds is not None:
            closed = result.loc[result["is_closed"], "close_time"]
            if closed.empty:
                raise DataInsufficientError("no closed candles")
            stale_from = closed.iloc[-1] + timedelta(seconds=max_age_seconds)
            if stale_from < cutoff and (is_market_open(cutoff) or any(
                    is_market_open(t.to_pydatetime()) for t in pd.date_range(stale_from, cutoff, freq="1h"))):
                raise DataInsufficientError("stale candles")
        result.attrs["timeframe"] = tf.value
    return result.reset_index(drop=True)


def validate_quote(bid, ask, timestamp, *, now=None, max_age_seconds=30,
                   pip_size=0.0001, max_spread_pips=5):
    now = utc_timestamp(now) if now is not None else datetime.now(timezone.utc)
    stamp = utc_timestamp(timestamp)
    if not all(isfinite(v) and v > 0 for v in (bid, ask, pip_size)) or ask < bid:
        raise DataInsufficientError("invalid quote prices")
    age = (now - stamp).total_seconds()
    if age < 0 or age > max_age_seconds:
        raise DataInsufficientError("future or stale quote")
    if (ask - bid) / pip_size > max_spread_pips:
        raise DataInsufficientError("abnormal spread")
