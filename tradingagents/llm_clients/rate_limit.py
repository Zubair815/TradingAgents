"""Process-wide sliding-window pacing for hosted LLM free tiers."""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    value = int(raw)
    if value < 0:
        raise ValueError(f"{name} must be zero or greater")
    return value


def estimate_chat_tokens(messages: list[Any], kwargs: dict[str, Any], output_reserve: int = 512) -> int:
    """Conservatively estimate billed tokens without retaining prompt content."""
    characters = 0
    for message in messages:
        content = getattr(message, "content", message)
        characters += len(str(content)) + 16
    for key in ("tools", "response_format"):
        value = kwargs.get(key)
        if value is not None:
            try:
                characters += len(json.dumps(value, default=str))
            except (TypeError, ValueError):
                characters += len(str(value))
    return max(1, math.ceil(characters / 4) + max(0, int(output_reserve)))


@dataclass
class _Reservation:
    created_at: float
    tokens: int


class SlidingWindowRateLimiter:
    """Serialize requests against request/minute and token/minute budgets."""

    def __init__(
        self,
        *,
        requests_per_minute: int,
        tokens_per_minute: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.requests_per_minute = requests_per_minute
        self.tokens_per_minute = tokens_per_minute
        self.window_seconds = window_seconds
        self._clock = clock
        self._sleeper = sleeper
        self._events: deque[_Reservation] = deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0].created_at >= self.window_seconds:
            self._events.popleft()

    def acquire(self, estimated_tokens: int) -> _Reservation:
        reservation_tokens = max(1, int(estimated_tokens))
        if self.tokens_per_minute and reservation_tokens > self.tokens_per_minute:
            raise ValueError(
                "Estimated request size exceeds the configured Groq tokens-per-minute limit; "
                "reduce prompt size or TRADINGAGENTS_MAX_TOKENS"
            )

        while True:
            with self._lock:
                now = self._clock()
                self._prune(now)
                requests_full = bool(
                    self.requests_per_minute
                    and len(self._events) >= self.requests_per_minute
                )
                tokens_full = bool(
                    self.tokens_per_minute
                    and sum(event.tokens for event in self._events) + reservation_tokens
                    > self.tokens_per_minute
                )
                if not requests_full and not tokens_full:
                    reservation = _Reservation(now, reservation_tokens)
                    self._events.append(reservation)
                    return reservation
                wait_for = max(0.01, self.window_seconds - (now - self._events[0].created_at))
            self._sleeper(wait_for)

    def reconcile(self, reservation: _Reservation, actual_tokens: int | None) -> None:
        if actual_tokens is None or actual_tokens <= 0:
            return
        with self._lock:
            reservation.tokens = int(actual_tokens)


_LIMITERS: dict[str, SlidingWindowRateLimiter] = {}
_LIMITERS_LOCK = threading.Lock()


def get_groq_limiter(model: str) -> SlidingWindowRateLimiter:
    """Return one limiter per Groq model, matching Groq's per-model limits."""
    with _LIMITERS_LOCK:
        if model not in _LIMITERS:
            _LIMITERS[model] = SlidingWindowRateLimiter(
                requests_per_minute=_positive_int_env("TRADINGAGENTS_GROQ_RPM_LIMIT", 30),
                tokens_per_minute=_positive_int_env("TRADINGAGENTS_GROQ_TPM_LIMIT", 8000),
            )
        return _LIMITERS[model]
