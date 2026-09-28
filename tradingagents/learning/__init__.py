"""Forex Learning, Post-Trade Reflection & Lesson Retrieval Package (Phase 17).

Exports:
- Domain models: ``ForexLesson``, ``RetrievedLesson``, ``ReflectionRating``,
  ``TradeReflection``, ``ReflectionContext``.
- Persistent SQLite lesson repository: ``ForexLessonStore``.
- Post-trade reflection agent: ``ForexReflectionAgent``.
- Contextual heuristic retrieval: ``LessonRetriever``.
- Unified coordinator façade: ``ForexLearningManager``.
"""

from tradingagents.learning.agent import ForexReflectionAgent
from tradingagents.learning.history_provider import (
    InMemoryTradeHistoryProvider,
    MT5TradeHistoryProvider,
    TradeHistoryProvider,
    TradeHistoryResult,
)
from tradingagents.learning.manager import ForexLearningManager
from tradingagents.learning.models import (
    EvidenceClass,
    ForexLesson,
    ReflectionContext,
    ReflectionRating,
    RetrievedLesson,
    TradeReflection,
    classify_evidence,
)
from tradingagents.learning.retriever import LessonRetriever
from tradingagents.learning.store import ForexLessonStore

__all__ = [
    # Facade Manager
    "ForexLearningManager",
    # Agent & Retriever
    "ForexReflectionAgent",
    "LessonRetriever",
    # Store
    "ForexLessonStore",
    # History Provider (Phase 11)
    "TradeHistoryProvider",
    "MT5TradeHistoryProvider",
    "InMemoryTradeHistoryProvider",
    "TradeHistoryResult",
    # Models
    "ForexLesson",
    "EvidenceClass",
    "classify_evidence",
    "RetrievedLesson",
    "ReflectionRating",
    "TradeReflection",
    "ReflectionContext",
]
