"""LangChain agent tools for Forex market data and multi-timeframe analysis (Phase 3).

Exposes:
- ``get_forex_candles``: Fetches precision-formatted OHLCV candles for any timeframe (M1..W1, H4).
- ``get_multi_timeframe_data``: Fetches synchronized multi-horizon overview (D1, H4, H1, M15).
"""

from __future__ import annotations

from typing import Annotated, Any

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
from tradingagents.forex.domain import Timeframe
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


def _resolve_tool_timeframes(
    execution_tf: str = "",
    context_tfs: Any = None,
) -> tuple[Timeframe, ...]:
    if not execution_tf and not context_tfs:
        return (Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15)
    tfs: list[Timeframe] = []
    if context_tfs:
        for c in context_tfs:
            try:
                tf_member = Timeframe.from_string(c) if isinstance(c, str) else c
                if tf_member not in tfs:
                    tfs.append(tf_member)
            except Exception:
                pass
    if execution_tf:
        try:
            exec_member = Timeframe.from_string(execution_tf) if isinstance(execution_tf, str) else execution_tf
            if exec_member not in tfs:
                tfs.append(exec_member)
        except Exception:
            pass
    return tuple(tfs) if tfs else (Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15)



@tool
def get_forex_candles_tool(
    symbol: Annotated[str, "Forex currency pair symbol, e.g. EURUSD, USDJPY, GBPUSD, AUDUSD"],
    timeframe: Annotated[str, "Candle timeframe: M1, M5, M15, M30, H1, H4, D1, W1"] = "",
    count: Annotated[int, "Number of recent candles to fetch (e.g. 30, 50, 100)"] = 50,
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
    forex_as_of_utc: Annotated[str, InjectedState("forex_as_of_utc")] = "",
    forex_execution_timeframe: Annotated[str, InjectedState("forex_execution_timeframe")] = "",
) -> str:
    """Retrieve OHLCV candle price data for a Forex pair and timeframe.

    Returns CSV data formatted with instrument-accurate decimal precision.
    Includes High, Low, Open, Close, and Volume.
    """
    eff_tf = timeframe or forex_execution_timeframe or "H1"
    try:
        df = fetch_forex_candles(
            symbol=symbol,
            timeframe=eff_tf,
            count=count,
            as_of=resolve_forex_cutoff(trade_date=trade_date, run_as_of=forex_as_of_utc),
        )
        return format_forex_candles_csv(df, pair=symbol, max_rows=count)
    except Exception as exc:
        raise DataInsufficientError(f" Could not retrieve Forex candles for {symbol} ({eff_tf}): {exc}") from exc


@tool
def get_multi_timeframe_data_tool(
    symbol: Annotated[str, "Forex currency pair symbol, e.g. EURUSD, USDJPY, GBPUSD"],
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
    forex_as_of_utc: Annotated[str, InjectedState("forex_as_of_utc")] = "",
    forex_execution_timeframe: Annotated[str, InjectedState("forex_execution_timeframe")] = "",
    forex_context_timeframes: Annotated[Any, InjectedState("forex_context_timeframes")] = None,
) -> str:
    """Retrieve synchronized multi-timeframe market data for a Forex pair across configured execution and context timeframes."""
    try:
        tfs = _resolve_tool_timeframes(forex_execution_timeframe, forex_context_timeframes)
        bundle = fetch_multi_timeframe_data(
            symbol=symbol,
            timeframes=tfs,
            as_of=resolve_forex_cutoff(trade_date=trade_date, run_as_of=forex_as_of_utc),
            count=20,
        )
        return format_multi_timeframe_summary(bundle, max_rows=5)
    except Exception as exc:
        raise DataInsufficientError(f" Could not retrieve multi-timeframe data for {symbol}: {exc}") from exc


@tool
def get_forex_indicators_tool(
    symbol: Annotated[str, "Forex currency pair symbol, e.g. EURUSD, USDJPY, GBPUSD"],
    timeframe: Annotated[str, "Candle timeframe: M1, M5, M15, M30, H1, H4, D1, W1"] = "",
    spread_pips: Annotated[float | None, "Current broker spread in pips if known (e.g. 1.2)"] = None,
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
    forex_as_of_utc: Annotated[str, InjectedState("forex_as_of_utc")] = "",
    forex_execution_timeframe: Annotated[str, InjectedState("forex_execution_timeframe")] = "",
) -> str:
    """Compute precision Forex technical indicators (EMA stack, RSI, ATR, Spread/ATR ratio, Bollinger Bands, ADX, MACD).

    Evaluates trend alignment, cost drag, volatility, and momentum for decision-making.
    """
    eff_tf = timeframe or forex_execution_timeframe or "H1"
    try:
        df = fetch_forex_candles(
            symbol=symbol,
            timeframe=eff_tf,
            count=100,
            as_of=resolve_forex_cutoff(trade_date=trade_date, run_as_of=forex_as_of_utc),
        )
        snapshot = build_forex_indicator_snapshot(
            df=df,
            pair=symbol,
            timeframe=eff_tf,
            spread_pips=spread_pips,
        )
        return format_data_provenance(df) + "\n" + format_indicator_snapshot(snapshot)
    except Exception as exc:
        raise DataInsufficientError(f" Could not compute Forex indicators for {symbol} ({eff_tf}): {exc}") from exc


@tool
def get_multi_timeframe_indicators_tool(
    symbol: Annotated[str, "Forex currency pair symbol, e.g. EURUSD, USDJPY, GBPUSD"],
    spread_pips: Annotated[float | None, "Current broker spread in pips if known (e.g. 1.2)"] = None,
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
    forex_as_of_utc: Annotated[str, InjectedState("forex_as_of_utc")] = "",
    forex_execution_timeframe: Annotated[str, InjectedState("forex_execution_timeframe")] = "",
    forex_context_timeframes: Annotated[Any, InjectedState("forex_context_timeframes")] = None,
) -> str:
    """Compute synchronized multi-timeframe indicator matrix across configured execution and context timeframes."""
    try:
        tfs = _resolve_tool_timeframes(forex_execution_timeframe, forex_context_timeframes)
        bundle = fetch_multi_timeframe_data(
            symbol=symbol,
            timeframes=tfs,
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
    timeframe: Annotated[str, "Candle timeframe: M1, M5, M15, M30, H1, H4, D1, W1"] = "",
    lookback: Annotated[int, "Fractal swing lookback candle window"] = 3,
    lookforward: Annotated[int, "Fractal swing lookforward candle window"] = 3,
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
    forex_as_of_utc: Annotated[str, InjectedState("forex_as_of_utc")] = "",
    forex_execution_timeframe: Annotated[str, InjectedState("forex_execution_timeframe")] = "",
) -> str:
    """Analyze Forex market structure (HH/HL/LH/LL swings, BOS, CHoCH, FVGs, Order Blocks, and Key S/R).

    Identifies directional structure, swing failure, institutional order blocks, and key breakout levels.
    """
    eff_tf = timeframe or forex_execution_timeframe or "H1"
    try:
        df = fetch_forex_candles(
            symbol=symbol,
            timeframe=eff_tf,
            count=100,
            as_of=resolve_forex_cutoff(trade_date=trade_date, run_as_of=forex_as_of_utc),
        )
        snapshot = build_market_structure_snapshot(
            df=df,
            pair=symbol,
            timeframe=eff_tf,
            lookback=lookback,
            lookforward=lookforward,
        )
        return format_data_provenance(df) + "\n" + format_market_structure_snapshot(snapshot)
    except Exception as exc:
        raise DataInsufficientError(f" Could not analyze market structure for {symbol} ({eff_tf}): {exc}") from exc


@tool
def get_multi_timeframe_market_structure_tool(
    symbol: Annotated[str, "Forex currency pair symbol, e.g. EURUSD, USDJPY, GBPUSD"],
    lookback: Annotated[int, "Fractal swing lookback candle window"] = 3,
    lookforward: Annotated[int, "Fractal swing lookforward candle window"] = 3,
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
    forex_as_of_utc: Annotated[str, InjectedState("forex_as_of_utc")] = "",
    forex_execution_timeframe: Annotated[str, InjectedState("forex_execution_timeframe")] = "",
    forex_context_timeframes: Annotated[Any, InjectedState("forex_context_timeframes")] = None,
) -> str:
    """Analyze multi-timeframe market structure alignment across configured execution and context timeframes."""
    try:
        tfs = _resolve_tool_timeframes(forex_execution_timeframe, forex_context_timeframes)
        bundle = fetch_multi_timeframe_data(
            symbol=symbol,
            timeframes=tfs,
            as_of=resolve_forex_cutoff(trade_date=trade_date, run_as_of=forex_as_of_utc),
            count=100,
        )
        alignment = analyze_multi_timeframe_structure(
            bundle=bundle,
            lookback=lookback,
            lookforward=lookforward,
            execution_timeframe=forex_execution_timeframe,
            context_timeframes=forex_context_timeframes,
        )
        return "\n".join(format_data_provenance(df) for df in bundle.candles.values()) + "\n" + format_multi_timeframe_structure_summary(alignment)
    except Exception as exc:
        raise DataInsufficientError(f" Could not analyze multi-timeframe market structure for {symbol}: {exc}") from exc


