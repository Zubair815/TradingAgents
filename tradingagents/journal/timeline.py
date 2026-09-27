"""Chronological Trade Audit Timeline & Markdown Visualizer (Phase 15).

Provides append-only chronological event recording and markdown timeline
rendering for complete lifecycle tracking from proposal generation to trade settlement.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from tradingagents.database.journal import ForexTradeJournal
from tradingagents.journal.models import EventType, TradeEvent

logger = logging.getLogger(__name__)

_EVENT_ICONS: dict[EventType, str] = {
    EventType.PROPOSAL_CREATED: "📝",
    EventType.RISK_EVALUATION: "🛡️",
    EventType.PROPOSAL_APPROVED: "✅",
    EventType.PROPOSAL_REJECTED: "❌",
    EventType.PROPOSAL_MODIFIED: "⚠️",
    EventType.PROPOSAL_EXPIRED: "⏰",
    EventType.PROPOSAL_EXECUTED: "⚡",
    EventType.PROPOSAL_SKIPPED: "⏭️",
    EventType.PROPOSAL_WAITING_USER: "⏳",
    EventType.PROPOSAL_INVALIDATED: "🚫",
    EventType.PROPOSAL_SUPERSEDED: "🔄",
    EventType.ORDER_CREATED: "📨",
    EventType.ORDER_SUBMITTED: "📨",
    EventType.DEAL_FILLED: "⚡",
    EventType.ORDER_FILLED: "⚡",
    EventType.POSITION_OPENED: "🚀",
    EventType.SL_CHANGED: "🛡️",
    EventType.STOP_LOSS_MODIFIED: "🛡️",
    EventType.TP_CHANGED: "🎯",
    EventType.TAKE_PROFIT_MODIFIED: "🎯",
    EventType.POSITION_ADDED: "➕",
    EventType.BREAK_EVEN_MOVE: "🔒",
    EventType.BREAKEVEN_APPLIED: "🔒",
    EventType.PARTIAL_CLOSE: "✂️",
    EventType.FINAL_CLOSE: "🏁",
    EventType.POSITION_CLOSED: "🏁",
    EventType.COMMISSION: "💸",
    EventType.SWAP: "🔄",
    EventType.FEE: "🏷️",
    EventType.NEWS_EVENT_ALERT: "📰",
    EventType.NOTE_ADDED: "📌",
    EventType.RECONCILIATION_MATCH: "🔗",
}


class EventTimeline:
    """Manages recording, querying, and rendering chronological trade events."""

    def __init__(self, journal: ForexTradeJournal | None = None) -> None:
        self.journal = journal
        self._in_memory_events: list[TradeEvent] = []

    def record_event(
        self,
        event_type: EventType | str,
        trade_id: str | None = None,
        proposal_id: str | None = None,
        broker_position_id: str | None = None,
        broker_order_id: str | None = None,
        broker_deal_id: str | None = None,
        old_value: Any = None,
        new_value: Any = None,
        price: float | None = None,
        volume: float | None = None,
        source: str | None = None,
        actor: str = "System",
        description: str = "",
        metadata: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        timestamp_utc: datetime | str | None = None,
        event_id: str | None = None,
    ) -> TradeEvent:
        """Record an immutable chronological event into the trade timeline."""
        evt_type = (
            event_type
            if isinstance(event_type, EventType)
            else EventType.from_str(str(event_type))
        )

        if isinstance(timestamp_utc, str):
            try:
                dt = datetime.fromisoformat(timestamp_utc.replace("Z", "+00:00"))
            except Exception:
                dt = datetime.now(timezone.utc)
        elif isinstance(timestamp_utc, datetime):
            dt = (
                timestamp_utc
                if timestamp_utc.tzinfo is not None
                else timestamp_utc.replace(tzinfo=timezone.utc)
            )
        else:
            dt = datetime.now(timezone.utc)

        eff_meta = metadata if metadata is not None else (payload or {})
        eff_source = source or actor

        event = TradeEvent(
            event_id=event_id or f"evt_{uuid.uuid4().hex[:12]}",
            trade_id=trade_id,
            proposal_id=proposal_id,
            broker_position_id=str(broker_position_id) if broker_position_id is not None else None,
            broker_order_id=str(broker_order_id) if broker_order_id is not None else None,
            broker_deal_id=str(broker_deal_id) if broker_deal_id is not None else None,
            event_type=evt_type,
            timestamp_utc=dt,
            old_value=old_value,
            new_value=new_value,
            price=price,
            volume=volume,
            source=eff_source,
            actor=actor,
            description=description,
            metadata=eff_meta,
            payload=eff_meta,
        )

        if self.journal is not None:
            self.journal.record_event(
                event_type=evt_type.value,
                trade_id=trade_id,
                proposal_id=proposal_id,
                broker_position_id=broker_position_id,
                broker_order_id=broker_order_id,
                broker_deal_id=broker_deal_id,
                old_value=old_value,
                new_value=new_value,
                price=price,
                volume=volume,
                source=eff_source,
                actor=actor,
                description=description,
                metadata=eff_meta,
                payload=eff_meta,
                event_id=event.event_id,
                timestamp_utc=dt.isoformat(),
            )
        else:
            # Uniqueness check for in-memory timeline
            is_dup_deal = broker_deal_id is not None and any(
                e.trade_id == trade_id
                and e.event_type == evt_type
                and e.broker_deal_id == str(broker_deal_id)
                for e in self._in_memory_events
            )
            is_dup_order = broker_order_id is not None and any(
                e.trade_id == trade_id
                and e.event_type == evt_type
                and e.broker_order_id == str(broker_order_id)
                for e in self._in_memory_events
            )
            if is_dup_deal or is_dup_order:
                return event
            self._in_memory_events.append(event)

        logger.debug(
            "Recorded trade event [%s] for trade=%s, prop=%s: %s",
            evt_type.value,
            trade_id,
            proposal_id,
            description,
        )
        return event

    def get_timeline_for_trade(self, trade_id: str) -> list[TradeEvent]:
        """Fetch chronological event stream for a specific trade."""
        if self.journal is not None:
            raw = self.journal.get_events(trade_id=trade_id)
            return [self._dict_to_event(r) for r in raw]
        return [e for e in self._in_memory_events if e.trade_id == trade_id]

    def get_timeline_for_proposal(self, proposal_id: str) -> list[TradeEvent]:
        """Fetch chronological event stream for a specific proposal."""
        if self.journal is not None:
            raw = self.journal.get_events(proposal_id=proposal_id)
            return [self._dict_to_event(r) for r in raw]
        return [e for e in self._in_memory_events if e.proposal_id == proposal_id]

    def get_events(
        self,
        trade_id: str | None = None,
        proposal_id: str | None = None,
        event_type: EventType | str | None = None,
        limit: int = 100,
    ) -> list[TradeEvent]:
        """Query chronological events with optional filtering."""
        str_type = (
            event_type.value
            if isinstance(event_type, EventType)
            else (str(event_type) if event_type else None)
        )
        if self.journal is not None:
            raw = self.journal.get_events(
                trade_id=trade_id,
                proposal_id=proposal_id,
                event_type=str_type,
                limit=limit,
            )
            return [self._dict_to_event(r) for r in raw]

        filtered = self._in_memory_events
        if trade_id:
            filtered = [e for e in filtered if e.trade_id == trade_id]
        if proposal_id:
            filtered = [e for e in filtered if e.proposal_id == proposal_id]
        if event_type:
            target = (
                event_type
                if isinstance(event_type, EventType)
                else EventType.from_str(str(event_type))
            )
            filtered = [e for e in filtered if e.event_type == target]
        return sorted(filtered, key=lambda x: x.timestamp_utc)[:limit]

    def render_markdown_timeline(
        self, trade_id: str | None = None, proposal_id: str | None = None
    ) -> str:
        """Render a formatted, visual Markdown audit timeline for a trade or proposal."""
        events = self.get_events(
            trade_id=trade_id, proposal_id=proposal_id, limit=200
        )
        target_name = f"Trade `{trade_id}`" if trade_id else f"Proposal `{proposal_id}`"

        if not events:
            return f"### Event Timeline: {target_name}\n\n*No chronological events recorded.*"

        lines = [
            f"### ⏱️ Chronological Event Timeline: {target_name}",
            "",
            "| Timestamp (UTC) | Event | Actor | Description | Key Details |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]

        for e in events:
            icon = _EVENT_ICONS.get(e.event_type, "📌")
            ts_str = e.timestamp_utc.strftime("%Y-%m-%d %H:%M:%S")
            evt_label = f"{icon} `{e.event_type.value}`"
            actor_label = f"**{e.actor}**"
            desc_label = e.description

            # Format payload details compactly
            if e.payload:
                items = [
                    f"`{k}`: {v}"
                    for k, v in e.payload.items()
                    if k not in ("notes", "reasoning")
                ]
                detail_str = ", ".join(items[:4])
            else:
                detail_str = "-"

            lines.append(
                f"| {ts_str} | {evt_label} | {actor_label} | {desc_label} | {detail_str} |"
            )

        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _dict_to_event(d: dict[str, Any]) -> TradeEvent:
        ts = d.get("timestamp_utc")
        if isinstance(ts, str):
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                dt = datetime.now(timezone.utc)
        elif isinstance(ts, datetime):
            dt = ts
        else:
            dt = datetime.now(timezone.utc)

        payload_data = d.get("payload") or d.get("metadata", {})
        return TradeEvent(
            event_id=d.get("event_id", ""),
            trade_id=d.get("trade_id"),
            proposal_id=d.get("proposal_id"),
            broker_position_id=d.get("broker_position_id"),
            broker_order_id=d.get("broker_order_id"),
            broker_deal_id=d.get("broker_deal_id"),
            event_type=EventType.from_str(d.get("event_type", "NOTE_ADDED")),
            timestamp_utc=dt,
            old_value=d.get("old_value"),
            new_value=d.get("new_value"),
            price=d.get("price"),
            volume=d.get("volume"),
            source=d.get("source") or d.get("actor", "System"),
            actor=d.get("actor", "System"),
            description=d.get("description", ""),
            metadata=payload_data,
            payload=payload_data,
        )
