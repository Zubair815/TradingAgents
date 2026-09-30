"""Bounded retention primitives for the single-process dashboard runtime.

The dictionaries managed by the web layer are transient presentation state.
Trading, journal, research, and learning evidence remains authoritative in its
existing persistent stores and is never modified by these helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "done"})


@dataclass(frozen=True)
class RetentionPolicy:
    stale_active_seconds: float = 30 * 60
    terminal_max_age_seconds: float = 24 * 60 * 60
    terminal_max_count: int = 100
    event_max_count: int = 500
    backtest_max_age_seconds: float = 7 * 24 * 60 * 60
    backtest_max_count: int = 100
    tombstone_max_age_seconds: float = 24 * 60 * 60
    tombstone_max_count: int = 200


DEFAULT_RETENTION_POLICY = RetentionPolicy()


def timestamp(value: Any) -> float | None:
    """Parse an ISO timestamp or numeric epoch without raising."""
    if isinstance(value, (int, float)):
        return float(value)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def record_timestamp(record: dict[str, Any], *, terminal: bool = False) -> float | None:
    keys = ("finished_at", "created_at", "started_at") if terminal else ("started_at", "created_at")
    for key in keys:
        parsed = timestamp(record.get(key))
        if parsed is not None:
            return parsed
    return None


def append_bounded(events: list[dict[str, Any]], event: dict[str, Any], limit: int) -> None:
    """Append while retaining the newest deterministic suffix."""
    events.append(event)
    overflow = len(events) - max(1, limit)
    if overflow > 0:
        del events[:overflow]


def oldest_excess_ids(
    records: dict[str, dict[str, Any]],
    ids: list[str],
    keep: int,
    *,
    terminal: bool = False,
) -> list[str]:
    """Return deterministic oldest IDs beyond a count limit."""
    if len(ids) <= max(0, keep):
        return []
    ordered = sorted(
        ids,
        key=lambda item_id: (
            record_timestamp(records[item_id], terminal=terminal) or 0.0,
            item_id,
        ),
    )
    return ordered[: len(ids) - max(0, keep)]


def prune_tombstones(
    tombstones: dict[str, dict[str, Any]], policy: RetentionPolicy, now: float
) -> None:
    expired = [
        item_id
        for item_id, item in tombstones.items()
        if now - float(item.get("expired_at", 0.0)) > policy.tombstone_max_age_seconds
    ]
    for item_id in expired:
        tombstones.pop(item_id, None)
    overflow = len(tombstones) - max(0, policy.tombstone_max_count)
    if overflow > 0:
        oldest = sorted(
            tombstones,
            key=lambda item_id: (float(tombstones[item_id].get("expired_at", 0.0)), item_id),
        )[:overflow]
        for item_id in oldest:
            tombstones.pop(item_id, None)
