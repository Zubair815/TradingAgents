"""Deterministic Forex Trade Lifecycle State Machine & Event Manager (Phase 15).

Provides strict state transition validation, breakeven stop detection,
partial close execution, and automated timeline auditing.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexRiskDecision,
    ForexRiskDecisionAction,
    ForexTraderProposal,
)
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import (
    ProposalStatus,
    TradeExitReason,
    TradeJournalRecord,
    TradeStatus,
)
from tradingagents.forex.domain import get_forex_pair, normalize_forex_pair
from tradingagents.forex.pips import pip_size_for, pip_value_in_account_currency, pips_directional
from tradingagents.journal.models import EventType, LifecycleState
from tradingagents.journal.timeline import EventTimeline

logger = logging.getLogger(__name__)


class LifecycleError(Exception):
    """Base exception for trade lifecycle violations."""
    pass


class LifecycleTransitionError(LifecycleError):
    """Raised when an illegal state machine transition is attempted."""
    pass


ALLOWED_TRANSITIONS: dict[LifecycleState, set[LifecycleState]] = {
    LifecycleState.CREATED: {
        LifecycleState.PROPOSED,
        LifecycleState.CANCELLED,
        LifecycleState.INVALIDATED,
    },
    LifecycleState.PROPOSED: {
        LifecycleState.APPROVED,
        LifecycleState.REJECTED,
        LifecycleState.RISK_REJECTED,
        LifecycleState.MODIFIED,
        LifecycleState.CANCELLED,
        LifecycleState.EXPIRED,
        LifecycleState.INVALIDATED,
        LifecycleState.SUPERSEDED,
    },
    LifecycleState.APPROVED: {
        LifecycleState.WAITING_USER,
        LifecycleState.PENDING_FILL,
        LifecycleState.ACTIVE_POSITION,
        LifecycleState.EXECUTED,
        LifecycleState.SKIPPED,
        LifecycleState.SKIPPED_BY_USER,
        LifecycleState.CANCELLED,
        LifecycleState.EXPIRED,
        LifecycleState.INVALIDATED,
        LifecycleState.SUPERSEDED,
    },
    LifecycleState.MODIFIED: {
        LifecycleState.WAITING_USER,
        LifecycleState.PENDING_FILL,
        LifecycleState.ACTIVE_POSITION,
        LifecycleState.EXECUTED,
        LifecycleState.SKIPPED,
        LifecycleState.SKIPPED_BY_USER,
        LifecycleState.CANCELLED,
        LifecycleState.EXPIRED,
        LifecycleState.INVALIDATED,
        LifecycleState.SUPERSEDED,
    },
    LifecycleState.WAITING_USER: {
        LifecycleState.PENDING_FILL,
        LifecycleState.ACTIVE_POSITION,
        LifecycleState.EXECUTED,
        LifecycleState.SKIPPED,
        LifecycleState.SKIPPED_BY_USER,
        LifecycleState.CANCELLED,
        LifecycleState.EXPIRED,
        LifecycleState.INVALIDATED,
        LifecycleState.SUPERSEDED,
    },
    LifecycleState.PENDING_FILL: {
        LifecycleState.ACTIVE_POSITION,
        LifecycleState.EXECUTED,
        LifecycleState.CANCELLED,
        LifecycleState.EXPIRED,
        LifecycleState.INVALIDATED,
        LifecycleState.SUPERSEDED,
    },
    LifecycleState.EXECUTED: {
        LifecycleState.ACTIVE_POSITION,
        LifecycleState.SETTLED,
    },
    LifecycleState.ACTIVE_POSITION: {
        LifecycleState.PARTIALLY_CLOSED,
        LifecycleState.SETTLED,
    },
    LifecycleState.PARTIALLY_CLOSED: {
        LifecycleState.PARTIALLY_CLOSED,
        LifecycleState.SETTLED,
    },
    LifecycleState.SETTLED: set(),
    LifecycleState.REJECTED: set(),
    LifecycleState.RISK_REJECTED: set(),
    LifecycleState.CANCELLED: set(),
    LifecycleState.EXPIRED: set(),
    LifecycleState.SKIPPED: set(),
    LifecycleState.SKIPPED_BY_USER: set(),
    LifecycleState.INVALIDATED: set(),
    LifecycleState.SUPERSEDED: set(),
}


class TradeLifecycleManager:
    """Orchestrates deterministic trade lifecycle state transitions and timeline logging."""

    def __init__(
        self,
        journal: ForexTradeJournal,
        timeline: EventTimeline | None = None,
    ) -> None:
        self.journal = journal
        self.timeline = timeline or EventTimeline(journal=journal)

    def can_transition(
        self, from_state: LifecycleState, to_state: LifecycleState
    ) -> bool:
        """Check if transition from one lifecycle state to another is permitted."""
        return to_state in ALLOWED_TRANSITIONS.get(from_state, set())

    def validate_transition(
        self, from_state: LifecycleState, to_state: LifecycleState
    ) -> None:
        """Validate state transition, raising LifecycleTransitionError if forbidden."""
        if not self.can_transition(from_state, to_state):
            raise LifecycleTransitionError(
                f"Illegal lifecycle transition: Cannot move from {from_state.value} to {to_state.value}."
            )

    # -----------------------------------------------------------------------
    # 1. Proposal Phase
    # -----------------------------------------------------------------------

    def submit_proposal(
        self,
        proposal: ForexTraderProposal,
        metadata: dict[str, Any] | None = None,
        actor: str = "ForexTrader",
    ) -> str:
        """Submit a newly generated ForexTraderProposal and log lifecycle creation."""
        proposal_id = self.journal.save_proposal(
            proposal=proposal,
            status=ProposalStatus.PROPOSED,
            metadata=metadata or {},
        )

        self.timeline.record_event(
            event_type=EventType.PROPOSAL_CREATED,
            proposal_id=proposal_id,
            actor=actor,
            description=f"Generated {proposal.pair} {proposal.action.value} proposal",
            payload={
                "pair": proposal.pair,
                "action": proposal.action.value,
                "setup_type": proposal.setup_type.value,
                "entry_price": proposal.entry_price,
                "stop_loss": proposal.stop_loss,
                "take_profit_1": proposal.take_profit_1,
                "risk_reward_ratio": proposal.risk_reward_ratio,
            },
        )
        return proposal_id

    def evaluate_risk(
        self,
        proposal_id: str,
        risk_decision: ForexRiskDecision,
        actor: str = "ForexRiskEngine",
    ) -> ProposalStatus:
        """Process a deterministic risk evaluation on a proposed trade."""
        record = self.journal.get_proposal(proposal_id)
        if record is None:
            raise LifecycleError(f"Proposal '{proposal_id}' does not exist.")

        curr_state = LifecycleState(record.status.value)
        if curr_state != LifecycleState.PROPOSED:
            raise LifecycleTransitionError(
                f"Cannot evaluate risk for proposal {proposal_id} in status {curr_state.value}."
            )

        if risk_decision.decision == ForexRiskDecisionAction.APPROVE:
            next_status = ProposalStatus.APPROVED
            next_state = LifecycleState.APPROVED
            evt_type = EventType.PROPOSAL_APPROVED
            desc = f"Approved {record.pair} trade with {risk_decision.approved_lot_size} lots"
        elif risk_decision.decision == ForexRiskDecisionAction.MODIFY:
            next_status = ProposalStatus.MODIFIED
            next_state = LifecycleState.MODIFIED
            evt_type = EventType.PROPOSAL_MODIFIED
            desc = f"Modified {record.pair} trade: lot size / stops adjusted by risk engine"
        else:
            next_status = ProposalStatus.REJECTED
            next_state = LifecycleState.REJECTED
            evt_type = EventType.PROPOSAL_REJECTED
            desc = f"Rejected {record.pair} trade: {risk_decision.executive_rationale}"

        self.validate_transition(curr_state, next_state)

        # Update in database
        self.journal.update_proposal_status(
            proposal_id=proposal_id,
            status=next_status,
            risk_decision=risk_decision,
        )

        # Log timeline event
        self.timeline.record_event(
            event_type=evt_type,
            proposal_id=proposal_id,
            actor=actor,
            description=desc,
            payload={
                "decision": risk_decision.decision.value,
                "approved_lots": risk_decision.approved_lot_size,
                "max_risk_percent": risk_decision.max_risk_percent,
                "rationale": risk_decision.executive_rationale,
            },
        )
        return next_status

    # -----------------------------------------------------------------------
    # 2. Execution & Trade Opening Phase
    # -----------------------------------------------------------------------

    def open_position_from_proposal(
        self,
        proposal_id: str,
        open_price: float,
        lots: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        ticket: int | str | None = None,
        actor: str = "MT5Observer",
        open_time: datetime | str | None = None,
    ) -> str:
        """Transition an approved proposal into an active open position."""
        record = self.journal.get_proposal(proposal_id)
        if record is None:
            raise LifecycleError(f"Proposal '{proposal_id}' not found.")

        curr_state = LifecycleState(record.status.value)
        if curr_state not in (LifecycleState.APPROVED, LifecycleState.MODIFIED, LifecycleState.WAITING_USER):
            raise LifecycleTransitionError(
                f"Cannot open position for proposal in state {curr_state.value}. Must be APPROVED, MODIFIED, or WAITING_USER."
            )

        eff_sl = stop_loss if stop_loss is not None else (record.stop_loss or 0.0)
        eff_tp = take_profit if take_profit is not None else record.take_profit_1

        open_time_str = (
            open_time.astimezone(timezone.utc).isoformat()
            if isinstance(open_time, datetime)
            else (str(open_time) if open_time else None)
        )
        trade_record = self.journal.record_trade_open(
            pair=record.pair,
            action=record.action,
            open_price=open_price,
            stop_loss=eff_sl,
            take_profit=eff_tp,
            lots=lots,
            proposal_id=proposal_id,
            metadata={"broker_ticket": str(ticket) if ticket else None},
            open_time_utc=open_time_str,
        )
        trade_id = trade_record.trade_id

        # Calculate entry slippage
        expected_entry = record.entry_price or open_price
        pip_sz = pip_size_for(record.pair)
        slippage_pips = round(abs(open_price - expected_entry) / pip_sz, 1)

        self.timeline.record_event(
            event_type=EventType.POSITION_OPENED,
            trade_id=trade_id,
            proposal_id=proposal_id,
            actor=actor,
            description=f"Opened {record.pair} {record.action.value} position ({lots} lots @ {open_price})",
            payload={
                "trade_id": trade_id,
                "ticket": ticket,
                "open_price": open_price,
                "expected_price": expected_entry,
                "slippage_pips": slippage_pips,
                "lots": lots,
                "stop_loss": eff_sl,
                "take_profit": eff_tp,
            },
            timestamp_utc=open_time_str,
        )
        return trade_id

    def open_unplanned_position(
        self,
        pair: str,
        action: ForexAction,
        open_price: float,
        lots: float,
        stop_loss: float = 0.0,
        take_profit: float | None = None,
        ticket: int | str | None = None,
        open_time_utc: str | None = None,
        actor: str = "MT5Observer",
        notes: str = "Unplanned manual trade",
    ) -> str:
        """Record an unplanned manual execution in the journal without a prior proposal."""
        norm_pair = normalize_forex_pair(pair)
        trade_record = self.journal.record_trade_open(
            pair=norm_pair,
            action=action,
            open_price=open_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            lots=lots,
            proposal_id=None,
            open_time_utc=open_time_utc,
            tags=["MANUAL_UNPLANNED"],
            notes=notes,
            metadata={"broker_ticket": str(ticket) if ticket else None, "unplanned": True},
        )
        trade_id = trade_record.trade_id

        self.timeline.record_event(
            event_type=EventType.POSITION_OPENED,
            trade_id=trade_id,
            proposal_id=None,
            actor=actor,
            description=f"Opened unplanned manual {norm_pair} {action.value} position ({lots} lots @ {open_price})",
            payload={
                "trade_id": trade_id,
                "ticket": ticket,
                "open_price": open_price,
                "unplanned": True,
            },
            timestamp_utc=open_time_utc,
        )
        return trade_id

    # -----------------------------------------------------------------------
    # 3. Position Management (Stop-loss, Breakeven, Partial Closes)
    # -----------------------------------------------------------------------

    def modify_stop_loss(
        self,
        trade_id: str,
        new_stop_loss: float,
        reason: str = "",
        actor: str = "Trader",
    ) -> bool:
        """Modify stop loss level; automatically detects and flags breakeven adjustment."""
        trade = self.journal.get_trade(trade_id)
        if trade is None:
            raise LifecycleError(f"Trade '{trade_id}' not found.")
        if trade.status != TradeStatus.OPEN:
            raise LifecycleError(f"Cannot modify SL for trade {trade_id} with status {trade.status.value}.")

        old_sl = trade.stop_loss
        is_breakeven = False

        # Detect breakeven: Long SL >= open_price; Short SL <= open_price
        if trade.action == ForexAction.LONG and new_stop_loss >= trade.open_price or trade.action == ForexAction.SHORT and new_stop_loss <= trade.open_price:
            is_breakeven = True

        # Update in DB
        with self.journal._lock:
            conn = self.journal._get_connection()
            try:
                with conn:
                    conn.execute(
                        "UPDATE trades SET stop_loss = ? WHERE trade_id = ?;",
                        (new_stop_loss, trade_id),
                    )
            finally:
                if not self.journal._is_memory and conn is not self.journal._mem_conn:
                    conn.close()

        evt_type = EventType.BREAKEVEN_APPLIED if is_breakeven else EventType.STOP_LOSS_MODIFIED
        desc = (
            f"Breakeven stop applied @ {new_stop_loss} ({reason})"
            if is_breakeven
            else f"Stop-loss adjusted from {old_sl} to {new_stop_loss} ({reason})"
        )

        self.timeline.record_event(
            event_type=evt_type,
            trade_id=trade_id,
            proposal_id=trade.proposal_id,
            actor=actor,
            description=desc,
            payload={
                "old_stop_loss": old_sl,
                "new_stop_loss": new_stop_loss,
                "is_breakeven": is_breakeven,
                "reason": reason,
            },
        )
        return is_breakeven

    def modify_take_profit(
        self,
        trade_id: str,
        new_take_profit: float,
        reason: str = "",
        actor: str = "Trader",
    ) -> None:
        """Modify take-profit target price level."""
        trade = self.journal.get_trade(trade_id)
        if trade is None:
            raise LifecycleError(f"Trade '{trade_id}' not found.")
        if trade.status != TradeStatus.OPEN:
            raise LifecycleError(f"Cannot modify TP for trade {trade_id} with status {trade.status.value}.")

        old_tp = trade.take_profit
        with self.journal._lock:
            conn = self.journal._get_connection()
            try:
                with conn:
                    conn.execute(
                        "UPDATE trades SET take_profit = ? WHERE trade_id = ?;",
                        (new_take_profit, trade_id),
                    )
            finally:
                if not self.journal._is_memory and conn is not self.journal._mem_conn:
                    conn.close()

        self.timeline.record_event(
            event_type=EventType.TAKE_PROFIT_MODIFIED,
            trade_id=trade_id,
            proposal_id=trade.proposal_id,
            actor=actor,
            description=f"Take-profit updated from {old_tp} to {new_take_profit} ({reason})",
            payload={
                "old_take_profit": old_tp,
                "new_take_profit": new_take_profit,
                "reason": reason,
            },
        )

    def partial_close_trade(
        self,
        trade_id: str,
        lots_to_close: float,
        close_price: float,
        exit_reason: TradeExitReason = TradeExitReason.TAKE_PROFIT,
        actor: str = "Trader",
    ) -> dict[str, Any]:
        """Execute a partial position scale-out, updating remaining trade volume."""
        trade = self.journal.get_trade(trade_id)
        if trade is None:
            raise LifecycleError(f"Trade '{trade_id}' not found.")
        if trade.status != TradeStatus.OPEN:
            raise LifecycleError(f"Cannot partially close trade {trade_id} with status {trade.status.value}.")
        if lots_to_close <= 0 or lots_to_close >= trade.lots:
            raise LifecycleError(
                f"Partial close lots ({lots_to_close}) must be > 0 and strictly less than active lots ({trade.lots})."
            )

        remaining_lots = round(trade.lots - lots_to_close, 2)
        pair = get_forex_pair(trade.pair)
        if pair is None:
            raise LifecycleError(
                f"Partial-close P&L is unavailable for unsupported instrument {trade.pair}."
            )
        pip_sz = pair.pip_size
        if trade.action == ForexAction.LONG:
            pip_diff = round(pips_directional(trade.open_price, close_price, pip_sz), 1)
        else:
            pip_diff = round(pips_directional(close_price, trade.open_price, pip_sz), 1)
        pip_value = pip_value_in_account_currency(
            pair=pair, lot_size=lots_to_close, account_currency=self.journal.account_currency,
            current_quote_price=close_price,
        )
        realized_profit = pip_diff * pip_value

        metadata = dict(trade.metadata)
        partial = dict(metadata.get("partial_close_totals", {}))
        partial["gross_profit"] = round(float(partial.get("gross_profit", 0.0)) + realized_profit, 2)
        partial["pip_lots"] = round(float(partial.get("pip_lots", 0.0)) + pip_diff * lots_to_close, 8)
        partial["lots_closed"] = round(float(partial.get("lots_closed", 0.0)) + lots_to_close, 8)
        metadata["partial_close_totals"] = partial

        with self.journal._lock:
            conn = self.journal._get_connection()
            try:
                with conn:
                    conn.execute(
                        "UPDATE trades SET lots = ?, metadata_json = ? WHERE trade_id = ?;",
                        (remaining_lots, json.dumps(metadata), trade_id),
                    )
            finally:
                if not self.journal._is_memory and conn is not self.journal._mem_conn:
                    conn.close()

        self.timeline.record_event(
            event_type=EventType.PARTIAL_CLOSE,
            trade_id=trade_id,
            proposal_id=trade.proposal_id,
            actor=actor,
            description=f"Partially closed {lots_to_close} lots @ {close_price} (+{pip_diff} pips). Remaining: {remaining_lots} lots",
            payload={
                "lots_closed": lots_to_close,
                "remaining_lots": remaining_lots,
                "close_price": close_price,
                "pips_gained": pip_diff,
                "realized_profit": round(realized_profit, 2),
                "exit_reason": exit_reason.value,
            },
        )

        return {
            "trade_id": trade_id,
            "lots_closed": lots_to_close,
            "remaining_lots": remaining_lots,
            "pips_gained": pip_diff,
            "realized_profit": round(realized_profit, 2),
        }

    # -----------------------------------------------------------------------
    # 4. Final Settlement & Cancellation
    # -----------------------------------------------------------------------

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
        """Close an open trade position and record final settlement in the journal."""
        trade = self.journal.get_trade(trade_id)
        if trade is None:
            raise LifecycleError(f"Trade '{trade_id}' not found.")
        if trade.status != TradeStatus.OPEN:
            raise LifecycleError(f"Trade {trade_id} is already settled ({trade.status.value}).")

        if close_time is not None:
            close_dt = close_time if isinstance(close_time, datetime) else datetime.fromisoformat(str(close_time).replace("Z", "+00:00"))
            if close_dt.tzinfo is None:
                raise LifecycleError("close_time must include a timezone")
            open_dt = datetime.fromisoformat(trade.open_time_utc.replace("Z", "+00:00"))
            if close_dt.astimezone(timezone.utc) < open_dt.astimezone(timezone.utc):
                raise LifecycleError("close_time cannot be earlier than open_time")

        close_time_str = (
            close_time.isoformat()
            if isinstance(close_time, datetime)
            else (str(close_time) if close_time else None)
        )

        closed_trade = self.journal.record_trade_close(
            trade_id=trade_id,
            close_price=close_price,
            exit_reason=exit_reason,
            close_time_utc=close_time_str,
            swap=swap,
            gross_profit=gross_profit,
            commission=commission,
        )

        self.timeline.record_event(
            event_type=EventType.POSITION_CLOSED,
            trade_id=trade_id,
            proposal_id=closed_trade.proposal_id,
            actor=actor,
            description=f"Closed {closed_trade.pair} position @ {close_price} ({exit_reason.value}, {closed_trade.pips_gained:+.1f} pips, R: {closed_trade.r_multiple:+.2f})",
            payload={
                "close_price": close_price,
                "exit_reason": exit_reason.value,
                "pips_gained": closed_trade.pips_gained,
                "r_multiple": closed_trade.r_multiple,
                "gross_profit": closed_trade.gross_profit,
                "net_profit": closed_trade.net_profit,
            },
        )
        return closed_trade

    def cancel_proposal(
        self, proposal_id: str, reason: str = "", actor: str = "Trader"
    ) -> None:
        """Cancel an unfulfilled proposal."""
        record = self.journal.get_proposal(proposal_id)
        if record is None:
            raise LifecycleError(f"Proposal '{proposal_id}' not found.")

        curr_state = LifecycleState(record.status.value)
        self.validate_transition(curr_state, LifecycleState.CANCELLED)

        self.journal.update_proposal_status(
            proposal_id=proposal_id,
            status=ProposalStatus.CANCELLED,
        )

        self.timeline.record_event(
            event_type=EventType.PROPOSAL_REJECTED,
            proposal_id=proposal_id,
            actor=actor,
            description=f"Proposal cancelled: {reason}",
            payload={"reason": reason},
        )

    def expire_proposal(
        self, proposal_id: str, reason: str = "Market session closed / setup expired", actor: str = "System"
    ) -> None:
        """Expire a proposal that was not filled within its validity window."""
        record = self.journal.get_proposal(proposal_id)
        if record is None:
            raise LifecycleError(f"Proposal '{proposal_id}' not found.")

        curr_state = LifecycleState(record.status.value)
        if curr_state == LifecycleState.EXPIRED:
            logger.debug("Proposal '%s' is already expired; skipping redundant expiry.", proposal_id)
            return

        self.validate_transition(curr_state, LifecycleState.EXPIRED)

        self.journal.update_proposal_status(
            proposal_id=proposal_id,
            status=ProposalStatus.EXPIRED,
        )

        self.timeline.record_event(
            event_type=EventType.PROPOSAL_EXPIRED,
            proposal_id=proposal_id,
            actor=actor,
            description=f"Proposal expired: {reason}",
            payload={"reason": reason},
        )

    def record_user_action(
        self,
        proposal_id: str,
        action: str,
        actor: str = "User",
        reason: str = "",
    ) -> ProposalStatus:
        """Record a non-execution human decision on a proposal."""
        record = self.journal.get_proposal(proposal_id)
        if record is None:
            raise LifecycleError(f"Proposal '{proposal_id}' not found.")

        curr_state = LifecycleState(record.status.value)
        action_clean = action.strip().upper()

        if action_clean in ("EXECUTED", "EXECUTE"):
            raise LifecycleError(
                "EXECUTED requires a linked journal or MT5 execution; record the actual trade instead."
            )
        if action_clean == "SKIPPED_BY_USER":
            next_state = LifecycleState.SKIPPED_BY_USER
            next_status = ProposalStatus.SKIPPED_BY_USER
            evt_type = EventType.PROPOSAL_SKIPPED
            desc = f"Trader skipped proposal: {reason or 'User decision'}"
        elif action_clean in ("SKIPPED", "SKIP"):
            next_state = LifecycleState.SKIPPED
            next_status = ProposalStatus.SKIPPED
            evt_type = EventType.PROPOSAL_SKIPPED
            desc = f"Trader skipped proposal: {reason or 'User decision'}"
        elif action_clean in ("WAIT", "WAITING", "WAITING_USER"):
            next_state = LifecycleState.WAITING_USER
            next_status = ProposalStatus.WAITING_USER
            evt_type = EventType.PROPOSAL_WAITING_USER
            desc = f"Trader placed proposal on hold: {reason or 'Awaiting trigger'}"
        else:
            raise LifecycleError(
                f"Unsupported user action '{action}'. Must be SKIPPED, SKIPPED_BY_USER, or WAIT."
            )

        self.validate_transition(curr_state, next_state)

        self.journal.update_proposal_status(
            proposal_id=proposal_id,
            status=next_status,
        )

        self.timeline.record_event(
            event_type=evt_type,
            proposal_id=proposal_id,
            actor=actor,
            description=desc,
            payload={"action": action_clean, "reason": reason},
        )
        return next_status

    def reject_proposal(
        self, proposal_id: str, reason: str = "Rejected by user", actor: str = "User"
    ) -> None:
        """Reject a proposed trade while preserving a complete audit event."""
        record = self.journal.get_proposal(proposal_id)
        if record is None:
            raise LifecycleError(f"Proposal '{proposal_id}' not found.")
        self.validate_transition(LifecycleState(record.status.value), LifecycleState.REJECTED)
        self.journal.update_proposal_status(
            proposal_id,
            ProposalStatus.REJECTED,
        )
        self.timeline.record_event(
            event_type=EventType.PROPOSAL_REJECTED,
            proposal_id=proposal_id,
            actor=actor,
            description=f"Proposal rejected: {reason}",
            payload={"reason": reason},
        )

    def supersede_proposal(
        self,
        proposal_id: str,
        superseded_by_id: str | None = None,
        reason: str = "Superseded by newer analysis",
        actor: str = "System",
    ) -> None:
        """Mark an unfulfilled active proposal as SUPERSEDED by newer market analysis."""
        record = self.journal.get_proposal(proposal_id)
        if record is None:
            raise LifecycleError(f"Proposal '{proposal_id}' not found.")

        curr_state = LifecycleState(record.status.value)
        self.validate_transition(curr_state, LifecycleState.SUPERSEDED)

        self.journal.update_proposal_status(
            proposal_id=proposal_id,
            status=ProposalStatus.SUPERSEDED,
        )

        self.timeline.record_event(
            event_type=EventType.PROPOSAL_SUPERSEDED,
            proposal_id=proposal_id,
            actor=actor,
            description=f"Proposal superseded: {reason}",
            payload={"superseded_by": superseded_by_id, "reason": reason},
        )

    def check_and_expire_proposals(
        self,
        current_time: datetime | None = None,
    ) -> list[str]:
        """Check active proposals and transition those past their valid_until timestamp to EXPIRED."""
        now = current_time or datetime.now(timezone.utc)
        active_statuses = [
            ProposalStatus.PROPOSED,
            ProposalStatus.APPROVED,
            ProposalStatus.MODIFIED,
            ProposalStatus.WAITING_USER,
        ]
        expired_ids: list[str] = []
        for status in active_statuses:
            proposals = self.journal.list_proposals(
                status=status,
                limit=self.journal.count_proposals(status=status) or 1,
            )
            for prop in proposals:
                valid_until_str = prop.valid_until
                if not valid_until_str:
                    continue
                try:
                    clean_str = valid_until_str.replace("Z", "+00:00")
                    valid_dt = datetime.fromisoformat(clean_str)
                    if valid_dt.tzinfo is None:
                        valid_dt = valid_dt.replace(tzinfo=timezone.utc)
                    if now >= valid_dt:
                        self.expire_proposal(
                            prop.proposal_id,
                            reason=f"Proposal validity window elapsed ({valid_until_str})",
                        )
                        expired_ids.append(prop.proposal_id)
                except Exception as exc:
                    logger.warning(
                        "Could not parse valid_until '%s' for proposal %s: %s",
                        valid_until_str,
                        prop.proposal_id,
                        exc,
                    )
        return expired_ids
