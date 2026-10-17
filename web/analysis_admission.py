"""Bounded admission and idempotency for expensive dashboard analyses."""

from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass


def _bounded_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


class AdmissionRejected(RuntimeError):
    """Raised when the bounded analysis queue has no remaining capacity."""


class IdempotencyConflict(RuntimeError):
    """Raised when one idempotency key is reused for a different request."""


@dataclass(frozen=True)
class AdmissionResult:
    run_id: str
    duplicate: bool = False


class AnalysisAdmissionController:
    """Thread-safe active/queued bounds with request idempotency.

    Waiting work is intentionally represented by a bounded number of worker
    threads because the dashboard is a local, single-user application. The
    controller prevents an unbounded thread burst while avoiding a larger job
    queue dependency.
    """

    def __init__(self, max_active: int | None = None, max_queued: int | None = None):
        self.max_active = max_active if max_active is not None else _bounded_int(
            "TRADINGAGENTS_MAX_ACTIVE_ANALYSES", 1, minimum=1, maximum=16
        )
        self.max_queued = max_queued if max_queued is not None else _bounded_int(
            "TRADINGAGENTS_MAX_QUEUED_ANALYSES", 2, minimum=0, maximum=64
        )
        self._condition = threading.Condition(threading.RLock())
        self._queued: OrderedDict[str, None] = OrderedDict()
        self._active: set[str] = set()
        self._idempotency: OrderedDict[str, tuple[str, str]] = OrderedDict()

    @staticmethod
    def fingerprint(payload: str) -> str:
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def admit(
        self,
        run_id: str,
        *,
        idempotency_key: str | None = None,
        fingerprint: str | None = None,
    ) -> AdmissionResult:
        with self._condition:
            if idempotency_key:
                previous = self._idempotency.get(idempotency_key)
                if previous is not None:
                    previous_run, previous_fingerprint = previous
                    if previous_fingerprint != (fingerprint or ""):
                        raise IdempotencyConflict(
                            "Idempotency-Key was already used for a different analysis request"
                        )
                    return AdmissionResult(previous_run, duplicate=True)

            if len(self._active) + len(self._queued) >= self.max_active + self.max_queued:
                raise AdmissionRejected("The bounded analysis queue is full")

            self._queued[run_id] = None
            if idempotency_key:
                self._idempotency[idempotency_key] = (run_id, fingerprint or "")
                while len(self._idempotency) > 1024:
                    self._idempotency.popitem(last=False)
            self._condition.notify_all()
            return AdmissionResult(run_id)

    def start(self, run_id: str, cancelled: Callable[[], bool]) -> bool:
        with self._condition:
            while True:
                if cancelled():
                    self._queued.pop(run_id, None)
                    self._condition.notify_all()
                    return False
                is_next = next(iter(self._queued), None) == run_id
                if is_next and len(self._active) < self.max_active:
                    self._queued.pop(run_id, None)
                    self._active.add(run_id)
                    return True
                self._condition.wait(timeout=0.25)

    def release(self, run_id: str) -> None:
        with self._condition:
            self._queued.pop(run_id, None)
            self._active.discard(run_id)
            self._condition.notify_all()

    def snapshot(self) -> dict[str, int]:
        with self._condition:
            return {
                "active": len(self._active),
                "queued": len(self._queued),
                "max_active": self.max_active,
                "max_queued": self.max_queued,
            }

    def reset(self) -> None:
        with self._condition:
            self._queued.clear()
            self._active.clear()
            self._idempotency.clear()
            self._condition.notify_all()
