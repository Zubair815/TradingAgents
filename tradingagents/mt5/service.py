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

from tradingagents.database.models import (
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
        self._lifecycle_lock = threading.Lock()
        self.last_poll_at = None
        self.last_successful_poll_at = None
        self.last_error = None
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None

        # In-memory tracking structures for state comparison & idempotency
        self._known_positions: dict[int, MT5Position] = {}
        self._known_orders: dict[int, MT5Order] = {}
        self._trade_position_map: dict[int, str] = {}  # ticket -> trade_id
        self._processed_deal_tickets: set[int] = set()
        self._pending_post_close: set[str] = set()
        self._closed_position_tickets: set[int] = set()

        # Reconstruct known state from journal
        self._reconstruct_state_from_journal()

    def _reconstruct_state_from_journal(self) -> None:
        """Reconstruct position-to-trade mappings and processed tickets from DB on startup."""
        with self._lock:
            try:
                for trade in self.journal.list_trades(status=TradeStatus.CLOSED, limit=1000000):
                    ticket = trade.metadata.get("broker_ticket")
                    if ticket is not None:
                        with contextlib.suppress(ValueError, TypeError):
                            self._closed_position_tickets.add(int(ticket))
                    if trade.metadata.get("post_close_status") in ("PENDING", "PROCESSING"):
                        self._pending_post_close.add(trade.trade_id)
                open_trades = self.journal.list_trades(status=TradeStatus.OPEN, limit=1000000)
                for trade in open_trades:
                    ticket_raw = trade.metadata.get("broker_ticket") or trade.metadata.get("ticket")
                    if ticket_raw is not None:
                        with contextlib.suppress(ValueError, TypeError):
                            ticket = int(ticket_raw)
                            self._trade_position_map[ticket] = trade.trade_id
                            self._known_positions[ticket] = MT5Position(
                                ticket=ticket, symbol=trade.pair, type=trade.action, volume=trade.lots,
                                identifier=trade.metadata.get("broker_position_id", ticket),
                                price_open=trade.open_price, sl=trade.stop_loss, tp=trade.take_profit or 0,
                                time=datetime.fromisoformat(trade.open_time_utc),
                            )

                # Also load existing processed deals/events from trade timeline to guarantee idempotency across restarts
                events = self.journal_mgr.timeline.get_events(limit=5000)
                for evt in events:
                    payload = evt.payload or {}
                    deal_ticket = payload.get("deal_ticket")
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
        with self._lifecycle_lock:
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

    def stop(self, timeout: float = 5.0) -> bool:
        """Bounded join; retain a live thread reference to prevent duplicate workers."""
        with self._lifecycle_lock:
            self._stop_event.set()
            thread = self._worker_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0, timeout))
        return not self.is_running

    def status(self):
        return {
            "service_running": self.is_running,
            "mt5_connected": bool(self.observer.connection.is_connected()),
            "last_poll_at": self.last_poll_at,
            "last_successful_poll_at": self.last_successful_poll_at,
            "last_error": self.last_error,
            "poll_interval": self.poll_interval,
            "tracked_positions": len(self._trade_position_map),
            "tracked_orders": len(self._known_orders),
        }

    def _run_loop(self) -> None:
        """Background thread execution loop with backoff on failure."""
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                if not self.observer.connection.is_connected():
                    self._stop_event.wait(self.poll_interval)
                    continue
                self.poll_once()
                backoff = 1.0  # reset on successful poll
            except MT5Error as exc:
                logger.debug("MT5 observation unavailable (%s)", type(exc).__name__)
                backoff = min(backoff * 1.5, 30.0)
            except Exception as exc:
                logger.error("MT5 observation failed (%s)", type(exc).__name__)
                backoff = min(backoff * 1.5, 30.0)

            sleep_time = min(self.poll_interval * backoff, 30.0)
            self._stop_event.wait(timeout=sleep_time)

    def poll_once(self) -> dict[str, Any]:
        """Observe snapshots and apply only broker-confirmed fills, once per deal."""
        from tradingagents.journal.broker_deals import record_broker_deal

        self.last_poll_at = datetime.now(timezone.utc).isoformat()
        try:
            with self._lock:
                current_positions = {p.ticket: p for p in self.observer.get_open_positions()}
                current_orders = {o.ticket: o for o in self.observer.get_pending_orders()}
                # Do not truncate history to 50 fills or infer closes from missing positions.
                earliest = min((p.time for p in [*self._known_positions.values(), *current_positions.values()]), default=None)
                deals = sorted(self.observer.get_deals(date_from=earliest, date_to=datetime.now(timezone.utc), count=None),
                               key=lambda d: (d.time, d.ticket))
                events = []
                for ticket, order in current_orders.items():
                    if ticket not in self._known_orders:
                        self.journal_mgr.timeline.record_event(
                            event_type=EventType.ORDER_SUBMITTED, broker_order_id=str(ticket),
                            event_id=f"mt5-order:{ticket}", actor="MT5Observer",
                            payload={"ticket": ticket, "symbol": order.symbol, "volume": order.volume_current},
                        )
                        events.append({"type": "NEW_ORDER", "ticket": ticket})
                self._known_orders = current_orders

                for ticket, pos in current_positions.items():
                    if ticket in self._closed_position_tickets:
                        continue  # A position snapshot can lag its confirmed final deal.
                    if ticket not in self._trade_position_map:
                        # Replay already observed scale-outs from the original volume.
                        prior_exits = sum(d.volume for d in deals if d.position_id == (pos.identifier or ticket)
                                          and d.entry in ("OUT", "OUT_BY"))
                        initial = pos.model_copy(update={"volume": pos.volume + prior_exits})
                        trade_id = self._handle_new_position(initial)
                        self._trade_position_map[ticket] = trade_id
                        self._known_positions[ticket] = pos
                        events.append({"type": "NEW_POSITION", "ticket": ticket, "trade_id": trade_id})
                    trade_id = self._trade_position_map[ticket]
                    trade = self.journal.get_trade(trade_id)
                    # Compare persisted levels, so reconnect/restart doesn't lose modifications.
                    if abs((pos.sl or 0)-(trade.stop_loss or 0)) > 1e-6:
                        self.journal_mgr.modify_stop_loss(trade_id, pos.sl or 0, actor="MT5Observer")
                        events.append({"type": "SL_MODIFIED", "ticket": ticket, "trade_id": trade_id})
                    if abs((pos.tp or 0)-(trade.take_profit or 0)) > 1e-6:
                        self.journal_mgr.modify_take_profit(trade_id, pos.tp or 0, actor="MT5Observer")
                        events.append({"type": "TP_MODIFIED", "ticket": ticket, "trade_id": trade_id})
                    self._known_positions[ticket] = pos

                for ticket, trade_id in list(self._trade_position_map.items()):
                    pos = self._known_positions.get(ticket)
                    position_id = (pos.identifier or ticket) if pos else ticket
                    for deal in deals:
                        if deal.position_id != position_id:
                            continue
                        kind = record_broker_deal(self.journal, trade_id, deal)
                        if kind:
                            self._processed_deal_tickets.add(deal.ticket)
                            if kind != "DEAL_FILLED":
                                events.append({"type": kind, "ticket": ticket, "trade_id": trade_id,
                                               "volume_closed": deal.volume, "deal_ticket": deal.ticket})
                    trade = self.journal.get_trade(trade_id)
                    if trade.status == TradeStatus.CLOSED:
                        self._closed_position_tickets.add(ticket)
                        self._trade_position_map.pop(ticket, None)
                        self._known_positions.pop(ticket, None)
                        self._pending_post_close.add(trade_id)
                for trade_id in list(self._pending_post_close):
                    try:
                        self.post_close_processor.process_closed_trade(trade_id=trade_id)
                    except Exception as exc:
                        self.journal.update_trade_metadata(trade_id, {
                            "post_close_status": "FAILED", "post_close_error": type(exc).__name__,
                        })
                        logger.error("Post-close processing failed for %s (%s)", trade_id, type(exc).__name__)
                    self._pending_post_close.discard(trade_id)
                if self.on_event_callback:
                    for event in events:
                        try:
                            self.on_event_callback(event["type"], event)
                        except Exception as exc:
                            logger.warning("Observation callback failed (%s)", type(exc).__name__)
                self.last_successful_poll_at = datetime.now(timezone.utc).isoformat()
                self.last_error = None
                return {"events_count": len(events), "events": events,
                        "open_positions_count": len(current_positions), "pending_orders_count": len(current_orders)}
        except Exception as exc:
            # Never expose vendor exception strings, which may contain credentials.
            self.last_error = type(exc).__name__
            raise

    def _handle_new_position(self, pos: MT5Position) -> str:
        """Use the existing matcher; retain ambiguous candidates for human review."""
        results = self.journal_mgr.reconcile_broker_positions([pos], auto_reconcile=self.auto_reconcile)
        match = results[0] if results else None
        if match and match.is_matched and match.trade_id:
            trade_id = match.trade_id
        else:
            trade_id, _ = self.journal_mgr.matcher.record_manual_unplanned_trade(pos)
        self.journal.update_trade_metadata(trade_id, {
            "broker_ticket": str(pos.ticket), "broker_position_id": pos.identifier or pos.ticket,
            "initial_stop_loss": pos.sl or 0, "initial_lots": pos.volume,
            "reconciliation": match.model_dump(mode="json") if match else {},
        })
        # Preserve broker opening time for the M1 holding-window request.
        with self.journal._lock:
            conn = self.journal._get_connection()
            try:
                with conn:
                    conn.execute("UPDATE trades SET open_time_utc=? WHERE trade_id=?", (pos.time.isoformat(), trade_id))
            finally:
                if conn is not self.journal._mem_conn:
                    conn.close()
        return trade_id

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
