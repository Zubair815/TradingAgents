"""Deterministic previous-week context construction from completed MT5 candles."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from tradingagents.pair_observer.interfaces import PairObserverMarketSource
from tradingagents.pair_observer.levels import level_from_pivot, range_boundary_levels
from tradingagents.pair_observer.models import Timeframe, WeeklyReview, stable_fingerprint
from tradingagents.pair_observer.setups import top_down_direction
from tradingagents.pair_observer.structure import classify_structure, confirmed_pivots

REVIEW_HISTORY = {
    Timeframe.M5: 2_000,
    Timeframe.M15: 900,
    Timeframe.H1: 400,
    Timeframe.H4: 200,
    Timeframe.D1: 120,
}
REVIEW_PIVOT_STRENGTH = {
    Timeframe.M5: 2,
    Timeframe.M15: 2,
    Timeframe.H1: 2,
    Timeframe.H4: 3,
    Timeframe.D1: 3,
}


def previous_trading_week(now_utc: datetime) -> tuple[datetime, datetime]:
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc must be timezone-aware")
    now_utc = now_utc.astimezone(UTC)
    monday = now_utc.date() - timedelta(days=now_utc.weekday())
    if now_utc.weekday() < 4:
        monday -= timedelta(days=7)
    start = datetime.combine(monday, time.min, tzinfo=UTC)
    return start, start + timedelta(days=7)


def build_weekly_review(
    source: PairObserverMarketSource,
    pair: str,
    *,
    now_utc: datetime,
) -> WeeklyReview:
    week_start, nominal_week_end = previous_trading_week(now_utc)
    windows = {}
    warnings: list[str] = []
    for timeframe in Timeframe:
        candles = tuple(
            candle
            for candle in source.get_completed_candles(
                pair,
                timeframe,
                count=REVIEW_HISTORY[timeframe],
                as_of_utc=min(now_utc.astimezone(UTC), nominal_week_end),
            )
            if candle.completed and candle.close_time_utc <= nominal_week_end
        )
        if not candles:
            raise ValueError(f"no completed {timeframe.value} candles are available for review")
        windows[timeframe] = candles
    reviewed_week = tuple(
        candle
        for candle in windows[Timeframe.M15]
        if week_start <= candle.open_time_utc < nominal_week_end
    )
    if not reviewed_week:
        raise ValueError("the previous trading week has no completed candles")
    reviewed_through = max(candle.close_time_utc for candle in reviewed_week)

    structures = {}
    pivots = {}
    for timeframe, candles in windows.items():
        strength = REVIEW_PIVOT_STRENGTH[timeframe]
        tf_pivots = confirmed_pivots(candles, left_strength=strength, right_strength=strength)
        pivots[timeframe] = tf_pivots
        structures[timeframe.value] = classify_structure(candles, tf_pivots)

    h1_atr = _atr(windows[Timeframe.H1], 14)
    tick_size = source.get_tick_size(pair)
    levels = {}
    for timeframe in (Timeframe.D1, Timeframe.H4, Timeframe.H1):
        timeframe_atr = _atr(windows[timeframe], 14)
        for pivot in pivots[timeframe][-8:]:
            level = level_from_pivot(
                pair,
                pivot,
                atr=timeframe_atr,
                tick_size=tick_size,
                source_type=f"WEEKLY_REVIEW_{timeframe.value}_SWING",
            )
            levels[level.level_id] = level
    for level in range_boundary_levels(
        pair,
        reviewed_week,
        source_type="PREVIOUS_REVIEWED_WEEK",
        atr=h1_atr,
        tick_size=tick_size,
        detected_at_utc=reviewed_through,
        strength=0.9,
    ):
        levels[level.level_id] = level
    carried = tuple(sorted(levels.values(), key=lambda item: (-item.strength, item.level_id))[:40])
    if reviewed_through < week_start + timedelta(days=4):
        warnings.append("The stored week ended earlier than the nominal calendar boundary.")
    serialized = {key: value.model_dump(mode="json") for key, value in structures.items()}
    return WeeklyReview(
        review_id=f"weekly-{stable_fingerprint(pair, week_start.isoformat())[:24]}",
        pair=pair,
        week_start_utc=week_start,
        week_end_utc=reviewed_through,
        created_at_utc=now_utc.astimezone(UTC),
        direction=top_down_direction(structures),
        structures=serialized,
        carried_levels=carried,
        source_candle_counts={key.value: len(values) for key, values in windows.items()},
        warnings=tuple(warnings),
    ).with_integrity_hash()


def _atr(candles, period: int) -> float:
    if len(candles) < period + 1:
        raise ValueError("insufficient candles for weekly ATR")
    values = []
    for previous, current in zip(candles[-period - 1 : -1], candles[-period:], strict=True):
        values.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    result = sum(values) / len(values)
    if result <= 0:
        raise ValueError("weekly ATR must be positive")
    return result
