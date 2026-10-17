"""Point-in-time-safe market structure calculations."""

from __future__ import annotations

from collections.abc import Sequence

from tradingagents.pair_observer.models import (
    Candle,
    MarketStructure,
    Pivot,
    ReasonCode,
    StructureState,
)


def _validate_window(candles: Sequence[Candle]) -> None:
    if not candles:
        return
    first = candles[0]
    previous = None
    for candle in candles:
        if candle.pair != first.pair or candle.timeframe is not first.timeframe:
            raise ValueError("all candles must have the same pair and timeframe")
        if not candle.completed:
            raise ValueError("structure accepts completed candles only")
        if previous is not None and candle.open_time_utc <= previous:
            raise ValueError("candles must be strictly ordered and unique")
        previous = candle.open_time_utc


def confirmed_pivots(
    candles: Sequence[Candle],
    *,
    left_strength: int,
    right_strength: int,
    equality_tolerance: float = 0.0,
) -> tuple[Pivot, ...]:
    """Return fractal pivots only when their right confirmation bar is closed.

    Equal adjacent candidates inside ``equality_tolerance`` collapse into one
    deterministic liquidity-cluster representative.
    """

    if left_strength < 1 or right_strength < 1:
        raise ValueError("pivot strengths must be positive")
    if equality_tolerance < 0:
        raise ValueError("equality_tolerance cannot be negative")
    _validate_window(candles)
    if len(candles) < left_strength + right_strength + 1:
        return ()

    detected: list[Pivot] = []
    for index in range(left_strength, len(candles) - right_strength):
        candidate = candles[index]
        neighbors = candles[index - left_strength : index] + candles[index + 1 : index + right_strength + 1]
        highest_neighbor = max(item.high for item in neighbors)
        lowest_neighbor = min(item.low for item in neighbors)
        confirmed_at = candles[index + right_strength].close_time_utc
        if candidate.high > highest_neighbor + equality_tolerance:
            detected.append(
                Pivot(
                    timeframe=candidate.timeframe,
                    kind="HIGH",
                    price=candidate.high,
                    open_time_utc=candidate.open_time_utc,
                    confirmed_at_utc=confirmed_at,
                )
            )
        if candidate.low < lowest_neighbor - equality_tolerance:
            detected.append(
                Pivot(
                    timeframe=candidate.timeframe,
                    kind="LOW",
                    price=candidate.low,
                    open_time_utc=candidate.open_time_utc,
                    confirmed_at_utc=confirmed_at,
                )
            )

    clustered: list[Pivot] = []
    for pivot in detected:
        if (
            clustered
            and clustered[-1].kind == pivot.kind
            and abs(clustered[-1].price - pivot.price) <= equality_tolerance
        ):
            continue
        clustered.append(pivot)
    return tuple(sorted(clustered, key=lambda item: (item.confirmed_at_utc, item.kind)))


def classify_structure(
    candles: Sequence[Candle],
    pivots: Sequence[Pivot],
    *,
    break_tolerance: float = 0.0,
) -> MarketStructure:
    """Classify structure from confirmed pivots at the last candle cutoff."""

    _validate_window(candles)
    if not candles:
        raise ValueError("at least one candle is required")
    if break_tolerance < 0:
        raise ValueError("break_tolerance cannot be negative")
    cutoff = candles[-1].close_time_utc
    available = tuple(pivot for pivot in pivots if pivot.confirmed_at_utc <= cutoff)
    highs = [pivot for pivot in available if pivot.kind == "HIGH"]
    lows = [pivot for pivot in available if pivot.kind == "LOW"]
    reasons: tuple[ReasonCode, ...] = ()
    if len(highs) < 2 or len(lows) < 2:
        state = StructureState.INSUFFICIENT_DATA
        reasons = (ReasonCode.INSUFFICIENT_HISTORY,)
    else:
        higher_high = highs[-1].price > highs[-2].price + break_tolerance
        higher_low = lows[-1].price > lows[-2].price + break_tolerance
        lower_high = highs[-1].price < highs[-2].price - break_tolerance
        lower_low = lows[-1].price < lows[-2].price - break_tolerance
        if higher_high and higher_low:
            state = StructureState.BULLISH
        elif lower_high and lower_low:
            state = StructureState.BEARISH
        else:
            latest_close = candles[-1].close
            broke_high = latest_close > highs[-1].price + break_tolerance
            broke_low = latest_close < lows[-1].price - break_tolerance
            state = StructureState.TRANSITIONING if broke_high or broke_low else StructureState.RANGING
    return MarketStructure(
        pair=candles[0].pair,
        timeframe=candles[0].timeframe,
        state=state,
        as_of_utc=cutoff,
        decisive_pivots=available[-4:],
        reason_codes=reasons,
    )


def ema_atr_support(candles: Sequence[Candle], *, ema_period: int, atr_period: int) -> float | None:
    """Return EMA slope normalized by ATR; never changes structure itself."""

    _validate_window(candles)
    required = max(ema_period + 1, atr_period + 1)
    if ema_period < 2 or atr_period < 2 or len(candles) < required:
        return None
    alpha = 2.0 / (ema_period + 1)
    ema_values = [candles[0].close]
    for candle in candles[1:]:
        ema_values.append(alpha * candle.close + (1 - alpha) * ema_values[-1])
    true_ranges = []
    for previous, current in zip(candles[-atr_period - 1 : -1], candles[-atr_period:], strict=True):
        true_ranges.append(
            max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close))
        )
    atr = sum(true_ranges) / len(true_ranges)
    if atr <= 0:
        return None
    return (ema_values[-1] - ema_values[-2]) / atr
