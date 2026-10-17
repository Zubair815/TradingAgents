"""Deterministic swing-zone construction and lifecycle."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from tradingagents.pair_observer.models import (
    Candle,
    Direction,
    LevelKind,
    LevelState,
    MarketLevel,
    Pivot,
    stable_fingerprint,
)


def level_from_pivot(
    pair: str,
    pivot: Pivot,
    *,
    atr: float,
    tick_size: float,
    width_atr_fraction: float = 0.15,
    source_type: str = "SWING",
) -> MarketLevel:
    """Create an ATR-scaled zone from a confirmed pivot."""

    if atr <= 0 or tick_size <= 0 or not 0 < width_atr_fraction <= 1:
        raise ValueError("invalid zone sizing inputs")
    width = max(tick_size, atr * width_atr_fraction)
    is_low = pivot.kind == "LOW"
    proximal = pivot.price if is_low else pivot.price
    distal = pivot.price - width if is_low else pivot.price + width
    kind = LevelKind.DEMAND if is_low else LevelKind.SUPPLY
    direction = Direction.LONG if is_low else Direction.SHORT
    level_id = stable_fingerprint(pair, pivot.timeframe, pivot.kind, pivot.open_time_utc, pivot.price)[:32]
    return MarketLevel(
        level_id=level_id,
        pair=pair,
        timeframe=pivot.timeframe,
        kind=kind,
        direction=direction,
        proximal_price=proximal,
        distal_price=distal,
        state=LevelState.ACTIVE,
        origin_open_utc=pivot.open_time_utc,
        detected_at_utc=pivot.confirmed_at_utc,
        strength=0.5,
        source_type=source_type,
    )


def range_boundary_levels(
    pair: str,
    candles: Sequence[Candle],
    *,
    source_type: str,
    atr: float,
    tick_size: float,
    detected_at_utc: datetime,
    strength: float = 0.65,
) -> tuple[MarketLevel, MarketLevel]:
    """Create stable support/resistance zones for a sourced price range."""
    if not candles:
        raise ValueError("boundary level source requires candles")
    high_candle = max(candles, key=lambda item: item.high)
    low_candle = min(candles, key=lambda item: item.low)
    width = max(tick_size, atr * 0.1)
    common = (pair, source_type, detected_at_utc.date().isoformat())
    resistance = MarketLevel(
        level_id=stable_fingerprint(*common, "HIGH", high_candle.high)[:32],
        pair=pair,
        timeframe=high_candle.timeframe,
        kind=LevelKind.RESISTANCE,
        direction=Direction.SHORT,
        proximal_price=high_candle.high,
        distal_price=high_candle.high + width,
        state=LevelState.ACTIVE,
        origin_open_utc=high_candle.open_time_utc,
        detected_at_utc=detected_at_utc,
        strength=strength,
        source_type=source_type,
    )
    support = MarketLevel(
        level_id=stable_fingerprint(*common, "LOW", low_candle.low)[:32],
        pair=pair,
        timeframe=low_candle.timeframe,
        kind=LevelKind.SUPPORT,
        direction=Direction.LONG,
        proximal_price=low_candle.low,
        distal_price=low_candle.low - width,
        state=LevelState.ACTIVE,
        origin_open_utc=low_candle.open_time_utc,
        detected_at_utc=detected_at_utc,
        strength=strength,
        source_type=source_type,
    )
    return resistance, support


def advance_level(level: MarketLevel, candle: Candle) -> MarketLevel:
    """Advance a level from one completed candle without rewriting history."""

    if not candle.completed:
        raise ValueError("level lifecycle accepts completed candles only")
    if candle.pair != level.pair or candle.timeframe is not level.timeframe:
        raise ValueError("candle does not match level market identity")
    if candle.close_time_utc < level.detected_at_utc:
        raise ValueError("candle predates level detection")
    if level.state in {LevelState.INVALIDATED, LevelState.EXPIRED, LevelState.CONSUMED, LevelState.RETESTED}:
        return level

    is_demand = level.direction is Direction.LONG
    zone_low = min(level.proximal_price, level.distal_price)
    zone_high = max(level.proximal_price, level.distal_price)
    touched = candle.low <= zone_high and candle.high >= zone_low
    if level.state is LevelState.RETEST_ELIGIBLE and touched:
        return level.model_copy(
            update={
                "state": LevelState.RETESTED,
                "touch_count": level.touch_count + 1,
                "last_touch_utc": candle.close_time_utc,
            }
        )
    distal_broken = candle.close < level.distal_price if is_demand else candle.close > level.distal_price
    if distal_broken:
        next_state = (
            LevelState.RETEST_ELIGIBLE
            if level.state is LevelState.BROKEN
            else LevelState.BROKEN
        )
        return level.model_copy(
            update={"state": next_state, "invalidation_reason": "DISTAL_CLOSE_BREACH"}
        )

    if touched:
        touches = level.touch_count + 1
        reacted = candle.close > zone_high if is_demand else candle.close < zone_low
        state = LevelState.CONSUMED if touches >= 3 else LevelState.REACTED if reacted else LevelState.TOUCHED
        return level.model_copy(
            update={
                "state": state,
                "touch_count": touches,
                "last_touch_utc": candle.close_time_utc,
                "strength": max(0.0, level.strength - 0.1),
            }
        )
    distance = zone_low - candle.high if candle.high < zone_low else candle.low - zone_high
    if level.state is LevelState.ACTIVE and 0 <= distance <= max(zone_high - zone_low, 1e-12):
        return level.model_copy(update={"state": LevelState.APPROACHED})
    return level
