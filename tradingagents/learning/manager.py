"""Unified Forex Learning Manager Façade (Phase 17).

Coordinates post-trade reflection, lesson extraction and storage, contextual memory
retrieval, and automated injection of historical heuristics into trader prompts.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import TradeJournalRecord
from tradingagents.learning.agent import ForexReflectionAgent
from tradingagents.learning.models import (
    ReflectionContext,
    TradeReflection,
)
from tradingagents.learning.retriever import LessonRetriever
from tradingagents.learning.store import ForexLessonStore
from tradingagents.metrics.mfe_mae import calculate_trade_mfe_mae, parse_utc_timestamp
from tradingagents.metrics.models import TradeOutcomeCategory
from tradingagents.metrics.outcome import TradeOutcomeEngine

if TYPE_CHECKING:
    from tradingagents.learning.history_provider import TradeHistoryProvider

logger = logging.getLogger(__name__)


class ForexLearningManager:
    """Unified façade coordinating post-trade reflection and memory-augmented guidance."""

    def __init__(
        self,
        journal: ForexTradeJournal | None = None,
        db_path: str | Path | None = None,
        store: ForexLessonStore | None = None,
        history_provider: TradeHistoryProvider | None = None,
    ) -> None:
        if journal is not None:
            self.journal = journal
            self.store = store or ForexLessonStore(journal=self.journal)
        elif store is not None:
            self.store = store
            self.journal = store.journal
        else:
            self.journal = ForexTradeJournal(db_path=db_path, auto_migrate=True)
            self.store = ForexLessonStore(journal=self.journal)

        self.agent = ForexReflectionAgent(store=self.store)
        self.retriever = LessonRetriever(store=self.store)

        if history_provider is not None:
            self.history_provider = history_provider
        else:
            from tradingagents.learning.history_provider import MT5TradeHistoryProvider

            self.history_provider = MT5TradeHistoryProvider()

    def reflect_on_trade(
        self,
        trade: TradeJournalRecord | str,
        candles: Sequence[Any] | pd.DataFrame | None = None,
    ) -> TradeReflection:
        """Execute full post-trade reflection lifecycle for a settled position.

        1. Fetches trade, proposal, fills, and timeline events from journal.
        2. Calculates MFE/MAE excursions and classifies trade outcome.
        3. Evaluates execution discipline and extracts actionable heuristics.
        4. Persists lessons into lesson store and updates trade reflection in journal.
        """
        # Resolve trade record
        if isinstance(trade, str):
            if self.journal is None:
                raise ValueError("Cannot resolve trade_id without an active journal.")
            trade_rec = self.journal.get_trade(trade)
            if trade_rec is None:
                raise ValueError(f"Trade with id {trade!r} not found in journal.")
        else:
            trade_rec = trade

        # Fetch proposal if linked
        proposal_rec = None
        if trade_rec.proposal_id and self.journal is not None:
            proposal_rec = self.journal.get_proposal(trade_rec.proposal_id)

        # Fetch execution fills and timeline events
        executions: list[Any] = []
        events: list[Any] = []
        if self.journal is not None:
            executions = self.journal.list_executions_for_trade(trade_rec.trade_id)
            raw_events = self.journal.get_events(trade_id=trade_rec.trade_id)
            # Reconstruct TradeEvent models if possible
            for rev in raw_events:
                events.append(rev)

        # Calculate excursion metrics & outcome classification
        if candles is not None:
            mfe_mae = calculate_trade_mfe_mae(
                trade=trade_rec,
                candles=candles,
                source="MANUAL",
            )
        else:
            open_dt = parse_utc_timestamp(trade_rec.open_time_utc) or datetime.now(timezone.utc)
            close_dt = parse_utc_timestamp(trade_rec.close_time_utc) or datetime.now(timezone.utc)
            history_res = self.history_provider.get_history(
                pair=trade_rec.pair,
                start_time=open_dt,
                end_time=close_dt,
                timeframe="M1",
            )
            candle_data = history_res.candles if history_res.candles is not None else []
            mfe_mae = calculate_trade_mfe_mae(
                trade=trade_rec,
                candles=candle_data,
                source=history_res.source,
                resolution=history_res.resolution,
                precision=history_res.precision,
                retrieval_time_utc=history_res.retrieval_time_utc,
                is_available=history_res.is_available,
                unavailable_reason=history_res.unavailable_reason,
            )
        outcome = TradeOutcomeEngine.classify_outcome(mfe_mae=mfe_mae, trade=trade_rec)

        # Assemble full operational context (Phase 14)
        risk_dec = None
        if proposal_rec and getattr(proposal_rec, "risk_decision", None):
            risk_dec = proposal_rec.risk_decision
        elif trade_rec.metadata and "risk_decision" in trade_rec.metadata:
            risk_dec = trade_rec.metadata["risk_decision"]

        exec_qual = trade_rec.metadata.get("execution_quality") if trade_rec.metadata else None
        sess = trade_rec.metadata.get("session") if trade_rec.metadata else None
        setup = getattr(proposal_rec, "setup_type", None) or (trade_rec.metadata.get("setup_type") if trade_rec.metadata else "TREND_CONTINUATION")
        if hasattr(setup, "value"):
            setup = setup.value

        news_ctx = (
            proposal_rec.metadata.get("news_context")
            if (proposal_rec and proposal_rec.metadata)
            else (trade_rec.metadata.get("news_context", {}) if trade_rec.metadata else {})
        )

        context = ReflectionContext(
            trade=trade_rec,
            proposal=proposal_rec,
            mfe_mae=mfe_mae,
            outcome=outcome,
            executions=executions,
            events=events,
            risk_decision=risk_dec if isinstance(risk_dec, dict) else None,
            execution_quality=exec_qual,
            session=sess,
            setup=str(setup),
            market_news_context=news_ctx if isinstance(news_ctx, dict) else {},
        )

        # Execute reflection agent
        reflection = self.agent.reflect(context)

        # Save extracted lessons into lesson store (Phase 15: merge similar heuristics)
        if reflection.lessons:
            self.store.save_lessons(reflection.lessons, merge_similar=True)

        # Weaken contradicting failure lessons if trade was a confirmed winner
        if outcome and outcome.category in (
            TradeOutcomeCategory.STANDARD_WIN,
            TradeOutcomeCategory.PERFECT_EXIT,
        ):
            self.store.weaken_contradicting_lessons(
                pair=trade_rec.pair,
                setup=context.setup or "TREND_CONTINUATION",
            )

        # Update SQLite trade record with reflection summary & tags
        if self.journal is not None:
            self.journal.update_trade_reflection(
                trade_id=trade_rec.trade_id,
                reflection=reflection.summary,
                tags=reflection.tags,
            )
            # Emit timeline audit event
            self.journal.record_event(
                trade_id=trade_rec.trade_id,
                proposal_id=trade_rec.proposal_id,
                event_type="NOTE_ADDED",
                actor="ForexReflectionAgent",
                description=f"Generated post-trade reflection ({reflection.rating.value})",
                payload={
                    "rating": reflection.rating.value,
                    "lessons_extracted": len(reflection.lessons),
                    "tags": reflection.tags,
                },
            )

        return reflection

    def retrieve_guidance_for_proposal(
        self,
        pair: str,
        timeframe: str | None = None,
        setup_type: str | None = None,
        direction: str | None = None,
        session: str | None = None,
        market_regime: str | None = None,
        action: str | None = None,
        tags: list[str] | None = None,
        limit: int = 5,
        min_relevance: float = 0.35,
        min_support: int | None = None,
        half_life_days: float | None = None,
        as_of: datetime | str | None = None,
    ) -> str:
        """Retrieve historical lessons and format into an LLM prompt guidance block."""
        retrieved = self.retriever.retrieve_lessons(
            pair=pair,
            timeframe=timeframe,
            setup_type=setup_type,
            direction=direction or action,
            session=session,
            market_regime=market_regime,
            tags=tags,
            limit=limit,
            min_relevance=min_relevance,
            min_support=min_support,
            half_life_days=half_life_days,
            as_of=as_of,
        )
        return self.retriever.format_lessons_for_prompt(retrieved)

    def render_lessons_markdown_report(
        self,
        pair: str | None = None,
        setup_type: str | None = None,
        limit: int = 50,
    ) -> str:
        """Render a formatted institutional Markdown table of active lessons."""
        lessons = self.store.list_lessons(pair=pair, setup_type=setup_type, limit=limit)
        lines: list[str] = [
            "# Institutional Trading Lessons & Prescriptive Directives",
            f"*Total Lessons Stored: {len(lessons)}*",
            "",
            "| ID | Pair | Setup Type | Outcome Category | Rule / Trap | Prescriptive Actionable Directive |",
            "|:---|:---:|:---:|:---:|:---|:---|",
        ]

        if not lessons:
            lines.append("| — | — | — | — | *No lessons recorded yet* | — |")
            return "\n".join(lines)

        for lsn in lessons:
            rule_str = lsn.rule_violated or "General Best Practice"
            lines.append(
                f"| `{lsn.lesson_id}` | **{lsn.pair}** | `{lsn.setup_type}` | `{lsn.outcome_category}` | {rule_str} | {lsn.actionable_rule} |"
            )

        return "\n".join(lines)
