"""Continuous Read-Only MetaTrader 5 Observation Service (Phase 8).

Periodically polls open positions, pending orders, and history deals from MT5.
Reconciles broker state with the Forex Trade Journal, detects SL/TP modifications,
records partial/full closes, and maintains an idempotent audit timeline.

ABSOLUTE SAFETY GUARANTEE:
This service is strictly READ-ONLY. It never invokes `order_send()`, never places
or closes trades on the broker terminal, and never modifies live orders.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from tradingagents.agents.schemas_forex import ForexAction
from tradingagents.database.models import (
    TradeExitReason,
    TradeStatus,
)

if TYPE_CHECKING:
    from tradingagents.journal.post_close import ClosedTradeProcessor
from tradingagents.journal.manager import ForexJournalManager
from tradingagents.journal.models import EventType
from tradingagents.mt5.errors import MT5Error
from tradingagents.mt5.models import MT5Order, MT5Position
from tradingagents.mt5.observer import MT5Observer

logger = logging.getLogger(__name__)


class MT5ObservationService:
    """Continuous background worker orchestrating read-only MT5 state synchronization."""

    def __init__(
        self,
        observer: MT5Observer,
        journal_mgr: ForexJournalManager,
        poll_interval_seconds: float = 5.0,
        auto_reconcile: bool = True,
        on_event_callback: Callable[[str, dict[str, Any]], None] | None = None,
        post_close_processor: ClosedTradeProcessor | None = None,
    ) -> None:
        self.observer = observer
        self.journal_mgr = journal_mgr
        self.journal = journal_mgr.journal
        self.poll_interval = max(0.1, float(poll_interval_seconds))
        self.auto_reconcile = auto_reconcile
        self.on_event_callback = on_event_callback

        if post_close_processor is not None:
            self.post_close_processor = post_close_processor
        else:
            from tradingagents.journal.post_close import ClosedTradeProcessor
            from tradingagents.learning.history_provider import MT5TradeHistoryProvider

            hist_prov = MT5TradeHistoryProvider(observer=self.observer)
            self.post_close_processor = ClosedTradeProcessor(
                journal=self.journal,
                history_provider=hist_prov,
            )

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None

        # In-memory tracking structures for state comparison & idempotency
        self._known_positions: dict[int, MT5Position] = {}
        self._known_orders: dict[int, MT5Order] = {}
        self._trade_position_map: dict[int, str] = {}  # ticket -> trade_id
        self._processed_deal_tickets: set[int] = set()

        # Reconstruct known state from journal
        self._reconstruct_state_from_journal()

    def _reconstruct_state_from_journal(self) -> None:
        """Reconstruct position-to-trade mappings and processed tickets from DB on startup."""
        with self._lock:
            try:
                open_trades = self.journal.list_trades(status=TradeStatus.OPEN, limit=1000)
                for trade in open_trades:
                    ticket_raw = trade.metadata.get("broker_ticket") or trade.metadata.get("ticket")
                    if ticket_raw is not None:
                        with contextlib.suppress(ValueError, TypeError):
                            self._trade_position_map[int(ticket_raw)] = trade.trade_id

                # Also load existing processed deals/events from trade timeline to guarantee idempotency across restarts
                events = self.journal_mgr.timeline.get_events(limit=5000)
                for evt in events:
                    payload = evt.payload or {}
                    deal_ticket = payload.get("deal_ticket") or payload.get("ticket")
                    if deal_ticket is not None:
                        with contextlib.suppress(ValueError, TypeError):
                            self._processed_deal_tickets.add(int(deal_ticket))
            except Exception as exc:
                logger.warning("Error reconstructing state from journal: %s", exc)

    @property
    def is_running(self) -> bool:
        return self._worker_thread is not None and self._worker_thread.is_alive()

    def start(self) -> None:
        """Start background polling thread."""
        with self._lock:
            if self.is_running:
                logger.info("MT5ObservationService already running.")
                return

            self._stop_event.clear()
            self._worker_thread = threading.Thread(
                target=self._run_loop,
                name="MT5ObservationService-Worker",
                daemon=True,
            )
            self._worker_thread.start()
            logger.info("MT5ObservationService background thread started (interval=%.1fs).", self.poll_interval)

    def stop(self, timeout: float = 5.0) -> None:
        """Stop background worker cleanly."""
        with self._lock:
            if not self.is_running:
                return

            self._stop_event.set()
            thread = self._worker_thread
            self._worker_thread = None

        if thread is not None:
            thread.join(timeout=timeout)
            logger.info("MT5ObservationService stopped.")

    def _run_loop(self) -> None:
        """Background thread execution loop with backoff on failure."""
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                self.poll_once()
                backoff = 1.0  # reset on successful poll
            except MT5Error as exc:
                logger.debug("MT5 terminal unreachable during observation poll: %s", exc)
                backoff = min(backoff * 1.5, 30.0)
            except Exception as exc:
                logger.error("Unexpected error in MT5 observation poll loop: %s", exc, exc_info=True)
                backoff = min(backoff * 1.5, 30.0)

            sleep_time = min(self.poll_interval * backoff, 30.0)
            self._stop_event.wait(timeout=sleep_time)

    def poll_once(self) -> dict[str, Any]:
        """Perform a single deterministic synchronization cycle.

        Returns summary of detected events during this cycle.
        """
        with self._lock:
            events_detected: list[dict[str, Any]] = []

            # 1. Fetch current MT5 snapshots
            try:
                current_positions = {p.ticket: p for p in self.observer.get_open_positions()}
            except Exception as exc:
                raise MT5Error(f"Could not read MT5 positions: {exc}") from exc

            try:
                current_orders = {o.ticket: o for o in self.observer.get_pending_orders()}
            except Exception as exc:
                raise MT5Error(f"Could not read MT5 orders: {exc}") from exc

            try:
                recent_deals = self.observer.get_deals(count=50)
            except Exception as exc:
                recent_deals = []
                logger.debug("Could not read MT5 deals: %s", exc)

            # 2. Detect New Pending Orders
            for ticket, order in current_orders.items():
                if ticket not in self._known_orders:
                    self._known_orders[ticket] = order
                    evt_data = {
                        "ticket": ticket,
                        "symbol": order.symbol,
                        "order_type": order.type.value if hasattr(order.type, "value") else str(order.type),
                        "volume": order.volume_current,
                        "price": order.price_open,
                        "sl": order.sl,
                        "tp": order.tp,
                    }
                    self._emit_event(
                        EventType.ORDER_SUBMITTED,
                        f"Detected new MT5 pending order #{ticket} ({order.symbol} {order.volume_current} lots @ {order.price_open})",
                        evt_data,
                    )
                    events_detected.append({"type": "NEW_ORDER", **evt_data})

            # 3. Detect New Positions or Reconciliation
            for ticket, pos in current_positions.items():
                if ticket not in self._known_positions and ticket not in self._trade_position_map:
                    # New position detected
                    trade_id = self._handle_new_position(pos)
                    if trade_id:
                        self._trade_position_map[ticket] = trade_id
                        self._known_positions[ticket] = pos
                        events_detected.append({
                            "type": "NEW_POSITION",
                            "ticket": ticket,
                            "trade_id": trade_id,
                            "symbol": pos.symbol,
                            "volume": pos.volume,
                            "open_price": pos.price_open,
                        })
                elif ticket not in self._known_positions and ticket in self._trade_position_map:
                    # Pre-mapped position from database reconstruction
                    self._known_positions[ticket] = pos
                elif ticket in self._known_positions:
                    # Existing position: check for SL, TP, or Volume changes
                    prev_pos = self._known_positions[ticket]
                    trade_id = self._trade_position_map.get(ticket)

                    # Check SL modification
                    if abs((pos.sl or 0.0) - (prev_pos.sl or 0.0)) > 1e-6:
                        if trade_id:
                            self.journal_mgr.modify_stop_loss(
                                trade_id=trade_id,
                                new_stop_loss=pos.sl or 0.0,
                                reason="MT5 terminal SL modification",
                                actor="MT5Observer",
                            )
                        events_detected.append({
                            "type": "SL_MODIFIED",
                            "ticket": ticket,
                            "trade_id": trade_id,
                            "old_sl": prev_pos.sl,
                            "new_sl": pos.sl,
                        })

                    # Check TP modification
                    if abs((pos.tp or 0.0) - (prev_pos.tp or 0.0)) > 1e-6:
                        if trade_id:
                            self.journal_mgr.modify_take_profit(
                                trade_id=trade_id,
                                new_take_profit=pos.tp or 0.0,
                                reason="MT5 terminal TP modification",
                                actor="MT5Observer",
                            )
                        events_detected.append({
                            "type": "TP_MODIFIED",
                            "ticket": ticket,
                            "trade_id": trade_id,
                            "old_tp": prev_pos.tp,
                            "new_tp": pos.tp,
                        })

                    # Check Partial Close (volume reduction)
                    if pos.volume < prev_pos.volume - 1e-5:
                        volume_closed = round(prev_pos.volume - pos.volume, 2)
                        close_price = pos.price_current or pos.price_open
                        for deal in recent_deals:
                            if (
                                deal.position_id == ticket
                                and deal.ticket not in self._processed_deal_tickets
                                and deal.volume
                                and abs(deal.volume - volume_closed) < 1e-4
                            ):
                                close_price = deal.price
                                self._processed_deal_tickets.add(deal.ticket)
                                break

                        if trade_id:
                            self.journal_mgr.partial_close(
                                trade_id=trade_id,
                                lots_to_close=volume_closed,
                                close_price=close_price,
                                exit_reason=TradeExitReason.TAKE_PROFIT,
                                actor="MT5Observer",
                            )
                        events_detected.append({
                            "type": "PARTIAL_CLOSE",
                            "ticket": ticket,
                            "trade_id": trade_id,
                            "volume_closed": volume_closed,
                            "remaining_volume": pos.volume,
                            "close_price": close_price,
                        })

                    # Update known state
                    self._known_positions[ticket] = pos

            # 4. Detect Closed Positions (in _known_positions but no longer in current_positions)
            closed_tickets = [t for t in self._known_positions if t not in current_positions]
            for ticket in closed_tickets:
                pos = self._known_positions.pop(ticket)
                trade_id = self._trade_position_map.pop(ticket, None)

                # Look for matching exit deal
                exit_price = pos.price_current or pos.price_open
                exit_profit = pos.profit
                exit_commission = 0.0
                exit_swap = pos.swap
                exit_time = datetime.now(timezone.utc).isoformat()
                exit_reason = TradeExitReason.MANUAL

                for deal in recent_deals:
                    if deal.position_id == ticket and deal.ticket not in self._processed_deal_tickets:
                        exit_price = deal.price
                        exit_profit = deal.profit
                        exit_commission = deal.commission
                        exit_swap = deal.swap
                        exit_time = deal.time.isoformat() if hasattr(deal.time, "isoformat") else str(deal.time)
                        self._processed_deal_tickets.add(deal.ticket)
                        break

                # Determine exit reason (SL, TP, or MANUAL)
                if pos.sl and abs(exit_price - pos.sl) < (0.0005 if "JPY" not in pos.symbol else 0.05):
                    exit_reason = TradeExitReason.STOP_LOSS
                elif pos.tp and abs(exit_price - pos.tp) < (0.0005 if "JPY" not in pos.symbol else 0.05):
                    exit_reason = TradeExitReason.TAKE_PROFIT

                if trade_id:
                    self.journal_mgr.close_trade(
                        trade_id=trade_id,
                        close_price=exit_price,
                        exit_reason=exit_reason,
                        close_time=exit_time,
                        gross_profit=exit_profit,
                        commission=exit_commission,
                        swap=exit_swap,
                        actor="MT5Observer",
                    )
                    # Automatically trigger closed-trade pipeline (Phase 13)
                    if self.post_close_processor is not None:
                        try:
                            self.post_close_processor.process_closed_trade(trade_id=trade_id)
                        except Exception as proc_err:
                            logger.error("Error in automated post-close pipeline for trade %s: %s", trade_id, proc_err)

                events_detected.append({
                    "type": "POSITION_CLOSED",
                    "ticket": ticket,
                    "trade_id": trade_id,
                    "close_price": exit_price,
                    "exit_reason": exit_reason.value,
                    "profit": exit_profit,
                })

            return {
                "events_count": len(events_detected),
                "events": events_detected,
                "open_positions_count": len(current_positions),
                "pending_orders_count": len(current_orders),
            }

    def _handle_new_position(self, pos: MT5Position) -> str:
        """Reconcile new position against approved proposals or record as manual trade."""
        if self.auto_reconcile:
            match_results = self.journal_mgr.reconcile_broker_positions([pos], auto_reconcile=True)
            if match_results and match_results[0].is_matched and match_results[0].trade_id:
                return match_results[0].trade_id

        if isinstance(pos.type, ForexAction):
            action = pos.type
        elif str(pos.type).upper() in ("0", "BUY", "LONG", "FOREXACTION.LONG"):
            action = ForexAction.LONG
        else:
            action = ForexAction.SHORT
        trade = self.journal.record_trade_open(
            pair=pos.symbol,
            action=action,
            open_price=pos.price_open,
            stop_loss=pos.sl or 0.0,
            take_profit=pos.tp,
            lots=pos.volume,
            proposal_id=None,
            metadata={"broker_ticket": str(pos.ticket), "source": "MANUAL_UNPLANNED"},
        )
        self.journal_mgr.timeline.record_event(
            event_type=EventType.POSITION_OPENED,
            trade_id=trade.trade_id,
            actor="MT5Observer",
            description=f"Recorded manual unplanned trade #{pos.ticket} ({pos.symbol} {pos.volume} lots @ {pos.price_open})",
            payload={"ticket": pos.ticket, "symbol": pos.symbol, "volume": pos.volume, "price": pos.price_open},
        )
        return trade.trade_id

    def _emit_event(self, event_type: EventType, description: str, data: dict[str, Any]) -> None:
        """Log event to timeline and trigger callback if registered."""
        try:
            self.journal_mgr.timeline.record_event(
                event_type=event_type,
                actor="MT5Observer",
                description=description,
                payload=data,
            )
            if self.on_event_callback:
                self.on_event_callback(event_type.value, data)
        except Exception as exc:
            logger.warning("Failed emitting timeline event: %s", exc)
