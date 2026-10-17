"""Pure setup evaluation and alert construction."""

from __future__ import annotations

from datetime import datetime

from tradingagents.pair_observer.confirmations import confirmation_type
from tradingagents.pair_observer.models import (
    AlertKind,
    Candle,
    Direction,
    EligibilityResult,
    MarketLevel,
    MarketStructure,
    NewsStatus,
    ObserverAlert,
    SetupState,
    SetupType,
    StructureState,
    stable_fingerprint,
)


def stable_setup_id(
    pair: str,
    direction: Direction,
    setup_type: SetupType,
    level_id: str,
) -> str:
    return stable_fingerprint(pair, direction, setup_type, level_id)[:32]


def top_down_direction(structures: dict[str, MarketStructure]) -> Direction:
    d1 = structures.get("D1")
    h4 = structures.get("H4")
    if not d1 or not h4:
        return Direction.NEUTRAL
    bullish_ok = {StructureState.BULLISH, StructureState.RANGING, StructureState.TRANSITIONING}
    bearish_ok = {StructureState.BEARISH, StructureState.RANGING, StructureState.TRANSITIONING}
    if d1.state is StructureState.BULLISH and h4.state in bullish_ok:
        return Direction.LONG
    if h4.state is StructureState.BULLISH and d1.state in bullish_ok:
        return Direction.LONG
    if d1.state is StructureState.BEARISH and h4.state in bearish_ok:
        return Direction.SHORT
    if h4.state is StructureState.BEARISH and d1.state in bearish_ok:
        return Direction.SHORT
    return Direction.NEUTRAL


def nearby_level(levels: tuple[MarketLevel, ...], direction: Direction, price: float, atr: float) -> MarketLevel | None:
    candidates = [
        level
        for level in levels
        if level.direction is direction
        and level.state.value in {"ACTIVE", "APPROACHED", "TOUCHED", "REACTED", "RETEST_ELIGIBLE"}
        and min(level.proximal_price, level.distal_price) - atr * 0.25 <= price <= max(level.proximal_price, level.distal_price) + atr * 0.25
    ]
    return max(candidates, key=lambda item: item.strength, default=None)


def breakout_retest_level(
    levels: tuple[MarketLevel, ...],
    direction: Direction,
    m15_candles: tuple[Candle, ...],
) -> MarketLevel | None:
    """Find a completed-close breakout followed by a held retest."""
    if len(m15_candles) < 2 or direction is Direction.NEUTRAL:
        return None
    breakout, retest = m15_candles[-2:]
    for level in sorted(levels, key=lambda item: item.strength, reverse=True):
        if level.state.value in {"INVALIDATED", "EXPIRED", "CONSUMED"}:
            continue
        low = min(level.proximal_price, level.distal_price)
        high = max(level.proximal_price, level.distal_price)
        if (
            direction is Direction.LONG
            and level.direction is Direction.SHORT
            and breakout.close > high
            and retest.low <= high
            and retest.close > high
        ):
            return level
        if (
            direction is Direction.SHORT
            and level.direction is Direction.LONG
            and breakout.close < low
            and retest.high >= low
            and retest.close < low
        ):
            return level
    return None


def build_alert(
    *,
    pair: str,
    direction: Direction,
    setup_type: SetupType,
    level: MarketLevel,
    m5_candles: tuple[Candle, ...],
    eligibility: EligibilityResult,
    created_at_utc: datetime,
    developing_allowed: bool,
    ready_allowed: bool,
) -> ObserverAlert | None:
    if direction is Direction.NEUTRAL or not eligibility.eligible:
        return None
    confirmation = confirmation_type(m5_candles, direction)
    if confirmation and ready_allowed:
        kind, state = AlertKind.TECHNICALLY_READY, SetupState.TECHNICALLY_READY
    elif developing_allowed:
        kind, state = AlertKind.DEVELOPING, SetupState.DEVELOPING
    else:
        return None
    setup_id = stable_setup_id(pair, direction, setup_type, level.level_id)
    # One developing and one ready alert may exist for a setup. Repeated polls
    # and later candles cannot create duplicates for the same state.
    fingerprint = stable_fingerprint(setup_id, kind)
    news = eligibility.news_status
    label = " — NEWS UNVERIFIED" if news is NewsStatus.NEWS_UNVERIFIED else ""
    return ObserverAlert(
        alert_id=fingerprint[:32],
        setup_id=setup_id,
        pair=pair,
        direction=direction,
        setup_type=setup_type,
        kind=kind,
        state=state,
        created_at_utc=created_at_utc,
        fingerprint=fingerprint,
        news_status=news,
        headline=f"{pair} {direction.value} {kind.value.replace('_', ' ')}{label}",
        message=f"{setup_type.value} at {level.proximal_price:.5f}; M5 confirmation: {confirmation or 'pending'}.",
    )
