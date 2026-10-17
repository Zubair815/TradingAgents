from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tradingagents.pair_observer.levels import advance_level, level_from_pivot
from tradingagents.pair_observer.models import Candle, LevelState, StructureState, Timeframe
from tradingagents.pair_observer.structure import (
    classify_structure,
    confirmed_pivots,
    ema_atr_support,
)

START = datetime(2026, 1, 5, 8, tzinfo=UTC)


def candles_from(prices: list[tuple[float, float, float, float]]) -> list[Candle]:
    result = []
    for index, (open_, high, low, close) in enumerate(prices):
        opened = START + timedelta(minutes=5 * index)
        result.append(
            Candle(
                pair="EURUSD",
                timeframe=Timeframe.M5,
                open_time_utc=opened,
                close_time_utc=opened + timedelta(minutes=5),
                open=open_,
                high=high,
                low=low,
                close=close,
                tick_volume=100,
                completed=True,
                source="SYNTHETIC",
            )
        )
    return result


def test_pivot_is_not_available_before_right_confirmation_closes() -> None:
    candles = candles_from(
        [
            (1.00, 1.02, 0.99, 1.01),
            (1.01, 1.03, 1.00, 1.02),
            (1.02, 1.08, 1.01, 1.04),
            (1.04, 1.05, 1.00, 1.01),
            (1.01, 1.03, 0.98, 1.00),
        ]
    )
    assert confirmed_pivots(candles[:-1], left_strength=2, right_strength=2) == ()
    pivots = confirmed_pivots(candles, left_strength=2, right_strength=2)
    high = next(pivot for pivot in pivots if pivot.kind == "HIGH")
    assert high.open_time_utc == candles[2].open_time_utc
    assert high.confirmed_at_utc == candles[4].close_time_utc


def test_historical_replay_does_not_retime_confirmed_pivot() -> None:
    candles = candles_from(
        [
            (1.00, 1.02, 0.99, 1.01),
            (1.01, 1.03, 1.00, 1.02),
            (1.02, 1.08, 1.01, 1.04),
            (1.04, 1.05, 1.00, 1.01),
            (1.01, 1.03, 0.98, 1.00),
            (1.00, 1.04, 0.99, 1.03),
        ]
    )
    initial = confirmed_pivots(candles[:5], left_strength=2, right_strength=2)
    replayed = confirmed_pivots(candles, left_strength=2, right_strength=2)
    assert initial[0] in replayed


def test_structure_classifies_higher_highs_and_higher_lows() -> None:
    candles = candles_from([(1.0, 1.01, 0.99, 1.0)] * 20)
    cutoff = candles[-1].close_time_utc
    from tradingagents.pair_observer.models import Pivot

    pivots = (
        Pivot(timeframe=Timeframe.M5, kind="LOW", price=0.98, open_time_utc=START, confirmed_at_utc=START),
        Pivot(timeframe=Timeframe.M5, kind="HIGH", price=1.03, open_time_utc=START, confirmed_at_utc=START),
        Pivot(timeframe=Timeframe.M5, kind="LOW", price=0.99, open_time_utc=START, confirmed_at_utc=cutoff),
        Pivot(timeframe=Timeframe.M5, kind="HIGH", price=1.04, open_time_utc=START, confirmed_at_utc=cutoff),
    )
    assert classify_structure(candles, pivots).state is StructureState.BULLISH


def test_ema_slope_is_atr_normalized_and_directionally_symmetric() -> None:
    rising = candles_from([(1 + i * 0.001, 1.002 + i * 0.001, 0.999 + i * 0.001, 1.001 + i * 0.001) for i in range(25)])
    falling = candles_from([(2 - i * 0.001, 2.002 - i * 0.001, 1.999 - i * 0.001, 2.001 - i * 0.001) for i in range(25)])
    up = ema_atr_support(rising, ema_period=5, atr_period=5)
    down = ema_atr_support(falling, ema_period=5, atr_period=5)
    assert up is not None and down is not None
    assert up == pytest.approx(-down)


def test_swing_zone_uses_atr_and_invalidates_only_on_completed_distal_close() -> None:
    candles = candles_from([(1.0, 1.01, 0.99, 1.0)] * 5)
    pivot = confirmed_pivots(
        candles_from([(1.0, 1.01, 1.0, 1.0), (1.0, 1.01, 0.99, 1.0), (1.0, 1.01, 0.95, 1.0), (1.0, 1.01, 0.99, 1.0), (1.0, 1.01, 1.0, 1.0)]),
        left_strength=2,
        right_strength=2,
    )[0]
    level = level_from_pivot("EURUSD", pivot, atr=0.02, tick_size=0.00001)
    assert level.proximal_price - level.distal_price == pytest.approx(0.003)
    touched = advance_level(level, candles[-1].model_copy(update={"low": 0.949, "close": 0.951}))
    assert touched.state is LevelState.REACTED
    broken = advance_level(level, candles[-1].model_copy(update={"low": 0.94, "close": 0.94}))
    assert broken.state is LevelState.BROKEN
    eligible = advance_level(broken, candles[-1].model_copy(update={"low": 0.94, "close": 0.94}))
    assert eligible.state is LevelState.RETEST_ELIGIBLE
