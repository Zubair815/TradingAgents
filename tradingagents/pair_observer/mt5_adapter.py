"""Read-only adapter over the existing serialized MT5Observer."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from tradingagents.mt5.observer import MT5Observer
from tradingagents.pair_observer.models import Candle, QuoteSnapshot, Timeframe


class MT5PairObserverSource:
    def __init__(self, observer: MT5Observer):
        self._observer = observer

    def get_completed_candles(
        self,
        pair: str,
        timeframe: Timeframe,
        *,
        count: int,
        as_of_utc: datetime,
    ) -> Sequence[Candle]:
        bars = self._observer.get_candles(pair, timeframe.value, count=count, as_of=as_of_utc)
        return tuple(
            Candle(
                pair=pair,
                timeframe=timeframe,
                open_time_utc=bar.timestamp,
                close_time_utc=bar.close_time,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                tick_volume=int(bar.volume),
                completed=bool(bar.is_closed),
                source="MT5",
            )
            for bar in bars
        )

    def get_quote(
        self,
        pair: str,
        *,
        as_of_utc: datetime,
        max_age_seconds: int,
    ) -> QuoteSnapshot:
        tick = self._observer.get_current_tick(pair, max_age_seconds=max_age_seconds)
        return QuoteSnapshot(
            pair=pair,
            broker_symbol=tick.broker_symbol or pair,
            bid=tick.bid,
            ask=tick.ask,
            spread_pips=tick.spread_pips,
            observed_at_utc=tick.time,
        )

    def get_tick_size(self, pair: str) -> float:
        return self._observer.get_symbol_info(pair).point

    def has_open_exposure(self, pair: str, *, as_of_utc: datetime) -> bool:
        positions = self._observer.get_open_positions(symbol=pair)
        orders = self._observer.get_pending_orders(symbol=pair)
        return bool(positions or orders)
