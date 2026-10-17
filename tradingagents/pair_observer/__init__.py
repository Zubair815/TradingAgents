"""Deterministic, read-only headless pair observer.

The package is intentionally independent from FastAPI, SQLite, MetaTrader 5,
Windows notification APIs, and all LLM clients. Concrete adapters live at the
system boundary and depend on these contracts, never the reverse.
"""

from tradingagents.pair_observer.models import (
    AlertKind,
    Candle,
    Direction,
    MarketLevel,
    MarketStructure,
    NewsStatus,
    ObserverAlert,
    ObserverSnapshotEnvelope,
    SetupState,
    SetupType,
    StructureState,
    Timeframe,
    WeeklyReview,
)

__all__ = [
    "AlertKind",
    "Candle",
    "Direction",
    "MarketLevel",
    "MarketStructure",
    "NewsStatus",
    "ObserverAlert",
    "ObserverSnapshotEnvelope",
    "SetupState",
    "SetupType",
    "StructureState",
    "Timeframe",
    "WeeklyReview",
]
