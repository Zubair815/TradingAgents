"""Run-scoped LLM token accounting without retaining prompt or response text."""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _integer(mapping: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0, int(value))
    return 0


def _usage_mapping(response: Any) -> dict[str, Any] | None:
    direct = getattr(response, "usage_metadata", None)
    if isinstance(direct, dict) and direct:
        return direct
    metadata = getattr(response, "response_metadata", None)
    if not isinstance(metadata, dict):
        return None
    for key in ("token_usage", "usage"):
        candidate = metadata.get(key)
        if isinstance(candidate, dict) and candidate:
            return candidate
    return None


@dataclass
class UsageTracker:
    """Aggregate actual provider-reported token usage for one application run."""

    run_id: str
    provider: str | None = None
    model: str | None = None
    _records: list[dict[str, Any]] = field(default_factory=list, init=False)
    _seen: set[str] = field(default_factory=set, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def observe(self, response: Any) -> None:
        usage = _usage_mapping(response)
        if usage is None:
            return
        input_details = usage.get("input_token_details") or {}
        output_details = usage.get("output_token_details") or {}
        input_tokens = _integer(usage, "input_tokens", "prompt_tokens")
        output_tokens = _integer(usage, "output_tokens", "completion_tokens")
        total_tokens = _integer(usage, "total_tokens") or input_tokens + output_tokens
        cached_tokens = _integer(usage, "cached_tokens", "cache_read_input_tokens")
        if isinstance(input_details, dict):
            cached_tokens = cached_tokens or _integer(input_details, "cache_read", "cached_tokens")
        reasoning_tokens = _integer(usage, "reasoning_tokens")
        if isinstance(output_details, dict):
            reasoning_tokens = reasoning_tokens or _integer(output_details, "reasoning")
        if total_tokens <= 0 and input_tokens <= 0 and output_tokens <= 0:
            return

        response_id = getattr(response, "id", None) or f"object:{id(response)}"
        signature_payload = {
            "response_id": response_id,
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
            "cached": cached_tokens,
            "reasoning": reasoning_tokens,
            "model": self.model,
        }
        signature = hashlib.sha256(
            json.dumps(signature_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
        record = {
            "usage_id": signature,
            "run_id": self.run_id,
            "provider": self.provider,
            "model": self.model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cached_tokens": cached_tokens,
            "reasoning_tokens": reasoning_tokens,
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            if signature in self._seen:
                return
            self._seen.add(signature)
            self._records.append(record)

    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(record) for record in self._records]

    def summary(self) -> dict[str, Any]:
        records = self.records()
        return {
            "status": "available" if records else "unavailable",
            "source": "provider_metadata",
            "call_count": len(records),
            "input_tokens": sum(item["input_tokens"] for item in records),
            "output_tokens": sum(item["output_tokens"] for item in records),
            "total_tokens": sum(item["total_tokens"] for item in records),
            "cached_tokens": sum(item["cached_tokens"] for item in records),
            "reasoning_tokens": sum(item["reasoning_tokens"] for item in records),
        }


_ACTIVE_TRACKER: contextvars.ContextVar[UsageTracker | None] = contextvars.ContextVar(
    "tradingagents_usage_tracker", default=None
)


def observe_response_usage(response: Any) -> None:
    tracker = _ACTIVE_TRACKER.get()
    if tracker is not None:
        tracker.observe(response)


@contextlib.contextmanager
def track_usage(tracker: UsageTracker) -> Iterator[UsageTracker]:
    token = _ACTIVE_TRACKER.set(tracker)
    try:
        yield tracker
    finally:
        _ACTIVE_TRACKER.reset(token)
