"""Completed-M5 deterministic confirmation detectors."""

from __future__ import annotations

from collections.abc import Sequence

from tradingagents.pair_observer.models import Candle, Direction


def confirmation_type(candles: Sequence[Candle], direction: Direction) -> str | None:
    if direction is Direction.NEUTRAL or len(candles) < 2:
        return None
    previous, current = candles[-2:]
    if not previous.completed or not current.completed:
        return None
    if current.timeframe.value != "M5" or previous.timeframe.value != "M5":
        return None
    prev_low, prev_high = sorted((previous.open, previous.close))
    body_low, body_high = sorted((current.open, current.close))
    bullish = current.close > current.open
    bearish = current.close < current.open
    if direction is Direction.LONG and bullish and body_low <= prev_low and body_high >= prev_high:
        return "BULLISH_ENGULFING"
    if direction is Direction.SHORT and bearish and body_low <= prev_low and body_high >= prev_high:
        return "BEARISH_ENGULFING"
    body = abs(current.close - current.open)
    full = current.high - current.low
    if full <= 0:
        return None
    lower_wick = min(current.open, current.close) - current.low
    upper_wick = current.high - max(current.open, current.close)
    if direction is Direction.LONG and bullish and lower_wick >= max(body * 2, full * 0.45):
        return "BULLISH_REJECTION"
    if direction is Direction.SHORT and bearish and upper_wick >= max(body * 2, full * 0.45):
        return "BEARISH_REJECTION"
    return None
