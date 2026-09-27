"""LangChain agent tools for Forex market data and multi-timeframe analysis (Phase 3).

Exposes:
- ``get_forex_candles``: Fetches precision-formatted OHLCV candles for any timeframe (M1..W1, H4).
- ``get_multi_timeframe_data``: Fetches synchronized multi-horizon overview (D1, H4, H1, M15).
"""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.dataflows.forex_context import resolve_forex_cutoff
from tradingagents.dataflows.forex_data import (
    fetch_forex_candles,
    fetch_multi_timeframe_data,
    format_data_provenance,
    format_forex_candles_csv,
    format_multi_timeframe_summary,
)
from tradingagents.dataflows.forex_quality import DataInsufficientError
from tradingagents.forex.indicators import (
    build_forex_indicator_snapshot,
    compute_multi_timeframe_indicators,
    format_indicator_snapshot,
    format_multi_timeframe_indicators_summary,
)
from tradingagents.forex.market_structure import (
    analyze_multi_timeframe_structure,
    build_market_structure_snapshot,
    format_market_structure_snapshot,
    format_multi_timeframe_structure_summary,
)


@tool
def get_forex_candles_tool(
    symbol: Annotated[str, "Forex currency pair symbol, e.g. EURUSD, USDJPY, GBPUSD, AUDUSD"],
    timeframe: Annotated[str, "Candle timeframe: M1, M5, M15, M30, H1, H4, D1, W1"] = "H1",
    count: Annotated[int, "Number of recent candles to fetch (e.g. 30, 50, 100)"] = 50,
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
    forex_as_of_utc: Annotated[str, InjectedState("forex_as_of_utc")] = "",
) -> str:
    """Retrieve OHLCV candle price data for a Forex pair and timeframe.

    Returns CSV data formatted with instrument-accurate decimal precision.
    Includes High, Low, Open, Close, and Volume.
    """
    try:
        df = fetch_forex_candles(
            symbol=symbol,
            timeframe=timeframe,
            count=count,
            as_of=resolve_forex_cutoff(trade_date=trade_date, run_as_of=forex_as_of_utc),
        )
        return format_forex_candles_csv(df, pair=symbol, max_rows=count)
    except Exception as exc:
        raise DataInsufficientError(f" Could not retrieve Forex candles for {symbol} ({timeframe}): {exc}") from exc


@tool
def get_multi_timeframe_data_tool(
    symbol: Annotated[str, "Forex currency pair symbol, e.g. EURUSD, USDJPY, GBPUSD"],
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
    forex_as_of_utc: Annotated[str, InjectedState("forex_as_of_utc")] = "",
) -> str:
    """Retrieve synchronized multi-timeframe market data (D1, H4, H1, M15) for a Forex pair.

    Provides the macro trend (D1), intermediate swing context (H4/H1), and tactical execution
    timeframe (M15) with candle directions and pip ranges.
    """
    try:
        bundle = fetch_multi_timeframe_data(
            symbol=symbol,
            as_of=resolve_forex_cutoff(trade_date=trade_date, run_as_of=forex_as_of_utc),
            count=20,
        )
        return format_multi_timeframe_summary(bundle, max_rows=5)
    except Exception as exc:
        raise DataInsufficientError(f" Could not retrieve multi-timeframe data for {symbol}: {exc}") from exc


@tool
def get_forex_indicators_tool(
    symbol: Annotated[str, "Forex currency pair symbol, e.g. EURUSD, USDJPY, GBPUSD"],
    timeframe: Annotated[str, "Candle timeframe: M1, M5, M15, M30, H1, H4, D1, W1"] = "H1",
    spread_pips: Annotated[float | None, "Current broker spread in pips if known (e.g. 1.2)"] = None,
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
    forex_as_of_utc: Annotated[str, InjectedState("forex_as_of_utc")] = "",
) -> str:
    """Compute precision Forex technical indicators (EMA stack, RSI, ATR, Spread/ATR ratio, Bollinger Bands, ADX, MACD).

    Evaluates trend alignment, cost drag, volatility, and momentum for decision-making.
    """
    try:
        df = fetch_forex_candles(
            symbol=symbol,
            timeframe=timeframe,
            count=100,
            as_of=resolve_forex_cutoff(trade_date=trade_date, run_as_of=forex_as_of_utc),
        )
        snapshot = build_forex_indicator_snapshot(
            df=df,
            pair=symbol,
            timeframe=timeframe,
            spread_pips=spread_pips,
        )
        return format_data_provenance(df) + "\n" + format_indicator_snapshot(snapshot)
    except Exception as exc:
        raise DataInsufficientError(f" Could not compute Forex indicators for {symbol} ({timeframe}): {exc}") from exc


@tool
def get_multi_timeframe_indicators_tool(
    symbol: Annotated[str, "Forex currency pair symbol, e.g. EURUSD, USDJPY, GBPUSD"],
    spread_pips: Annotated[float | None, "Current broker spread in pips if known (e.g. 1.2)"] = None,
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
    forex_as_of_utc: Annotated[str, InjectedState("forex_as_of_utc")] = "",
) -> str:
    """Compute synchronized multi-timeframe indicator matrix (D1, H4, H1, M15) for a Forex pair.

    Provides high-timeframe trend alignment, volatility context, and tactical oscillator readings.
    """
    try:
        bundle = fetch_multi_timeframe_data(
            symbol=symbol,
            as_of=resolve_forex_cutoff(trade_date=trade_date, run_as_of=forex_as_of_utc),
            count=100,
        )
        snapshots = compute_multi_timeframe_indicators(
            bundle=bundle,
            spread_pips=spread_pips,
        )
        return "\n".join(format_data_provenance(df) for df in bundle.candles.values()) + "\n" + format_multi_timeframe_indicators_summary(snapshots)
    except Exception as exc:
        raise DataInsufficientError(f" Could not compute multi-timeframe indicators for {symbol}: {exc}") from exc


@tool
def get_market_structure_tool(
    symbol: Annotated[str, "Forex currency pair symbol, e.g. EURUSD, USDJPY, GBPUSD"],
    timeframe: Annotated[str, "Candle timeframe: M1, M5, M15, M30, H1, H4, D1, W1"] = "H1",
    lookback: Annotated[int, "Fractal swing lookback candle window"] = 3,
    lookforward: Annotated[int, "Fractal swing lookforward candle window"] = 3,
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
    forex_as_of_utc: Annotated[str, InjectedState("forex_as_of_utc")] = "",
) -> str:
    """Analyze Forex market structure (HH/HL/LH/LL swings, BOS, CHoCH, FVGs, Order Blocks, and Key S/R).

    Identifies directional structure, swing failure, institutional order blocks, and key breakout levels.
    """
    try:
        df = fetch_forex_candles(
            symbol=symbol,
            timeframe=timeframe,
            count=100,
            as_of=resolve_forex_cutoff(trade_date=trade_date, run_as_of=forex_as_of_utc),
        )
        snapshot = build_market_structure_snapshot(
            df=df,
            pair=symbol,
            timeframe=timeframe,
            lookback=lookback,
            lookforward=lookforward,
        )
        return format_data_provenance(df) + "\n" + format_market_structure_snapshot(snapshot)
    except Exception as exc:
        raise DataInsufficientError(f" Could not analyze market structure for {symbol} ({timeframe}): {exc}") from exc


@tool
def get_multi_timeframe_market_structure_tool(
    symbol: Annotated[str, "Forex currency pair symbol, e.g. EURUSD, USDJPY, GBPUSD"],
    lookback: Annotated[int, "Fractal swing lookback candle window"] = 3,
    lookforward: Annotated[int, "Fractal swing lookforward candle window"] = 3,
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
    forex_as_of_utc: Annotated[str, InjectedState("forex_as_of_utc")] = "",
) -> str:
    """Analyze multi-timeframe market structure alignment across H4, H1, and M15 for a Forex pair.

    Checks macro trend continuation vs tactical counter-trend pullbacks and overall confluence bias.
    """
    try:
        bundle = fetch_multi_timeframe_data(
            symbol=symbol,
            as_of=resolve_forex_cutoff(trade_date=trade_date, run_as_of=forex_as_of_utc),
            count=100,
        )
        alignment = analyze_multi_timeframe_structure(
            bundle=bundle,
            lookback=lookback,
            lookforward=lookforward,
        )
        return "\n".join(format_data_provenance(df) for df in bundle.candles.values()) + "\n" + format_multi_timeframe_structure_summary(alignment)
    except Exception as exc:
        raise DataInsufficientError(f" Could not analyze multi-timeframe market structure for {symbol}: {exc}") from exc


