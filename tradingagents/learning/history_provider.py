"""Historical Price Data Provider for Post-Trade Excursion Analysis (Phase 11).

Provides an abstract interface and concrete implementations for acquiring
intraday historical bars/ticks during a trade's exact holding interval.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TradeHistoryResult:
    """Historical bar/tick data and provenance metadata for MFE/MAE calculations."""

    candles: Sequence[Any] | None = None
    source: str = "MT5"
    resolution: str = "M1"
    precision: str = "BAR_APPROXIMATION"
    retrieval_time_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    is_available: bool = True
    unavailable_reason: str | None = None


class TradeHistoryProvider(ABC):
    """Abstract interface for acquiring historical bars/ticks during trade holding interval."""

    @abstractmethod
    def get_history(
        self,
        pair: str,
        start_time: datetime,
        end_time: datetime,
        timeframe: str = "M1",
    ) -> TradeHistoryResult:
        """Fetch historical price data for the specified pair and time window."""
        ...


class MT5TradeHistoryProvider(TradeHistoryProvider):
    """Acquires historical M1 bars directly from MetaTrader 5."""

    def __init__(self, observer: Any = None) -> None:
        self.observer = observer

    def get_history(
        self,
        pair: str,
        start_time: datetime,
        end_time: datetime,
        timeframe: str = "M1",
    ) -> TradeHistoryResult:
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            obs = self.observer
            if obs is None:
                from tradingagents.mt5.observer import MT5Observer

                obs = MT5Observer()

            if not getattr(obs, "is_connected", False):
                conn_mgr = getattr(obs, "connection", None)
                if conn_mgr and not conn_mgr.is_connected:
                    try:
                        conn_mgr.connect()
                    except Exception as conn_err:
                        logger.debug("MT5 connection attempt failed: %s", conn_err)

            if not getattr(obs, "is_connected", False):
                return TradeHistoryResult(
                    candles=None,
                    source="MT5",
                    resolution=timeframe,
                    precision="UNAVAILABLE",
                    retrieval_time_utc=now_iso,
                    is_available=False,
                    unavailable_reason="MFE_MAE_UNAVAILABLE: MT5 terminal is not connected.",
                )

            bars = obs.get_candles_range(pair, timeframe, start_time, end_time)
            if not bars:
                return TradeHistoryResult(
                    candles=None,
                    source="MT5",
                    resolution=timeframe,
                    precision="UNAVAILABLE",
                    retrieval_time_utc=now_iso,
                    is_available=False,
                    unavailable_reason=f"MFE_MAE_UNAVAILABLE: No MT5 {timeframe} bars returned for holding period.",
                )

            return TradeHistoryResult(
                candles=bars,
                source="MT5",
                resolution=timeframe,
                precision="BAR_APPROXIMATION",
                retrieval_time_utc=now_iso,
                is_available=True,
                unavailable_reason=None,
            )
        except Exception as exc:
            logger.debug("Failed fetching MT5 trade history: %s", exc)
            return TradeHistoryResult(
                candles=None,
                source="MT5",
                resolution=timeframe,
                precision="UNAVAILABLE",
                retrieval_time_utc=now_iso,
                is_available=False,
                unavailable_reason=f"MFE_MAE_UNAVAILABLE: {exc}",
            )


class InMemoryTradeHistoryProvider(TradeHistoryProvider):
    """Deterministic in-memory history provider for unit testing and offline backtests."""

    def __init__(
        self,
        candle_map: dict[str, Sequence[Any]]
        | Callable[[str, datetime, datetime, str], Sequence[Any]]
        | None = None,
        source: str = "MT5",
        precision: str = "BAR_APPROXIMATION",
    ) -> None:
        self.candle_map = candle_map or {}
        self.source = source
        self.precision = precision

    def get_history(
        self,
        pair: str,
        start_time: datetime,
        end_time: datetime,
        timeframe: str = "M1",
    ) -> TradeHistoryResult:
        now_iso = datetime.now(timezone.utc).isoformat()
        if callable(self.candle_map):
            bars = self.candle_map(pair, start_time, end_time, timeframe)
        else:
            bars = self.candle_map.get(pair) or self.candle_map.get(f"{pair}_{timeframe}")

        if not bars:
            return TradeHistoryResult(
                candles=None,
                source=self.source,
                resolution=timeframe,
                precision="UNAVAILABLE",
                retrieval_time_utc=now_iso,
                is_available=False,
                unavailable_reason=f"MFE_MAE_UNAVAILABLE: No historical {timeframe} bars found for {pair}.",
            )

        return TradeHistoryResult(
            candles=bars,
            source=self.source,
            resolution=timeframe,
            precision=self.precision,
            retrieval_time_utc=now_iso,
            is_available=True,
            unavailable_reason=None,
        )
