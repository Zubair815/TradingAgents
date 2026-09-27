"""Forex Market Structure Engine.

Deterministic quantitative price action analysis:
- Swing High & Swing Low identification with configurable fractal window.
- Swing tagging: Higher High (HH), Higher Low (HL), Lower High (LH), Lower Low (LL), Equal High/Low (EQH/EQL).
- Structural Breaks: Break of Structure (BOS, continuation) and Change of Character (CHoCH, reversal).
- Fair Value Gaps (FVG): Bullish and Bearish 3-candle imbalances with mitigation tracking.
- Order Blocks (OB): Last opposite candle before structural displacement.
- Support & Resistance proximity: Nearest key levels and distance in pips.
- Multi-Timeframe Structure Alignment: Confluence between Macro (D1/H4) and Tactical (H1/M15).
- Clean prompt/report formatters for LLM decision agents.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from tradingagents.dataflows.forex_quality import DataInsufficientError

if TYPE_CHECKING:
    from tradingagents.dataflows.forex_data import MultiTimeframeData

import pandas as pd

from tradingagents.forex.domain import (
    ForexPair,
    Timeframe,
    get_forex_pair,
    resolve_timeframe,
)
from tradingagents.forex.pips import pip_size_for

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SwingType(str, Enum):
    """Type of swing pivot point."""

    HIGH = "HIGH"
    LOW = "LOW"


class SwingTag(str, Enum):
    """Classification of swing relative to preceding swing of same type."""

    HH = "HH"    # Higher High
    LH = "LH"    # Lower High
    HL = "HL"    # Higher Low
    LL = "LL"    # Lower Low
    EQH = "EQH"  # Equal High (Double Top)
    EQL = "EQL"  # Equal Low (Double Bottom)


class StructureTrend(str, Enum):
    """Directional bias derived from market structure progression."""

    BULLISH = "BULLISH"      # Series of HHs and HLs
    BEARISH = "BEARISH"      # Series of LHs and LLs
    RANGING = "RANGING"      # Mixed or converging/expanding swings
    UNDEFINED = "UNDEFINED"  # Insufficient swing history


class BreakType(str, Enum):
    """Nature of structural break."""

    BOS = "BOS"      # Break of Structure (trend continuation)
    CHOCH = "CHOCH"  # Change of Character (potential trend reversal)


class BreakDirection(str, Enum):
    """Direction of structural break."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class FVGType(str, Enum):
    """Fair Value Gap bias."""

    BULLISH = "BULLISH"  # Buying imbalance: candle 3 low > candle 1 high
    BEARISH = "BEARISH"  # Selling imbalance: candle 3 high < candle 1 low


class OBType(str, Enum):
    """Order Block direction."""

    BULLISH = "BULLISH"  # Last down candle before bullish displacement
    BEARISH = "BEARISH"  # Last up candle before bearish displacement


class AlignmentBias(str, Enum):
    """Overall multi-timeframe structural confluence."""

    CONFLUENT_BULLISH = "CONFLUENT_BULLISH"
    CONFLUENT_BEARISH = "CONFLUENT_BEARISH"
    PULLBACK_COMPLETION_BULLISH = "PULLBACK_COMPLETION_BULLISH"
    PULLBACK_COMPLETION_BEARISH = "PULLBACK_COMPLETION_BEARISH"
    COUNTER_TREND = "COUNTER_TREND"
    MIXED = "MIXED"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SwingPoint:
    """Individual swing high or swing low pivot."""

    index: int
    timestamp: datetime
    price: float
    swing_type: SwingType
    tag: SwingTag | None = None


@dataclass(frozen=True)
class StructuralBreak:
    """Event where price closed beyond a prior significant swing level."""

    index: int
    timestamp: datetime
    break_type: BreakType
    direction: BreakDirection
    broken_level: float
    break_price: float
    prior_swing: SwingPoint


@dataclass
class FairValueGap:
    """3-candle price imbalance (FVG)."""

    start_index: int
    end_index: int
    timestamp: datetime
    fvg_type: FVGType
    top: float
    bottom: float
    midpoint: float
    size_pips: float
    is_mitigated: bool = False
    mitigated_at: datetime | None = None


@dataclass
class OrderBlock:
    """Institutional supply/demand footprint before displacement."""

    candle_index: int
    timestamp: datetime
    ob_type: OBType
    top: float
    bottom: float
    midpoint: float
    size_pips: float
    is_mitigated: bool = False
    mitigated_at: datetime | None = None


@dataclass(frozen=True)
class MarketStructureSnapshot:
    """Point-in-time quantitative market structure summary for a single timeframe."""

    symbol: str
    pair: ForexPair | None
    timeframe: Timeframe | None
    timestamp: datetime
    current_price: float
    trend: StructureTrend
    recent_swings: list[SwingPoint]
    latest_swing_high: SwingPoint | None
    latest_swing_low: SwingPoint | None
    last_break: StructuralBreak | None
    active_fvgs: list[FairValueGap]
    active_order_blocks: list[OrderBlock]
    nearest_support_price: float | None
    nearest_support_pips: float | None
    nearest_resistance_price: float | None
    nearest_resistance_pips: float | None


@dataclass(frozen=True)
class MultiTimeframeStructureAlignment:
    """Multi-horizon structural confluence summary across timeframes."""

    symbol: str
    pair: ForexPair | None
    timestamp: datetime
    structures: dict[Timeframe, MarketStructureSnapshot]
    macro_trend: StructureTrend
    tactical_trend: StructureTrend
    is_aligned: bool
    alignment_bias: AlignmentBias


# ---------------------------------------------------------------------------
# Core Algorithms: Swing Detection & Tagging
# ---------------------------------------------------------------------------


def _ensure_utc_datetime(val: Any) -> datetime:
    """Safely convert value to UTC datetime."""
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)
    try:
        ts = pd.to_datetime(val)
        if ts.tzinfo is None:
            return ts.tz_localize("UTC").to_pydatetime()
        return ts.tz_convert("UTC").to_pydatetime()
    except Exception:
        return datetime.now(timezone.utc)


def find_swing_points(
    df: pd.DataFrame,
    lookback: int = 3,
    lookforward: int = 3,
    pair: ForexPair | str | None = None,
) -> list[SwingPoint]:
    """Identify swing high and swing low pivots in an OHLCV DataFrame.

    A candle i is a swing high if High[i] is strictly greater than all Highs in
    [i - lookback, i) and greater than or equal to all Highs in (i, i + lookforward].
    Respects point-in-time verification: pivots are only confirmed after lookforward bars close.

    Returns a chronological list of SwingPoint objects tagged with HH/HL/LH/LL/EQH/EQL.
    """
    if df is None or len(df) < (lookback + lookforward + 1):
        return []

    from tradingagents.dataflows.forex_data import validate_forex_candles

    v_df = validate_forex_candles(df)
    n = len(v_df)
    highs = v_df["High"].values
    lows = v_df["Low"].values
    dates = v_df["Date"].values

    pip_size = pip_size_for(pair) if pair else 0.0001
    tolerance = 1.5 * pip_size  # Swings within 1.5 pips classified as EQH/EQL

    raw_swings: list[SwingPoint] = []

    for i in range(lookback, n - lookforward):
        current_high = highs[i]
        current_low = lows[i]

        is_high = True
        for j in range(i - lookback, i + lookforward + 1):
            if j == i:
                continue
            if highs[j] > current_high or (highs[j] == current_high and j < i):
                is_high = False
                break

        is_low = True
        for j in range(i - lookback, i + lookforward + 1):
            if j == i:
                continue
            if lows[j] < current_low or (lows[j] == current_low and j < i):
                is_low = False
                break

        dt = _ensure_utc_datetime(dates[i])

        if is_high and not is_low:
            raw_swings.append(
                SwingPoint(
                    index=i,
                    timestamp=dt,
                    price=float(current_high),
                    swing_type=SwingType.HIGH,
                )
            )
        elif is_low and not is_high:
            raw_swings.append(
                SwingPoint(
                    index=i,
                    timestamp=dt,
                    price=float(current_low),
                    swing_type=SwingType.LOW,
                )
            )
        elif is_high and is_low:
            # Neutral / single-bar expansion outside candle
            raw_swings.append(
                SwingPoint(
                    index=i,
                    timestamp=dt,
                    price=float(current_high),
                    swing_type=SwingType.HIGH,
                )
            )
            raw_swings.append(
                SwingPoint(
                    index=i,
                    timestamp=dt,
                    price=float(current_low),
                    swing_type=SwingType.LOW,
                )
            )

    # Chronological sort
    raw_swings.sort(key=lambda s: (s.index, 0 if s.swing_type == SwingType.LOW else 1))

    # Tag swings relative to previous swing of the SAME type
    tagged_swings: list[SwingPoint] = []
    prev_high: SwingPoint | None = None
    prev_low: SwingPoint | None = None

    for sp in raw_swings:
        tag: SwingTag | None = None
        if sp.swing_type == SwingType.HIGH:
            if prev_high is not None:
                diff = sp.price - prev_high.price
                if abs(diff) <= tolerance:
                    tag = SwingTag.EQH
                elif diff > 0:
                    tag = SwingTag.HH
                else:
                    tag = SwingTag.LH
            prev_high = sp
        else:
            if prev_low is not None:
                diff = sp.price - prev_low.price
                if abs(diff) <= tolerance:
                    tag = SwingTag.EQL
                elif diff > 0:
                    tag = SwingTag.HL
                else:
                    tag = SwingTag.LL
            prev_low = sp

        tagged_swings.append(
            SwingPoint(
                index=sp.index,
                timestamp=sp.timestamp,
                price=sp.price,
                swing_type=sp.swing_type,
                tag=tag,
            )
        )

    return tagged_swings


# ---------------------------------------------------------------------------
# Structural Breaks: BOS & CHoCH
# ---------------------------------------------------------------------------


def detect_structural_breaks(
    df: pd.DataFrame,
    swings: list[SwingPoint],
) -> list[StructuralBreak]:
    """Detect Break of Structure (BOS) and Change of Character (CHoCH) events.

    A break occurs when a subsequent candle's Close decisively breaches a prior swing level.
    - Bullish BOS: Price closes above prior Swing High in a bullish trend.
    - Bearish BOS: Price closes below prior Swing Low in a bearish trend.
    - Bullish CHoCH: Price closes above prior Lower High (LH), breaking bearish structure.
    - Bearish CHoCH: Price closes below prior Higher Low (HL), breaking bullish structure.
    """
    if df is None or df.empty or len(swings) < 2:
        return []

    from tradingagents.dataflows.forex_data import validate_forex_candles

    v_df = validate_forex_candles(df)
    closes = v_df["Close"].values
    dates = v_df["Date"].values
    n = len(v_df)

    breaks: list[StructuralBreak] = []

    # Track active swing levels that have not yet been broken
    active_highs = [s for s in swings if s.swing_type == SwingType.HIGH]
    active_lows = [s for s in swings if s.swing_type == SwingType.LOW]

    broken_high_indices: set[int] = set()
    broken_low_indices: set[int] = set()

    current_trend = StructureTrend.UNDEFINED

    for i in range(n):
        close_price = closes[i]
        dt = _ensure_utc_datetime(dates[i])

        # Check highs that formed BEFORE bar i
        for high_sp in active_highs:
            if high_sp.index >= i or high_sp.index in broken_high_indices:
                continue

            if close_price > high_sp.price:
                # Upward break
                broken_high_indices.add(high_sp.index)
                if high_sp.tag == SwingTag.LH or current_trend == StructureTrend.BEARISH:
                    b_type = BreakType.CHOCH
                    current_trend = StructureTrend.BULLISH
                else:
                    b_type = BreakType.BOS
                    current_trend = StructureTrend.BULLISH

                breaks.append(
                    StructuralBreak(
                        index=i,
                        timestamp=dt,
                        break_type=b_type,
                        direction=BreakDirection.BULLISH,
                        broken_level=high_sp.price,
                        break_price=float(close_price),
                        prior_swing=high_sp,
                    )
                )

        # Check lows that formed BEFORE bar i
        for low_sp in active_lows:
            if low_sp.index >= i or low_sp.index in broken_low_indices:
                continue

            if close_price < low_sp.price:
                # Downward break
                broken_low_indices.add(low_sp.index)
                if low_sp.tag == SwingTag.HL or current_trend == StructureTrend.BULLISH:
                    b_type = BreakType.CHOCH
                    current_trend = StructureTrend.BEARISH
                else:
                    b_type = BreakType.BOS
                    current_trend = StructureTrend.BEARISH

                breaks.append(
                    StructuralBreak(
                        index=i,
                        timestamp=dt,
                        break_type=b_type,
                        direction=BreakDirection.BEARISH,
                        broken_level=low_sp.price,
                        break_price=float(close_price),
                        prior_swing=low_sp,
                    )
                )

    return breaks


def classify_structure_trend(
    swings: list[SwingPoint],
    recent_breaks: list[StructuralBreak] | None = None,
) -> StructureTrend:
    """Classify the current market structure trend based on swing sequence and latest breaks."""
    if not swings:
        return StructureTrend.UNDEFINED

    # If recent break occurred, use it as a primary signal
    if recent_breaks:
        last_break = recent_breaks[-1]
        if last_break.direction == BreakDirection.BULLISH:
            return StructureTrend.BULLISH
        elif last_break.direction == BreakDirection.BEARISH:
            return StructureTrend.BEARISH

    high_tags = [s.tag for s in swings if s.swing_type == SwingType.HIGH and s.tag is not None]
    low_tags = [s.tag for s in swings if s.swing_type == SwingType.LOW and s.tag is not None]

    if not high_tags and not low_tags:
        return StructureTrend.UNDEFINED

    last_high_tag = high_tags[-1] if high_tags else None
    last_low_tag = low_tags[-1] if low_tags else None

    if last_high_tag in (SwingTag.HH, SwingTag.EQH) and last_low_tag == SwingTag.HL:
        return StructureTrend.BULLISH
    if last_high_tag == SwingTag.LH and last_low_tag in (SwingTag.LL, SwingTag.EQL):
        return StructureTrend.BEARISH
    if last_high_tag == SwingTag.HH and last_low_tag == SwingTag.LL:
        return StructureTrend.RANGING  # Expanding megaphone
    if last_high_tag == SwingTag.LH and last_low_tag == SwingTag.HL:
        return StructureTrend.RANGING  # Converging triangle

    if last_high_tag == SwingTag.HH:
        return StructureTrend.BULLISH
    if last_low_tag == SwingTag.LL:
        return StructureTrend.BEARISH

    return StructureTrend.RANGING


# ---------------------------------------------------------------------------
# Fair Value Gaps (FVG)
# ---------------------------------------------------------------------------


def find_fair_value_gaps(
    df: pd.DataFrame,
    pair: ForexPair | str | None = None,
    min_gap_pips: float = 1.0,
) -> list[FairValueGap]:
    """Identify 3-candle Fair Value Gaps (imbalances) and determine mitigation status."""
    if df is None or len(df) < 3:
        return []

    from tradingagents.dataflows.forex_data import validate_forex_candles

    v_df = validate_forex_candles(df)
    n = len(v_df)
    highs = v_df["High"].values
    lows = v_df["Low"].values
    dates = v_df["Date"].values

    pip_size = pip_size_for(pair) if pair else 0.0001
    min_gap_price = min_gap_pips * pip_size

    fvgs: list[FairValueGap] = []

    for i in range(2, n):
        c1_high = highs[i - 2]
        c1_low = lows[i - 2]
        c3_high = highs[i]
        c3_low = lows[i]
        dt = _ensure_utc_datetime(dates[i])

        # Bullish FVG: Candle 3 Low > Candle 1 High
        if (c3_low - c1_high) >= min_gap_price:
            bottom = float(c1_high)
            top = float(c3_low)
            midpoint = (bottom + top) / 2.0
            size_pips = (top - bottom) / pip_size

            # Check if mitigated in subsequent bars
            is_mit = False
            mit_at = None
            for k in range(i + 1, n):
                if lows[k] <= bottom:
                    is_mit = True
                    mit_at = _ensure_utc_datetime(dates[k])
                    break

            fvgs.append(
                FairValueGap(
                    start_index=i - 2,
                    end_index=i,
                    timestamp=dt,
                    fvg_type=FVGType.BULLISH,
                    top=top,
                    bottom=bottom,
                    midpoint=midpoint,
                    size_pips=size_pips,
                    is_mitigated=is_mit,
                    mitigated_at=mit_at,
                )
            )

        # Bearish FVG: Candle 3 High < Candle 1 Low
        elif (c1_low - c3_high) >= min_gap_price:
            top = float(c1_low)
            bottom = float(c3_high)
            midpoint = (bottom + top) / 2.0
            size_pips = (top - bottom) / pip_size

            is_mit = False
            mit_at = None
            for k in range(i + 1, n):
                if highs[k] >= top:
                    is_mit = True
                    mit_at = _ensure_utc_datetime(dates[k])
                    break

            fvgs.append(
                FairValueGap(
                    start_index=i - 2,
                    end_index=i,
                    timestamp=dt,
                    fvg_type=FVGType.BEARISH,
                    top=top,
                    bottom=bottom,
                    midpoint=midpoint,
                    size_pips=size_pips,
                    is_mitigated=is_mit,
                    mitigated_at=mit_at,
                )
            )

    return fvgs


# ---------------------------------------------------------------------------
# Order Blocks (OB)
# ---------------------------------------------------------------------------


def find_order_blocks(
    df: pd.DataFrame,
    breaks: list[StructuralBreak] | None = None,
    pair: ForexPair | str | None = None,
    max_blocks: int = 5,
) -> list[OrderBlock]:
    """Identify Institutional Order Blocks.

    A Bullish Order Block is the last bearish candle prior to a bullish break or displacement.
    A Bearish Order Block is the last bullish candle prior to a bearish break or displacement.
    """
    if df is None or len(df) < 5:
        return []

    from tradingagents.dataflows.forex_data import validate_forex_candles

    v_df = validate_forex_candles(df)
    n = len(v_df)
    opens = v_df["Open"].values
    highs = v_df["High"].values
    lows = v_df["Low"].values
    closes = v_df["Close"].values
    dates = v_df["Date"].values

    pip_size = pip_size_for(pair) if pair else 0.0001
    order_blocks: list[OrderBlock] = []

    if breaks is None:
        swings = find_swing_points(df, lookback=2, lookforward=2, pair=pair)
        breaks = detect_structural_breaks(df, swings)

    for brk in breaks:
        break_idx = brk.index
        search_start = max(0, brk.prior_swing.index - 3)

        if brk.direction == BreakDirection.BULLISH:
            # Find the last bearish candle (Close < Open) between prior swing and break
            cand_idx: int | None = None
            for j in range(break_idx - 1, search_start - 1, -1):
                if closes[j] < opens[j]:
                    cand_idx = j
                    break

            if cand_idx is not None:
                top = float(highs[cand_idx])
                bottom = float(lows[cand_idx])
                midpoint = (top + bottom) / 2.0
                size_pips = (top - bottom) / pip_size

                # Check if mitigated after the break
                is_mit = False
                mit_at = None
                for k in range(break_idx + 1, n):
                    if closes[k] < bottom:
                        is_mit = True
                        mit_at = _ensure_utc_datetime(dates[k])
                        break

                order_blocks.append(
                    OrderBlock(
                        candle_index=cand_idx,
                        timestamp=_ensure_utc_datetime(dates[cand_idx]),
                        ob_type=OBType.BULLISH,
                        top=top,
                        bottom=bottom,
                        midpoint=midpoint,
                        size_pips=size_pips,
                        is_mitigated=is_mit,
                        mitigated_at=mit_at,
                    )
                )

        elif brk.direction == BreakDirection.BEARISH:
            # Find the last bullish candle (Close > Open) between prior swing and break
            cand_idx: int | None = None
            for j in range(break_idx - 1, search_start - 1, -1):
                if closes[j] > opens[j]:
                    cand_idx = j
                    break

            if cand_idx is not None:
                top = float(highs[cand_idx])
                bottom = float(lows[cand_idx])
                midpoint = (top + bottom) / 2.0
                size_pips = (top - bottom) / pip_size

                is_mit = False
                mit_at = None
                for k in range(break_idx + 1, n):
                    if closes[k] > top:
                        is_mit = True
                        mit_at = _ensure_utc_datetime(dates[k])
                        break

                order_blocks.append(
                    OrderBlock(
                        candle_index=cand_idx,
                        timestamp=_ensure_utc_datetime(dates[cand_idx]),
                        ob_type=OBType.BEARISH,
                        top=top,
                        bottom=bottom,
                        midpoint=midpoint,
                        size_pips=size_pips,
                        is_mitigated=is_mit,
                        mitigated_at=mit_at,
                    )
                )

    # Deduplicate and take most recent unmitigated or active blocks
    order_blocks.sort(key=lambda b: b.candle_index, reverse=True)
    return order_blocks[:max_blocks]


# ---------------------------------------------------------------------------
# Key Support & Resistance Levels
# ---------------------------------------------------------------------------


def find_support_resistance_levels(
    current_price: float,
    swings: list[SwingPoint],
    pair: ForexPair | str | None = None,
) -> tuple[float | None, float | None, float | None, float | None]:
    """Determine nearest support and resistance levels from recent swings.

    Returns:
        tuple of (nearest_support, nearest_resistance, support_pips, resistance_pips)
    """
    pip_size = pip_size_for(pair) if pair else 0.0001

    supports = [s.price for s in swings if s.price < current_price]
    resistances = [s.price for s in swings if s.price > current_price]

    nearest_sup = max(supports) if supports else None
    nearest_res = min(resistances) if resistances else None

    sup_pips = (current_price - nearest_sup) / pip_size if nearest_sup is not None else None
    res_pips = (nearest_res - current_price) / pip_size if nearest_res is not None else None

    return nearest_sup, nearest_res, sup_pips, res_pips


# ---------------------------------------------------------------------------
# Single-Timeframe Snapshot Builder
# ---------------------------------------------------------------------------


def build_market_structure_snapshot(
    df: pd.DataFrame,
    pair: ForexPair | str | None = None,
    timeframe: Timeframe | str | None = None,
    lookback: int = 3,
    lookforward: int = 3,
) -> MarketStructureSnapshot:
    """Build a comprehensive point-in-time MarketStructureSnapshot from an OHLCV DataFrame."""
    if df is None or df.empty:
        raise ValueError("Cannot build market structure snapshot from empty DataFrame.")

    from tradingagents.dataflows.forex_data import validate_forex_candles

    v_df = validate_forex_candles(df)
    latest_row = v_df.iloc[-1]
    curr_price = float(latest_row["Close"])
    dt = _ensure_utc_datetime(latest_row["Date"])

    resolved_pair = get_forex_pair(pair) if isinstance(pair, str) else pair
    sym = resolved_pair.symbol if resolved_pair else (str(pair) if pair else "EURUSD")

    resolved_tf: Timeframe | None = None
    if isinstance(timeframe, Timeframe):
        resolved_tf = timeframe
    elif isinstance(timeframe, str):
        from tradingagents.dataflows.forex_data import resolve_timeframe
        resolved_tf = resolve_timeframe(timeframe)

    # 1. Swings
    swings = find_swing_points(v_df, lookback=lookback, lookforward=lookforward, pair=resolved_pair)

    # 2. Breaks
    breaks = detect_structural_breaks(v_df, swings)
    last_break = breaks[-1] if breaks else None

    # 3. Trend
    trend = classify_structure_trend(swings, breaks)

    # 4. FVGs
    all_fvgs = find_fair_value_gaps(v_df, pair=resolved_pair)
    active_fvgs = [f for f in all_fvgs if not f.is_mitigated]

    # 5. Order Blocks
    all_obs = find_order_blocks(v_df, breaks=breaks, pair=resolved_pair)
    active_obs = [ob for ob in all_obs if not ob.is_mitigated]

    # 6. Support & Resistance
    sup, res, sup_pips, res_pips = find_support_resistance_levels(curr_price, swings, resolved_pair)

    high_swings = [s for s in swings if s.swing_type == SwingType.HIGH]
    low_swings = [s for s in swings if s.swing_type == SwingType.LOW]

    latest_high = high_swings[-1] if high_swings else None
    latest_low = low_swings[-1] if low_swings else None

    return MarketStructureSnapshot(
        symbol=sym,
        pair=resolved_pair,
        timeframe=resolved_tf,
        timestamp=dt,
        current_price=curr_price,
        trend=trend,
        recent_swings=swings[-6:] if len(swings) >= 6 else swings,
        latest_swing_high=latest_high,
        latest_swing_low=latest_low,
        last_break=last_break,
        active_fvgs=active_fvgs[-3:],
        active_order_blocks=active_obs[-3:],
        nearest_support_price=sup,
        nearest_support_pips=sup_pips,
        nearest_resistance_price=res,
        nearest_resistance_pips=res_pips,
    )


# ---------------------------------------------------------------------------
# Multi-Timeframe Structure Alignment
# ---------------------------------------------------------------------------


def analyze_multi_timeframe_structure(
    bundle: MultiTimeframeData,
    lookback: int = 3,
    lookforward: int = 3,
    execution_timeframe: Timeframe | str | None = None,
    context_timeframes: Sequence[Timeframe | str] | None = None,
) -> MultiTimeframeStructureAlignment:
    """Analyze multi-timeframe market structure alignment across requested timeframes."""
    structures: dict[Timeframe, MarketStructureSnapshot] = {}

    for tf, df in bundle.candles.items():
        if df.empty or len(df) < (lookback + lookforward + 1):
            continue
        try:
            snap = build_market_structure_snapshot(
                df=df,
                pair=bundle.pair,
                timeframe=tf,
                lookback=lookback,
                lookforward=lookforward,
            )
            structures[tf] = snap
        except DataInsufficientError:
            raise
        except Exception as exc:
            logger.warning(f"Could not build market structure for timeframe {tf.value}: {exc}")

    # Determine Tactical trend (execution timeframe prioritized)
    tactical_tf: Timeframe | None = None
    if execution_timeframe is not None:
        try:
            cand_exec = resolve_timeframe(execution_timeframe)
            if cand_exec in structures:
                tactical_tf = cand_exec
        except Exception:
            pass
    if tactical_tf is None:
        if Timeframe.M15 in structures:
            tactical_tf = Timeframe.M15
        elif Timeframe.H1 in structures:
            tactical_tf = Timeframe.H1
        elif structures:
            tactical_tf = min(structures.keys(), key=lambda t: t.seconds)

    # Determine Macro trend (highest context timeframe prioritized)
    macro_tf: Timeframe | None = None
    if context_timeframes is not None:
        valid_ctx = []
        for c in context_timeframes:
            try:
                ctf = resolve_timeframe(c)
                if ctf in structures and (tactical_tf is None or ctf != tactical_tf):
                    valid_ctx.append(ctf)
            except Exception:
                pass
        if valid_ctx:
            macro_tf = max(valid_ctx, key=lambda t: t.seconds)
    if macro_tf is None:
        if Timeframe.H4 in structures and tactical_tf != Timeframe.H4:
            macro_tf = Timeframe.H4
        elif Timeframe.D1 in structures and tactical_tf != Timeframe.D1:
            macro_tf = Timeframe.D1
        elif structures:
            candidates = [t for t in structures if t != tactical_tf]
            macro_tf = max(candidates, key=lambda t: t.seconds) if candidates else tactical_tf

    # Determine Intermediate trend (timeframe between tactical and macro)
    inter_tf: Timeframe | None = None
    if tactical_tf and macro_tf and tactical_tf != macro_tf:
        between = [
            t for t in structures
            if min(tactical_tf.seconds, macro_tf.seconds) < t.seconds < max(tactical_tf.seconds, macro_tf.seconds)
        ]
        if between:
            if Timeframe.H1 in between:
                inter_tf = Timeframe.H1
            else:
                inter_tf = max(between, key=lambda t: t.seconds)
    if inter_tf is None and Timeframe.H1 in structures and Timeframe.H1 not in (tactical_tf, macro_tf):
        inter_tf = Timeframe.H1

    macro_trend = structures[macro_tf].trend if macro_tf and macro_tf in structures else StructureTrend.UNDEFINED
    tactical_trend = structures[tactical_tf].trend if tactical_tf and tactical_tf in structures else StructureTrend.UNDEFINED
    inter_trend = structures[inter_tf].trend if inter_tf and inter_tf in structures else StructureTrend.UNDEFINED

    # Evaluate alignment bias
    is_aligned = False
    bias = AlignmentBias.MIXED

    if macro_trend == StructureTrend.BULLISH:
        if tactical_trend == StructureTrend.BULLISH and inter_trend in (StructureTrend.BULLISH, StructureTrend.UNDEFINED):
            is_aligned = True
            bias = AlignmentBias.CONFLUENT_BULLISH
        elif tactical_trend == StructureTrend.BEARISH or inter_trend == StructureTrend.BEARISH:
            # Tactical pullback inside macro uptrend
            bias = AlignmentBias.PULLBACK_COMPLETION_BULLISH if tactical_trend == StructureTrend.BULLISH else AlignmentBias.COUNTER_TREND
        else:
            bias = AlignmentBias.MIXED
    elif macro_trend == StructureTrend.BEARISH:
        if tactical_trend == StructureTrend.BEARISH and inter_trend in (StructureTrend.BEARISH, StructureTrend.UNDEFINED):
            is_aligned = True
            bias = AlignmentBias.CONFLUENT_BEARISH
        elif tactical_trend == StructureTrend.BULLISH or inter_trend == StructureTrend.BULLISH:
            bias = AlignmentBias.PULLBACK_COMPLETION_BEARISH if tactical_trend == StructureTrend.BEARISH else AlignmentBias.COUNTER_TREND
        else:
            bias = AlignmentBias.MIXED

    ts = bundle.as_of or datetime.now(timezone.utc)

    return MultiTimeframeStructureAlignment(
        symbol=bundle.symbol,
        pair=bundle.pair,
        timestamp=ts,
        structures=structures,
        macro_trend=macro_trend,
        tactical_trend=tactical_trend,
        is_aligned=is_aligned,
        alignment_bias=bias,
    )


# ---------------------------------------------------------------------------
# Formatting Utilities for Agent Prompts & Markdown Logs
# ---------------------------------------------------------------------------


def format_market_structure_snapshot(snapshot: MarketStructureSnapshot) -> str:
    """Format single-timeframe market structure into clear markdown for agent prompts."""
    digits = snapshot.pair.digits if snapshot.pair else 5
    p_fmt = f"{{:.{digits}f}}"

    lines = [
        f"**{snapshot.symbol} [{snapshot.timeframe.value if snapshot.timeframe else 'Structure'}] Market Structure**",
        f"- Structure Trend: **{snapshot.trend.value}**",
        f"- Current Price: {p_fmt.format(snapshot.current_price)}",
    ]

    # Swings
    if snapshot.latest_swing_high:
        lines.append(
            f"- Recent Swing High: {p_fmt.format(snapshot.latest_swing_high.price)} "
            f"[{snapshot.latest_swing_high.tag.value if snapshot.latest_swing_high.tag else 'HIGH'}]"
        )
    if snapshot.latest_swing_low:
        lines.append(
            f"- Recent Swing Low: {p_fmt.format(snapshot.latest_swing_low.price)} "
            f"[{snapshot.latest_swing_low.tag.value if snapshot.latest_swing_low.tag else 'LOW'}]"
        )

    # Last break
    if snapshot.last_break:
        b = snapshot.last_break
        lines.append(
            f"- Last Structural Break: {b.break_type.value} ({b.direction.value}) @ {p_fmt.format(b.broken_level)}"
        )

    # Key Support & Resistance
    sup_str = (
        f"{p_fmt.format(snapshot.nearest_support_price)} ({snapshot.nearest_support_pips:.1f} pips below)"
        if snapshot.nearest_support_price is not None and snapshot.nearest_support_pips is not None
        else "None detected"
    )
    res_str = (
        f"{p_fmt.format(snapshot.nearest_resistance_price)} ({snapshot.nearest_resistance_pips:.1f} pips above)"
        if snapshot.nearest_resistance_price is not None and snapshot.nearest_resistance_pips is not None
        else "None detected"
    )
    lines.append(f"- Nearest Key Support: {sup_str}")
    lines.append(f"- Nearest Key Resistance: {res_str}")

    # Active Imbalances (FVG)
    if snapshot.active_fvgs:
        fvg_strs = [
            f"{f.fvg_type.value} [{p_fmt.format(f.bottom)} - {p_fmt.format(f.top)} | {f.size_pips:.1f} pips]"
            for f in snapshot.active_fvgs
        ]
        lines.append(f"- Active Unmitigated FVGs: {', '.join(fvg_strs)}")

    # Active Order Blocks
    if snapshot.active_order_blocks:
        ob_strs = [
            f"{ob.ob_type.value} [{p_fmt.format(ob.bottom)} - {p_fmt.format(ob.top)} | {ob.size_pips:.1f} pips]"
            for ob in snapshot.active_order_blocks
        ]
        lines.append(f"- Active Order Blocks: {', '.join(ob_strs)}")

    return "\n".join(lines)


def format_multi_timeframe_structure_summary(alignment: MultiTimeframeStructureAlignment) -> str:
    """Format multi-timeframe structure alignment into a comprehensive markdown summary."""
    lines = ["### Multi-Timeframe Market Structure Matrix\n"]

    trend_icon = (
        "🟢" if alignment.macro_trend == StructureTrend.BULLISH
        else ("🔴" if alignment.macro_trend == StructureTrend.BEARISH else "⚪")
    )
    lines.append(f"**Overall Confluence Bias**: {trend_icon} **{alignment.alignment_bias.value}** "
                 f"(Aligned: {'YES ✅' if alignment.is_aligned else 'NO ⚠️'})\n")

    lines.append("| Timeframe | Trend | Latest Swing High | Latest Swing Low | Nearest Support | Nearest Resistance |")
    lines.append("|---|---|---|---|---|---|")

    digits = alignment.pair.digits if alignment.pair else 5
    p_fmt = f"{{:.{digits}f}}"

    ordered_tfs = sorted(alignment.structures.keys(), key=lambda tf: tf.seconds, reverse=True)

    for tf in ordered_tfs:
        if tf not in alignment.structures:
            continue
        s = alignment.structures[tf]
        t_icon = "🟢" if s.trend == StructureTrend.BULLISH else ("🔴" if s.trend == StructureTrend.BEARISH else "⚪")

        sh_str = (
            f"{p_fmt.format(s.latest_swing_high.price)} ({s.latest_swing_high.tag.value if s.latest_swing_high.tag else 'HIGH'})"
            if s.latest_swing_high else "-"
        )
        sl_str = (
            f"{p_fmt.format(s.latest_swing_low.price)} ({s.latest_swing_low.tag.value if s.latest_swing_low.tag else 'LOW'})"
            if s.latest_swing_low else "-"
        )
        sup_str = f"{p_fmt.format(s.nearest_support_price)} ({s.nearest_support_pips:.1f}p)" if s.nearest_support_price is not None and s.nearest_support_pips is not None else "-"
        res_str = f"{p_fmt.format(s.nearest_resistance_price)} ({s.nearest_resistance_pips:.1f}p)" if s.nearest_resistance_price is not None and s.nearest_resistance_pips is not None else "-"

        lines.append(
            f"| **{tf.value}** | {t_icon} {s.trend.value} | {sh_str} | {sl_str} | {sup_str} | {res_str} |"
        )

    lines.append("\n#### Detailed Structure Snapshots\n")
    for tf in ordered_tfs:
        if tf in alignment.structures:
            lines.append(format_market_structure_snapshot(alignment.structures[tf]))
            lines.append("")

    return "\n".join(lines)
