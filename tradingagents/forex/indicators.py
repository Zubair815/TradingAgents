"""Forex Technical Indicators Engine.

Pure, deterministic quantitative indicator engine tailored for Forex instruments:
- Pip-based Volatility:
  - Average True Range (ATR) in price, in pips, and as a percentage of price (ATR%).
  - Spread / ATR ratio: Quantifies the drag of transaction costs relative to candle range.
- Trend & Moving Averages:
  - Exponential Moving Averages: EMA(8), EMA(21), EMA(34), EMA(50), EMA(200), SMA(200).
  - EMA stack alignment: STRONG_BULLISH, BULLISH, STRONG_BEARISH, BEARISH, NEUTRAL.
  - Price distance from 200 EMA in pips (mean reversion / extension metric).
- Momentum & Oscillators:
  - Relative Strength Index (RSI-14) with Wilder's smoothing and regime classification.
  - Moving Average Convergence Divergence (MACD 12, 26, 9) line, signal, and histogram.
  - Average Directional Index (ADX-14) with +DI, -DI, and trend strength thresholding.
  - Stochastic Oscillator (%K, %D).
- Volatility Bands:
  - Bollinger Bands (20, 2.0), Bandwidth in pips, %B position.
- Multi-Timeframe Snapshot & Reporting:
  - ``ForexIndicatorSnapshot`` dataclass.
  - ``build_forex_indicator_snapshot()`` for single timeframes.
  - ``compute_multi_timeframe_indicators()`` for multi-horizon analysis.
  - Markdown formatters for agent prompts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional, Union

from tradingagents.dataflows.forex_quality import DataInsufficientError

if TYPE_CHECKING:
    from tradingagents.dataflows.forex_data import MultiTimeframeData

import numpy as np
import pandas as pd

from tradingagents.forex.domain import ForexPair, Timeframe, get_forex_pair
from tradingagents.forex.pips import pip_size_for, price_to_pips

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Indicator Regimes & Status Enums
# ---------------------------------------------------------------------------


class TrendRegime(str, Enum):
    """Moving average trend stack classification."""

    STRONG_BULLISH = "STRONG_BULLISH"
    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    BEARISH = "BEARISH"
    STRONG_BEARISH = "STRONG_BEARISH"


class RSIRegime(str, Enum):
    """RSI momentum classification."""

    OVERBOUGHT = "OVERBOUGHT"          # RSI >= 70
    BULLISH_MOMENTUM = "BULLISH"       # 50 <= RSI < 70
    NEUTRAL = "NEUTRAL"                # 45 <= RSI <= 55
    BEARISH_MOMENTUM = "BEARISH"       # 30 < RSI <= 50
    OVERSOLD = "OVERSOLD"              # RSI <= 30


class TrendStrength(str, Enum):
    """ADX trend strength classification."""

    EXTREME_TREND = "EXTREME_TREND"    # ADX >= 40
    STRONG_TREND = "STRONG_TREND"      # 25 <= ADX < 40
    WEAK_OR_RANGING = "RANGING"        # ADX < 25


# ---------------------------------------------------------------------------
# Core Math: Moving Averages & Wilder Smoothing
# ---------------------------------------------------------------------------


def calculate_sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=period, min_periods=1).mean()


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False, min_periods=1).mean()


def calculate_wilder_ma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing (equivalent to EMA with alpha = 1/period)."""
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=1).mean()


# ---------------------------------------------------------------------------
# Volatility Indicators: True Range & ATR
# ---------------------------------------------------------------------------


def calculate_true_range(df: pd.DataFrame) -> pd.Series:
    """True Range (TR) according to J. Welles Wilder."""
    high = df["High"]
    low = df["Low"]
    prev_close = df["Close"].shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    # First row has no previous close, TR is simply High - Low
    tr.iloc[0] = high.iloc[0] - low.iloc[0]
    return tr


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (ATR) in price units using Wilder's smoothing."""
    tr = calculate_true_range(df)
    return calculate_wilder_ma(tr, period=period)


def calculate_atr_pips(
    df: pd.DataFrame,
    pair: Union[ForexPair, str],
    period: int = 14,
) -> pd.Series:
    """Average True Range (ATR) expressed in pips."""
    atr_price = calculate_atr(df, period=period)
    pip_size = pip_size_for(pair)
    return atr_price / pip_size


def calculate_atr_percent(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR expressed as a percentage of Close price: (ATR / Close) * 100."""
    atr_price = calculate_atr(df, period=period)
    return (atr_price / df["Close"]) * 100.0


def calculate_spread_atr_ratio(
    spread_pips: float,
    atr_pips: float,
) -> float:
    """Calculate the ratio of broker spread to ATR.

    A ratio above 10-15% indicates significant friction where spreads eat
    a substantial portion of expected bar range (common during low-liquidity
    or Asian session on exotic pairs).
    """
    if atr_pips <= 0.0 or spread_pips < 0.0:
        return 0.0
    return spread_pips / atr_pips


def is_spread_favorable(
    spread_pips: float,
    atr_pips: float,
    max_acceptable_ratio: float = 0.12,
) -> bool:
    """Check if transaction costs (spread) are acceptable relative to ATR volatility."""
    ratio = calculate_spread_atr_ratio(spread_pips, atr_pips)
    return ratio <= max_acceptable_ratio


# ---------------------------------------------------------------------------
# Momentum Indicators: RSI, MACD, Stochastic
# ---------------------------------------------------------------------------


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (RSI) using Wilder's smoothing."""
    delta = series.diff().fillna(0.0)
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = calculate_wilder_ma(gain, period=period)
    avg_loss = calculate_wilder_ma(loss, period=period)

    rsi = pd.Series(50.0, index=series.index, dtype=float)
    pure_gain = (avg_gain > 0.0) & (avg_loss == 0.0)
    pure_loss = (avg_gain == 0.0) & (avg_loss > 0.0)
    normal = (avg_loss > 0.0) & (avg_gain > 0.0)

    rsi[pure_gain] = 100.0
    rsi[pure_loss] = 0.0
    if normal.any():
        rs = avg_gain[normal] / avg_loss[normal]
        rsi[normal] = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def calculate_macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Moving Average Convergence Divergence (MACD).

    Returns:
        tuple of (macd_line, signal_line, histogram)
    """
    fast_ema = calculate_ema(series, period=fast)
    slow_ema = calculate_ema(series, period=slow)
    macd_line = fast_ema - slow_ema
    signal_line = calculate_ema(macd_line, period=signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calculate_stochastic(
    df: pd.DataFrame,
    k_period: int = 14,
    d_period: int = 3,
    slowing: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """Stochastic Oscillator (%K, %D).

    Returns:
        tuple of (%K, %D)
    """
    lowest_low = df["Low"].rolling(window=k_period, min_periods=1).min()
    highest_high = df["High"].rolling(window=k_period, min_periods=1).max()

    denom = highest_high - lowest_low
    denom = denom.replace(0.0, np.nan)
    raw_k = ((df["Close"] - lowest_low) / denom) * 100.0
    raw_k = raw_k.fillna(50.0)

    # Slowing / smoothing for %K
    if slowing > 1:
        percent_k = raw_k.rolling(window=slowing, min_periods=1).mean()
    else:
        percent_k = raw_k

    percent_d = percent_k.rolling(window=d_period, min_periods=1).mean()
    return percent_k, percent_d


# ---------------------------------------------------------------------------
# Trend Strength: ADX
# ---------------------------------------------------------------------------


def calculate_adx(
    df: pd.DataFrame,
    period: int = 14,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Average Directional Index (ADX), +DI, and -DI using Wilder's smoothing.

    Returns:
        tuple of (ADX, +DI, -DI)
    """
    high = df["High"]
    low = df["Low"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    tr = calculate_true_range(df)
    smoothed_tr = calculate_wilder_ma(tr, period=period)
    smoothed_plus_dm = calculate_wilder_ma(plus_dm, period=period)
    smoothed_minus_dm = calculate_wilder_ma(minus_dm, period=period)

    tr_safe = smoothed_tr.replace(0.0, np.nan)
    plus_di = (smoothed_plus_dm / tr_safe) * 100.0
    minus_di = (smoothed_minus_dm / tr_safe) * 100.0
    plus_di = plus_di.fillna(0.0)
    minus_di = minus_di.fillna(0.0)

    di_sum = plus_di + minus_di
    di_diff = (plus_di - minus_di).abs()
    di_sum_safe = di_sum.replace(0.0, np.nan)
    dx = (di_diff / di_sum_safe) * 100.0
    dx = dx.fillna(0.0)

    adx = calculate_wilder_ma(dx, period=period)
    return adx, plus_di, minus_di


# ---------------------------------------------------------------------------
# Volatility Bands: Bollinger Bands
# ---------------------------------------------------------------------------


def calculate_bollinger_bands(
    series: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands.

    Returns:
        tuple of (upper_band, middle_band, lower_band)
    """
    middle = calculate_sma(series, period=period)
    rolling_std = series.rolling(window=period, min_periods=1).std().fillna(0.0)
    upper = middle + (rolling_std * std_dev)
    lower = middle - (rolling_std * std_dev)
    return upper, middle, lower


# ---------------------------------------------------------------------------
# Trend & Alignment Classification Helpers
# ---------------------------------------------------------------------------


def classify_ema_trend(
    close: float,
    ema8: float,
    ema21: float,
    ema50: float,
    ema200: Optional[float] = None,
) -> TrendRegime:
    """Classify the EMA alignment into TrendRegime."""
    if ema200 is not None:
        if close > ema8 > ema21 > ema50 > ema200:
            return TrendRegime.STRONG_BULLISH
        if close < ema8 < ema21 < ema50 < ema200:
            return TrendRegime.STRONG_BEARISH

    if close > ema8 > ema21:
        return TrendRegime.BULLISH
    if close < ema8 < ema21:
        return TrendRegime.BEARISH

    return TrendRegime.NEUTRAL


def classify_rsi_regime(rsi: float) -> RSIRegime:
    """Classify RSI value into RSIRegime."""
    if rsi >= 70.0:
        return RSIRegime.OVERBOUGHT
    if rsi <= 30.0:
        return RSIRegime.OVERSOLD
    if 55.0 <= rsi < 70.0:
        return RSIRegime.BULLISH_MOMENTUM
    if 30.0 < rsi <= 45.0:
        return RSIRegime.BEARISH_MOMENTUM
    return RSIRegime.NEUTRAL


def classify_adx_strength(adx: float) -> TrendStrength:
    """Classify ADX trend strength."""
    if adx >= 40.0:
        return TrendStrength.EXTREME_TREND
    if adx >= 25.0:
        return TrendStrength.STRONG_TREND
    return TrendStrength.WEAK_OR_RANGING


# ---------------------------------------------------------------------------
# Comprehensive Indicator Enrichment
# ---------------------------------------------------------------------------


def compute_forex_indicators(
    df: pd.DataFrame,
    pair: Optional[Union[ForexPair, str]] = None,
) -> pd.DataFrame:
    """Compute all standard Forex technical indicators on an OHLCV DataFrame.

    Returns a new DataFrame enriched with columns:
    - Moving averages: ema_8, ema_21, ema_34, ema_50, ema_200, sma_200
    - Volatility: tr, atr_14, atr_14_pips, atr_percent
    - Momentum: rsi_14, macd, macd_signal, macd_hist, stoch_k, stoch_d
    - Trend strength: adx_14, plus_di, minus_di
    - Bands: bb_upper, bb_middle, bb_lower, bb_width, bb_width_pips, bb_pct_b
    """
    from tradingagents.dataflows.forex_data import validate_forex_candles

    valid_df = validate_forex_candles(df)

    out = valid_df.copy()

    close = out["Close"]

    # Moving averages
    out["ema_8"] = calculate_ema(close, 8)
    out["ema_21"] = calculate_ema(close, 21)
    out["ema_34"] = calculate_ema(close, 34)
    out["ema_50"] = calculate_ema(close, 50)
    out["ema_200"] = calculate_ema(close, 200)
    out["sma_200"] = calculate_sma(close, 200)

    # Volatility
    out["tr"] = calculate_true_range(out)
    out["atr_14"] = calculate_atr(out, 14)
    out["atr_percent"] = calculate_atr_percent(out, 14)

    if pair is not None:
        p = get_forex_pair(pair) if isinstance(pair, str) else pair
        pip_size = p.pip_size if p else pip_size_for(pair)
        out["atr_14_pips"] = out["atr_14"] / pip_size
    else:
        out["atr_14_pips"] = out["atr_14"] / 0.0001  # Default 5-digit pip

    # Momentum
    out["rsi_14"] = calculate_rsi(close, 14)

    # MACD
    m_line, m_sig, m_hist = calculate_macd(close, 12, 26, 9)
    out["macd"] = m_line
    out["macd_signal"] = m_sig
    out["macd_hist"] = m_hist

    # Stochastic
    stoch_k, stoch_d = calculate_stochastic(out, 14, 3, 3)
    out["stoch_k"] = stoch_k
    out["stoch_d"] = stoch_d

    # ADX
    adx_val, p_di, m_di = calculate_adx(out, 14)
    out["adx_14"] = adx_val
    out["plus_di"] = p_di
    out["minus_di"] = m_di

    # Bollinger Bands
    bb_u, bb_m, bb_l = calculate_bollinger_bands(close, 20, 2.0)
    out["bb_upper"] = bb_u
    out["bb_middle"] = bb_m
    out["bb_lower"] = bb_l
    out["bb_width"] = (bb_u - bb_l) / bb_m.replace(0.0, np.nan) * 100.0

    denom = (bb_u - bb_l).replace(0.0, np.nan)
    out["bb_pct_b"] = (close - bb_l) / denom

    if pair is not None:
        p = get_forex_pair(pair) if isinstance(pair, str) else pair
        pip_size = p.pip_size if p else pip_size_for(pair)
        out["bb_width_pips"] = (bb_u - bb_l) / pip_size
    else:
        out["bb_width_pips"] = (bb_u - bb_l) / 0.0001

    return out


# ---------------------------------------------------------------------------
# Indicator Snapshot Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForexIndicatorSnapshot:
    """Point-in-time quantitative indicator summary for an instrument."""

    timestamp: datetime
    symbol: str
    pair: Optional[ForexPair]
    timeframe: Optional[Timeframe]
    close: float
    spread_pips: float
    atr_14_pips: float
    atr_percent: float
    spread_atr_ratio: float
    is_spread_acceptable: bool
    rsi_14: float
    rsi_regime: RSIRegime
    ema_trend: TrendRegime
    distance_to_ema200_pips: Optional[float]
    bb_upper: float
    bb_middle: float
    bb_lower: float
    bb_width_pips: float
    bb_pct_b: float
    macd_hist: float
    adx_14: float
    adx_strength: TrendStrength


def build_forex_indicator_snapshot(
    df: pd.DataFrame,
    pair: Optional[Union[ForexPair, str]] = None,
    timeframe: Optional[Union[Timeframe, str]] = None,
    spread_pips: Optional[float] = None,
) -> ForexIndicatorSnapshot:
    """Build a point-in-time quantitative indicator snapshot from the latest bar."""
    if df is None or df.empty:
        raise ValueError("Cannot build indicator snapshot from empty DataFrame.")

    resolved_pair: Optional[ForexPair] = None
    if isinstance(pair, ForexPair):
        resolved_pair = pair
    elif isinstance(pair, str):
        resolved_pair = get_forex_pair(pair)

    resolved_tf: Optional[Timeframe] = None
    if isinstance(timeframe, Timeframe):
        resolved_tf = timeframe
    elif isinstance(timeframe, str):
        from tradingagents.dataflows.forex_data import resolve_timeframe
        resolved_tf = resolve_timeframe(timeframe)

    enriched = compute_forex_indicators(df, resolved_pair)
    latest = enriched.iloc[-1]

    close_val = float(latest["Close"])
    pip_size = resolved_pair.pip_size if resolved_pair else pip_size_for(resolved_pair or "EURUSD")

    # Spread handling (default fallback: 1.0 pip for standard, 1.5 for exotics)
    sp_pips = float(spread_pips) if spread_pips is not None else 1.0
    atr_p = float(latest["atr_14_pips"])
    ratio = calculate_spread_atr_ratio(sp_pips, atr_p)
    acceptable = is_spread_favorable(sp_pips, atr_p)

    rsi_v = float(latest["rsi_14"])
    rsi_reg = classify_rsi_regime(rsi_v)

    ema8 = float(latest["ema_8"])
    ema21 = float(latest["ema_21"])
    ema50 = float(latest["ema_50"])
    ema200 = float(latest["ema_200"]) if not pd.isna(latest.get("ema_200")) else None

    trend_reg = classify_ema_trend(close_val, ema8, ema21, ema50, ema200)

    dist_200_pips = None
    if ema200 is not None:
        dist_200_pips = (close_val - ema200) / pip_size

    adx_v = float(latest["adx_14"])
    adx_str = classify_adx_strength(adx_v)

    ts = latest["Date"]
    if isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    sym = resolved_pair.symbol if resolved_pair else "UNKNOWN"

    return ForexIndicatorSnapshot(
        timestamp=ts,
        symbol=sym,
        pair=resolved_pair,
        timeframe=resolved_tf,
        close=close_val,
        spread_pips=sp_pips,
        atr_14_pips=atr_p,
        atr_percent=float(latest["atr_percent"]),
        spread_atr_ratio=ratio,
        is_spread_acceptable=acceptable,
        rsi_14=rsi_v,
        rsi_regime=rsi_reg,
        ema_trend=trend_reg,
        distance_to_ema200_pips=dist_200_pips,
        bb_upper=float(latest["bb_upper"]),
        bb_middle=float(latest["bb_middle"]),
        bb_lower=float(latest["bb_lower"]),
        bb_width_pips=float(latest["bb_width_pips"]),
        bb_pct_b=float(latest["bb_pct_b"]),
        macd_hist=float(latest["macd_hist"]),
        adx_14=adx_v,
        adx_strength=adx_str,
    )


# ---------------------------------------------------------------------------
# Multi-Timeframe Indicator Analysis
# ---------------------------------------------------------------------------


def compute_multi_timeframe_indicators(
    bundle: MultiTimeframeData,
    spread_pips: Optional[float] = None,
) -> dict[Timeframe, ForexIndicatorSnapshot]:
    """Compute indicator snapshots across all timeframes in a MultiTimeframeData bundle."""
    snapshots: dict[Timeframe, ForexIndicatorSnapshot] = {}
    for tf, df in bundle.candles.items():
        if df.empty or len(df) < 5:
            continue
        try:
            snap = build_forex_indicator_snapshot(
                df=df,
                pair=bundle.pair,
                timeframe=tf,
                spread_pips=spread_pips,
            )
            snapshots[tf] = snap
        except DataInsufficientError:
            raise
        except Exception as exc:
            logger.warning(f"Could not compute indicators for timeframe {tf.value}: {exc}")

    return snapshots


# ---------------------------------------------------------------------------
# Prompt & Markdown Formatting Utilities
# ---------------------------------------------------------------------------


def format_indicator_snapshot(snapshot: ForexIndicatorSnapshot) -> str:
    """Format a single indicator snapshot into clean text for agent prompts."""
    digits = snapshot.pair.digits if snapshot.pair else 5
    p_fmt = f"{{:.{digits}f}}"

    lines = [
        f"**{snapshot.symbol} [{snapshot.timeframe.value if snapshot.timeframe else 'Current'}] Indicator Snapshot**",
        f"- Close: {p_fmt.format(snapshot.close)} | ATR(14): {snapshot.atr_14_pips:.1f} pips ({snapshot.atr_percent:.2f}%)",
        f"- Spread: {snapshot.spread_pips:.1f} pips | Spread/ATR: {snapshot.spread_atr_ratio:.1%} "
        f"[{'FAVORABLE COST ✅' if snapshot.is_spread_acceptable else 'HIGH SPREAD FRICTION ⚠️'}]",
        f"- Trend (EMA Stack): {snapshot.ema_trend.value}",
    ]

    if snapshot.distance_to_ema200_pips is not None:
        lines.append(f"- Distance from 200 EMA: {snapshot.distance_to_ema200_pips:+.1f} pips")

    lines.extend([
        f"- RSI(14): {snapshot.rsi_14:.1f} [{snapshot.rsi_regime.value}]",
        f"- ADX(14): {snapshot.adx_14:.1f} [{snapshot.adx_strength.value}]",
        f"- Bollinger Bands: U={p_fmt.format(snapshot.bb_upper)} M={p_fmt.format(snapshot.bb_middle)} L={p_fmt.format(snapshot.bb_lower)}",
        f"- BB Width: {snapshot.bb_width_pips:.1f} pips (%B: {snapshot.bb_pct_b:.2f})",
        f"- MACD Histogram: {snapshot.macd_hist:+.6f}",
    ])

    return "\n".join(lines)


def format_multi_timeframe_indicators_summary(
    snapshots: dict[Timeframe, ForexIndicatorSnapshot],
) -> str:
    """Render a comprehensive multi-timeframe indicator alignment summary."""
    if not snapshots:
        return "No indicator data available."

    lines = ["### Multi-Timeframe Technical Indicator Matrix\n"]

    # Table summary
    lines.append("| Timeframe | Trend | RSI(14) | ATR(14) pips | Spread/ATR | ADX(14) | BB %B |")
    lines.append("|---|---|---|---|---|---|---|")

    # Order timeframes from highest to lowest
    ordered_tfs = [Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M30, Timeframe.M15, Timeframe.M5]

    for tf in ordered_tfs:
        if tf not in snapshots:
            continue
        s = snapshots[tf]
        trend_icon = "🟢" if "BULLISH" in s.ema_trend.value else ("🔴" if "BEARISH" in s.ema_trend.value else "⚪")
        lines.append(
            f"| **{tf.value}** | {trend_icon} {s.ema_trend.value} | {s.rsi_14:.1f} ({s.rsi_regime.value}) | "
            f"{s.atr_14_pips:.1f} | {s.spread_atr_ratio:.1%} | {s.adx_14:.1f} | {s.bb_pct_b:.2f} |"
        )

    lines.append("\n#### Detailed Snapshot Per Horizon\n")
    for tf in ordered_tfs:
        if tf in snapshots:
            lines.append(format_indicator_snapshot(snapshots[tf]))
            lines.append("")

    return "\n".join(lines)
