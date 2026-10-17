"""Dependency-inversion ports for pair-observer boundary adapters."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from tradingagents.pair_observer.models import (
    Candle,
    ObserverAlert,
    ObserverSnapshotEnvelope,
    QuoteSnapshot,
    Timeframe,
)


class PairObserverMarketSource(Protocol):
    """Read-only market input. Implementations must never expose trade methods."""

    def get_completed_candles(
        self,
        pair: str,
        timeframe: Timeframe,
        *,
        count: int,
        as_of_utc: datetime,
    ) -> Sequence[Candle]: ...

    def get_quote(
        self,
        pair: str,
        *,
        as_of_utc: datetime,
        max_age_seconds: int,
    ) -> QuoteSnapshot: ...

    def get_tick_size(self, pair: str) -> float: ...

    def has_open_exposure(self, pair: str, *, as_of_utc: datetime) -> bool: ...


class ObserverClock(Protocol):
    def now_utc(self) -> datetime: ...


class ObserverRepository(Protocol):
    def save_alert_with_snapshot(
        self,
        alert: ObserverAlert,
        snapshot: ObserverSnapshotEnvelope,
    ) -> bool:
        """Persist atomically; return false when fingerprint already exists."""
        ...

    def get_snapshot(self, snapshot_id: str) -> ObserverSnapshotEnvelope: ...


class AlertSink(Protocol):
    def deliver(self, alert: ObserverAlert) -> None: ...


class ObserverSnapshotProvider(Protocol):
    """Stable future bridge; analysis consumes snapshots through this port."""

    def get_snapshot(self, snapshot_id: str) -> ObserverSnapshotEnvelope: ...
