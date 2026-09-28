"""Unified Forex Journal Manager Facade (Phase 15).

Assembles database persistence, lifecycle state machine, chronological timeline
auditing, proposal matching, and post-trade performance analytics into a cohesive interface.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from tradingagents.agents.schemas_forex import (
    ForexRiskDecision,
    ForexTraderProposal,
)
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import (
    ProposalStatus,
    TradeExitReason,
    TradeJournalRecord,
)
from tradingagents.journal.analytics import PostTradeAnalytics
from tradingagents.journal.lifecycle import TradeLifecycleManager
from tradingagents.journal.matching import ProposalMatcher
from tradingagents.journal.models import (
    EventType,
    MatchResult,
    PerformanceReport,
    TradeEvent,
)
from tradingagents.journal.timeline import EventTimeline


class ForexJournalManager:
    """Unified coordinator for the Forex Trade Journal subsystem."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        journal: ForexTradeJournal | None = None,
        account_currency: str = "USD",
        match_threshold: float = 0.70,
    ) -> None:
        self.journal = journal or ForexTradeJournal(
            db_path=db_path, auto_migrate=True, account_currency=account_currency
        )
        self.timeline = EventTimeline(journal=self.journal)
        self.lifecycle = TradeLifecycleManager(
            journal=self.journal, timeline=self.timeline
        )
        self.matcher = ProposalMatcher(
            journal=self.journal,
            lifecycle=self.lifecycle,
            match_threshold=match_threshold,
        )
        self.analytics = PostTradeAnalytics()

    # -----------------------------------------------------------------------
    # Lifecycle & Proposal Operations
    # -----------------------------------------------------------------------

    def submit_proposal(
        self,
        proposal: ForexTraderProposal,
        metadata: dict[str, Any] | None = None,
        actor: str = "ForexTrader",
    ) -> str:
        """Submit and log a new trade proposal."""
        return self.lifecycle.submit_proposal(
            proposal=proposal, metadata=metadata, actor=actor
        )

    def evaluate_risk(
        self,
        proposal_id: str,
        risk_decision: ForexRiskDecision,
        actor: str = "ForexRiskEngine",
    ) -> ProposalStatus:
        """Record risk engine evaluation and transition proposal state."""
        return self.lifecycle.evaluate_risk(
            proposal_id=proposal_id, risk_decision=risk_decision, actor=actor
        )

    def record_user_action(
        self,
        proposal_id: str,
        action: str,
        actor: str = "User",
        reason: str = "",
    ) -> ProposalStatus:
        """Record human user decision on a proposal (EXECUTED, SKIPPED, WAIT)."""
        return self.lifecycle.record_user_action(
            proposal_id=proposal_id, action=action, actor=actor, reason=reason
        )

    def expire_proposal(
        self,
        proposal_id: str,
        reason: str = "Validity window expired",
        actor: str = "System",
    ) -> None:
        """Expire a proposal that was not executed within its window."""
        self.lifecycle.expire_proposal(proposal_id=proposal_id, reason=reason, actor=actor)

    def check_and_expire_proposals(
        self,
        current_time: datetime | None = None,
    ) -> list[str]:
        """Check all active proposals and expire those past their valid_until timestamp."""
        return self.lifecycle.check_and_expire_proposals(current_time=current_time)

    def supersede_proposals(
        self,
        pair: str,
        new_proposal_id: str | None = None,
        reason: str = "Superseded by newer analysis",
        actor: str = "System",
    ) -> list[str]:
        """Supersede active proposals for a pair when a new proposal is created."""
        older_ids = self.journal.supersede_proposals(
            pair=pair, exclude_proposal_id=new_proposal_id
        )
        for pid in older_ids:
            self.timeline.record_event(
                event_type=EventType.PROPOSAL_SUPERSEDED,
                proposal_id=pid,
                actor=actor,
                description=f"Proposal superseded: {reason}",
                payload={"superseded_by": new_proposal_id, "reason": reason},
            )
        return older_ids

    def open_trade(
        self,
        proposal_id: str,
        open_price: float,
        lots: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        ticket: int | str | None = None,
        actor: str = "MT5Observer",
    ) -> str:
        """Transition proposal into an active open trade."""
        return self.lifecycle.open_position_from_proposal(
            proposal_id=proposal_id,
            open_price=open_price,
            lots=lots,
            stop_loss=stop_loss,
            take_profit=take_profit,
            ticket=ticket,
            actor=actor,
        )

    def modify_stop_loss(
        self,
        trade_id: str,
        new_stop_loss: float,
        reason: str = "",
        actor: str = "Trader",
    ) -> bool:
        """Update stop loss level and automatically detect breakeven."""
        return self.lifecycle.modify_stop_loss(
            trade_id=trade_id, new_stop_loss=new_stop_loss, reason=reason, actor=actor
        )

    def modify_take_profit(
        self,
        trade_id: str,
        new_take_profit: float,
        reason: str = "",
        actor: str = "Trader",
    ) -> None:
        """Update take profit target price."""
        self.lifecycle.modify_take_profit(
            trade_id=trade_id, new_take_profit=new_take_profit, reason=reason, actor=actor
        )

    def partial_close(
        self,
        trade_id: str,
        lots_to_close: float,
        close_price: float,
        exit_reason: TradeExitReason = TradeExitReason.TAKE_PROFIT,
        actor: str = "Trader",
    ) -> dict[str, Any]:
        """Execute a partial position scale-out."""
        return self.lifecycle.partial_close_trade(
            trade_id=trade_id,
            lots_to_close=lots_to_close,
            close_price=close_price,
            exit_reason=exit_reason,
            actor=actor,
        )

    def close_trade(
        self,
        trade_id: str,
        close_price: float,
        exit_reason: TradeExitReason,
        close_time: datetime | str | None = None,
        gross_profit: float | None = None,
        commission: float = 0.0,
        swap: float = 0.0,
        actor: str = "MT5Observer",
    ) -> TradeJournalRecord:
        """Close an active position and settle trade record."""
        return self.lifecycle.close_trade(
            trade_id=trade_id,
            close_price=close_price,
            exit_reason=exit_reason,
            close_time=close_time,
            gross_profit=gross_profit,
            commission=commission,
            swap=swap,
            actor=actor,
        )

    # -----------------------------------------------------------------------
    # Reconciliation & Proposal Matching
    # -----------------------------------------------------------------------

    def reconcile_broker_positions(
        self,
        positions: Sequence[Any],
        auto_reconcile: bool = True,
    ) -> list[MatchResult]:
        """Reconcile broker executions with active approved proposals."""
        return self.matcher.reconcile_positions(
            positions=positions, auto_reconcile=auto_reconcile
        )

    # -----------------------------------------------------------------------
    # Timeline & Analytics
    # -----------------------------------------------------------------------

    def get_timeline(
        self, trade_id: str | None = None, proposal_id: str | None = None, limit: int = 100
    ) -> list[TradeEvent]:
        """Fetch chronological timeline audit events."""
        return self.timeline.get_events(
            trade_id=trade_id, proposal_id=proposal_id, limit=limit
        )

    def render_timeline(
        self, trade_id: str | None = None, proposal_id: str | None = None
    ) -> str:
        """Render formatted visual Markdown audit timeline."""
        return self.timeline.render_markdown_timeline(
            trade_id=trade_id, proposal_id=proposal_id
        )

    def compute_performance(
        self, initial_capital: float = 100000.0
    ) -> PerformanceReport:
        """Compute institutional performance analytics across all closed trades."""
        trades = self.journal.list_trades()
        return self.analytics.compute_performance_report(
            trades=trades, initial_capital=initial_capital
        )

    def render_performance_dashboard(
        self, initial_capital: float = 100000.0
    ) -> str:
        """Generate high-impact Markdown performance dashboard."""
        report = self.compute_performance(initial_capital=initial_capital)
        return self.analytics.render_markdown_dashboard(report)

    def compute_comprehensive_performance(
        self, initial_capital: float = 100000.0
    ) -> Any:
        """Compute comprehensive performance analytics including multi-dimensional segmentation (Phase 19)."""
        from tradingagents.analytics.performance import ForexPerformanceEngine

        engine = ForexPerformanceEngine(initial_capital=initial_capital)
        trades = self.journal.list_trades(limit=10000)
        proposals = self.journal.list_proposals(limit=5000)
        events = self.timeline.get_events(limit=10000)
        return engine.generate_performance_report(
            trades=trades, proposals=proposals, events=events, initial_capital=initial_capital
        )

    def close(self) -> None:
        """Clean up underlying database resources."""
        self.journal.close()
